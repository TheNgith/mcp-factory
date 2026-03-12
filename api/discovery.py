"""api/discovery.py – Binary discovery pipeline: subprocess invocation, GUI bridge, invocable extraction.

_extract_invocables: normalise any discovery JSON payload to a flat list.
_call_gui_bridge:    dispatch Windows-only analysis to the GUI bridge VM.
_run_discovery:      run the discovery subprocess, merge results, call bridge.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from api.config import (
    IS_WINDOWS,
    GUI_BRIDGE_URL,
    GUI_BRIDGE_SECRET,
    SRC_DISCOVERY_DIR,
    ARTIFACT_CONTAINER,
)
from api.storage import _upload_to_blob, _get_job_status, _persist_job_status

logger = logging.getLogger("mcp_factory.api")


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
    # Retry up to _BRIDGE_MAX_RETRIES times.  On a cold first upload the bridge
    # analysis can take 60-120 s; if the first HTTP connection times out or is
    # dropped by network infrastructure, the bridge will have finished and
    # *cached* the result by the time the retry arrives, so the retry returns
    # almost immediately.
    _BRIDGE_MAX_RETRIES = 2
    _last_exc: Exception | None = None
    for _attempt in range(_BRIDGE_MAX_RETRIES):
        try:
            logger.info(
                "[%s] Calling GUI bridge at %s for %s (attempt %d/%d)",
                job_id, GUI_BRIDGE_URL, binary_path.name, _attempt + 1, _BRIDGE_MAX_RETRIES,
            )
            resp = httpx.post(
                f"{GUI_BRIDGE_URL}/analyze",
                json=payload,
                headers={"X-Bridge-Key": GUI_BRIDGE_SECRET},
                timeout=180,  # 60s GUI timeout on bridge + other analyzers + network margin
            )
            resp.raise_for_status()
            data = resp.json()
            invocables = data.get("invocables", [])
            if data.get("errors"):
                logger.warning("[%s] Bridge reported partial errors: %s", job_id, data["errors"])
            logger.info("[%s] Bridge returned %d invocables", job_id, len(invocables))
            return invocables
        except Exception as exc:
            _last_exc = exc
            if _attempt < _BRIDGE_MAX_RETRIES - 1:
                logger.warning(
                    "[%s] Bridge attempt %d failed (%s) — retrying in 10 s "
                    "(bridge likely cached the result; retry should be near-instant)",
                    job_id, _attempt + 1, exc,
                )
                time.sleep(10)
    # All attempts exhausted
    logger.warning(
        "[%s] GUI bridge call failed after %d attempt(s): %s",
        job_id, _BRIDGE_MAX_RETRIES, _last_exc, exc_info=True,
    )
    # Persist the warning into the job record so the caller can surface it
    try:
        existing = _get_job_status(job_id) or {}
        existing["bridge_warning"] = f"GUI bridge unreachable — Windows analysis skipped: {_last_exc}"
        _persist_job_status(job_id, existing)
    except Exception as _e:
        logger.warning("[%s] Failed to persist bridge warning to job status: %s", job_id, _e)
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

    # ── Update progress before the bridge call so the UI doesn't freeze at 30% ──
    # _call_gui_bridge can block for up to 180s (+ 10s retry sleep); without this
    # update the job appears stuck at the "running 30%" status written in worker.py.
    if GUI_BRIDGE_URL and GUI_BRIDGE_SECRET:
        existing = _get_job_status(job_id) or {}
        _persist_job_status(job_id, {
            **existing,
            "status": "running",
            "progress": 60,
            "message": "Local analysis complete — calling Windows GUI bridge…",
            "updated_at": time.time(),
        })

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
