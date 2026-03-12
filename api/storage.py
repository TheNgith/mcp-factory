"""api/storage.py â€“ Azure Blob Storage, Storage Queue, and job-status helpers.

State:
  _JOB_STATUS          â€“ in-memory job-status cache (backed by Blob).
  _JOB_INVOCABLE_MAPS  â€“ per-job invocable registry (backed by Blob).
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any

from azure.storage.blob import BlobServiceClient

from api.config import (
    STORAGE_ACCOUNT,
    ARTIFACT_CONTAINER,
    ANALYSIS_QUEUE,
    _get_credential,
)

logger = logging.getLogger("mcp_factory.api")

# â”€â”€ Per-job invocable registries â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Backed by both in-memory cache and Blob Storage so state survives container
# recycles (scale-to-zero, deployments, crashes).  P7.
# Structure: {job_id: {tool_name: invocable_dict}}
_JOB_INVOCABLE_MAPS: dict[str, dict[str, Any]] = {}
_JOB_MAP_LOCK = threading.Lock()

# â”€â”€ Async job status store (P3) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# status schema: {status, progress, message, result, error, created_at, updated_at}
_JOB_STATUS: dict[str, dict[str, Any]] = {}
_JOB_STATUS_LOCK = threading.Lock()


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



def _persist_job_status(job_id: str, payload: dict, *, sync: bool = False) -> bool:
    """Write job status to in-memory cache and persist to Blob.

    - Always updates in-memory immediately.
    - If sync=True: blocking, retrying Blob write (for worker threads).
    - If sync=False: run the retrying write in a daemon thread so async
      request handlers never block the event loop.
    """
    with _JOB_STATUS_LOCK:
        _JOB_STATUS[job_id] = payload

    def _upload_with_retry() -> bool:
        _MAX_RETRIES = 3
        _RETRY_DELAY = 2
        data = json.dumps(payload).encode()
        for attempt in range(_MAX_RETRIES):
            try:
                _upload_to_blob(ARTIFACT_CONTAINER, f"{job_id}/status.json", data)
                return True
            except Exception as exc:
                if attempt < _MAX_RETRIES - 1:
                    logger.warning(
                        "[%s] Failed to persist status to Blob (attempt %d/%d): %s — retrying in %ds",
                        job_id, attempt + 1, _MAX_RETRIES, exc, _RETRY_DELAY,
                    )
                    time.sleep(_RETRY_DELAY)
                else:
                    logger.error(
                        "[%s] Failed to persist status to Blob after %d attempts: %s — job visible only on this pod until Blob storage is restored.",
                        job_id, _MAX_RETRIES, exc,
                    )
        return False

    if sync:
        return _upload_with_retry()

    threading.Thread(
        target=_upload_with_retry,
        daemon=True,
        name=f"persist-{job_id}",
    ).start()
    return True


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
    except Exception as exc:
        logger.warning("[%s] get_job_status blob miss: %s", job_id, exc)
        return None


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
    # Cache miss â€” reload from Blob (handles container restarts, P7)
    try:
        data: dict = json.loads(
            _download_blob(ARTIFACT_CONTAINER, f"{job_id}/invocables_map.json")
        )
        with _JOB_MAP_LOCK:
            _JOB_INVOCABLE_MAPS.setdefault(job_id, {}).update(data)
        return data.get(name)
    except Exception as exc:
        logger.warning("[%s] get_invocable blob miss for '%s': %s", job_id, name, exc)
        return None


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

