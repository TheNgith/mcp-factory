"""
api/main.py – MCP Factory REST API
Exposes the discovery pipeline and MCP generation over HTTP.
Integrates with Azure Blob Storage, Azure OpenAI, and Application Insights.

Logic lives in the sub-modules imported below; this file contains only the
FastAPI app, middleware, endpoint handlers (thin wrappers), and startup event.
"""

from __future__ import annotations

import json
import logging
import secrets as _secrets
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

# ── Sub-module imports — constants, telemetry, helpers ────────────────────
from api.config import (
    PIPELINE_API_KEY,
    UPLOAD_CONTAINER,
    ARTIFACT_CONTAINER,
    _SAFE_PATH_PREFIXES,
)
from api.storage import (
    _upload_to_blob,
    _download_blob,
    _persist_job_status,
    _get_job_status,
    _get_invocable,
    _enqueue_analysis,
    _load_findings,
)
from api.storage import _JOB_INVOCABLE_MAPS as _job_inv_maps  # for explore/report
from api.worker import _queue_worker_loop, _analyze_worker
from api.executor import _execute_tool
from api.chat import stream_chat
from api.generate import run_generate
from api.explore_phases import _infer_param_desc

logging.basicConfig(level=logging.INFO, stream=sys.stdout)
logger = logging.getLogger("mcp_factory.api")
# Attach a dedicated stdout handler so our logs survive uvicorn's
# logging.config.dictConfig() which resets root handlers on startup.
if not logger.handlers:
    _stdout_handler = logging.StreamHandler(sys.stdout)
    _stdout_handler.setFormatter(logging.Formatter("%(levelname)s:%(name)s:%(message)s"))
    logger.addHandler(_stdout_handler)
    logger.propagate = False  # don't double-log through the root logger

# Serialize analyze-path requests so concurrent calls don't compete on the
# bridge's cancel-and-replace logic (_active_kill_event / _active_target_stem).
_analyze_path_lock = threading.Lock()

# ── FastAPI app ────────────────────────────────────────────────────────────
app = FastAPI(title="MCP Factory API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def _pipeline_api_key_guard(request: Request, call_next):
    """If PIPELINE_API_KEY is set, every non-health request must present it."""
    if not PIPELINE_API_KEY:
        return await call_next(request)
    if request.url.path == "/health":
        return await call_next(request)
    provided = request.headers.get("X-Pipeline-Key", "")
    if not provided or not _secrets.compare_digest(provided, PIPELINE_API_KEY):
        return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
    return await call_next(request)


@app.middleware("http")
async def _request_timing(request: Request, call_next):
    """Emit per-request latency so az containerapp logs can act as an APM-lite."""
    t0 = time.perf_counter()
    response = await call_next(request)
    dt_ms = (time.perf_counter() - t0) * 1000.0
    logger.info(
        "[http] %s %s -> %s in %.1f ms",
        request.method,
        request.url.path,
        response.status_code,
        dt_ms,
    )
    return response


# ── Startup ────────────────────────────────────────────────────────────────

def _startup_probe() -> None:
    from api.storage import _upload_to_blob, _queue_service_client
    from api.config import ARTIFACT_CONTAINER, ANALYSIS_QUEUE, GUI_BRIDGE_URL, GUI_BRIDGE_SECRET

    # Probe 1: Blob Storage
    try:
        _upload_to_blob(ARTIFACT_CONTAINER, "_probe/startup.txt", b"ok")
        logger.info("STARTUP ✓ Blob Storage reachable (container: %s)", ARTIFACT_CONTAINER)
    except Exception as exc:
        logger.error("STARTUP ✗ Blob Storage unreachable: %s", exc)

    # Probe 2: Storage Queue
    try:
        svc = _queue_service_client()
        if svc:
            svc.get_queue_client(ANALYSIS_QUEUE).get_queue_properties()
            logger.info("STARTUP ✓ Storage Queue reachable (queue: %s)", ANALYSIS_QUEUE)
        else:
            logger.warning("STARTUP — Storage Queue not configured")
    except Exception as exc:
        logger.error("STARTUP ✗ Storage Queue unreachable: %s", exc)

    # Probe 3: GUI Bridge
    if GUI_BRIDGE_URL and GUI_BRIDGE_SECRET:
        try:
            import httpx
            r = httpx.get(
                f"{GUI_BRIDGE_URL}/health",
                headers={"X-Bridge-Key": GUI_BRIDGE_SECRET},
                timeout=10,
            )
            r.raise_for_status()
            logger.info("STARTUP ✓ GUI Bridge reachable (%s)", GUI_BRIDGE_URL)
        except Exception as exc:
            logger.error("STARTUP ✗ GUI Bridge unreachable: %s", exc)
    else:
        logger.warning("STARTUP — GUI Bridge not configured (GUI_BRIDGE_URL or GUI_BRIDGE_SECRET missing)")


@app.on_event("startup")
async def _startup():
    threading.Thread(target=_queue_worker_loop, daemon=True, name="queue-worker").start()
    threading.Thread(target=_startup_probe, daemon=True, name="startup-probe").start()


# ── Endpoints ──────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "version": "1.0.0"}


@app.post("/api/analyze-path")
async def analyze_path(body: dict[str, Any]):
    """Section 2.b: Analyze a file path already on the server's filesystem.
    Body: {path, hints?}. Returns {job_id, status_url}; poll GET /api/jobs/{id}.
    """
    path_str  = (body.get("path") or "").strip()
    hints     = (body.get("hints") or "").strip()
    use_cases = (body.get("use_cases") or "").strip()

    if not path_str:
        raise HTTPException(400, "path is required")

    # Resolve symlinks/".." before prefix check to block path-traversal.
    target = Path(path_str).resolve()
    if not any(str(target).startswith(str(p.resolve())) for p in _SAFE_PATH_PREFIXES):
        raise HTTPException(
            403,
            f"Path {path_str!r} is outside the allowed directories. "
            "Upload the file instead.",
        )
    if not target.exists():
        raise HTTPException(
            400,
            f"Path not found on the server: {path_str!r}. "
            "In the cloud deployment the container does not have access to your "
            "local Windows filesystem — upload the file instead.",
        )

    job_id = str(uuid.uuid4())[:8]
    logger.info(f"[{job_id}] Async analyze installed path: {target}")

    _persist_job_status(job_id, {
        "status": "queued",
        "progress": 0,
        "message": f"Queued analysis for {target.name}",
        "hints": hints,
        "use_cases": use_cases,
        "component_name": target.stem,
        "result": None,
        "error": None,
        "created_at": time.time(),
        "updated_at": time.time(),
    })

    def _guarded_analyze():
        """Run under _analyze_path_lock so concurrent path-analyses are serialized."""
        with _analyze_path_lock:
            _analyze_worker(job_id, target, hints, target.name, None)

    t = threading.Thread(
        target=_guarded_analyze,
        daemon=True,
        name=f"worker-{job_id}",
    )
    t.start()

    return JSONResponse({
        "job_id": job_id,
        "status": "queued",
        "status_url": f"/api/jobs/{job_id}",
    }, status_code=202)


@app.post("/api/analyze")
async def analyze(
    file: UploadFile = File(...),
    hints: str = Form(default=""),
    use_cases: str = Form(default=""),
):
    """Section 2-3: Upload a binary, start async discovery.
    Returns {job_id, status_url}; poll GET /api/jobs/{id}.
    """
    job_id = str(uuid.uuid4())[:8]
    suffix = Path(file.filename).suffix or ".bin"
    blob_name = f"{job_id}/input{suffix}"

    content = await file.read()
    if len(content) == 0:
        raise HTTPException(400, "Empty file uploaded")

    logger.info("[%s] STEP 1 \u2713  Received %s (%d bytes)", job_id, file.filename, len(content))

    original_name = Path(file.filename).name or f"upload{suffix}"

    blob_uploaded = False
    try:
        _upload_to_blob(UPLOAD_CONTAINER, blob_name, content)
        blob_uploaded = True
        logger.info("[%s] STEP 2 \u2713  Binary uploaded to Blob (uploads/%s)", job_id, blob_name)
    except Exception as exc:
        logger.error("[%s] STEP 2 \u2717  Binary upload to Blob failed: %s", job_id, exc)

    ok = _persist_job_status(job_id, {
        "status": "queued",
        "progress": 0,
        "message": f"Queued analysis for {original_name}",
        "hints": hints,
        "use_cases": use_cases,
        "component_name": Path(file.filename).stem,
        "result": None,
        "error": None,
        "created_at": time.time(),
        "updated_at": time.time(),
    })
    if ok:
        logger.info("[%s] STEP 3 \u2713  Initial status written to Blob", job_id)
    else:
        logger.error("[%s] STEP 3 \u2717  Initial status failed to write to Blob", job_id)

    # Enqueue for durable processing; fall back to a direct thread if unavailable.
    enqueued = blob_uploaded and _enqueue_analysis(job_id, blob_name, hints, original_name)
    if enqueued:
        logger.info("[%s] STEP 4 \u2713  Job enqueued to Storage Queue", job_id)
    else:
        logger.warning(
            "[%s] STEP 4 \u2717  Queue unavailable (blob_uploaded=%s) \u2014 falling back to direct thread. "
            "Job status may be lost on pod restart or cross-pod poll.",
            job_id, blob_uploaded,
        )
        tmp_dir  = Path(tempfile.mkdtemp(prefix=f"upload_{job_id}_"))
        tmp_path = tmp_dir / original_name
        tmp_path.write_bytes(content)
        threading.Thread(
            target=_analyze_worker,
            args=(job_id, tmp_path, hints, original_name, tmp_dir),
            daemon=True,
            name=f"worker-{job_id}",
        ).start()

    return JSONResponse({
        "job_id": job_id,
        "status": "queued",
        "status_url": f"/api/jobs/{job_id}",
    }, status_code=202)


@app.get("/api/jobs/{job_id}")
async def get_job_status(job_id: str):
    """P3: Poll async job status. status ∈ {queued, running, done, error}."""
    state = _get_job_status(job_id)
    if state is None:
        raise HTTPException(404, f"Job '{job_id}' not found")
    return JSONResponse({"job_id": job_id, **state})


@app.post("/api/generate")
async def generate(body: dict[str, Any]):
    """
    Section 4: Accept selected invocables, generate MCP server JSON definition.
    Returns the MCP tool schema ready for an LLM to consume.
    """
    return JSONResponse(run_generate(body))


@app.post("/api/execute")
def execute_tool(body: dict[str, Any]):
    """Execute a tool call. Body: {job_id?, tool_name, arguments, invocable?}."""
    tool_name  = body.get("tool_name", "")
    arguments  = body.get("arguments", {})
    job_id     = body.get("job_id", "")
    inline_inv = body.get("invocable")

    if not tool_name:
        raise HTTPException(400, "tool_name is required")

    if inline_inv:
        inv = inline_inv
    elif job_id:
        inv = _get_invocable(job_id, tool_name)
        if inv is None:
            raise HTTPException(
                404,
                f"Tool '{tool_name}' not found for job '{job_id}'. "
                "Register invocables via /api/generate first.",
            )
    else:
        raise HTTPException(400, "Provide job_id or invocable")

    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            arguments = {}

    logger.info(f"[execute] {tool_name} args={arguments}")
    result = _execute_tool(inv, arguments)
    return JSONResponse({"tool_name": tool_name, "result": result})


@app.post("/api/chat")
async def chat(body: dict[str, Any]):
    """Streaming agentic chat — yields SSE events as tool calls execute."""
    return StreamingResponse(
        stream_chat(body),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/download/{job_id}/{filename}")
def download(job_id: str, filename: str):
    """Section 5: Download a generated artifact from Blob Storage."""
    blob_name = f"{job_id}/{filename}"
    try:
        data = _download_blob(ARTIFACT_CONTAINER, blob_name)
    except Exception as e:
        raise HTTPException(404, f"Artifact not found: {e}")

    return StreamingResponse(
        iter([data]),
        media_type="application/json",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.get("/api/findings/{job_id}")
def get_findings(job_id: str):
    """Return all LLM-recorded findings for a job as JSON.

    Findings are written by the record_finding synthetic tool during chat
    sessions and persist in Blob Storage.  Clients can download this to get
    a human-readable reverse-engineering document produced by the LLM.
    """
    findings = _load_findings(job_id)
    return JSONResponse(content={"job_id": job_id, "findings": findings})


@app.post("/api/jobs/{job_id}/explore")
async def explore_job(job_id: str, body: dict[str, Any] = None):
    """Spawn an autonomous exploration worker for a job.

    The worker iterates over the job's invocables, calls each with probe values,
    and calls enrich_invocable + record_finding to enrich the schema.
    Returns immediately; poll GET /api/jobs/{job_id} for explore_phase progress.

    Optional body: {"invocables": [...]}  — if omitted, uses the invocables
    already registered for this job.
    """
    from api.explore import _explore_worker

    body = body or {}
    invocables: list | None = body.get("invocables") if body else None

    if not invocables:
        # Load from in-memory registry (populated by /api/generate)
        from api.storage import _JOB_INVOCABLE_MAPS
        inv_map = _JOB_INVOCABLE_MAPS.get(job_id)
        if not inv_map:
            # Try blob
            try:
                raw = _download_blob(ARTIFACT_CONTAINER, f"{job_id}/invocables_map.json")
                inv_map = json.loads(raw)
            except Exception:
                inv_map = None

        if not inv_map:
            raise HTTPException(
                404,
                f"No invocables found for job '{job_id}'. "
                "Call /api/generate first, or pass invocables in the request body.",
            )
        invocables = list(inv_map.values())

    current = _get_job_status(job_id)
    if current is None:
        # Create a minimal status entry so the worker can update it
        _persist_job_status(job_id, {
            "status": "done",
            "explore_phase": "queued",
            "progress": 100,
            "message": "Exploration queued",
            "result": None,
            "error": None,
            "created_at": time.time(),
            "updated_at": time.time(),
        })

    t = threading.Thread(
        target=_explore_worker,
        args=(job_id, invocables),
        daemon=True,
        name=f"explore-{job_id}",
    )
    t.start()

    logger.info("[%s] Exploration worker spawned for %d invocables", job_id, len(invocables))
    return JSONResponse(
        {"job_id": job_id, "status": "exploring", "invocable_count": len(invocables)},
        status_code=202,
    )


@app.post("/api/jobs/{job_id}/refine")
async def refine_job(job_id: str, body: dict[str, Any] = None):
    """Targeted re-exploration based on user feedback after initial discovery.

    Accepts user corrections and a list of specific functions to re-explore.
    Only re-runs the named functions — already-correct functions are untouched.
    Also accepts free-text feedback that is injected as high-priority hints.

    Body:
      {
        "corrections": "CS_GetAccountBalance description is wrong — amounts are millicents",
        "target_functions": ["CS_GetAccountBalance", "CS_ProcessRefund"],
        "missing": "CS_ApplyDiscount — applies a percentage discount to an order"
      }

    Returns 202 immediately; poll GET /api/jobs/{job_id} for explore_phase progress.
    """
    from api.explore import _explore_worker
    from api.storage import _JOB_INVOCABLE_MAPS, _patch_finding

    body = body or {}
    corrections: str = (body.get("corrections") or "").strip()
    target_names: list[str] = body.get("target_functions") or []
    missing_desc: str = (body.get("missing") or "").strip()

    # Load all invocables for this job
    inv_map = _JOB_INVOCABLE_MAPS.get(job_id)
    if not inv_map:
        try:
            raw = _download_blob(ARTIFACT_CONTAINER, f"{job_id}/invocables_map.json")
            inv_map = json.loads(raw)
        except Exception:
            inv_map = None
    if not inv_map:
        raise HTTPException(404, f"No invocables found for job '{job_id}'.")

    all_invocables = list(inv_map.values())

    # Determine target set — default to all if none specified
    if target_names:
        target_invocables = [inv for inv in all_invocables if inv["name"] in target_names]
        if not target_invocables:
            raise HTTPException(400, f"None of the specified functions found in job '{job_id}'.")
    else:
        target_invocables = all_invocables

    # Clear existing findings for targeted functions so re-exploration is clean
    for fn_name in [inv["name"] for inv in target_invocables]:
        try:
            _patch_finding(job_id, fn_name, {"status": "pending_refinement", "finding": "", "working_call": None})
        except Exception:
            pass

    # Inject corrections + missing info into job status as high-priority hints
    current = _get_job_status(job_id) or {}
    existing_hints = current.get("hints", "")
    new_hints_parts = [existing_hints] if existing_hints else []
    if corrections:
        new_hints_parts.append(f"CORRECTION: {corrections}")
    if missing_desc:
        new_hints_parts.append(f"MISSING FUNCTION: {missing_desc}")
    combined_hints = " | ".join(new_hints_parts)
    _persist_job_status(job_id, {
        **current,
        "hints": combined_hints,
        "explore_phase": "queued",
        "explore_progress": "0/0",
        "explore_message": "Refinement queued…",
        "updated_at": time.time(),
    })

    t = threading.Thread(
        target=_explore_worker,
        args=(job_id, target_invocables),
        daemon=True,
        name=f"refine-{job_id}",
    )
    t.start()

    logger.info(
        "[%s] Refinement worker spawned: %d target functions, corrections=%r, missing=%r",
        job_id, len(target_invocables), corrections[:80] if corrections else "", missing_desc[:80] if missing_desc else "",
    )
    return JSONResponse(
        {
            "job_id": job_id,
            "status": "refining",
            "target_functions": [inv["name"] for inv in target_invocables],
            "corrections_applied": bool(corrections),
            "missing_added": bool(missing_desc),
        },
        status_code=202,
    )


@app.post("/api/jobs/{job_id}/answer-gaps")
async def answer_gaps(job_id: str, body: dict[str, Any] = None):
    """Accept user answers to gap questions and merge them into vocab + hints.

    Body: {"answers": [{"function": "CS_ProcessPayment", "question": "...", "answer": "..."}]}

    Each answer is:
    - Written into vocab.json under gap_answers so the chat LLM sees it in context
    - Appended to status.json hints so the next explore/refine pass uses it
    """
    body = body or {}
    answers: list[dict] = body.get("answers") or []
    if not answers:
        raise HTTPException(400, "No answers provided.")

    # Merge into vocab.json
    vocab: dict = {}
    try:
        raw = _download_blob(ARTIFACT_CONTAINER, f"{job_id}/vocab.json")
        vocab = json.loads(raw)
    except Exception:
        pass

    gap_answers = vocab.get("gap_answers") or {}
    for a in answers:
        fn = a.get("function") or "general"
        ans_text = (a.get("answer") or "").strip()
        if ans_text:
            gap_answers[fn] = ans_text
    vocab["gap_answers"] = gap_answers

    try:
        _upload_to_blob(ARTIFACT_CONTAINER, f"{job_id}/vocab.json", json.dumps(vocab).encode())
    except Exception as e:
        logger.warning("[%s] answer-gaps: failed to persist vocab: %s", job_id, e)

    # Append answers as hint corrections so next re-explore uses them
    current = _get_job_status(job_id) or {}
    existing_hints = current.get("hints", "")
    new_parts = [existing_hints] if existing_hints else []
    for a in answers:
        fn = a.get("function") or "general"
        ans_text = (a.get("answer") or "").strip()
        if ans_text:
            new_parts.append(f"DOMAIN ANSWER ({fn}): {ans_text}")
    combined = " | ".join(new_parts)
    _persist_job_status(job_id, {**current, "hints": combined, "updated_at": time.time()})

    logger.info("[%s] answer-gaps: stored %d answers", job_id, len([a for a in answers if a.get("answer", "").strip()]))
    return JSONResponse({"job_id": job_id, "answers_stored": len(gap_answers)})


@app.get("/api/jobs/{job_id}/session-snapshot")
async def session_snapshot(job_id: str):
    """Return a ZIP archive containing every artifact for this job.

    The ZIP is structured so save-session.ps1 can extract it directly into a
    dated sessions/ folder.  Contents:

      schema/01-pre-enrichment.json   ← mcp_schema snapshotted before explore
      schema/02-post-enrichment.json  ← mcp_schema after explore/refine
      artifacts/findings.json
      artifacts/vocab.json
      artifacts/api_reference.md
      artifacts/behavioral_spec.py
      hints.txt                       ← user hints + use_cases verbatim
      clarification-questions.md      ← formatted from explore_questions
      session-meta.json               ← job_id, component, timestamps
    """
    import io
    import zipfile as _zf

    status = _get_job_status(job_id) or {}

    zbuf = io.BytesIO()
    with _zf.ZipFile(zbuf, "w", _zf.ZIP_DEFLATED) as zf:

        # ── Schema snapshots ─────────────────────────────────────────────────
        for blob_name, zip_name in [
            (f"{job_id}/mcp_schema_t0.json",  "schema/01-pre-enrichment.json"),
            (f"{job_id}/mcp_schema.json",      "schema/02-post-enrichment.json"),
        ]:
            try:
                zf.writestr(zip_name, _download_blob(ARTIFACT_CONTAINER, blob_name))
            except Exception:
                pass  # blob not yet created is fine — optional at each stage

        # ── Core artifacts ───────────────────────────────────────────────────
        for blob_name, zip_name in [
            (f"{job_id}/findings.json",       "artifacts/findings.json"),
            (f"{job_id}/vocab.json",           "artifacts/vocab.json"),
            (f"{job_id}/api_reference.md",     "artifacts/api_reference.md"),
            (f"{job_id}/behavioral_spec.py",   "artifacts/behavioral_spec.py"),
            (f"{job_id}/invocables_map.json",  "artifacts/invocables_map.json"),
        ]:
            try:
                zf.writestr(zip_name, _download_blob(ARTIFACT_CONTAINER, blob_name))
            except Exception:
                pass

        # ── Hints ─────────────────────────────────────────────────────────────
        hints_lines = []
        if status.get("hints"):
            hints_lines.append("# User Hints\n")
            hints_lines.append(status["hints"])
        if status.get("use_cases"):
            hints_lines.append("\n\n# Use Cases\n")
            hints_lines.append(status["use_cases"])
        zf.writestr("hints.txt", "\n".join(hints_lines) if hints_lines else "(none)")

        # ── Clarification questions → markdown ────────────────────────────────
        gaps = status.get("explore_questions") or []
        if gaps:
            lines = ["# Clarification Questions from Discovery\n"]
            for i, g in enumerate(gaps, 1):
                q = g.get("question") or g.get("uncertainty") or ""
                td = g.get("technical_detail") or ""
                fn = g.get("function") or "general"
                lines.append(f"## {i}. {fn}\n**Question:** {q}\n")
                if td:
                    lines.append(f"**Technical detail:** `{td}`\n")
                ans = (status.get("vocab_gap_answers") or {}).get(fn, "")
                lines.append(f"**Answer:** {ans if ans else '(unanswered)'}\n")
            zf.writestr("clarification-questions.md", "\n".join(lines))

        # ── Chat transcript ────────────────────────────────────────────────
        try:
            zf.writestr("chat_transcript.txt",
                        _download_blob(ARTIFACT_CONTAINER, f"{job_id}/chat_transcript.txt"))
        except Exception:
            zf.writestr("chat_transcript.txt",
                        "(No transcript recorded yet. Start a chat session to generate one.)")
        # ── Executor trace (structured per-call diagnostics) ───────────
        try:
            zf.writestr("executor_trace.json",
                        _download_blob(ARTIFACT_CONTAINER, f"{job_id}/executor_trace.json"))
        except Exception:
            pass  # not yet generated — silently omit

        # ── Raw diagnosis records (one per chat message) ───────────────
        try:
            zf.writestr("diagnosis_raw.json",
                        _download_blob(ARTIFACT_CONTAINER, f"{job_id}/diagnosis_raw.json"))
        except Exception:
            pass  # not yet generated — silently omit
        # ── Session metadata ───────────────────────────────────────────────────
        meta = {
            "job_id":        job_id,
            "component":     status.get("component_name", "unknown"),
            "explore_phase": status.get("explore_phase"),
            "hints":         status.get("hints", ""),
            "use_cases":     status.get("use_cases", ""),
            "created_at":    status.get("created_at"),
            "updated_at":    status.get("updated_at"),
            "gap_count":     len(gaps),
            "finding_count": len(_load_findings(job_id)),
        }
        zf.writestr("session-meta.json", json.dumps(meta, indent=2))

        # ── Model operating context (exact system message the LLM receives) ──
        try:
            from api.chat import _build_system_message
            _inv_raw = _download_blob(ARTIFACT_CONTAINER, f"{job_id}/invocables_map.json")
            _inv_map = json.loads(_inv_raw)
            # invocables_map is a dict keyed by name — flatten to list
            _invocables = list(_inv_map.values()) if isinstance(_inv_map, dict) else _inv_map
            _sys_msg = _build_system_message(_invocables, job_id)
            _ctx_text = (
                "# Model Operating Context\n"
                "# This is the exact system message injected into every chat session for this job.\n"
                "# Captured at snapshot time — regenerate a new snapshot after any vocab/enrichment change.\n\n"
                + _sys_msg["content"]
            )
            zf.writestr("model_context.txt", _ctx_text)
        except Exception:
            pass  # non-fatal — snapshot still valid without it

    zbuf.seek(0)
    return StreamingResponse(
        iter([zbuf.read()]),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="session-{job_id}.zip"'},
    )


@app.get("/api/jobs/{job_id}/report")
async def get_report(job_id: str):
    """Generate a markdown documentation report for a job.

    Combines the enriched invocables schema with LLM-recorded findings.
    Returns a markdown document as text/markdown for direct download.
    """
    # Load invocables
    from api.storage import _JOB_INVOCABLE_MAPS
    inv_map = _JOB_INVOCABLE_MAPS.get(job_id)
    if not inv_map:
        try:
            raw = _download_blob(ARTIFACT_CONTAINER, f"{job_id}/invocables_map.json")
            inv_map = json.loads(raw)
        except Exception:
            inv_map = {}

    findings = _load_findings(job_id)
    findings_by_fn: dict[str, list] = {}
    for f in findings:
        fn = f.get("function", "unknown")
        findings_by_fn.setdefault(fn, []).append(f)

    lines: list[str] = [
        "# MCP Factory — DLL Documentation Report",
        "",
        f"**Job ID:** `{job_id}`  ",
        f"**Generated:** {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}  ",
        f"**Functions documented:** {len(inv_map)}",
        "",
        "---",
        "",
    ]

    for fn_name, inv in sorted(inv_map.items()):
        desc = inv.get("description") or inv.get("doc") or inv.get("signature") or ""
        lines.append(f"## `{fn_name}`")
        lines.append("")
        if desc:
            lines.append(desc)
            lines.append("")

        fn_findings = findings_by_fn.get(fn_name, [])
        params = inv.get("parameters") or []
        if params:
            lines.append("**Parameters:**")
            lines.append("")
            lines.append("| Name | Type | Description |")
            lines.append("|------|------|-------------|")
            for p in params:
                pname = p.get("name", "?")
                ptype = p.get("type") or p.get("json_type") or "unknown"
                pdesc = p.get("description", "")
                # Replace useless Ghidra boilerplate with a human-readable description
                if not pdesc or pdesc.startswith("Parameter recovered by Ghidra"):
                    pdesc = _infer_param_desc(pname, ptype, fn_findings)
                lines.append(f"| `{pname}` | `{ptype}` | {pdesc} |")
            lines.append("")

        ret_type = inv.get("return_type") or (inv.get("signature", {}) or {}) .get("return_type") if isinstance(inv.get("signature"), dict) else None
        if ret_type:
            lines.append(f"**Returns:** `{ret_type}`")
            lines.append("")

        if fn_findings:
            lines.append("**Findings from exploration:**")
            lines.append("")
            for f in fn_findings:
                param_part = f" (`{f['param']}`)" if f.get("param") else ""
                lines.append(f"- {param_part}{f.get('finding', '')}")
                if f.get("working_call"):
                    lines.append(f"  - Working call: `{json.dumps(f['working_call'])}`")
            lines.append("")

        lines.append("---")
        lines.append("")

    markdown = "\n".join(lines)

    # Also persist to blob for later download
    try:
        _upload_to_blob(
            ARTIFACT_CONTAINER,
            f"{job_id}/report.md",
            markdown.encode(),
        )
    except Exception as exc:
        logger.warning("[%s] Failed to persist report to blob: %s", job_id, exc)

    from fastapi.responses import Response as _Response
    return _Response(
        content=markdown,
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="report-{job_id}.md"'},
    )

