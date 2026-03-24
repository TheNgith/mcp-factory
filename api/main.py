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
import os as _os
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
from api.storage import _JOB_INVOCABLE_MAPS
from api.worker import _queue_worker_loop, _analyze_worker
from api.executor import _execute_tool
from api.chat import stream_chat
from api.generate import run_generate
from api.pipeline.helpers import _infer_param_desc

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

_GAP_RESOLUTION_ENABLED = _os.getenv("EXPLORE_ENABLE_GAP_RESOLUTION", "1").strip().lower() not in {
    "0", "false", "no", "off"
}


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"0", "false", "no", "off", ""}


def _as_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


def _normalize_explore_runtime_settings(body: dict[str, Any] | None) -> dict[str, Any]:
    body = body or {}
    raw = body.get("explore_settings") or {}
    if not isinstance(raw, dict):
        raw = {}

    mode = str(raw.get("mode") or "normal").strip().lower()
    if mode not in {"dev", "normal", "extended"}:
        mode = "normal"

    mode_defaults = {
        "dev": {
            "cap_profile": "dev",
            "max_rounds": 2,
            "max_tool_calls": 5,
            "gap_resolution_enabled": False,
            "clarification_questions_enabled": True,
        },
        "normal": {
            "cap_profile": "deploy",
            "max_rounds": 5,
            "max_tool_calls": 8,
            "gap_resolution_enabled": True,
            "clarification_questions_enabled": True,
        },
        "extended": {
            "cap_profile": "deploy",
            "max_rounds": 7,
            "max_tool_calls": 14,
            "gap_resolution_enabled": True,
            "clarification_questions_enabled": True,
        },
    }

    defaults = mode_defaults[mode]
    cap_profile = str(raw.get("cap_profile") or defaults["cap_profile"]).strip().lower()
    if cap_profile not in {"dev", "stabilize", "deploy"}:
        cap_profile = defaults["cap_profile"]

    # Allow per-request model override for A/B model comparison.
    # Must be a known deployment name; empty string means use env-var default.
    _model_override = str(raw.get("model") or "").strip()

    _instruction_fragment = str(raw.get("instruction_fragment") or "").strip()
    _context_density = str(raw.get("context_density") or "full").strip().lower()
    if _context_density not in {"full", "minimal", "none"}:
        _context_density = "full"

    return {
        "mode": mode,
        "cap_profile": cap_profile,
        "max_rounds": _as_int(raw.get("max_rounds"), int(defaults["max_rounds"]), 1, 12),
        "max_tool_calls": _as_int(raw.get("max_tool_calls"), int(defaults["max_tool_calls"]), 1, 24),
        "max_functions": _as_int(raw.get("max_functions"), 50, 1, 500),
        "min_direct_probes_per_function": _as_int(raw.get("min_direct_probes_per_function"), 1, 1, 5),
        "skip_documented": _as_bool(raw.get("skip_documented"), True),
        "deterministic_fallback_enabled": _as_bool(raw.get("deterministic_fallback_enabled"), True),
        "gap_resolution_enabled": _as_bool(raw.get("gap_resolution_enabled"), bool(defaults["gap_resolution_enabled"])),
        "clarification_questions_enabled": _as_bool(
            raw.get("clarification_questions_enabled"),
            bool(defaults["clarification_questions_enabled"]),
        ),
        "model": _model_override,
        "instruction_fragment": _instruction_fragment,
        "context_density": _context_density,
        "prior_job_id": str(raw.get("prior_job_id") or "").strip(),
        "checkpoint_id": str(raw.get("checkpoint_id") or "").strip(),
        "focus_functions": list(raw.get("focus_functions") or []),
        "skip_to_stage": str(raw.get("skip_to_stage") or "").strip(),
    }


def _normalize_ablation_tags(payload: dict[str, Any] | None) -> dict[str, Any]:
    payload = payload or {}

    def _as_optional_int(value: Any) -> int | None:
        if value is None or str(value).strip() == "":
            return None
        try:
            return int(value)
        except Exception:
            return None

    return {
        "prompt_profile_id": (payload.get("prompt_profile_id") or None),
        "layer": _as_optional_int(payload.get("layer")),
        "ablation_variable": (payload.get("ablation_variable") or None),
        "ablation_value": (payload.get("ablation_value") or None),
        "run_set_id": (payload.get("run_set_id") or None),
        "coordinator_cycle": _as_optional_int(payload.get("coordinator_cycle")),
        "playbook_step": (payload.get("playbook_step") or None),
    }

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


@app.post("/api/check-cache")
async def check_cache(body: dict[str, Any]):
    """Pre-flight cache check: given a binary's SHA-256, report if a cached
    discovery result exists in blob storage.
    Body: {sha256: "<64 hex chars>"}.
    """
    sha = (body.get("sha256") or "").strip().lower()
    if not sha or len(sha) != 64:
        raise HTTPException(400, "sha256 is required (64 hex chars)")
    blob_name = f"discovery-cache/{sha}/discovery_result.json"
    try:
        raw = _download_blob(ARTIFACT_CONTAINER, blob_name)
        data = json.loads(raw)
        invocables = data.get("invocables", [])
        return JSONResponse({
            "cached": True,
            "sha256": sha,
            "invocable_count": len(invocables),
            "cached_at": data.get("cached_at"),
            "invocable_names": [inv.get("name", "") for inv in invocables[:30]],
        })
    except Exception:
        return JSONResponse({"cached": False, "sha256": sha})


@app.post("/api/analyze-path")
async def analyze_path(body: dict[str, Any]):
    """Section 2.b: Analyze a file path already on the server's filesystem.
    Body: {path, hints?}. Returns {job_id, status_url}; poll GET /api/jobs/{id}.
    """
    path_str  = (body.get("path") or "").strip()
    hints     = (body.get("hints") or "").strip()
    use_cases = (body.get("use_cases") or "").strip()
    ablation_tags = _normalize_ablation_tags(body)

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
        **ablation_tags,
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
    skip_cache: str = Form(default=""),
    prompt_profile_id: str | None = Form(default=None),
    layer: int | None = Form(default=None),
    ablation_variable: str | None = Form(default=None),
    ablation_value: str | None = Form(default=None),
    run_set_id: str | None = Form(default=None),
    coordinator_cycle: int | None = Form(default=None),
    playbook_step: str | None = Form(default=None),
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
    _skip = skip_cache.lower() in ("1", "true", "yes")
    ablation_tags = _normalize_ablation_tags(
        {
            "prompt_profile_id": prompt_profile_id,
            "layer": layer,
            "ablation_variable": ablation_variable,
            "ablation_value": ablation_value,
            "run_set_id": run_set_id,
            "coordinator_cycle": coordinator_cycle,
            "playbook_step": playbook_step,
        }
    )

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
        **ablation_tags,
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
    enqueued = blob_uploaded and _enqueue_analysis(job_id, blob_name, hints, original_name,
                                                        skip_cache=_skip)
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
            args=(job_id, tmp_path, hints, original_name, tmp_dir, _skip),
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
    from api.pipeline.orchestrator import _explore_worker

    body = body or {}
    invocables: list | None = body.get("invocables") if body else None
    runtime_settings = _normalize_explore_runtime_settings(body)

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

    _persist_job_status(job_id, {
        **(_get_job_status(job_id) or {}),
        "explore_phase": "queued",
        "explore_progress": "0/0",
        "explore_message": "Exploration queued",
        "explore_runtime": runtime_settings,
        "explore_cancel_requested": False,
        "explore_started_at": time.time(),
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
        {
            "job_id": job_id,
            "status": "exploring",
            "invocable_count": len(invocables),
            "explore_runtime": runtime_settings,
        },
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
    from api.pipeline.orchestrator import _explore_worker
    from api.storage import _JOB_INVOCABLE_MAPS, _patch_finding

    body = body or {}
    runtime_settings = _normalize_explore_runtime_settings(body)
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
        "explore_runtime": runtime_settings,
        "explore_cancel_requested": False,
        "explore_started_at": time.time(),
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
            "explore_runtime": runtime_settings,
        },
        status_code=202,
    )


@app.post("/api/jobs/{job_id}/explore-cancel")
async def cancel_explore(job_id: str):
    """Request cancellation of an active explore/refine run.

    Cancellation is cooperative: the worker checks this flag between operations,
    then finalizes and persists partial artifacts before exiting.
    """
    current = _get_job_status(job_id)
    if current is None:
        raise HTTPException(404, f"Job '{job_id}' not found")

    _persist_job_status(
        job_id,
        {
            **current,
            "explore_cancel_requested": True,
            "explore_phase": "cancel_requested",
            "explore_message": "Cancellation requested — stopping after current step…",
            "updated_at": time.time(),
        },
    )
    return JSONResponse({"job_id": job_id, "status": "cancel_requested"}, status_code=202)


@app.post("/api/jobs/{job_id}/answer-gaps")
async def answer_gaps(job_id: str, body: dict[str, Any] = None):
    """Accept user answers to gap questions and merge them into vocab + hints.

    Body: {"answers": [{"function": "CS_ProcessPayment", "question": "...", "answer": "..."}]}

    Each answer is:
    - Written into vocab.json under gap_answers so the chat LLM sees it in context
    - Appended to status.json hints
    - Triggers a targeted mini-session per function and re-runs gap generation
    """
    body = body or {}
    answers: list[dict] = body.get("answers") or []
    if not answers:
        raise HTTPException(400, "No answers provided.")

    _cur_settings = (_get_job_status(job_id) or {}).get("explore_runtime") or {}
    _gap_enabled = _as_bool(_cur_settings.get("gap_resolution_enabled"), _GAP_RESOLUTION_ENABLED)
    if not _gap_enabled:
        return JSONResponse(
            {
                "job_id": job_id,
                "status": "gap_resolution_disabled",
                "message": "Gap resolution/mini-sessions are disabled for this run.",
            },
            status_code=202,
        )

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
    _answers_by_fn: dict[str, str] = {}
    for a in answers:
        fn = a.get("function") or "general"
        ans_text = (a.get("answer") or "").strip()
        if ans_text:
            new_parts.append(f"DOMAIN ANSWER ({fn}): {ans_text}")
            _answers_by_fn[fn] = ans_text
    combined = " | ".join(new_parts)

    # Mark matching clarification questions as answered so phase closure can
    # deterministically transition to done when applicable.
    _updated_questions = []
    for _q in (current.get("explore_questions") or []):
        if not isinstance(_q, dict):
            _updated_questions.append(_q)
            continue
        _fn = _q.get("function") or "general"
        _ans = _answers_by_fn.get(_fn)
        if _ans:
            _updated_questions.append({**_q, "answered": True, "answer": _ans})
        else:
            _updated_questions.append(_q)

    _persist_job_status(
        job_id,
        {
            **current,
            "hints": combined,
            "explore_questions": _updated_questions,
            "updated_at": time.time(),
        },
    )

    logger.info("[%s] answer-gaps: stored %d answers", job_id, len([a for a in answers if a.get("answer", "").strip()]))

    # Trigger targeted mini-sessions for every function that received an answer,
    # then re-run gap generation to surface any remaining unknowns.
    from api.pipeline.s06_gaps.gap_resolution import _run_gap_answer_mini_sessions
    inv_map = _JOB_INVOCABLE_MAPS.get(job_id)
    if not inv_map:
        try:
            raw_inv = _download_blob(ARTIFACT_CONTAINER, f"{job_id}/invocables_map.json")
            inv_map = json.loads(raw_inv)
        except Exception:
            inv_map = None
    if inv_map:
        all_invocables = list(inv_map.values())
        t = threading.Thread(
            target=_run_gap_answer_mini_sessions,
            args=(job_id, all_invocables),
            daemon=True,
            name=f"gap-refinement-{job_id}",
        )
        t.start()
        logger.info("[%s] answer-gaps: spawned gap mini-session worker for %d invocables",
                    job_id, len(all_invocables))
        return JSONResponse(
            {"job_id": job_id, "answers_stored": len(gap_answers), "status": "re_exploring"},
            status_code=202,
        )
    else:
        logger.warning("[%s] answer-gaps: no invocables found — answers stored but no re-exploration triggered", job_id)
        return JSONResponse({"job_id": job_id, "answers_stored": len(gap_answers)})



# Session-snapshot and report endpoints live in routes_session.py
from api.routes_session import router as session_router  # noqa: E402
app.include_router(session_router)
