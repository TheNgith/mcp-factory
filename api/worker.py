"""api/worker.py – Azure Storage Queue worker and per-job analysis runner.

_queue_worker_loop: background thread that polls the Storage Queue and runs
                    jobs durably (at-least-once delivery).
_analyze_worker:    runs _run_discovery for one job and persists status.
"""

from __future__ import annotations

import json
import logging
import tempfile
import threading
import time
from pathlib import Path

from api.config import UPLOAD_CONTAINER, ANALYSIS_QUEUE
from api.storage import (
    _queue_service_client,
    _download_blob,
    _persist_job_status,
)
from api.telemetry import _ai_span

logger = logging.getLogger("mcp_factory.api")


def _analyze_worker(
    job_id: str,
    tmp_path: Path,
    hints: str,
    original_name: str,
    cleanup_dir: Path | None = None,
) -> None:
    """Background worker — runs discovery and updates job status in Blob (P3)."""
    # Deferred import avoids a load-time cycle: worker → discovery → storage
    # (discovery imports storage; both are already imported above, so CPython's
    # module cache makes this effectively free after the first import).
    from api.discovery import _run_discovery

    _persist_job_status(job_id, {
        "status": "running",
        "progress": 10,
        "message": f"Discovery started for {original_name}",
        "result": None,
        "error": None,
        "created_at": time.time(),
        "updated_at": time.time(),
    }, sync=True)
    try:
        _persist_job_status(job_id, {
            "status": "running",
            "progress": 30,
            "message": "Running binary analysis pipeline…",
            "result": None,
            "error": None,
            "created_at": time.time(),
            "updated_at": time.time(),
        }, sync=True)
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
        }, sync=True)
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
        }, sync=True)
    finally:
        try:
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
            if cleanup_dir and cleanup_dir.exists():
                cleanup_dir.rmdir()
        except Exception as exc:
            logger.warning("[%s] Temp file cleanup failed: %s", job_id, exc)


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
    except Exception as exc:
        logger.warning("Queue create failed (may already exist): %s", exc)

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
