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


def _append_transcript(job_id: str, user_text: str, assistant_text: str,
                       tool_log: list | None = None) -> None:
    """Append a user/assistant exchange to {job_id}/chat_transcript.txt in blob.

    If tool_log is provided (list of {"call", "args", "result"} dicts) it is
    formatted between the user turn and the assistant turn so the full agentic
    trace is captured.

    Read-modify-write: safe for single-threaded chat sessions (one active
    stream_chat per job_id at a time).  Non-fatal on any error.
    """
    try:
        blob_name = f"{job_id}/chat_transcript.txt"
        if tool_log:
            lines = []
            for entry in tool_log:
                args_str = json.dumps(entry.get("args") or {}, separators=(",", ":"))
                result = entry.get("result") or ""
                lines.append(f"🔧 {entry['call']}({args_str})\n   ↳ {result}")
            tool_section = "[TOOL CALLS]\n" + "\n".join(lines) + "\n\n---\n\n"
        else:
            tool_section = ""
        entry = (
            f"[USER]\n{user_text}\n\n---\n\n"
            f"{tool_section}"
            f"[ASSISTANT]\n{assistant_text}\n\n---\n\n"
        )
        try:
            existing = _download_blob(ARTIFACT_CONTAINER, blob_name).decode("utf-8", errors="replace")
        except Exception:
            existing = ""
        _upload_to_blob(ARTIFACT_CONTAINER, blob_name, (existing + entry).encode("utf-8"))
    except Exception as exc:
        logger.warning("[%s] _append_transcript failed: %s", job_id, exc)



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
        # Use more retries and a longer delay for blocking (sync) callers such
        # as the final "done" persist in the worker, where durability matters most.
        _MAX_RETRIES = 5 if sync else 3
        _RETRY_DELAY = 3 if sync else 2
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
    """Return job status; always reads from Blob for non-terminal states.

    Non-terminal states (anything other than 'done'/'error') are never cached
    in memory across pods — otherwise a pod that saw 'running' will serve that
    stale value forever even after the worker pod writes 'done' to Blob.
    """
    with _JOB_STATUS_LOCK:
        result = _JOB_STATUS.get(job_id)
    # Only trust the in-memory entry when it is a terminal state; for running/
    # pending states always go to Blob so cross-pod reads see the latest write.
    if result is not None and result.get("status") in ("done", "error"):
        return result
    try:
        data = json.loads(_download_blob(ARTIFACT_CONTAINER, f"{job_id}/status.json"))
        with _JOB_STATUS_LOCK:
            _JOB_STATUS[job_id] = data
        return data
    except Exception as exc:
        # Blob miss is normal during the first seconds before the first persist;
        # return the in-memory value (which may be 'running') as a fallback.
        if result is not None:
            return result
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


# ── Per-job LLM findings (probe-and-learn memory) ─────────────────────────
# The LLM writes entries here via the record_finding synthetic tool.
# Entries survive session end and are injected into the system prompt on the
# next session so the model starts informed instead of probing from scratch.
_JOB_FINDINGS: dict[str, list] = {}
_JOB_FINDINGS_LOCK = threading.Lock()


def _save_finding(job_id: str, entry: dict) -> None:
    """Append a single finding dict to in-memory cache and blob."""
    import datetime
    entry.setdefault("recorded_at", datetime.datetime.utcnow().isoformat() + "Z")
    with _JOB_FINDINGS_LOCK:
        _JOB_FINDINGS.setdefault(job_id, []).append(entry)
        findings = list(_JOB_FINDINGS[job_id])
    try:
        _upload_to_blob(
            ARTIFACT_CONTAINER,
            f"{job_id}/findings.json",
            json.dumps(findings, indent=2).encode(),
        )
    except Exception as exc:
        logger.warning("[%s] Failed to persist finding to Blob: %s", job_id, exc)


def _load_findings(job_id: str) -> list:
    """Return all findings for a job; reads from blob on cache miss."""
    with _JOB_FINDINGS_LOCK:
        cached = _JOB_FINDINGS.get(job_id)
    if cached is not None:
        return cached
    try:
        data = json.loads(_download_blob(ARTIFACT_CONTAINER, f"{job_id}/findings.json"))
        with _JOB_FINDINGS_LOCK:
            _JOB_FINDINGS[job_id] = data
        return data
    except Exception:
        return []


def _patch_finding(job_id: str, function_name: str, patch: dict) -> None:
    """Update the most recent finding for function_name in-place.

    Used by the consistency-enforcement step in explore_worker to override
    the LLM's classification with ground-truth observed DLL return values.
    """
    with _JOB_FINDINGS_LOCK:
        findings = _JOB_FINDINGS.get(job_id, [])
        updated = False
        for i in range(len(findings) - 1, -1, -1):
            if findings[i].get("function") == function_name:
                findings[i].update(patch)
                updated = True
                break
        all_findings = list(findings)
    if not updated:
        return
    try:
        _upload_to_blob(
            ARTIFACT_CONTAINER,
            f"{job_id}/findings.json",
            json.dumps(all_findings, indent=2).encode(),
        )
    except Exception as exc:
        logger.warning("[%s] Failed to persist patched finding to Blob: %s", job_id, exc)


def _patch_invocable(job_id: str, function_name: str, patch: dict) -> str:
    """Apply a patch dict to an invocable and re-upload both blob artifacts.

    patch keys:
      - "function_description": str  → replaces inv["description"]
      - "<param_name>": {"name": str, "description": str}  → renames param + sets desc;
        also infers direction ("in"/"out") from description text
      - "parameters": list  → replaces the full parameters list wholesale (backfill path)
      - "criticality": str  → sets criticality label
      - "depends_on": list  → sets dependency list

    Returns a human-readable confirmation string.
    """
    import re as _re
    with _JOB_MAP_LOCK:
        inv_map = _JOB_INVOCABLE_MAPS.get(job_id, {})
        inv = inv_map.get(function_name)
        if inv is None:
            return f"_patch_invocable: function '{function_name}' not found in job '{job_id}'"
        inv = dict(inv)  # shallow copy to avoid mutating shared reference
        inv["parameters"] = [dict(p) for p in (inv.get("parameters") or [])]

    renames: list[str] = []

    # Apply function description
    if "function_description" in patch:
        inv["description"] = patch["function_description"]
        inv["doc"] = patch["function_description"]

    # Apply parameter renames/descriptions
    for param in inv["parameters"]:
        old_name = param.get("name", "")
        if old_name in patch and isinstance(patch[old_name], dict):
            p_patch = patch[old_name]
            new_name = p_patch.get("name") or p_patch.get("semantic_name")
            if new_name and new_name != old_name:
                param["name"] = new_name
                renames.append(f"{old_name} → {new_name}")
            if "description" in p_patch:
                desc = p_patch["description"]
                param["description"] = desc
                # Infer direction from the description the LLM wrote, so that
                # generate.py correctly includes/excludes params from `required`.
                # Ghidra marks byte* as "out" by default but const char* inputs
                # are also byte* — the LLM's description is the ground truth.
                desc_lower = desc.lower()
                if _re.search(r"\boutput\b.*\bbuffer\b|\bbuffer\b.*\boutput\b|\bout\b.*\bpointer\b|\bauto.allocated\b", desc_lower):
                    param["direction"] = "out"
                elif _re.search(r"\binput\b|\bprovide[sd]?\b|\bpass\b|\bspecif", desc_lower):
                    param["direction"] = "in"

    # Accept a pre-built parameters list (e.g. from backfill) — replaces the
    # current list wholesale, direction already set by the caller.
    if "parameters" in patch and isinstance(patch["parameters"], list):
        inv["parameters"] = patch["parameters"]

    # Scalar fields from backfill/refinement
    if "criticality" in patch:
        inv["criticality"] = patch["criticality"]
    if "depends_on" in patch:
        inv["depends_on"] = patch["depends_on"]
    if "description" in patch:
        new_desc = patch["description"]
        existing_desc = (inv.get("description") or inv.get("doc") or "").strip()
        # Guard: only overwrite if the existing description is blank or is a raw
        # Ghidra type-signature (e.g. "undefined8 CS_Foo(byte * param_1, ...)").
        # This prevents backfill from downgrading a human-readable enriched description
        # back to a Ghidra annotation when the synthesis doc has incomplete coverage.
        _is_ghidra_sig = bool(_re.match(
            r"^(undefined\d*|void|int\d*|uint\d*|char|byte|BOOL|HRESULT|HANDLE|DWORD|LONG)\b",
            existing_desc, _re.I,
        ))
        if not existing_desc or _is_ghidra_sig:
            inv["description"] = new_desc
            inv["doc"] = new_desc

    # Write back to in-memory map
    with _JOB_MAP_LOCK:
        _JOB_INVOCABLE_MAPS.setdefault(job_id, {})[function_name] = inv

    # Re-upload invocables_map.json
    with _JOB_MAP_LOCK:
        full_map = dict(_JOB_INVOCABLE_MAPS.get(job_id, {}))
    try:
        _upload_to_blob(
            ARTIFACT_CONTAINER,
            f"{job_id}/invocables_map.json",
            json.dumps(full_map).encode(),
        )
    except Exception as exc:
        logger.warning("[%s] _patch_invocable: failed to re-upload invocables_map: %s", job_id, exc)

    # Regenerate and re-upload mcp_schema.json
    try:
        from api.generate import run_generate  # type: ignore
        invocables_list = list(full_map.values())
        # Derive component name from existing schema blob if possible
        component = "mcp-component"
        try:
            existing = json.loads(_download_blob(ARTIFACT_CONTAINER, f"{job_id}/mcp_schema.json"))
            component = existing.get("component", component)
        except Exception:
            pass
        run_generate({"job_id": job_id, "selected": invocables_list, "component_name": component})
    except Exception as exc:
        logger.warning("[%s] _patch_invocable: failed to regenerate schema: %s", job_id, exc)

    rename_str = (", ".join(renames)) if renames else "no param renames"
    logger.info("[%s] _patch_invocable: patched %s (%s)", job_id, function_name, rename_str)
    return f"Schema updated for {function_name}: {rename_str}."


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

