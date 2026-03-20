"""api/discovery.py – Binary discovery pipeline: subprocess invocation, GUI bridge, invocable extraction.

_extract_invocables: normalise any discovery JSON payload to a flat list.
_call_gui_bridge:    dispatch Windows-only analysis to the GUI bridge VM.
_run_discovery:      run the discovery subprocess, merge results, call bridge.
"""

from __future__ import annotations

import hashlib
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
from api.storage import _upload_to_blob, _download_blob, _get_job_status, _persist_job_status

logger = logging.getLogger("mcp_factory.api")


# ── Discovery result cache (blob-backed) ──────────────────────────────────────
# Keyed by SHA-256 of the uploaded binary.  A cache hit skips the entire
# discovery pipeline (subprocess + bridge) and returns the previously
# computed invocables list.  The cache blob lives at:
#   artifacts / discovery-cache / {sha256} / discovery_result.json
# and a separate bridge-only cache at:
#   artifacts / discovery-cache / {sha256} / bridge_result.json
_DISCOVERY_CACHE_PREFIX = "discovery-cache"


def _dll_sha256(binary_path: Path) -> str:
    """Compute the SHA-256 hex digest of a file."""
    h = hashlib.sha256()
    with open(binary_path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):  # 1 MB chunks
            h.update(chunk)
    return h.hexdigest()


def _read_cache_blob(sha: str, filename: str) -> dict | None:
    """Try to read a JSON cache blob.  Returns None on miss."""
    blob_name = f"{_DISCOVERY_CACHE_PREFIX}/{sha}/{filename}"
    try:
        raw = _download_blob(ARTIFACT_CONTAINER, blob_name)
        return json.loads(raw)
    except Exception:
        return None


def _write_cache_blob(sha: str, filename: str, data: dict) -> None:
    """Write a JSON cache blob (best-effort, never raises)."""
    blob_name = f"{_DISCOVERY_CACHE_PREFIX}/{sha}/{filename}"
    try:
        _upload_to_blob(ARTIFACT_CONTAINER, blob_name, json.dumps(data).encode())
        logger.info("Discovery cache written: %s", blob_name)
    except Exception as exc:
        logger.warning("Failed to write discovery cache blob %s: %s", blob_name, exc)


def _persist_bridge_warning(job_id: str, message: str) -> None:
    """Write a bridge_warning field into the job status blob."""
    try:
        existing = _get_job_status(job_id) or {}
        existing["bridge_warning"] = f"GUI bridge unreachable — Windows analysis skipped: {message}"
        _persist_job_status(job_id, existing, sync=True)
    except Exception as exc:
        logger.warning("[%s] Failed to persist bridge warning: %s", job_id, exc)


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


# ── Required / default fields for every invocable dict ────────────────────
_INVOCABLE_DEFAULTS: dict[str, Any] = {
    "name":        "",
    "source_type": "unknown",
    "confidence":  "low",
    "description": "",
    "parameters":  [],
    "execution":   {},
}


def _normalize_invocable(raw: Any) -> dict | None:
    """Coerce a bridge/discovery invocable into a canonical dict shape.

    Returns None if *raw* cannot be salvaged (no name, wrong type, etc.).
    Ensures every value is JSON-serializable (stringifies anything exotic).
    """
    if not isinstance(raw, dict):
        # dataclass / namedtuple → dict
        if hasattr(raw, "to_dict"):
            raw = raw.to_dict()
        elif hasattr(raw, "__dict__"):
            raw = {k: v for k, v in vars(raw).items() if not k.startswith("_")}
        else:
            return None

    name = raw.get("name", "")
    if not name or not isinstance(name, str):
        return None

    out: dict[str, Any] = {}
    for key, default in _INVOCABLE_DEFAULTS.items():
        val = raw.get(key, default)
        # Stringify non-serializable values (e.g. Path objects)
        if isinstance(val, Path):
            val = str(val)
        out[key] = val

    # Carry over extra keys the pipeline may use (signature, return_type, …)
    for key, val in raw.items():
        if key not in out:
            if isinstance(val, Path):
                val = str(val)
            out[key] = val

    # Ensure execution.dll_path / exe_path are strings, not Path objects
    if isinstance(out.get("execution"), dict):
        for pk in ("dll_path", "exe_path", "executable_path", "target_path"):
            if pk in out["execution"] and not isinstance(out["execution"][pk], str):
                out["execution"][pk] = str(out["execution"][pk])

    return out


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

    # ── Fast /health pre-check (5 s timeout) — fail fast instead of blocking
    # 70 s per job when the bridge is down.
    try:
        probe_resp = httpx.get(
            f"{GUI_BRIDGE_URL}/health",
            headers={"X-Bridge-Key": GUI_BRIDGE_SECRET},
            timeout=5.0,
        )
        if probe_resp.status_code != 200:
            msg = f"Bridge /health returned status {probe_resp.status_code}"
            print(f"[DIAG {job_id}] BRIDGE PRE-CHECK FAILED: {msg}", flush=True)
            logger.warning("[%s] %s — skipping bridge call", job_id, msg)
            _persist_bridge_warning(job_id, msg)
            return []
        print(f"[DIAG {job_id}] bridge /health OK", flush=True)
    except Exception as health_exc:
        msg = f"Bridge /health unreachable: {health_exc}"
        print(f"[DIAG {job_id}] BRIDGE PRE-CHECK FAILED: {msg}", flush=True)
        logger.warning("[%s] %s — skipping bridge call", job_id, msg)
        _persist_bridge_warning(job_id, msg)
        return []

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
        "types":   ["gui", "com", "cli", "registry", "ghidra"],
        "content": content_b64,   # None → bridge falls back to system-path lookup
    }
    # ── Bridge blob cache check ────────────────────────────────────────────
    _bridge_sha = _dll_sha256(binary_path)
    if not skip_cache:
        _cached_bridge = _read_cache_blob(_bridge_sha, "bridge_result.json")
    else:
        _cached_bridge = None
    if _cached_bridge and isinstance(_cached_bridge.get("invocables"), list):
        _bc = len(_cached_bridge["invocables"])
        logger.info("[%s] BRIDGE CACHE HIT (%s) — %d invocables", job_id, _bridge_sha[:12], _bc)
        print(f"[DIAG {job_id}] bridge blob cache hit sha={_bridge_sha[:12]}, {_bc} invocables", flush=True)
        return _cached_bridge["invocables"]

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
            result_line: str | None = None
            with httpx.stream(
                "POST",
                f"{GUI_BRIDGE_URL}/analyze",
                json=payload,
                headers={"X-Bridge-Key": GUI_BRIDGE_SECRET},
                # connect/write/read are per-operation timeouts.  read=30 is safe
                # because the bridge sends a heartbeat every 15 s, so we will
                # never wait more than ~15 s between data chunks.
                timeout=httpx.Timeout(connect=30.0, read=30.0, write=30.0, pool=10.0),
            ) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    stripped = line.strip()
                    if not stripped:
                        continue
                    try:
                        obj = json.loads(stripped)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(obj, dict) and obj.get("status") == "running":
                        logger.info("[%s] Bridge heartbeat — analysis in progress", job_id)
                        continue
                    result_line = stripped  # last non-heartbeat line is the result
            if not result_line:
                raise RuntimeError("Bridge stream closed without returning a result")
            data = json.loads(result_line)

            # ── Normalize the bridge response ──
            # The bridge may return:
            #   {"invocables": [...], "count": N, "errors": {...}}
            #   [...] (bare list)
            #   {"invocables": [...]} (minimal)
            # Accept all of these shapes.
            if isinstance(data, list):
                raw_invocables = data
            elif isinstance(data, dict):
                raw_invocables = data.get("invocables", [])
                if not isinstance(raw_invocables, list):
                    raw_invocables = []
            else:
                raw_invocables = []

            if isinstance(data, dict) and data.get("errors"):
                logger.warning("[%s] Bridge reported partial errors: %s", job_id, data["errors"])

            # Validate and normalize each invocable to a canonical shape
            invocables = []
            for raw_inv in raw_invocables:
                normed = _normalize_invocable(raw_inv)
                if normed is not None:
                    invocables.append(normed)

            logger.info("[%s] Bridge returned %d invocables (%d after normalization)",
                        job_id, len(raw_invocables), len(invocables))
            # ── Write bridge blob cache ───────────────────────────────────
            if invocables:
                _write_cache_blob(_bridge_sha, "bridge_result.json", {
                    "invocables": invocables,
                    "cached_at": time.time(),
                    "binary_sha256": _bridge_sha,
                })
            return invocables
        except Exception as exc:
            _last_exc = exc
            print(f"[DIAG {job_id}] bridge attempt {_attempt + 1} failed: {exc}", flush=True)
            if _attempt < _BRIDGE_MAX_RETRIES - 1:
                logger.warning(
                    "[%s] Bridge attempt %d failed (%s) — retrying in 10 s "
                    "(bridge likely cached the result; retry should be near-instant)",
                    job_id, _attempt + 1, exc,
                )
                time.sleep(10)
    # All attempts exhausted
    print(f"[DIAG {job_id}] bridge ALL ATTEMPTS EXHAUSTED: {_last_exc}", flush=True)
    logger.warning(
        "[%s] GUI bridge call failed after %d attempt(s): %s",
        job_id, _BRIDGE_MAX_RETRIES, _last_exc, exc_info=True,
    )
    # Persist the warning into the job record so the caller can surface it
    _persist_bridge_warning(job_id, str(_last_exc))
    return []


def _run_discovery(binary_path: Path, job_id: str, hints: str = "",
                   *, skip_cache: bool = False) -> dict:
    """Run the discovery pipeline on a local file path. Returns invocables list."""
    # ── Discovery cache check ─────────────────────────────────────────────
    _sha = _dll_sha256(binary_path)
    if not skip_cache:
        _cached_discovery = _read_cache_blob(_sha, "discovery_result.json")
        if _cached_discovery and isinstance(_cached_discovery.get("invocables"), list):
            _inv_count = len(_cached_discovery["invocables"])
            logger.info("[%s] DISCOVERY CACHE HIT (%s) — %d invocables, skipping full pipeline",
                        job_id, _sha[:12], _inv_count)
            print(f"[DIAG {job_id}] discovery cache hit sha={_sha[:12]}, {_inv_count} invocables", flush=True)
            return {
                "job_id": job_id,
                "artifact_blob": _cached_discovery.get("artifact_blob", f"{job_id}/cached"),
                "invocables": _cached_discovery["invocables"],
            }
    else:
        logger.info("[%s] skip_cache=True — bypassing discovery cache", job_id)
    logger.info("[%s] Discovery cache miss (%s) — running full pipeline", job_id, _sha[:12])

    out_dir = Path(tempfile.mkdtemp(prefix=f"mcp_{job_id}_"))
    cmd = [
        sys.executable,
        str(SRC_DISCOVERY_DIR / "main.py"),
        "--dll", str(binary_path),
        "--out", str(out_dir),
        "--no-demangle",
    ]
    if hints:
        import re as _re
        _safe_tag = _re.sub(r"[^A-Za-z0-9_\-]", "_", hints[:40])
        cmd += ["--tag", _safe_tag]
    if IS_WINDOWS:
        cmd += ["--registry"]  # scan HKLM App Paths, Uninstall, COM CLSIDs (§1.c / P9)

    # PYTHONPATH must include the discovery package directory so all sibling
    # modules (classify, exports, schema, …) resolve correctly.
    discovery_env = {
        **os.environ,
        "PYTHONPATH": str(SRC_DISCOVERY_DIR),
    }

    print(f"[DIAG {job_id}] subprocess start", flush=True)
    logger.info("[%s] STEP 7 \u2713  Discovery subprocess launched", job_id)
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=240,
        env=discovery_env,
    )
    print(f"[DIAG {job_id}] subprocess done rc={result.returncode}", flush=True)


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
    if result.returncode != 0:
        logger.error("[%s] STEP 8 \u2717  Discovery subprocess failed rc=%d: %s", job_id, result.returncode, result.stderr[-300:])
    elif not mcp_files:
        logger.error("[%s] STEP 8 \u2717  Discovery produced no output files", job_id)
    else:
        logger.info("[%s] STEP 8 \u2713  Discovery complete: %d file(s), %d invocables", job_id, len(mcp_files), len(merged_invocables))

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
        }, sync=True)

    # ── Augment with Windows-only analysis via GUI bridge (if configured) ──
    # The bridge covers GUI buttons, COM/TLB interfaces, Windows EXE CLI help,
    # and registry scan — none of which run in the Linux container.
    bridge_invocables = _call_gui_bridge(binary_path, job_id, hints)
    if not GUI_BRIDGE_URL or not GUI_BRIDGE_SECRET:
        logger.info("[%s] STEP 9 \u2014  Bridge not configured, skipped", job_id)
    elif bridge_invocables:
        logger.info("[%s] STEP 9 \u2713  Bridge returned %d invocables", job_id, len(bridge_invocables))
    else:
        logger.error("[%s] STEP 9 \u2717  Bridge returned 0 invocables (check NSG outbound rules for port 8090 / errno 110)", job_id)
    if bridge_invocables:
        # Bridge invocables (Ghidra / COM / GUI) are richer than the plain
        # pefile export stubs from the discovery subprocess — they carry
        # recovered parameter types from the decompiler.
        # Strategy: bridge wins.  If a name already exists in merged_invocables
        # (added by the subprocess with parameters:[]), REPLACE it.  New names
        # are appended as usual.
        bridge_by_name = {inv.get("name", ""): inv for inv in bridge_invocables if inv.get("name")}
        # Replace any existing invocable whose name matches a bridge result
        for i, existing in enumerate(merged_invocables):
            ename = existing.get("name", "")
            if ename in bridge_by_name:
                merged_invocables[i] = bridge_by_name.pop(ename)
        # Append any bridge invocables with names not seen before
        for name, inv in bridge_by_name.items():
            merged_invocables.append(inv)
            seen_names.add(name)

    # ── Write discovery cache ─────────────────────────────────────────────
    _write_cache_blob(_sha, "discovery_result.json", {
        "invocables": merged_invocables,
        "artifact_blob": primary_blob,
        "cached_at": time.time(),
        "binary_sha256": _sha,
    })

    return {
        "job_id": job_id,
        "artifact_blob": primary_blob,
        "invocables": merged_invocables,
    }
