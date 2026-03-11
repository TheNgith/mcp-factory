"""
api/main.py – MCP Factory REST API
Exposes the discovery pipeline and MCP generation over HTTP.
Integrates with Azure Blob Storage, Azure OpenAI, and Application Insights.
"""

from __future__ import annotations

import contextlib
import ctypes
import json
import logging
import os
import platform
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any

IS_WINDOWS = platform.system() == "Windows"

import secrets as _secrets
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

# ── Azure SDK imports ──────────────────────────────────────────────────────
from azure.identity import ManagedIdentityCredential, DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, generate_blob_sas, BlobSasPermissions
from openai import AzureOpenAI

# ── App Insights telemetry ─────────────────────────────────────────────────
APPINSIGHTS_CONN = os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING", "")
_AI_TRACER = None
if APPINSIGHTS_CONN:
    try:
        from opencensus.ext.azure.log_exporter import AzureLogHandler
        from opencensus.ext.azure.trace_exporter import AzureExporter
        from opencensus.trace.tracer import Tracer
        from opencensus.trace.samplers import AlwaysOnSampler
        _ai_handler = AzureLogHandler(connection_string=APPINSIGHTS_CONN)
        logging.getLogger().addHandler(_ai_handler)
        _AI_TRACER = Tracer(
            exporter=AzureExporter(connection_string=APPINSIGHTS_CONN),
            sampler=AlwaysOnSampler(),
        )
    except Exception:
        pass  # telemetry is best-effort


@contextlib.contextmanager
def _ai_span(name: str, **props):
    """Emit a custom App Insights event with duration and optional properties.

    Works via two channels:
    - Structured log entry (AzureLogHandler picks up custom_dimensions)
    - OpenCensus trace span (AzureExporter sends to Application Insights)
    Both are best-effort; failures are silently swallowed.
    """
    t0 = time.perf_counter()
    span = None
    try:
        if _AI_TRACER:
            span = _AI_TRACER.start_span(name=name)
            for k, v in props.items():
                span.add_attribute(k, str(v))
        yield
    finally:
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        if span and _AI_TRACER:
            # Flush in a daemon thread so a slow/unreachable App Insights
            # endpoint can never block the worker thread.
            try:
                threading.Thread(
                    target=_AI_TRACER.end_span, daemon=True, name="ai-flush"
                ).start()
            except Exception:
                pass
        dims = {"event": name, "duration_ms": elapsed_ms, **{k: str(v) for k, v in props.items()}}
        # Fire the custom_dimensions log in a daemon thread — AzureLogHandler
        # flushes synchronously and can block 90s if App Insights is slow.
        def _emit_telemetry(d=dims, n=name, ms=elapsed_ms):
            logger.info(
                "[telemetry] %s completed in %dms",
                n, ms,
                extra={"custom_dimensions": d},
            )
        threading.Thread(target=_emit_telemetry, daemon=True, name="ai-log").start()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("mcp_factory.api")

# ── Allowed base paths for /api/analyze-path ─────────────────────────────
# Restrict server-side path analysis to safe upload/temp directories so a
# caller cannot read arbitrary container filesystem paths (e.g. /proc/self/environ).
_SAFE_PATH_PREFIXES: tuple[Path, ...] = (
    Path(tempfile.gettempdir()),
    Path("/app"),          # container working directory
    Path("C:/"),           # Windows local runs
    Path("D:/"),
)

# ── Config from environment ────────────────────────────────────────────────
STORAGE_ACCOUNT   = os.getenv("AZURE_STORAGE_ACCOUNT", "mcpfactorystore")
OPENAI_ENDPOINT   = os.getenv("AZURE_OPENAI_ENDPOINT", "")
OPENAI_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")
MANAGED_CLIENT_ID = os.getenv("AZURE_CLIENT_ID", "")   # Managed Identity clientId

# ── Windows GUI bridge (optional) ─────────────────────────────────────────
# Set GUI_BRIDGE_URL to the Windows runner VM's bridge address, e.g.:
#   http://<vm-public-ip>:8090
# Set GUI_BRIDGE_SECRET to the same BRIDGE_SECRET configured on the VM.
# If either is absent the pipeline works normally (Linux-only analysis).
GUI_BRIDGE_URL    = os.getenv("GUI_BRIDGE_URL", "").rstrip("/")
GUI_BRIDGE_SECRET = os.getenv("GUI_BRIDGE_SECRET", "")

# ── Pipeline API key guard (optional) ────────────────────────────────────
# Set PIPELINE_API_KEY on the container to require a shared key on every
# request.  Leave unset for open access during local development.
# The UI container forwards X-Pipeline-Key from its own UI_API_KEY secret.
PIPELINE_API_KEY = os.getenv("PIPELINE_API_KEY", "")

# ── Generation module (P1: MCP SDK server emit) ───────────────────────────
_GEN_DIR = Path(__file__).parent.parent / "src" / "generation"
if str(_GEN_DIR) not in sys.path:
    sys.path.insert(0, str(_GEN_DIR))

# ── Azure credential (Managed Identity in ACA, DefaultAzureCredential locally) ──
def _get_credential():
    if MANAGED_CLIENT_ID:
        return ManagedIdentityCredential(client_id=MANAGED_CLIENT_ID)
    return DefaultAzureCredential()

def _blob_client() -> BlobServiceClient:
    credential = _get_credential()
    return BlobServiceClient(
        account_url=f"https://{STORAGE_ACCOUNT}.blob.core.windows.net",
        credential=credential,
        # Prevent silent TCP-drop from hanging the worker thread indefinitely.
        # connection_timeout: TCP connect; read_timeout: per-read-op deadline.
        connection_timeout=10,
        read_timeout=30,
    )

def _openai_client() -> AzureOpenAI:
    credential = _get_credential()
    # Get token for Azure OpenAI
    token = credential.get_token("https://cognitiveservices.azure.com/.default")
    return AzureOpenAI(
        azure_endpoint=OPENAI_ENDPOINT,
        api_version="2024-10-21",
        azure_ad_token=token.token,
    )

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

UPLOAD_CONTAINER   = "uploads"
ARTIFACT_CONTAINER = "artifacts"
ANALYSIS_QUEUE     = "analysis-jobs"
SRC_DISCOVERY_DIR  = Path(__file__).parent.parent / "src" / "discovery"

# ── Azure Storage Queue client ────────────────────────────────────────────
def _queue_service_client():
    """Return an authenticated QueueServiceClient, or None if not configured."""
    if not STORAGE_ACCOUNT:
        return None
    try:
        from azure.storage.queue import QueueServiceClient
        return QueueServiceClient(
            account_url=f"https://{STORAGE_ACCOUNT}.queue.core.windows.net",
            credential=_get_credential(),
        )
    except Exception as exc:
        logger.warning("Queue service client init failed: %s", exc)
        return None


def _enqueue_analysis(job_id: str, blob_name: str, hints: str, original_name: str) -> bool:
    """Push an analysis job onto the Storage Queue. Returns True on success."""
    svc = _queue_service_client()
    if not svc:
        return False
    try:
        qc = svc.get_queue_client(ANALYSIS_QUEUE)
        msg = json.dumps({
            "job_id":        job_id,
            "blob_name":     blob_name,
            "hints":         hints,
            "original_name": original_name,
        })
        qc.send_message(msg, visibility_timeout=0)
        logger.info("[%s] Enqueued analysis job.", job_id)
        return True
    except Exception as exc:
        logger.warning("[%s] Failed to enqueue: %s", job_id, exc)
        return False


def _queue_worker_loop() -> None:
    """Background thread: poll the analysis queue and process jobs durably.

    - Picks one message at a time with a 10-minute visibility timeout.
    - Downloads the uploaded binary from Blob Storage, runs _analyze_worker.
    - Deletes the message only after the worker finishes (done OR error).
    - If the container restarts mid-analysis the msg becomes visible again
      and is retried automatically by the next instance.
    """
    svc = _queue_service_client()
    if not svc:
        logger.info("No queue configured — queue worker not started.")
        return

    qc = svc.get_queue_client(ANALYSIS_QUEUE)
    try:
        qc.create_queue()
    except Exception:
        pass  # already exists

    logger.info("Queue worker started — polling '%s'.", ANALYSIS_QUEUE)
    while True:
        try:
            messages = list(qc.receive_messages(max_messages=1, visibility_timeout=600))
            if not messages:
                time.sleep(3)
                continue

            msg = messages[0]
            try:
                data         = json.loads(msg.content)
                job_id        = data["job_id"]
                blob_name     = data["blob_name"]
                hints         = data.get("hints", "")
                original_name = data.get("original_name", "upload.bin")
            except Exception as exc:
                logger.error("Malformed queue message, discarding: %s", exc)
                qc.delete_message(msg)
                continue

            # Download the uploaded file from Blob Storage.
            try:
                content = _download_blob(UPLOAD_CONTAINER, blob_name)
            except Exception as exc:
                logger.error("[%s] Blob download failed, skipping job: %s", job_id, exc)
                _persist_job_status(job_id, {
                    "status": "error", "progress": 0,
                    "message": "Blob download failed",
                    "result": None, "error": str(exc),
                    "created_at": time.time(), "updated_at": time.time(),
                })
                qc.delete_message(msg)
                continue

            suffix   = Path(original_name).suffix or ".bin"
            tmp_dir  = Path(tempfile.mkdtemp(prefix=f"queue_{job_id}_"))
            tmp_path = tmp_dir / original_name
            tmp_path.write_bytes(content)

            # Run synchronously in this thread (one job at a time per worker).
            t = threading.Thread(
                target=_analyze_worker,
                args=(job_id, tmp_path, hints, original_name, tmp_dir),
                daemon=True,
                name=f"queue-job-{job_id}",
            )
            t.start()
            t.join()  # wait before deleting — ensures at-least-once delivery

            qc.delete_message(msg)
            logger.info("[%s] Queue message deleted after completion.", job_id)

        except Exception as exc:
            logger.warning("Queue worker poll error: %s", exc)
            time.sleep(5)


# ── Per-job invocable registries ─────────────────────────────────────────────
# Backed by both in-memory cache and Blob Storage so state survives container
# recycles (scale-to-zero, deployments, crashes).  P7.
# Structure: {job_id: {tool_name: invocable_dict}}
_JOB_INVOCABLE_MAPS: dict[str, dict[str, Any]] = {}
_JOB_MAP_LOCK = threading.Lock()

# ── Async job status store (P3) ─────────────────────────────────────────────
# status schema: {status, progress, message, result, error, created_at, updated_at}
_JOB_STATUS: dict[str, dict[str, Any]] = {}
_JOB_STATUS_LOCK = threading.Lock()


def _persist_job_status(job_id: str, payload: dict) -> None:
    """Write job status to in-memory cache AND Blob Storage (non-blocking)."""
    with _JOB_STATUS_LOCK:
        _JOB_STATUS[job_id] = payload
    # Upload in a daemon thread — synchronous blob I/O in the worker thread
    # adds 2-10s per call and there are 3 calls per job.
    def _bg_upload(jid=job_id, p=payload):
        try:
            _upload_to_blob(
                ARTIFACT_CONTAINER,
                f"{jid}/status.json",
                json.dumps(p).encode(),
            )
        except Exception as exc:
            logger.warning("[%s] Failed to persist status to Blob: %s", jid, exc)
    threading.Thread(target=_bg_upload, daemon=True, name=f"persist-{job_id}").start()


def _get_job_status(job_id: str) -> dict | None:
    """Return job status from cache; reload from Blob on cache miss."""
    with _JOB_STATUS_LOCK:
        result = _JOB_STATUS.get(job_id)
    if result is not None:
        return result
    try:
        data = json.loads(_download_blob(ARTIFACT_CONTAINER, f"{job_id}/status.json"))
        with _JOB_STATUS_LOCK:
            _JOB_STATUS[job_id] = data
        return data
    except Exception:
        return None


def _analyze_worker(
    job_id: str,
    tmp_path: Path,
    hints: str,
    original_name: str,
    cleanup_dir: Path | None = None,
) -> None:
    """Background worker — runs discovery and updates job status in Blob (P3)."""
    _persist_job_status(job_id, {
        "status": "running",
        "progress": 10,
        "message": f"Discovery started for {original_name}",
        "result": None,
        "error": None,
        "created_at": time.time(),
        "updated_at": time.time(),
    })
    try:
        _persist_job_status(job_id, {
            "status": "running",
            "progress": 30,
            "message": "Running binary analysis pipeline…",
            "result": None,
            "error": None,
            "created_at": time.time(),
            "updated_at": time.time(),
        })
        print(f"[DIAG {job_id}] entering _run_discovery", flush=True)
        with _ai_span("analyze_async", job_id=job_id, filename=original_name, hints=hints):
            result = _run_discovery(tmp_path, job_id, hints)
        print(f"[DIAG {job_id}] _run_discovery returned {len(result.get('invocables',[]))} invocables", flush=True)
        _persist_job_status(job_id, {
            "status": "done",
            "progress": 100,
            "message": f"Analysis complete — {len(result.get('invocables', []))} invocables found",
            "result": result,
            "error": None,
            "created_at": time.time(),
            "updated_at": time.time(),
        })
    except Exception as exc:
        logger.error("[%s] Async discovery failed: %s", job_id, exc)
        _persist_job_status(job_id, {
            "status": "error",
            "progress": 0,
            "message": "Analysis failed",
            "result": None,
            "error": str(exc),
            "created_at": time.time(),
            "updated_at": time.time(),
        })
    finally:
        try:
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
            if cleanup_dir and cleanup_dir.exists():
                cleanup_dir.rmdir()
        except Exception:
            pass


def _register_invocables(job_id: str, invocables: list[dict]) -> None:
    inv_map = {inv["name"]: inv for inv in invocables}
    with _JOB_MAP_LOCK:
        _JOB_INVOCABLE_MAPS[job_id] = inv_map
    # Persist to Blob so state survives container recycles (P7)
    try:
        _upload_to_blob(
            ARTIFACT_CONTAINER,
            f"{job_id}/invocables_map.json",
            json.dumps(inv_map).encode(),
        )
    except Exception as exc:
        logger.warning("[%s] Failed to persist invocables to Blob: %s", job_id, exc)


def _get_invocable(job_id: str, name: str) -> dict | None:
    with _JOB_MAP_LOCK:
        result = _JOB_INVOCABLE_MAPS.get(job_id, {}).get(name)
    if result is not None:
        return result
    # Cache miss — reload from Blob (handles container restarts, P7)
    try:
        data: dict = json.loads(
            _download_blob(ARTIFACT_CONTAINER, f"{job_id}/invocables_map.json")
        )
        with _JOB_MAP_LOCK:
            _JOB_INVOCABLE_MAPS.setdefault(job_id, {}).update(data)
        return data.get(name)
    except Exception:
        return None


# ── ctypes type maps (Windows-only) ────────────────────────────────────────
_CTYPES_RESTYPE: dict = {}
_CTYPES_ARGTYPE: dict = {}

if IS_WINDOWS:
    _CTYPES_RESTYPE = {
        "void":           None,
        "bool":           ctypes.c_bool,
        "int":            ctypes.c_int,
        "unsigned":       ctypes.c_uint,
        "unsigned int":   ctypes.c_uint,
        "long":           ctypes.c_long,
        "unsigned long":  ctypes.c_ulong,
        "size_t":         ctypes.c_size_t,
        "float":          ctypes.c_float,
        "double":         ctypes.c_double,
        "char*":          ctypes.c_char_p,
        "const char*":    ctypes.c_char_p,
        "char *":         ctypes.c_char_p,
        "const char *":   ctypes.c_char_p,
    }
    _CTYPES_ARGTYPE = {
        "int":            ctypes.c_int,
        "unsigned":       ctypes.c_uint,
        "unsigned int":   ctypes.c_uint,
        "long":           ctypes.c_long,
        "unsigned long":  ctypes.c_ulong,
        "size_t":         ctypes.c_size_t,
        "float":          ctypes.c_float,
        "double":         ctypes.c_double,
        "bool":           ctypes.c_bool,
        "string":         ctypes.c_char_p,
        "str":            ctypes.c_char_p,
        "char*":          ctypes.c_char_p,
        "const char*":    ctypes.c_char_p,
        "char *":         ctypes.c_char_p,
        "const char *":   ctypes.c_char_p,
    }


# ── Execution helpers ──────────────────────────────────────────────────────

def _resolve_dll_path(raw: str) -> str:
    """Return an absolute path for *raw*, searching likely anchors."""
    p = Path(raw)
    if p.is_absolute() and p.exists():
        return str(p)
    project_root = Path(__file__).resolve().parent.parent
    candidate = project_root / raw
    if candidate.exists():
        return str(candidate)
    return raw  # let ctypes emit the real error


def _execute_dll(inv: dict, execution: dict, args: dict) -> str:
    if not IS_WINDOWS:
        return "DLL execution is only supported on Windows."
    dll_path  = _resolve_dll_path(execution.get("dll_path", ""))
    func_name = execution.get("function_name", "")

    ret_str = (
        inv.get("return_type")
        or (inv.get("signature") or {}).get("return_type", "unknown")
        or "unknown"
    ).strip()
    restype = _CTYPES_RESTYPE.get(ret_str.lower(), ctypes.c_size_t)

    params = list(inv.get("parameters") or [])
    if not params:
        sig_str = (inv.get("signature") or {}).get("parameters", "")
        if sig_str:
            for chunk in sig_str.split(","):
                tokens = chunk.strip().split()
                if len(tokens) >= 2:
                    raw_type = " ".join(tokens[:-1]).lower().strip("*").rstrip()
                    pname    = tokens[-1].lstrip("*")
                    params.append({"name": pname, "type": raw_type})

    try:
        lib = ctypes.CDLL(dll_path)
        fn  = getattr(lib, func_name)
        fn.restype = restype

        c_args = []
        if params and args:
            for p in params:
                pname = p.get("name", "")
                ptype = p.get("type", "string").lower().strip("*").rstrip()
                val   = args.get(pname)
                if val is None:
                    continue
                atype = _CTYPES_ARGTYPE.get(ptype, ctypes.c_char_p)
                if atype == ctypes.c_char_p:
                    c_args.append(ctypes.c_char_p(str(val).encode()))
                else:
                    c_args.append(atype(int(val)))
        elif args:
            for v in args.values():
                if isinstance(v, bool):
                    c_args.append(ctypes.c_bool(v))
                elif isinstance(v, int):
                    c_args.append(ctypes.c_size_t(v))
                elif isinstance(v, float):
                    c_args.append(ctypes.c_double(v))
                elif isinstance(v, str):
                    c_args.append(ctypes.c_char_p(v.encode()))

        result = fn(*c_args)
        if restype == ctypes.c_char_p:
            if isinstance(result, bytes):
                return f"Returned: {result.decode(errors='replace')}"
        return f"Returned: {result}"
    except Exception as exc:
        return f"DLL call error: {exc}"


def _execute_cli(execution: dict, name: str, args: dict) -> str:
    target = (
        execution.get("executable_path")
        or execution.get("target_path")
        or execution.get("dll_path", "")
    )
    if not target:
        return f"CLI error: no executable path configured for '{name}'"

    exe_stem = Path(target).stem.lower()
    if exe_stem == name.lower():
        # Launch-the-app invocable — just open it
        try:
            if IS_WINDOWS:
                subprocess.Popen(
                    [target],
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            else:
                subprocess.Popen([target])
            return (
                f"{Path(target).name} has been launched successfully. "
                "The application is now open. "
                "DO NOT call this launch tool again — it is already running. "
                "Proceed directly to using the other tools to interact with it."
            )
        except Exception as exc:
            return f"CLI error: {exc}"

    cmd = [target, name] + [str(v) for v in args.values()]
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if IS_WINDOWS else 0
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=creation_flags,
        )
        return r.stdout or r.stderr or f"exit_code={r.returncode}"
    except Exception as exc:
        return f"CLI error: {exc}"


def _execute_gui(execution: dict, name: str, args: dict) -> str:
    if not IS_WINDOWS:
        return "GUI actions are only supported on Windows."
    try:
        from pywinauto.application import Application  # type: ignore
    except ImportError:
        return "pywinauto is not installed; GUI actions unavailable."

    exe_path    = execution.get("exe_path", "")
    action_type = execution.get("action_type", "menu_click")

    # Minimal GUI dispatch — delegates to the generated server's full
    # implementation when running locally on Windows; here we handle the
    # most common actions for the cloud demo path.
    if action_type == "close_app":
        try:
            app = Application(backend="uia").connect(path=exe_path, timeout=3)
            app.kill()
            return "App closed."
        except Exception as exc:
            return f"GUI close error: {exc}"

    return (
        f"GUI action '{action_type}' requested for '{exe_path}'. "
        "Full GUI automation requires Windows with pywinauto installed."
    )


def _call_execute_bridge(inv: dict, args: dict) -> str | None:
    """Forward a tool-call to the Windows VM bridge /execute endpoint.

    Returns the result string on success, or None if the bridge is
    unavailable / returns an error (caller falls through to local execution).
    """
    import httpx
    try:
        resp = httpx.post(
            f"{GUI_BRIDGE_URL}/execute",
            json={"invocable": inv, "args": args},
            headers={"X-Bridge-Key": GUI_BRIDGE_SECRET},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json().get("result", "")
    except Exception as exc:
        logger.warning("Bridge /execute failed (falling through to local): %s", exc)
        return None


def _execute_tool(inv: dict, args: dict) -> str:
    """Dispatch a single tool call to the correct backend."""
    name      = inv.get("name", "")
    execution = inv.get("execution") or inv.get("mcp", {}).get("execution", {})
    method    = execution.get("method", "")

    if method == "dll_import":
        return _execute_dll(inv, execution, args)
    # GUI and CLI both need Windows — forward to the bridge when configured.
    if GUI_BRIDGE_URL and GUI_BRIDGE_SECRET:
        result = _call_execute_bridge(inv, args)
        if result is not None:
            return result
    if method == "gui_action":
        return _execute_gui(execution, name, args)
    return _execute_cli(execution, name, args)


# ── helpers ────────────────────────────────────────────────────────────────

def _upload_to_blob(container: str, blob_name: str, data: bytes) -> str:
    client = _blob_client()
    cc = client.get_container_client(container)
    cc.upload_blob(blob_name, data, overwrite=True, timeout=30)
    logger.info(f"Uploaded blob {container}/{blob_name}")
    return blob_name


def _download_blob(container: str, blob_name: str) -> bytes:
    client = _blob_client()
    cc = client.get_container_client(container)
    return cc.download_blob(blob_name).readall()


def _extract_invocables(data: Any) -> list:
    """Normalise a discovery JSON payload to a flat list of invocable dicts.

    The discovery pipeline emits:
        {"metadata": {...}, "invocables": [...], "summary": {...}}
    or legacy flat arrays / {name: info} objects.
    """
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        if "invocables" in data and isinstance(data["invocables"], list):
            return data["invocables"]
        # legacy: flat {name: info_dict} mapping
        return [
            {"name": k, **(v if isinstance(v, dict) else {"description": str(v)})}
            for k, v in data.items()
            if k not in ("metadata", "summary")
        ]
    return []


def _call_gui_bridge(binary_path: Path, job_id: str, hints: str = "") -> list[dict]:
    """Dispatch Windows-only analysis to the GUI bridge VM.

    Covers GUI (pywinauto), COM/TLB (pythoncom), CLI (Windows EXEs),
    and registry scan — none of which run in the Linux ACA container.

    Returns an empty list (silently) when the bridge is unconfigured or
    unreachable so the rest of the pipeline always completes.
    """
    if not GUI_BRIDGE_URL or not GUI_BRIDGE_SECRET:
        logger.warning("[%s] GUI bridge skipped — GUI_BRIDGE_URL or GUI_BRIDGE_SECRET not set", job_id)
        return []

    import httpx  # already in api/requirements.txt

    # Base64-encode the binary so the bridge can write it to a local temp file
    # on the Windows VM — avoids the "Linux path not found" problem for any
    # arbitrary uploaded EXE (not just Windows system executables).
    content_b64: str | None = None
    try:
        raw = binary_path.read_bytes()
        if len(raw) <= 50_000_000:  # skip inline transfer for files > 50 MB
            import base64
            content_b64 = base64.b64encode(raw).decode()
    except Exception as exc:
        logger.warning("[%s] Could not read binary for bridge content: %s", job_id, exc)

    payload = {
        "path":    str(binary_path),
        "hints":   hints,
        "types":   ["gui", "com", "cli", "registry"],
        "content": content_b64,   # None → bridge falls back to system-path lookup
    }
    try:
        logger.info("[%s] Calling GUI bridge at %s for %s", job_id, GUI_BRIDGE_URL, binary_path.name)
        resp = httpx.post(
            f"{GUI_BRIDGE_URL}/analyze",
            json=payload,
            headers={"X-Bridge-Key": GUI_BRIDGE_SECRET},
            timeout=180,  # 30s GUI timeout on bridge + other analyzers + network margin
        )
        resp.raise_for_status()
        data = resp.json()
        invocables = data.get("invocables", [])
        if data.get("errors"):
            logger.warning("[%s] Bridge reported partial errors: %s", job_id, data["errors"])
        logger.info("[%s] Bridge returned %d invocables", job_id, len(invocables))
        return invocables
    except Exception as exc:
        logger.warning("[%s] GUI bridge call failed: %s", job_id, exc, exc_info=True)
        # Persist the warning into the job record so the caller can surface it
        try:
            existing = _get_job_status(job_id) or {}
            existing["bridge_warning"] = f"GUI bridge unreachable — Windows analysis skipped: {exc}"
            _persist_job_status(job_id, existing)
        except Exception:
            pass
        return []


def _run_discovery(binary_path: Path, job_id: str, hints: str = "") -> dict:
    """Run the discovery pipeline on a local file path. Returns invocables list."""
    out_dir = Path(tempfile.mkdtemp(prefix=f"mcp_{job_id}_"))
    cmd = [
        sys.executable,
        str(SRC_DISCOVERY_DIR / "main.py"),
        "--dll", str(binary_path),
        "--out", str(out_dir),
        "--no-demangle",
    ]
    if hints:
        cmd += ["--tag", hints[:40].replace(" ", "_")]
    if IS_WINDOWS:
        cmd += ["--registry"]  # scan HKLM App Paths, Uninstall, COM CLSIDs (§1.c / P9)

    # PYTHONPATH must include the discovery package directory so all sibling
    # modules (classify, exports, schema, …) resolve correctly.
    discovery_env = {
        **os.environ,
        "PYTHONPATH": str(SRC_DISCOVERY_DIR),
    }

    print(f"[DIAG {job_id}] subprocess start", flush=True)
    logger.info(f"[{job_id}] Running discovery: {' '.join(cmd)}")
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=240,
        env=discovery_env,
    )
    print(f"[DIAG {job_id}] subprocess done rc={result.returncode}", flush=True)
    logger.info(f"[{job_id}] Discovery stdout: {result.stdout[-1000:]}")
    if result.returncode != 0:
        logger.warning(f"[{job_id}] Discovery stderr: {result.stderr[-1000:]}")
    elif result.stderr.strip():
        logger.info(f"[{job_id}] Discovery stderr (rc=0): {result.stderr[-500:]}")

    # ── Collect ALL *_mcp.json files produced (EXEs emit cli + gui + exports)
    mcp_files = sorted(out_dir.glob("*_mcp.json"))
    if not mcp_files:
        mcp_files = sorted(out_dir.glob("*.json"))

    if not mcp_files:
        raise RuntimeError(
            f"Discovery produced no output files.\n"
            f"returncode={result.returncode}\n"
            f"stderr: {result.stderr[-500:]}"
        )

    # ── Merge invocables from all output files and de-duplicate by name ──
    seen_names: set[str] = set()
    merged_invocables: list[dict] = []
    primary_blob = f"{job_id}/{mcp_files[0].name}"

    for mcp_file in mcp_files:
        try:
            file_data = json.loads(mcp_file.read_bytes())
        except Exception as exc:
            logger.warning(f"[{job_id}] Could not parse {mcp_file.name}: {exc}")
            continue

        invs = _extract_invocables(file_data)
        for inv in invs:
            name = inv.get("name", "")
            if name and name not in seen_names:
                seen_names.add(name)
                merged_invocables.append(inv)

        # Upload every artifact to Blob Storage
        blob_name = f"{job_id}/{mcp_file.name}"
        print(f"[DIAG {job_id}] uploading artifact {mcp_file.name}", flush=True)
        try:
            _upload_to_blob(ARTIFACT_CONTAINER, blob_name, mcp_file.read_bytes())
        except Exception as exc:
            logger.warning(f"[{job_id}] Blob upload failed for {mcp_file.name}: {exc}")
        print(f"[DIAG {job_id}] artifact upload done {mcp_file.name}", flush=True)

    print(f"[DIAG {job_id}] all artifacts uploaded, calling bridge", flush=True)
    print(f"[DIAG {job_id}] GUI_BRIDGE_URL={'SET' if GUI_BRIDGE_URL else 'NOT SET'}", flush=True)
    # Use plain logger.info (no custom_dimensions) here — the AzureLogHandler
    # flushes synchronously and can block 90s if App Insights is slow.
    logger.info(
        "[%s] Discovery complete: %d file(s), %d unique invocables",
        job_id, len(mcp_files), len(merged_invocables),
    )

    # ── Augment with Windows-only analysis via GUI bridge (if configured) ──
    # The bridge covers GUI buttons, COM/TLB interfaces, Windows EXE CLI help,
    # and registry scan — none of which run in the Linux container.
    bridge_invocables = _call_gui_bridge(binary_path, job_id, hints)
    if bridge_invocables:
        for inv in bridge_invocables:
            name = inv.get("name", "")
            if name and name not in seen_names:
                seen_names.add(name)
                merged_invocables.append(inv)
        logger.info("[%s] After bridge merge: %d total invocables", job_id, len(merged_invocables))

    return {
        "job_id": job_id,
        "artifact_blob": primary_blob,
        "invocables": merged_invocables,
    }


# ── Startup ────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def _startup():
    """Start the durable queue worker in a background thread."""
    t = threading.Thread(
        target=_queue_worker_loop, daemon=True, name="queue-worker"
    )
    t.start()


# ── Endpoints ──────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "version": "1.0.0"}


@app.post("/api/analyze-path")
async def analyze_path(body: dict[str, Any]):
    """
    Section 2.b: Analyze an installed directory or file already accessible on
    the server's filesystem (e.g. C:\\Program Files\\AppD\\ on a local Windows
    run, or a mounted volume path in a container).
    Body: {path: str, hints?: str}
    Returns immediately with {job_id, status_url} — poll GET /api/jobs/{id} (P3).
    """
    path_str = (body.get("path") or "").strip()
    hints    = (body.get("hints") or "").strip()

    if not path_str:
        raise HTTPException(400, "path is required")

    # ── Path traversal guard ───────────────────────────────────────
    # Resolve symlinks and ".." components before checking the prefix so a
    # caller cannot sneak past the allowlist with e.g. /tmp/../etc/passwd.
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

    # Seed status immediately so poll endpoint returns right away
    _persist_job_status(job_id, {
        "status": "queued",
        "progress": 0,
        "message": f"Queued analysis for {target.name}",
        "result": None,
        "error": None,
        "created_at": time.time(),
        "updated_at": time.time(),
    })

    t = threading.Thread(
        target=_analyze_worker,
        args=(job_id, target, hints, target.name, None),
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
    """
    Section 2-3: Accept a binary upload, run discovery asynchronously.
    Returns immediately with {job_id, status_url} — poll GET /api/jobs/{id} (P3).
    """
    job_id = str(uuid.uuid4())[:8]
    suffix = Path(file.filename).suffix or ".bin"
    blob_name = f"{job_id}/input{suffix}"

    content = await file.read()
    if len(content) == 0:
        raise HTTPException(400, "Empty file uploaded")

    logger.info(f"[{job_id}] Received {file.filename} ({len(content)} bytes)")

    original_name = Path(file.filename).name or f"upload{suffix}"

    # Save to Blob Storage immediately — the queue worker re-downloads from here.
    blob_uploaded = False
    try:
        _upload_to_blob(UPLOAD_CONTAINER, blob_name, content)
        blob_uploaded = True
    except Exception as exc:
        logger.warning("[%s] Blob upload failed: %s", job_id, exc)

    # Seed status so poll endpoint responds immediately.
    _persist_job_status(job_id, {
        "status": "queued",
        "progress": 0,
        "message": f"Queued analysis for {original_name}",
        "result": None,
        "error": None,
        "created_at": time.time(),
        "updated_at": time.time(),
    })

    # Primary path: enqueue onto Azure Storage Queue for durable processing.
    # The queue worker thread picks it up, downloads the blob, and runs analysis.
    # Fallback: spawn a direct thread (local/dev mode or if queue unavailable).
    enqueued = blob_uploaded and _enqueue_analysis(job_id, blob_name, hints, original_name)
    if not enqueued:
        logger.info("[%s] Queue unavailable — falling back to direct thread.", job_id)
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
    """
    P3: Poll async job status.
    Returns: {job_id, status, progress, message, result?, error?}
    status ∈ {queued, running, done, error}
    """
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
    job_id = body.get("job_id", str(uuid.uuid4())[:8])
    selected: list = body.get("selected", [])

    if not selected:
        raise HTTPException(400, "No invocables selected")

    # Build OpenAI function-calling tool schema from selected invocables
    tools = []
    for inv in selected:
        props: dict = {}
        required: list = []
        # parameters may be None (bridge raw field) or a string (legacy) —
        # normalise to a list so iteration is always safe.
        raw_params = inv.get("parameters") or []
        if isinstance(raw_params, str):
            raw_params = []
        for p in raw_params:
            if not isinstance(p, dict):
                continue
            pname = p.get("name", "arg")
            props[pname] = {
                "type": "string",
                "description": p.get("type", "string"),
            }
            required.append(pname)

        # Discovery pipeline uses `description`; older/generated schemas use
        # `doc` or `signature`.  Fall through all three, then the name.
        desc = (
            inv.get("doc")
            or inv.get("description")
            or inv.get("signature")
            or inv["name"]
        )

        tools.append({
            "type": "function",
            "function": {
                "name": inv["name"],
                "description": desc,
                "parameters": {
                    "type": "object",
                    "properties": props,
                    "required": required,
                },
            },
        })

    mcp_schema = {
        "job_id": job_id,
        "mcp_version": "1.0",
        "component": body.get("component_name", "mcp-component"),
        "tools": tools,
    }

    # Register invocables for later execution via /api/chat or /api/execute
    _register_invocables(job_id, selected)

    # Save schema to Blob
    schema_blob = f"{job_id}/mcp_schema.json"
    try:
        _upload_to_blob(ARTIFACT_CONTAINER, schema_blob, json.dumps(mcp_schema, indent=2).encode())
    except Exception as blob_exc:
        logger.warning("[%s] Schema blob upload failed (non-fatal): %s", job_id, blob_exc)

    # ── P1: Generate true MCP SDK server artifacts ─────────────────────────
    mcp_artifacts: dict = {}
    try:
        from section4_generate_server import generate_mcp_sdk_artifacts  # type: ignore
        component_name = mcp_schema["component"]
        mcp_artifacts = generate_mcp_sdk_artifacts(component_name, selected)
        for fname, content in [
            ("mcp_server.py",       mcp_artifacts.get("mcp_server_py", "")),
            ("mcp.json",            mcp_artifacts.get("mcp_json", "")),
            ("mcp_requirements.txt", mcp_artifacts.get("mcp_requirements_txt", "")),
        ]:
            if content:
                _upload_to_blob(
                    ARTIFACT_CONTAINER,
                    f"{job_id}/{fname}",
                    content.encode(),
                )
        logger.info("[%s] MCP SDK artifacts uploaded (mcp_server.py, mcp.json)", job_id)
    except Exception as mcp_exc:
        logger.warning("[%s] MCP SDK artifact generation failed: %s", job_id, mcp_exc)

    # ── P5: Embed & index invocables in Azure AI Search ────────────────────
    if OPENAI_ENDPOINT:
        try:
            from search import embed_and_index  # type: ignore
            _oai = _openai_client()
            embed_and_index(job_id, selected, _oai, functions=tools)
            logger.info("[%s] AI Search indexing triggered for %d tools", job_id, len(tools))
        except Exception as search_exc:
            logger.warning("[%s] AI Search indexing failed (non-fatal): %s", job_id, search_exc)

    logger.info(
        "[%s] Generated MCP schema with %d tools",
        job_id, len(tools),
        extra={"custom_dimensions": {
            "event": "generate_complete",
            "job_id": job_id,
            "tool_count": len(tools),
            "component": mcp_schema.get("component", ""),
        }},
    )
    return JSONResponse({
        "job_id": job_id,
        "schema_blob": schema_blob,
        "mcp_schema": mcp_schema,
        "mcp_server_blob": f"{job_id}/mcp_server.py" if mcp_artifacts else None,
        "mcp_json_blob": f"{job_id}/mcp.json" if mcp_artifacts else None,
    })


@app.post("/api/execute")
async def execute_tool(body: dict[str, Any]):
    """
    Execute a single tool call. Accepts either:
      - job_id + tool_name: looks up invocable from a previously registered job
      - invocable: full invocable dict supplied inline
    Body: {job_id?, tool_name, arguments, invocable?}
    """
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
    """
    Section 5: Agentic chat interface.
    Sends messages to Azure OpenAI with MCP tool definitions attached.
    When the model emits tool_calls, actually executes them and feeds the
    results back for a second completion — up to MAX_TOOL_ROUNDS rounds.
    Body: {messages, tools, invocables?, job_id?}
      invocables: full invocable dicts (with execution metadata) needed to
                  dispatch tool calls. If omitted, execution falls back to
                  /api/execute with job_id lookup.
    """
    messages: list   = body.get("messages", [])
    tools: list      = body.get("tools", [])
    invocables: list = body.get("invocables", [])
    job_id: str      = body.get("job_id", "")

    if not messages:
        raise HTTPException(400, "No messages provided")
    if not OPENAI_ENDPOINT:
        raise HTTPException(503, "Azure OpenAI endpoint not configured")

    # Build a local invocable registry for this request
    inv_map: dict[str, dict] = {inv["name"]: inv for inv in invocables}
    if job_id and invocables:
        _register_invocables(job_id, invocables)

    MAX_TOOL_ROUNDS = 50  # hard safety cap only — loop detection stops earlier
    conversation = list(messages)  # working copy
    _all_tool_results: list[dict] = []  # accumulated across all rounds for response
    _last_call_signature: str = ""    # for loop detection
    # actually call tools instead of narrating what the user should do.
    if not any(m.get("role") == "system" for m in conversation):
        tool_names = ", ".join(inv["name"] for inv in invocables) if invocables else "the available tools"
        conversation.insert(0, {
            "role": "system",
            "content": (
                "You are an AI agent with direct control over a Windows application via MCP tools. "
                "RULES YOU MUST FOLLOW:\n"
                "1. When asked to perform actions, call tools immediately — never describe what you would do.\n"
                "2. Do NOT launch an application that is already open. Only call the launch tool once. "
                "If the tool result says the app was launched or is already running, "
                "NEVER call that launch tool again in this session under any circumstances.\n"
                "3. You can call MULTIPLE tools in a single response — do this to perform sequences faster. "
                "For example, to press 4 then × then 3, issue all three tool calls at once.\n"
                "4. After completing all actions, report the final result shown on screen.\n"
                "5. If the user asks a question about your tools or capabilities (e.g. 'list your tools', "
                "'what can you do'), respond with a plain text answer — do NOT call any tools.\n"
                "You have access to these tools: " + tool_names + "."
            ),
        })

    try:
        client = _openai_client()
        msg = None
        _tool_calls_total = 0
        _chat_t0 = time.perf_counter()

        # ── P5: Semantic tool selection ─────────────────────────────────────
        # If the tool list is large (> 15), retrieve only the top-15 most
        # semantically relevant tools per user turn to stay inside the GPT-4o
        # 128-tool limit and reduce prompt tokens.
        _AI_SEARCH_TOP_K = 15
        _active_tools = list(tools)  # per-turn tool subset
        _last_user_message = next(
            (m.get("content", "") for m in reversed(conversation) if m.get("role") == "user"),
            "",
        )
        if len(tools) > _AI_SEARCH_TOP_K and job_id and _last_user_message:
            try:
                from search import retrieve_tools as _retrieve_tools  # type: ignore
                _semantic_tools = _retrieve_tools(job_id, _last_user_message, client, top_k=_AI_SEARCH_TOP_K)
                if _semantic_tools:
                    _active_tools = _semantic_tools
                    logger.info(
                        "[%s] Semantic retrieval: %d/%d tools selected",
                        job_id, len(_active_tools), len(tools),
                    )
            except Exception as _se:
                logger.warning("[%s] Semantic tool retrieval failed: %s", job_id, _se)

        # Track which launcher tools have already been called this session
        # so semantic retrieval can exclude them from subsequent rounds.
        _called_launchers: set[str] = set()

        for _round in range(MAX_TOOL_ROUNDS):
            # After round 0, refresh semantic tool selection with launchers excluded
            if _round > 0 and len(tools) > _AI_SEARCH_TOP_K and _called_launchers:
                try:
                    from search import retrieve_tools as _retrieve_tools  # type: ignore
                    _semantic_tools = _retrieve_tools(job_id, _last_user_message, client, top_k=_AI_SEARCH_TOP_K)
                    if _semantic_tools:
                        _active_tools = [t for t in _semantic_tools
                                         if t.get("function", {}).get("name") not in _called_launchers]
                except Exception:
                    pass

            kwargs: dict = {
                "model": OPENAI_DEPLOYMENT,
                "messages": conversation,
                "temperature": 0.2,
            }
            if _active_tools:
                kwargs["tools"] = _active_tools
                kwargs["tool_choice"] = "auto"

            response = client.chat.completions.create(**kwargs)
            msg = response.choices[0].message

            # No tool calls → final answer
            if not msg.tool_calls:
                logger.info(
                    "[chat] completed in %d round(s), %d tool call(s)",
                    _round + 1, _tool_calls_total,
                    extra={"custom_dimensions": {
                        "event": "chat_complete",
                        "job_id": job_id,
                        "rounds": _round + 1,
                        "tool_calls_total": _tool_calls_total,
                        "duration_ms": int((time.perf_counter() - _chat_t0) * 1000),
                    }},
                )
                return JSONResponse({
                    "role": msg.role,
                    "content": msg.content,
                    "tool_calls": [],
                    "tool_results": _all_tool_results,
                    "rounds": _round + 1,
                })

            # Append assistant turn with tool_calls to conversation
            assistant_turn: dict[str, Any] = {
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in msg.tool_calls
                ],
            }
            conversation.append(assistant_turn)

            _tool_calls_total += len(msg.tool_calls)

            # Loop detection: if every tool call this round is identical to last round, stop.
            _this_sig = "|".join(f"{tc.function.name}:{tc.function.arguments}" for tc in msg.tool_calls)
            if _this_sig == _last_call_signature:
                logger.warning("[chat] Loop detected (same calls twice) — forcing summary")
                break
            _last_call_signature = _this_sig

            # Execute each tool call and append tool result messages
            for tc in msg.tool_calls:
                fn_name = tc.function.name
                try:
                    fn_args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    fn_args = {}

                inv = inv_map.get(fn_name)
                if inv is None and job_id:
                    inv = _get_invocable(job_id, fn_name)

                if inv is not None:
                    tool_result = _execute_tool(inv, fn_args)
                    # Track launcher invocables (CLI tools whose name == exe stem)
                    # so they are excluded from semantic retrieval in subsequent rounds.
                    if inv.get("source_type") == "cli" and Path(inv.get("dll_path", "")).stem.lower() == fn_name.lower():
                        _called_launchers.add(fn_name)
                    logger.info(f"[chat/{_round}] Executed {fn_name}: {tool_result[:120]}")
                else:
                    tool_result = (
                        f"Tool '{fn_name}' executed (no invocable metadata "
                        f"available — pass 'invocables' in the request body "
                        f"or call /api/generate first). "
                        f"Raw arguments: {json.dumps(fn_args)}"
                    )
                    logger.warning(f"[chat/{_round}] No invocable for {fn_name}")

                _all_tool_results.append({"name": fn_name, "arguments": fn_args, "result": tool_result})
                conversation.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": tool_result,
                })

        # Exceeded MAX_TOOL_ROUNDS — force one final text-only summary from the model
        if msg is None:
            return JSONResponse({"role": "assistant", "content": "", "tool_calls": [], "tool_results": [], "rounds": 0})
        summary_content = "All steps completed."
        try:
            _summary_resp = client.chat.completions.create(
                model=OPENAI_DEPLOYMENT,
                messages=conversation,
                temperature=0.2,
                tools=_active_tools,
                tool_choice="none",
            )
            summary_content = _summary_resp.choices[0].message.content or summary_content
        except Exception as _se:
            logger.warning("[chat] Final summary call failed: %s", _se)
        return JSONResponse({
            "role": "assistant",
            "content": summary_content,
            "tool_calls": [],
            "tool_results": _all_tool_results,
            "rounds": MAX_TOOL_ROUNDS,
        })

    except Exception as e:
        logger.error(f"Chat error: {e}")
        raise HTTPException(500, f"Chat failed: {e}")


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
