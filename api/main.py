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
    path_str = (body.get("path") or "").strip()
    hints    = (body.get("hints") or "").strip()

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


def _infer_param_desc(pname: str, ptype: str, fn_findings: list) -> str:
    """Produce a human-readable parameter description from type info and findings.
    Called when the stored description is just Ghidra boilerplate."""
    import re as _re
    t = (ptype or "").lower().replace("const ", "").strip()
    base = t.rstrip(" *").strip()
    is_ptr = "*" in t

    # Collect all finding/notes text for this function
    all_text = " ".join(
        (f.get("finding", "") + " " + f.get("notes", ""))
        for f in fn_findings
    )

    # Output integer pointer (uint *, ulong *, etc.)
    if is_ptr and base in {"uint", "ulong", "ushort", "int", "uint32_t", "dword"}:
        m = _re.search(rf"{_re.escape(pname)}\s*[=:]\s*(\S+)", all_text)
        val = f" (observed: {m.group(1)})" if m else ""
        return f"Output — receives integer result{val}"

    # Output buffer (undefined*, undefined4*, undefined8*)
    if is_ptr and base in {"undefined", "undefined2", "undefined4", "undefined8", "void"}:
        # Try to describe what the output contains from findings
        if "pipe-delimited" in all_text or "|" in all_text:
            return "Output buffer — receives pipe-delimited key=value result string"
        if "balance" in all_text and pname in ("param_2", "param_4"):
            return "Output buffer — receives balance or result data"
        return "Output buffer — receives result data (omit from call; auto-allocated)"

    # Input string (byte *)
    if t == "byte *":
        hints = []
        if "CUST-" in all_text:
            hints.append("customer ID e.g. 'CUST-001'")
        if "ORD-" in all_text:
            hints.append("order ID e.g. 'ORD-20040301-0042'")
        if hints:
            return "Input string — " + " or ".join(hints)
        return "Input string parameter"

    # Windows DLL entry point params
    if base == "hinstance__":
        return "DLL instance handle (Windows DllMain param)"
    if t == "void *":
        return "Reserved pointer (Windows DllMain param)"

    # Plain integers
    if base in {"uint", "ulong", "ushort", "int", "uint32_t", "dword", "ulong32"}:
        # Check if findings mention this param by name
        m = _re.search(rf"{_re.escape(pname)}\s*[=:]\s*(\S+)", all_text)
        val = f" (e.g. {m.group(1)})" if m else ""
        return f"Integer input parameter{val}"

    return f"Parameter of type {ptype}"


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

