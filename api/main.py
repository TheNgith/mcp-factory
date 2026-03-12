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
)
from api.worker import _queue_worker_loop, _analyze_worker
from api.executor import _execute_tool
from api.chat import run_chat, stream_chat
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
    """Streaming agentic chat — yields SSE events as tool calls execute.
    Clients that want a blocking JSON response should call run_chat() directly.
    """
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

