"""
gui_bridge.py — Windows-only analysis bridge
=============================================
Runs on the self-hosted Windows runner VM (or any Windows machine with
pywinauto + pywin32 installed).  Exposes a small FastAPI HTTP server that
the Linux ACA pipeline can call to perform analysers that require Windows:

  • GUI  (pywinauto UIA tree walk)
  • COM / Type Library  (pythoncom / pywin32)
  • CLI  (run Windows EXEs for --help output)
  • Registry scan  (winreg HKLM App Paths / Uninstall / COM CLSIDs)

Authentication: every request must carry  X-Bridge-Key: <BRIDGE_SECRET>
(set the BRIDGE_SECRET env var before starting this server).

Usage (on the Windows VM):
    set BRIDGE_SECRET=<a long random string>
    python scripts/gui_bridge.py          # listens on 0.0.0.0:8090

The ACA pipeline reads GUI_BRIDGE_URL (e.g. http://<vm-ip>:8090) and
GUI_BRIDGE_SECRET from its environment / Key Vault secrets and calls
POST /analyze with:
    {
      "path":   "C:\\Windows\\System32\\calc.exe",   # or uploaded temp path
      "hints":  "calculator",
      "types":  ["gui", "com", "cli", "registry"]    # optional filter
    }

Returns standard discovery JSON  { "invocables": [...] }.
"""

from __future__ import annotations

import asyncio
import base64
import concurrent.futures
import json
import logging
import os
import secrets
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

# ── ensure the discovery package is importable ───────────────────────────────
_ROOT = Path(__file__).parent.parent
_DISCOVERY = _ROOT / "src" / "discovery"
for _p in [str(_ROOT), str(_DISCOVERY)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("gui_bridge")

# ── Analysis result cache ──────────────────────────────────────────────────────
# Key: resolved absolute exe path (lowercased); value: (timestamp, result_dict).
# A warm cache hit skips re-launching the same binary on repeated uploads,
# which matters most for UWP stubs that require a full cold-start window wait.
_ANALYSIS_CACHE: dict[str, tuple[float, dict]] = {}
_ANALYSIS_CACHE_TTL = 3600  # seconds — 1 hour

# ── Active analysis cancellation ─────────────────────────────────────────────
# Only one analysis runs at a time.  When a new /analyze request arrives,
# the current one is signalled to abort via kill_event and its launched
# process is killed so pywinauto unblocks from COM/UIA waits immediately.
# _run_analysis_sync checks kill_event between phases and returns early.
_active_kill_event: "threading.Event | None" = None
_active_target_stem: "str | None" = None
_active_lock = threading.Lock()


def _kill_processes_by_stem(stem: str) -> None:
    """Kill any running process whose name contains *stem* (case-insensitive)."""
    try:
        import psutil
        for proc in psutil.process_iter(["name", "pid"]):
            pname = (proc.info["name"] or "").lower()
            if stem in pname or pname.startswith(stem):
                try:
                    proc.kill()
                    logger.info("Killed lingering process %s (pid=%d)",
                                proc.info["name"], proc.info["pid"])
                except Exception:
                    pass
    except Exception:
        pass

# ── Auth ─────────────────────────────────────────────────────────────────────
BRIDGE_SECRET = os.getenv("BRIDGE_SECRET", "")
if not BRIDGE_SECRET:
    logger.warning(
        "BRIDGE_SECRET env var is not set — "
        "the bridge will reject ALL requests.  "
        "Set it before starting the server."
    )

app = FastAPI(title="MCP Factory GUI Bridge", version="1.0.0")


def _check_auth(x_bridge_key: str) -> None:
    """Constant-time secret comparison — raises 401 on mismatch."""
    if not BRIDGE_SECRET or not secrets.compare_digest(x_bridge_key, BRIDGE_SECRET):
        raise HTTPException(status_code=401, detail="Invalid or missing X-Bridge-Key")


# ── Request / response models ─────────────────────────────────────────────────
class AnalyzeRequest(BaseModel):
    path: str
    hints: str = ""
    types: list[str] = [
        "gui", "com", "cli", "registry",
        "dotnet", "rpc",
        "sql", "wsdl", "idl", "js", "script", "openapi", "jndi",
        "ghidra",
    ]  # base64-encoded binary content (optional): lets the bridge analyze an
    # uploaded file whose Linux temp path doesn't exist on the Windows VM.
    content: str | None = None


class ExecuteRequest(BaseModel):
    invocable: dict
    args: dict = {}


# ── Lazy analyzer imports (all Windows-only) ─────────────────────────────────
def _import_gui():
    from gui_analyzer import analyze_gui  # type: ignore
    return analyze_gui

def _import_com():
    from com_scan import scan_com_registry, com_objects_to_invocables  # type: ignore
    return scan_com_registry, com_objects_to_invocables

def _import_tlb():
    from tlb_analyzer import scan_type_library  # type: ignore
    return scan_type_library

def _import_cli():
    from cli_analyzer import analyze_cli  # type: ignore
    return analyze_cli

def _import_registry():
    from registry_analyzer import analyze_registry  # type: ignore
    return analyze_registry

def _import_dotnet():
    from dotnet_analyzer import get_dotnet_methods  # type: ignore
    return get_dotnet_methods

def _import_rpc():
    from rpc_analyzer import analyze_rpc, rpc_to_invocables  # type: ignore
    return analyze_rpc, rpc_to_invocables

def _import_sql():
    from sql_analyzer import analyze_sql  # type: ignore
    return analyze_sql

def _import_wsdl():
    from wsdl_analyzer import analyze_wsdl  # type: ignore
    return analyze_wsdl

def _import_idl():
    from idl_analyzer import analyze_idl  # type: ignore
    return analyze_idl

def _import_js():
    from js_analyzer import analyze_js  # type: ignore
    return analyze_js

def _import_script():
    from script_analyzer import analyze_script  # type: ignore
    return analyze_script

def _import_openapi():
    from openapi_analyzer import analyze_openapi  # type: ignore
    return analyze_openapi

def _import_jndi():
    from jndi_analyzer import analyze_jndi  # type: ignore
    return analyze_jndi

def _import_ghidra():
    from ghidra_analyzer import analyze_with_ghidra  # type: ignore
    return analyze_with_ghidra


def _inv_to_dict(inv: Any) -> dict:
    """Convert an Invocable dataclass (or dict) to a plain JSON-safe dict.

    Uses to_dict() when available so parameters is always a list
    (not the raw Optional[str] dataclass field) and all fields are
    in the canonical pipeline format.  Stringifies non-serializable
    values (Path objects, etc.) to prevent JSONResponse failures.
    """
    if isinstance(inv, dict):
        d = inv
    elif hasattr(inv, "to_dict"):
        d = inv.to_dict()
    else:
        d = {k: v for k, v in vars(inv).items() if not k.startswith("_")}

    # Recursively ensure everything is JSON-serializable
    return _make_serializable(d)


def _make_serializable(obj: Any) -> Any:
    """Recursively coerce Path / non-primitive objects to strings."""
    if isinstance(obj, dict):
        return {k: _make_serializable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_make_serializable(v) for v in obj]
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, (str, int, float, bool, type(None))):
        return obj
    return str(obj)


# How long (seconds) to allow the GUI analyzer before giving up.
# pywinauto can hang for 90+ s on UWP stubs (e.g. calc.exe on Server 2022).
# Raised to 60 s to give Win11 UWP apps enough time to expose their UIA tree.
GUI_ANALYZE_TIMEOUT = 60


def _run_analysis_sync(target: Path, requested: set, hints: str,
                       kill_event: threading.Event) -> dict:
    """Synchronous analysis worker — runs in a thread pool executor.

    Keeping all blocking I/O (pywinauto, COM, subprocess) in a plain function
    avoids stalling the uvicorn async event loop.

    kill_event is checked between each analysis phase.  When set (because a
    newer upload arrived), the function returns whatever partial results it has
    collected so far, freeing the thread immediately.
    """
    invocables: list[dict] = []
    errors: dict[str, str] = {}

    def _aborted() -> bool:
        if kill_event.is_set():
            logger.info("Analysis of %s superseded by newer request — stopping early",
                        target.name)
            errors["aborted"] = "superseded by newer upload"
            return True
        return False

    # ── GUI ──────────────────────────────────────────────────────────────────
    if not _aborted() and "gui" in requested and target.suffix.lower() == ".exe":
        try:
            analyze_gui_fn = _import_gui()
            # Enforce a hard time-box: pywinauto retries can add up to 90+ s on
            # headless Server 2022 where UWP windows never appear.
            # NOTE: do NOT use `with ThreadPoolExecutor(…) as pool` here —
            # shutdown(wait=True) blocks even after TimeoutError fires.
            # shutdown(wait=False) lets the hanging thread die in the background.
            _gui_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            _gui_future = _gui_pool.submit(analyze_gui_fn, target, 20)
            try:
                gui_results = _gui_future.result(timeout=GUI_ANALYZE_TIMEOUT)
            except concurrent.futures.TimeoutError:
                logger.warning(
                    "GUI analysis timed out after %ds for %s — killing process",
                    GUI_ANALYZE_TIMEOUT, target.name,
                )
                _kill_processes_by_stem(target.stem.lower())
                errors["gui"] = f"timed out after {GUI_ANALYZE_TIMEOUT}s"
                gui_results = []
            finally:
                _gui_pool.shutdown(wait=False)
            invocables.extend(_inv_to_dict(i) for i in gui_results)
            logger.info("GUI: %d invocables from %s", len(gui_results), target.name)
        except Exception as exc:
            logger.warning("GUI analysis failed: %s", exc)
            errors["gui"] = str(exc)

    # ── COM / Type Library ───────────────────────────────────────────────────
    if not _aborted() and "com" in requested:
        try:
            scan_type_library = _import_tlb()
            tlb_results = scan_type_library(target)
            for entry in tlb_results:
                for method in entry.get("methods", []):
                    execution: dict = {
                        "method":           "com_invoke",
                        "dll_path":         str(target),
                        "interface":        entry.get("name", ""),
                        "interface_guid":   entry.get("guid", ""),
                        "member":           method.get("name", ""),
                        # All CoClass CLSIDs in this TLB — tried in order at
                        # execute time until one exposes the method.
                        "coclass_candidates": entry.get("coclass_candidates", []),
                    }
                    invocables.append({
                        "name":        method.get("name", "unknown"),
                        "source_type": "com",
                        "signature":   method.get("signature", method.get("name", "")),
                        "confidence":  "high",
                        "dll_path":    str(target),
                        "doc_comment": method.get("doc", ""),
                        "parameters":  method.get("parameters", []),
                        "return_type": method.get("return_type", ""),
                        "execution":   execution,
                    })
            logger.info("COM/TLB: %d methods from %s", len(tlb_results), target.name)
        except Exception as exc:
            logger.warning("COM/TLB analysis failed: %s", exc)
            errors["com"] = str(exc)

    # ── CLI ──────────────────────────────────────────────────────────────────
    if not _aborted() and "cli" in requested and target.suffix.lower() == ".exe":
        try:
            analyze_cli = _import_cli()
            results = analyze_cli(target)
            invocables.extend(_inv_to_dict(i) for i in results)
            logger.info("CLI: %d invocables from %s", len(results), target.name)
        except Exception as exc:
            logger.warning("CLI analysis failed: %s", exc)
            errors["cli"] = str(exc)

    # ── Registry ─────────────────────────────────────────────────────────────
    if not _aborted() and "registry" in requested:
        try:
            analyze_registry = _import_registry()
            results = analyze_registry(hints=hints or target.stem)
            invocables.extend(_inv_to_dict(i) for i in results)
            logger.info("Registry: %d invocables", len(results))
        except Exception as exc:
            logger.warning("Registry analysis failed: %s", exc)
            errors["registry"] = str(exc)

    # ── .NET reflection ──────────────────────────────────────────────────────
    if not _aborted() and "dotnet" in requested and target.suffix.lower() in (".dll", ".exe"):
        try:
            get_dotnet_methods = _import_dotnet()
            results = get_dotnet_methods(target)
            invocables.extend(_inv_to_dict(i) for i in results)
            logger.info(".NET: %d methods from %s", len(results), target.name)
        except Exception as exc:
            logger.warning(".NET analysis failed: %s", exc)
            errors["dotnet"] = str(exc)

    # ── RPC interface detection ───────────────────────────────────────────────
    if not _aborted() and "rpc" in requested and target.suffix.lower() in (".dll", ".exe"):
        try:
            analyze_rpc, rpc_to_invocables = _import_rpc()
            rpc_result = analyze_rpc(target)
            results = rpc_to_invocables(rpc_result, target)
            invocables.extend(_inv_to_dict(i) for i in results)
            logger.info("RPC: %d interfaces from %s", len(results), target.name)
        except Exception as exc:
            logger.warning("RPC analysis failed: %s", exc)
            errors["rpc"] = str(exc)

    # ── SQL source files ──────────────────────────────────────────────────────
    if not _aborted() and "sql" in requested and target.suffix.lower() == ".sql":
        try:
            analyze_sql = _import_sql()
            results = analyze_sql(target)
            invocables.extend(_inv_to_dict(i) for i in results)
            logger.info("SQL: %d objects from %s", len(results), target.name)
        except Exception as exc:
            logger.warning("SQL analysis failed: %s", exc)
            errors["sql"] = str(exc)

    # ── WSDL / SOAP ───────────────────────────────────────────────────────────
    if not _aborted() and "wsdl" in requested and target.suffix.lower() in (".wsdl", ".xml"):
        try:
            analyze_wsdl = _import_wsdl()
            results = analyze_wsdl(target)
            invocables.extend(_inv_to_dict(i) for i in results)
            logger.info("WSDL: %d operations from %s", len(results), target.name)
        except Exception as exc:
            logger.warning("WSDL analysis failed: %s", exc)
            errors["wsdl"] = str(exc)

    # ── CORBA IDL ─────────────────────────────────────────────────────────────
    if not _aborted() and "idl" in requested and target.suffix.lower() == ".idl":
        try:
            analyze_idl = _import_idl()
            results = analyze_idl(target)
            invocables.extend(_inv_to_dict(i) for i in results)
            logger.info("IDL: %d methods from %s", len(results), target.name)
        except Exception as exc:
            logger.warning("IDL analysis failed: %s", exc)
            errors["idl"] = str(exc)

    # ── JavaScript / TypeScript ───────────────────────────────────────────────
    if not _aborted() and "js" in requested and target.suffix.lower() in (".js", ".ts", ".mjs", ".cjs"):
        try:
            analyze_js = _import_js()
            results = analyze_js(target)
            invocables.extend(_inv_to_dict(i) for i in results)
            logger.info("JS/TS: %d functions from %s", len(results), target.name)
        except Exception as exc:
            logger.warning("JS/TS analysis failed: %s", exc)
            errors["js"] = str(exc)

    # ── Scripts (Python, PowerShell, Batch, VBS, Shell, Ruby, PHP) ───────────
    _SCRIPT_EXTS = (".py", ".ps1", ".bat", ".cmd", ".vbs", ".sh", ".bash", ".rb", ".php")
    if not _aborted() and "script" in requested and target.suffix.lower() in _SCRIPT_EXTS:
        try:
            analyze_script = _import_script()
            results = analyze_script(target)
            invocables.extend(_inv_to_dict(i) for i in results)
            logger.info("Script: %d invocables from %s", len(results), target.name)
        except Exception as exc:
            logger.warning("Script analysis failed: %s", exc)
            errors["script"] = str(exc)

    # ── OpenAPI / Swagger / JSON-RPC descriptors ──────────────────────────────
    if not _aborted() and "openapi" in requested and target.suffix.lower() in (".yaml", ".yml", ".json"):
        try:
            analyze_openapi = _import_openapi()
            results = analyze_openapi(target)
            invocables.extend(_inv_to_dict(i) for i in results)
            logger.info("OpenAPI: %d operations from %s", len(results), target.name)
        except Exception as exc:
            logger.warning("OpenAPI analysis failed: %s", exc)
            errors["openapi"] = str(exc)

    # ── JNDI bindings ─────────────────────────────────────────────────────────
    _JNDI_EXTS = (".properties", ".jndi", ".xml")
    if not _aborted() and "jndi" in requested and target.suffix.lower() in _JNDI_EXTS:
        try:
            analyze_jndi = _import_jndi()
            results = analyze_jndi(target)
            invocables.extend(_inv_to_dict(i) for i in results)
            logger.info("JNDI: %d bindings from %s", len(results), target.name)
        except Exception as exc:
            logger.warning("JNDI analysis failed: %s", exc)
            errors["jndi"] = str(exc)

    # ── Ghidra fallback — stripped / undocumented binaries ─────────────────
    # Fires when a .dll or .exe yielded zero invocables from every other
    # analyzer (no TLB, no .NET, no RPC, no CLI help).  Ghidra disassembles
    # the binary and reconstructs function signatures from raw machine code.
    _PE_EXTS = (".dll", ".exe")
    if (not _aborted()
            and "ghidra" in requested
            and target.suffix.lower() in _PE_EXTS
            and len(invocables) == 0):
        try:
            logger.info("Ghidra fallback triggered for %s (all other analyzers returned 0)", target.name)
            analyze_with_ghidra = _import_ghidra()
            ghidra_results = analyze_with_ghidra(target, timeout_s=180, exported_only=True)
            invocables.extend(ghidra_results)
            logger.info("Ghidra: %d functions recovered from %s",
                        len(ghidra_results), target.name)
        except Exception as exc:
            logger.warning("Ghidra analysis failed: %s", exc)
            errors["ghidra"] = str(exc)

    # De-duplicate by name
    seen: set[str] = set()
    unique: list[dict] = []
    for inv in invocables:
        name = inv.get("name", "")
        if name and name not in seen:
            seen.add(name)
            unique.append(inv)

    return {
        "invocables": unique,
        "count":      len(unique),
        "errors":     errors,
        "source":     str(target),
    }


# ── Main endpoint ─────────────────────────────────────────────────────────────
@app.post("/analyze")
async def analyze(
    body: AnalyzeRequest,
    x_bridge_key: str = Header(default=""),
):
    _check_auth(x_bridge_key)

    tmp_dir: Path | None = None

    # Persistent upload directory — binaries written here survive after analysis
    # so that /execute calls can still load them via ctypes/subprocess.
    # Using a fixed path (not tempfile) means the dll_path stored in invocables
    # remains valid across the full analyze → chat → execute lifecycle.
    _UPLOAD_DIR = Path(r"C:\mcp-factory\uploads")
    _UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    if body.content:
        # The pipeline sent base64-encoded file bytes — decode to a persistent file.
        # This handles uploaded binaries whose Linux temp path doesn't exist on Windows.
        try:
            import shutil
            filename = Path(body.path).name
            target   = _UPLOAD_DIR / filename
            # Decode first — a binascii.Error here means genuinely corrupt content.
            _decoded = base64.b64decode(body.content)
            try:
                target.write_bytes(_decoded)
                logger.info(
                    "Decoded uploaded binary to %s (%d bytes)",
                    target, target.stat().st_size,
                )
            except OSError as _write_err:
                # The file may be locked because a previous /execute call loaded it
                # via ctypes.LoadLibrary and the handle wasn't released yet.
                # If the file already exists on disk (same binary, different session),
                # continue analysis with the existing copy rather than aborting.
                if target.exists():
                    logger.warning(
                        "Could not overwrite %s (%s) — file is locked by a loaded DLL; "
                        "reusing existing on-disk copy for analysis.",
                        target.name, _write_err,
                    )
                else:
                    raise  # truly missing upload — propagate as 500
            # Prefer the real system-path binary for GUI analysis — persistent copies
            # of UWP/MSIX stubs (e.g. calc.exe) can't trigger package activation
            # from arbitrary directories.  Search system paths directly by
            # filename instead of using _resolve_exe_path(), which short-circuits
            # when the persistent file itself already exists on disk.
            _sys_paths = [
                Path(r"C:\Windows\System32") / target.name,
                Path(r"C:\Windows") / target.name,
                Path(r"C:\Windows\SysWOW64") / target.name,
                Path(r"C:\Program Files") / target.name,
                Path(r"C:\Program Files (x86)") / target.name,
            ]
            for _sys_candidate in _sys_paths:
                if _sys_candidate.exists() and _sys_candidate.resolve() != target.resolve():
                    logger.info("Using system path %s for analysis instead of upload copy", _sys_candidate)
                    target = _sys_candidate
                    break
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Could not decode content: {exc}")
    else:
        target = Path(body.path)
        if not target.exists():
            # The path comes from the Linux ACA container (e.g. /tmp/mcp_xyz/calc.exe).
            # It won't exist here — try to resolve the filename against Windows system paths.
            filename = Path(body.path).name
            system_candidates = [
                Path(r"C:\Windows\System32") / filename,
                Path(r"C:\Windows") / filename,
                Path(r"C:\Windows\SysWOW64") / filename,
                Path(r"C:\Program Files") / filename,
                Path(r"C:\Program Files (x86)") / filename,
            ]
            for candidate in system_candidates:
                if candidate.exists():
                    logger.info("Resolved '%s' → '%s' via system path fallback", body.path, candidate)
                    target = candidate
                    break
            else:
                raise HTTPException(
                    status_code=404,
                    detail=f"Path not found: {body.path} (also searched system paths for '{filename}')",
                )

    requested = set(body.types)

    # ── Cache lookup ──────────────────────────────────────────────────────────
    _cache_key = str(target).lower()
    _cached = _ANALYSIS_CACHE.get(_cache_key)
    if _cached:
        _ts, _res = _cached
        if time.time() - _ts < _ANALYSIS_CACHE_TTL:
            logger.info("Cache hit for %s (%d invocables)", target.name, _res["count"])
            # Return single-line NDJSON — same wire format as the streaming path.
            async def _cached_gen():
                yield (json.dumps(_res) + "\n").encode()
            return StreamingResponse(_cached_gen(), media_type="application/x-ndjson")

    # ── Cancel any in-progress analysis and start fresh ───────────────────────
    # If another upload is being analysed right now, signal it to stop and kill
    # whatever process it launched.  This frees COM/UIA locks immediately so
    # the new analysis can start without queuing behind a stuck pywinauto thread.
    global _active_kill_event, _active_target_stem
    kill_event = threading.Event()
    with _active_lock:
        if _active_kill_event is not None and not _active_kill_event.is_set():
            prev_stem = _active_target_stem or ""
            logger.info("New upload (%s) — aborting in-progress analysis of %s",
                        target.name, prev_stem or "unknown")
            _active_kill_event.set()
            if prev_stem:
                _kill_processes_by_stem(prev_stem)
        _active_kill_event = kill_event
        _active_target_stem = target.stem.lower()

    async def _generate():
        global _active_kill_event, _active_target_stem
        loop = asyncio.get_running_loop()
        future = loop.run_in_executor(
            None,  # default ThreadPoolExecutor
            _run_analysis_sync,
            target,
            requested,
            body.hints,
            kill_event,
        )
        try:
            # Send a keepalive heartbeat every 15 s while the analysis runs.
            # Avoids Azure LB / ACA ingress idle-TCP drops on long analyses.
            # Using future.done() + asyncio.sleep avoids asyncio.wait_for /
            # asyncio.shield edge cases that can silently abort the generator.
            while not future.done():
                await asyncio.sleep(15)
                if not future.done():
                    yield b'{"status":"running"}\n'
            result = future.result()  # non-blocking: future is already done
        except Exception as exc:
            logger.error("Bridge analysis raised in generator: %s", exc)
            yield (json.dumps({"invocables": [], "count": 0, "errors": {"generator": str(exc)}}) + "\n").encode()
            return
        finally:
            # Release the global slot so the next request doesn't see a stale event.
            with _active_lock:
                if _active_kill_event is kill_event:
                    _active_kill_event = None
                    _active_target_stem = None
            # NOTE: uploaded binaries are now kept in C:\mcp-factory\uploads\
            # (not a temp dir) so they remain accessible for /execute calls.
            # tmp_dir is always None post-refactor; this guard is kept for safety.
            if tmp_dir is not None:
                try:
                    import shutil
                    shutil.rmtree(tmp_dir, ignore_errors=True)
                except Exception:
                    pass

        # Only cache results with more than 1 invocable — a count of 1 almost always
        # means the GUI analysis failed (cold-start) and only the CLI stub came back.
        # Skipping the cache forces a fresh retry on the next request.
        if result.get("count", 0) > 1:
            _ANALYSIS_CACHE[_cache_key] = (time.time(), result)
        else:
            logger.info(
                "Skipping cache for %s — only %d invocable(s) found (likely cold-start failure)",
                target.name, result.get("count", 0),
            )
        yield (json.dumps(result) + "\n").encode()

    return StreamingResponse(_generate(), media_type="application/x-ndjson")


# ── Execution helpers (mirror of api/main.py — runs on Windows) ──────────────

def _resolve_exe_path(path: str) -> str:
    """Resolve a possibly-Linux temp path to a real Windows executable path."""
    p = Path(path)
    if p.exists():
        return str(p)
    filename = p.name
    for candidate in [
        Path(r"C:\mcp-factory\uploads") / filename,
        Path(r"C:\Windows\System32") / filename,
        Path(r"C:\Windows") / filename,
        Path(r"C:\Windows\SysWOW64") / filename,
        Path(r"C:\Program Files") / filename,
        Path(r"C:\Program Files (x86)") / filename,
    ]:
        if candidate.exists():
            logger.info("Resolved '%s' → '%s'", path, candidate)
            return str(candidate)
    return path  # let the caller surface a natural error


def _execute_cli_bridge(execution: dict, name: str, args: dict) -> str:
    target = (
        execution.get("executable_path")
        or execution.get("target_path")
        or execution.get("dll_path", "")
    )
    if not target:
        return f"CLI error: no executable path configured for '{name}'"
    target = _resolve_exe_path(target)
    no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    # If the exe stem matches the invocable name, treat it as a launch invocable.
    # Check if it's already running first — avoids stacking duplicate windows.
    if Path(target).stem.lower() == name.lower():
        try:
            _running_app = _connect_app(target)
            return f"{Path(target).name} is already running — reusing existing window."
        except Exception:
            pass
        try:
            # Use shell `start ""` for MSIX/UWP stubs (e.g. calc.exe on Win10+)
            # which exit immediately when called via direct Popen. `start`
            # triggers proper COM/Shell activation and is safe for classic EXEs too.
            # Never use CREATE_NO_WINDOW here — it suppresses MSIX activation.
            subprocess.Popen(f'start "" "{target}"', shell=True)
            return f"Launched {Path(target).name}"
        except Exception as exc:
            return f"CLI error: {exc}"
    cmd = [target, name] + [str(v) for v in args.values()]
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=no_window,
        )
        return r.stdout or r.stderr or f"exit_code={r.returncode}"
    except Exception as exc:
        return f"CLI error: {exc}"


def _connect_app(exe_path: str, title_re: str = ""):
    """Connect to a running app window generically — no hardcoding.

    Strategy (in order):
    1. Enumerate all open Desktop windows and find the one whose title or
       owning process name contains the exe stem. Works for UWP apps whose
       real process (e.g. CalculatorApp.exe) differs from the launcher
       (calc.exe), as well as classic Win32 apps.
    2. Fall back to pywinauto connect(path=...) for classic apps.
    3. If an explicit title_re was supplied, try that too.
    """
    import psutil  # type: ignore
    from pywinauto import Desktop  # type: ignore
    from pywinauto.application import Application  # type: ignore

    exe_stem = Path(exe_path).stem.lower()  # e.g. "calc", "notepad"

    # Walk all visible top-level windows
    best_handle = None
    for w in Desktop(backend="uia").windows():
        try:
            title = (w.window_text() or "").lower()
            pid   = w.element_info.process_id
            try:
                proc_name = (psutil.Process(pid).name() or "").lower()
            except Exception:
                proc_name = ""
            if exe_stem in title or exe_stem in proc_name:
                best_handle = w.handle
                break
        except Exception:
            continue

    if best_handle:
        return Application(backend="uia").connect(handle=best_handle)

    # Fallback: classic Win32 connect by path
    if title_re:
        try:
            return Application(backend="uia").connect(title_re=title_re, timeout=3)
        except Exception:
            pass
    return Application(backend="uia").connect(path=exe_path, timeout=5)


def _read_display(app) -> str:
    """Try to read the current display value from the top window."""
    def _clean(t: str) -> str:
        # UWP Calculator prefixes values with accessibility text e.g. "Display is 8"
        import re
        t = re.sub(r"^(?:Display is|Result is|Expression is)\s*", "", t, flags=re.IGNORECASE)
        return t.strip()

    try:
        win = app.top_window()
        # Try common automation IDs for calculator/display controls
        for aid in ("CalculatorResults", "Display", "Result", "output", "NormalOutput"):
            try:
                t = win.child_window(auto_id=aid).window_text().strip()
                if t:
                    return _clean(t)
            except Exception:
                pass
        # Prefer a Text descendant that contains a digit (the numeric display)
        for ctrl in win.descendants(control_type="Text"):
            try:
                t = ctrl.window_text().strip()
                if t and any(c.isdigit() for c in t):
                    return _clean(t)
            except Exception:
                pass
        # Last resort: first non-empty Text descendant
        for ctrl in win.descendants(control_type="Text"):
            try:
                t = ctrl.window_text().strip()
                if t:
                    return _clean(t)
            except Exception:
                pass
    except Exception:
        pass
    return ""


def _execute_gui_bridge(execution: dict, name: str, args: dict) -> str:
    try:
        from pywinauto.application import Application  # type: ignore
    except ImportError:
        return "pywinauto is not installed on the bridge VM."
    exe_path    = _resolve_exe_path(execution.get("exe_path", ""))
    action_type = execution.get("action_type", "launch")
    if action_type in ("launch", "open_app") or not exe_path:
        try:
            # Use shell `start ""` so MSIX/UWP stubs get proper COM/Explorer
            # activation instead of a direct Popen (which exits immediately for
            # UWP stubs and is silently dropped in Session 0).
            subprocess.Popen(f'start "" "{exe_path}"', shell=True)
            # Poll for a visible window so callers get a meaningful error when
            # running in Session 0 (where the window never materialises).
            deadline = time.time() + 6
            window_found = False
            while time.time() < deadline:
                time.sleep(0.5)
                try:
                    _connect_app(exe_path)
                    window_found = True
                    break
                except Exception:
                    pass
            if not window_found:
                # Check whether the bridge itself is in a Session 0 (no desktop).
                try:
                    import ctypes
                    _sid = ctypes.windll.kernel32.WTSGetActiveConsoleSessionId()
                    _process_session = ctypes.c_ulong(0)
                    ctypes.windll.kernel32.ProcessIdToSessionId(
                        os.getpid(), ctypes.byref(_process_session))
                    if _process_session.value == 0:
                        return (
                            f"GUI launch error: bridge is running in Session 0 "
                            f"(SYSTEM service) — GUI windows cannot appear on the desktop. "
                            f"Restart gui_bridge.py as an interactive user: "
                            f"run install-bridge-service.ps1 on the VM."
                        )
                except Exception:
                    pass
                return (
                    f"Launched {Path(exe_path).name} but no window appeared within 6 s "
                    f"(possible Session 0 isolation or UWP activation failure)."
                )
            return f"Launched {Path(exe_path).name}"
        except Exception as exc:
            return f"GUI launch error: {exc}"
    if action_type == "close_app":
        try:
            app = _connect_app(exe_path)
            app.kill()
            return "App closed."
        except Exception as exc:
            return f"GUI close error: {exc}"
    if action_type == "menu_click":
        menu_path = args.get("menu_path") or execution.get("menu_path", "")
        try:
            app = _connect_app(exe_path)
            app.top_window().set_focus()
            app.top_window().menu_select(menu_path)
            return f"Menu '{menu_path}' selected."
        except Exception as exc:
            return f"GUI menu error: {exc}"
    if action_type == "button_click":
        button = args.get("button") or execution.get("button_name") or execution.get("button", "")
        try:
            app = _connect_app(exe_path)
            win = app.top_window()
            win.set_focus()
            # Try child_window by title first, fall back to bracket notation
            try:
                btn = win.child_window(title=button, control_type="Button")
                btn.click()
            except Exception:
                win[button].click()
            import time; time.sleep(0.3)
            display = _read_display(app)
            result = f"Clicked '{button}'."
            if display:
                result += f" Display shows: {display}"
            return result
        except Exception as exc:
            return f"GUI button error: {exc}"
    return f"GUI action '{action_type}' dispatched for '{Path(exe_path).name}'."


# ── COM CLSID candidate cache ──────────────────────────────────────────────
_COM_CLSID_CACHE: dict[str, list[str]] = {}  # dll_name_lower → [clsid, ...]
_MAX_COM_CANDIDATES = 25  # cap per-DLL to avoid iterating all 10 k+ CLSIDs


def _get_com_candidates(dll_lc: str) -> list[str]:
    """Return CLSIDs (strings) whose InprocServer32 DLL name matches dll_lc.

    Results are cached after the first call so subsequent tool invocations
    on the same DLL are instant.
    """
    if dll_lc in _COM_CLSID_CACHE:
        return _COM_CLSID_CACHE[dll_lc]
    import winreg
    found: list[str] = []
    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, "CLSID") as root:
            for i in range(winreg.QueryInfoKey(root)[0]):
                if len(found) >= _MAX_COM_CANDIDATES:
                    break
                try:
                    clsid = winreg.EnumKey(root, i)
                except OSError:
                    break
                try:
                    with winreg.OpenKey(root, f"{clsid}\\InprocServer32") as sk:
                        srv, _ = winreg.QueryValueEx(sk, "")
                    if dll_lc in srv.lower():
                        found.append(clsid)
                except OSError:
                    pass
    except OSError:
        pass
    _COM_CLSID_CACHE[dll_lc] = found
    return found


def _com_call_with_auto_dismiss(fn, arg_values: list, dialog_timeout: float = 20.0):
    """Call a COM method in a daemon thread.

    If the call doesn't return within 2 s (i.e. it opened a blocking modal
    dialog), use pywinauto to find the dialog window and click its primary
    accept button (OK / Select Folder / Open / Yes).  This lets the bridge
    serve users who have no direct access to the VM desktop.

    Returns the COM call's return value, or raises the exception it raised.
    """
    import threading

    result_box: list = [None]
    error_box:  list = [None]
    done_event = threading.Event()

    def _run():
        try:
            result_box[0] = fn(*arg_values) if arg_values else fn()
        except Exception as exc:
            error_box[0] = exc
        finally:
            done_event.set()

    t = threading.Thread(target=_run, daemon=True)
    t.start()

    # Fast path: method returned before a dialog could open
    if done_event.wait(timeout=2.0):
        if error_box[0] is not None:
            raise error_box[0]
        return result_box[0]

    # Slow path: assume a modal dialog is open — try to dismiss it
    logger.info("COM call is blocking — attempting auto-dismiss of modal dialog")
    _auto_dismiss_com_dialog(timeout=dialog_timeout)

    # Wait for the COM thread to finish after the dialog is gone
    done_event.wait(timeout=5.0)
    if error_box[0] is not None:
        raise error_box[0]
    return result_box[0]


def _auto_dismiss_com_dialog(timeout: float = 20.0) -> str | None:
    """Poll the desktop for a modal dialog and click its primary accept button.

    Uses win32gui directly (EnumWindows + EnumChildWindows) so it works
    regardless of pywinauto backend quirks.  Recognises common accept-button
    labels for BrowseForFolder, file-open pickers, message boxes, etc.
    Returns a description of what was clicked, or None if nothing was found.
    """
    import time as _time
    import win32gui
    import win32con

    _OK_LABELS = {"OK", "Select Folder", "Open", "Accept", "Yes", "Select", "Choose"}
    _clicked: list = []

    def _try_click_ok(dialog_hwnd: int, dialog_title: str) -> bool:
        """Return True if an accept button was found and clicked."""
        result: list = [False]

        def _enum_children(child_hwnd, _param):
            if result[0]:
                return  # already clicked
            try:
                cls  = win32gui.GetClassName(child_hwnd)
                text = win32gui.GetWindowText(child_hwnd)
                if cls == "Button" and text in _OK_LABELS:
                    if win32gui.IsWindowVisible(child_hwnd) and win32gui.IsWindowEnabled(child_hwnd):
                        # BM_CLICK is the most reliable way to press a button
                        # without needing to move the mouse cursor.
                        win32gui.SendMessage(child_hwnd, win32con.BM_CLICK, 0, 0)
                        logger.info(
                            "Auto-dismissed dialog '%s' via button '%s'",
                            dialog_title, text,
                        )
                        _clicked.append(f"dialog '{dialog_title}' dismissed via '{text}'")
                        result[0] = True
            except Exception:
                pass

        try:
            win32gui.EnumChildWindows(dialog_hwnd, _enum_children, None)
        except Exception:
            pass
        return result[0]

    deadline = _time.monotonic() + timeout

    while _time.monotonic() < deadline:
        _time.sleep(0.3)

        top_windows: list[tuple[int, str]] = []

        def _enum_top(hwnd, _param):
            try:
                if win32gui.IsWindowVisible(hwnd):
                    top_windows.append((hwnd, win32gui.GetWindowText(hwnd)))
            except Exception:
                pass

        try:
            win32gui.EnumWindows(_enum_top, None)
        except Exception:
            continue

        for hwnd, title in top_windows:
            if _try_click_ok(hwnd, title):
                return _clicked[-1]

    logger.warning("Auto-dismiss: no clickable dialog found within %.0f s", timeout)
    return None


def _execute_com_bridge(inv: dict, execution: dict, args: dict) -> str:
    """Invoke a COM method on an object hosted in the target DLL.

    Accepts both method="com_invoke" (legacy) and method="com_dispatch" (schema).
    Resolution priority:
      1. If execution has a 'clsid', dispatch it directly.
      2. Otherwise scan the registry for CoClasses whose InprocServer32 matches
         the dll_path, then try each one.
    """
    import win32com.client  # pywin32 – installed alongside pythoncom

    # Schema uses 'function_name'; legacy invocables use 'member'.
    member = (
        execution.get("member")
        or execution.get("function_name")
        or inv.get("name", "")
    )

    if not member:
        return "COM invoke error: no member/method specified in execution config"

    # Shell special-folder name → SHSpecialFolderID constant.
    # IShellDispatch.NameSpace (and similar) accept an integer VARIANT for
    # well-known folders; the model often supplies a friendly name instead.
    _SHELL_FOLDER_CONSTANTS: dict[str, int] = {
        "desktop": 0, "internet": 1, "programs": 2, "controls": 3,
        "printers": 4, "personal": 5, "my documents": 5, "favorites": 6,
        "startup": 7, "recent": 8, "sendto": 9, "bitbucket": 10,
        "recycle bin": 10, "startmenu": 11, "start menu": 11,
        "mydocuments": 5, "mymusic": 13, "my music": 13,
        "myvideo": 14, "my video": 14, "desktopdirectory": 16,
        "drives": 17, "my computer": 17, "mycomputer": 17, "network": 18,
        "nethood": 19, "fonts": 20, "templates": 21, "appdata": 26,
        "printhood": 27, "localappdata": 28, "windows": 36, "system": 37,
        "programfiles": 38, "program files": 38, "mypictures": 39,
        "my pictures": 39, "profile": 40, "systemx86": 41,
        "programfilesx86": 42, "commonfiles": 43, "commonprograms": 46,
        "commonstartmenu": 47, "commondesktop": 48, "commonappdata": 35,
        "commontemplates": 45, "commondocuments": 46, "downloads": 374,
    }

    def _coerce_com_arg(v: object) -> object:
        """Coerce a model-supplied string to int/float for COM VARIANT args."""
        if not isinstance(v, str):
            return v
        lv = v.strip().lower()
        if lv in _SHELL_FOLDER_CONSTANTS:
            return _SHELL_FOLDER_CONSTANTS[lv]
        try:
            return int(v)
        except (ValueError, TypeError):
            pass
        try:
            return float(v)
        except (ValueError, TypeError):
            pass
        return v

    arg_values  = [_coerce_com_arg(v) for v in args.values()]
    errors_seen: list[str] = []

    # Build candidate list in priority order:
    #   1. direct clsid (explicitly stored, most precise)
    #   2. coclass_candidates from TLB analysis (stored at analyze time)
    #   3. lazy TLB scan from the DLL right now (catches stale invocables that
    #      pre-date the coclass_candidates fix — no re-upload required)
    #   4. registry scan by DLL name (last resort)
    direct_clsid   = execution.get("clsid") or inv.get("clsid")
    tlb_candidates = execution.get("coclass_candidates") or []
    dll_path = _resolve_exe_path(execution.get("dll_path", ""))
    dll_lc   = Path(dll_path).name.lower() if dll_path else ""

    # Lazy TLB scan: if no stored candidates, load the DLL's type library now
    lazy_tlb_candidates: list = []
    if not tlb_candidates and dll_path and Path(dll_path).exists():
        try:
            import pythoncom as _pc
            _tlb = _pc.LoadTypeLib(dll_path)
            for _i in range(_tlb.GetTypeInfoCount()):
                try:
                    _ti   = _tlb.GetTypeInfo(_i)
                    _attr = _ti.GetTypeAttr()
                    if _attr.typekind == 5:  # TKIND_COCLASS
                        lazy_tlb_candidates.append(str(_attr.iid))
                except Exception:
                    pass
            logger.info("COM lazy TLB scan of '%s': %d CoClass candidates",
                        dll_lc, len(lazy_tlb_candidates))
        except Exception as _e:
            logger.debug("COM lazy TLB scan failed for '%s': %s", dll_lc, _e)

    # Deduplicated ordered candidate list
    seen: set = set()
    ordered_candidates: list = []
    for c in ([direct_clsid] if direct_clsid else []) + tlb_candidates + lazy_tlb_candidates:
        if c and c not in seen:
            seen.add(c)
            ordered_candidates.append(c)

    source = "stored TLB" if tlb_candidates else ("lazy TLB" if lazy_tlb_candidates else "registry")
    # Only fall back to registry scan if TLB (stored or lazy) gave us nothing
    if not ordered_candidates and dll_lc:
        ordered_candidates = _get_com_candidates(dll_lc)
        source = "registry"

    logger.info("COM dispatch '%s' on '%s': %d candidates from %s",
                member, dll_lc, len(ordered_candidates), source)

    if not ordered_candidates:
        return (
            f"COM invoke error: no CoClass candidates for '{dll_lc}' "
            f"and no CLSID/TLB data in execution config. Cannot dispatch '{member}'."
        )

    method_found_on: str | None = None  # tracks first CLSID where member exists
    call_errors: list[str] = []

    for clsid in ordered_candidates:
        try:
            obj = win32com.client.Dispatch(clsid)
        except Exception as ex:
            errors_seen.append(f"{clsid}: Dispatch failed: {ex}")
            continue

        fn = getattr(obj, member, None)
        if fn is None:
            continue

        # Member exists on this object — record it even if the call fails
        if method_found_on is None:
            method_found_on = clsid

        try:
            result = _com_call_with_auto_dismiss(fn, arg_values)
            return f"Returned: {result}"
        except Exception as call_ex:
            call_str = str(call_ex)
            call_errors.append(f"{clsid}: {call_str}")
            # "Invalid number of parameters" — try as a property (no-call)
            if "parameter" in call_str.lower() or "argument" in call_str.lower():
                try:
                    prop = getattr(obj, member)
                    if not callable(prop):
                        return f"Returned: {prop}"
                except Exception:
                    pass

    if method_found_on:
        # Method WAS found but every call attempt failed — likely missing required args
        _raw_hint = (
            execution.get("parameters")
            or inv.get("parameters")
            or []
        )
        # Normalise: list of dicts → list of names; list of strings → keep as-is
        if _raw_hint and isinstance(_raw_hint[0], dict):
            param_hint = [p.get("name", str(p)) for p in _raw_hint]
        else:
            param_hint = list(_raw_hint)
        hint = f" Expected parameters: {param_hint}" if param_hint else \
               " Provide required arguments (e.g. a folder path for Open)."
        return (
            f"COM method '{member}' found on {method_found_on} but call failed. "
            f"Errors: {'; '.join(call_errors[:3])}.{hint}"
        )

    return (
        f"COM invoke error: none of {len(ordered_candidates)} CoClass candidate(s) "
        f"for '{dll_lc}' expose '{member}'. "
        f"Errors: {'; '.join(errors_seen[:3])}"
    )


def _execute_script_bridge(execution: dict, name: str, args: dict) -> str:
    """Execute a script-based invocable (Python, PowerShell, Node, Ruby, PHP, etc.).

    Builds the appropriate interpreter command from the execution metadata
    produced by schema.Invocable._get_execution_metadata() and runs it as a
    subprocess.  Supports every method the schema defines for JIT languages.
    """
    method      = execution.get("method", "")
    script_path = (
        execution.get("script_path")
        or execution.get("module_path")
        or execution.get("example", "")  # fallback: parse from example
    )
    func_name   = execution.get("function_name", "") or execution.get("method_name", "")
    arg_values  = list(args.values())

    no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    # If the stored path is a Linux /tmp path that doesn't exist on this Windows
    # host, materialise the embedded script_content into a local temp file.
    _tmp_path: "str | None" = None
    script_content = execution.get("script_content")
    if script_path and not Path(script_path).exists() and script_content:
        suffix = Path(script_path).suffix or ".tmp"
        fd, _tmp_path = tempfile.mkstemp(suffix=suffix)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as _f:
                _f.write(script_content)
            script_path = _tmp_path
        except OSError:
            try:
                os.close(fd)
            except OSError:
                pass
            _tmp_path = None

    try:
        if method == "python_subprocess":
            # python -c "import importlib.util; spec=...; m.func(args)"
            arg_repr = ", ".join(repr(v) for v in arg_values)
            code = (
                f"import importlib.util, sys; "
                f"spec=importlib.util.spec_from_file_location('m', r'{script_path}'); "
                f"m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); "
                f"print(m.{func_name}({arg_repr}))"
            ) if func_name else (
                f"exec(open(r'{script_path}').read())"
            )
            cmd = ["python", "-c", code]

        elif method in ("node", "ts-node"):
            interp = "ts-node" if method == "ts-node" else "node"
            if func_name:
                arg_repr = ", ".join(repr(v) for v in arg_values)
                code = f"const m=require('{script_path}'); console.log(m.{func_name}({arg_repr}))"
                cmd = [interp, "-e", code]
            else:
                cmd = [interp, script_path]

        elif method == "powershell":
            if func_name:
                arg_str = " ".join(f'"{v}"' for v in arg_values)
                cmd = [
                    "powershell", "-NoProfile", "-NonInteractive",
                    "-Command",
                    f". '{script_path}'; {func_name} {arg_str}",
                ]
            else:
                cmd = ["powershell", "-NoProfile", "-NonInteractive", "-File", script_path]

        elif method == "cmd_call":
            label = execution.get("label", func_name)
            arg_str = " ".join(str(v) for v in arg_values)
            cmd = ["cmd", "/c", f'call "{script_path}" :{label} {arg_str}'.strip()]

        elif method in ("bash",):
            if func_name:
                arg_str = " ".join(f'"{v}"' for v in arg_values)
                cmd = ["bash", "-c", f'source "{script_path}"; {func_name} {arg_str}']
            else:
                cmd = ["bash", script_path] + [str(v) for v in arg_values]

        elif method == "ruby":
            if func_name:
                arg_repr = ", ".join(repr(v) for v in arg_values)
                cmd = ["ruby", "-r", script_path, "-e", f"puts {func_name}({arg_repr})"]
            else:
                cmd = ["ruby", script_path] + [str(v) for v in arg_values]

        elif method == "php":
            if func_name:
                arg_repr = ", ".join(f'"{v}"' for v in arg_values)
                cmd = ["php", "-r", f"require '{script_path}'; echo {func_name}({arg_repr});"]
            else:
                cmd = ["php", script_path] + [str(v) for v in arg_values]

        elif method == "cscript":
            cmd = ["cscript", "//nologo", script_path] + [str(v) for v in arg_values]

        elif method == "cmd":
            cmd = ["cmd", "/c", script_path] + [str(v) for v in arg_values]

        else:
            return f"Script error: unsupported method '{method}'"

        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            creationflags=no_window,
        )
        return r.stdout or r.stderr or f"exit_code={r.returncode}"

    except FileNotFoundError as exc:
        return f"Script error: interpreter not found — {exc}"
    except subprocess.TimeoutExpired:
        return "Script error: timed out after 30 s"
    except Exception as exc:
        return f"Script error: {exc}"
    finally:
        if _tmp_path:
            try:
                os.unlink(_tmp_path)
            except OSError:
                pass


def _execute_dotnet_bridge(execution: dict, name: str, args: dict) -> str:
    """Invoke a .NET method on this Windows VM via PowerShell reflection."""
    assembly  = execution.get("assembly_path", "")
    type_name = execution.get("type_name", "")
    func      = execution.get("function_name", name)
    is_static = execution.get("is_static", False)

    if not assembly:
        return ".NET error: no assembly_path in execution config"

    # Build argument list for PowerShell
    ps_args = ", ".join(f'"{v}"' for v in args.values()) if args else ""

    if is_static:
        invoke_expr = f'[{type_name}]::{func}({ps_args})'
        ps_script   = (
            f"$asm = [System.Reflection.Assembly]::LoadFile('{assembly}'); "
            f"$result = {invoke_expr}; "
            f"Write-Output $result"
        )
    else:
        ps_script = (
            f"$asm = [System.Reflection.Assembly]::LoadFile('{assembly}'); "
            f"$obj = [Activator]::CreateInstance($asm.GetType('{type_name}')); "
            f"$result = $obj.{func}({ps_args}); "
            f"Write-Output $result"
        )

    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
            capture_output=True,
            text=True,
            timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return r.stdout.strip() or r.stderr.strip() or f"exit_code={r.returncode}"
    except subprocess.TimeoutExpired:
        return ".NET error: timed out after 30 s"
    except Exception as exc:
        return f".NET error: {exc}"


def _execute_http_bridge(execution: dict, name: str, args: dict) -> str:
    """Dispatch HTTP-based invocables: OpenAPI, JSON-RPC, SOAP.

    Requires a 'base_url' key in the execution dict (populated at chat time
    from the user-provided server URL), or falls back to a localhost default.
    """
    import json as _json
    method = execution.get("method", "")

    try:
        import httpx as _httpx
    except ImportError:
        return "HTTP error: httpx not installed on the bridge VM"

    base_url = execution.get("base_url", "http://localhost")

    try:
        if method == "http_request":
            http_method = execution.get("http_method", "get").upper()
            path        = execution.get("path", "/")
            url         = base_url.rstrip("/") + "/" + path.lstrip("/")
            resp = _httpx.request(http_method, url, json=args or None, timeout=15)
            return resp.text or f"HTTP {resp.status_code}"

        elif method == "jsonrpc":
            url     = base_url.rstrip("/")
            payload = {"jsonrpc": "2.0", "method": name, "params": list(args.values()), "id": 1}
            resp    = _httpx.post(url, json=payload, timeout=15)
            data    = resp.json()
            if "error" in data:
                return f"JSON-RPC error: {data['error']}"
            return _json.dumps(data.get("result"), indent=2)

        elif method == "soap":
            url    = base_url.rstrip("/")
            action = execution.get("action", name)
            params_xml = "".join(
                f"<{k}>{v}</{k}>" for k, v in args.items()
            )
            body = (
                '<?xml version="1.0"?>'
                '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"'
                ' xmlns:tns="urn:service">'
                f'<soapenv:Body><tns:{action}>{params_xml}</tns:{action}></soapenv:Body>'
                '</soapenv:Envelope>'
            )
            resp = _httpx.post(
                url,
                content=body.encode(),
                headers={
                    "Content-Type": "text/xml; charset=utf-8",
                    "SOAPAction": f'"{action}"',
                },
                timeout=15,
            )
            return resp.text

        else:
            return f"HTTP error: unsupported method '{method}'"

    except Exception as exc:
        return f"HTTP error: {exc}"


def _execute_sql_bridge(execution: dict, name: str, args: dict) -> str:
    """Execute a SQL object (procedure, function, view, table) via sqlite3 subprocess.

    For source .sql files the connection_required flag is always True, but
    MCP Factory does not currently manage a live DB connection string.
    This dispatcher handles SQLite (.db / .sqlite) targets directly; for
    SQL Server / PostgreSQL it returns the parameterized statement so the
    user can run it manually.
    """
    source_file = execution.get("source_file", "")
    statement   = execution.get("statement", "")
    obj_type    = execution.get("object_type", "")

    # If source file is a SQLite DB, run directly
    db_exts = {".db", ".sqlite", ".sqlite3"}
    if source_file and Path(source_file).suffix.lower() in db_exts:
        try:
            r = subprocess.run(
                ["sqlite3", source_file, statement],
                capture_output=True, text=True, timeout=15,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return r.stdout or r.stderr or f"exit_code={r.returncode}"
        except FileNotFoundError:
            pass  # sqlite3 CLI not in PATH, fall through to informational
        except Exception as exc:
            return f"SQL error: {exc}"

    # Non-SQLite: return the parameterized statement for the user to execute
    param_hints = "; ".join(f"{k}={v}" for k, v in args.items())
    note = f"  -- params: {param_hints}" if param_hints else ""
    return (
        f"-- {obj_type.upper()} '{name}' discovered in {Path(source_file).name if source_file else 'unknown'}\n"
        f"-- Execute against your target database:\n"
        f"{statement}{note}"
    )


def _execute_dll_bridge(inv: dict, execution: dict, args: dict) -> str:
    """Call a native DLL function via ctypes on this Windows VM."""
    import ctypes

    _RESTYPE = {
        "void": None,
        "bool": ctypes.c_bool,
        "int": ctypes.c_int,
        "unsigned": ctypes.c_uint,
        "unsigned int": ctypes.c_uint,
        "long": ctypes.c_long,
        "unsigned long": ctypes.c_ulong,
        "size_t": ctypes.c_size_t,
        "float": ctypes.c_float,
        "double": ctypes.c_double,
        "char*": ctypes.c_char_p,
        "const char*": ctypes.c_char_p,
        "wchar_t*": ctypes.c_wchar_p,
        "const wchar_t*": ctypes.c_wchar_p,
        # Win32 / Ghidra-decompiler type names
        "dword": ctypes.c_ulong,
        "word": ctypes.c_ushort,
        "byte": ctypes.c_ubyte,
        "uint32_t": ctypes.c_uint,
        "int32_t": ctypes.c_int,
        "undefined4": ctypes.c_int,
        "undefined": ctypes.c_int,
        "undefined8": ctypes.c_longlong,
        "hresult": ctypes.c_long,
        "handle": ctypes.c_void_p,
        "lpvoid": ctypes.c_void_p,
    }
    _ARGTYPE = {
        "int": ctypes.c_int,
        "unsigned": ctypes.c_uint,
        "unsigned int": ctypes.c_uint,
        "long": ctypes.c_long,
        "unsigned long": ctypes.c_ulong,
        "size_t": ctypes.c_size_t,
        "float": ctypes.c_float,
        "double": ctypes.c_double,
        "bool": ctypes.c_bool,
        "string": ctypes.c_char_p,
        "str": ctypes.c_char_p,
        "char*": ctypes.c_char_p,
        "const char*": ctypes.c_char_p,
        "wchar_t*": ctypes.c_wchar_p,
        "const wchar_t*": ctypes.c_wchar_p,
        # Win32 / Ghidra-decompiler type names
        "dword": ctypes.c_ulong,
        "word": ctypes.c_ushort,
        "byte": ctypes.c_ubyte,
        "uint32_t": ctypes.c_uint,
        "int32_t": ctypes.c_int,
        "undefined4": ctypes.c_int,
    }

    dll_path  = _resolve_exe_path(execution.get("dll_path", ""))
    func_name = execution.get("function_name", "")
    if not dll_path or not func_name:
        return "DLL call error: missing dll_path or function_name in execution config"

    ret_str = (
        inv.get("return_type")
        or (inv.get("signature") or {}).get("return_type", "unknown")
        or "unknown"
    ).strip().lower()
    restype = _RESTYPE.get(ret_str, ctypes.c_int)

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

    # Wide-string output buffer functions: only allocate a buffer when the
    # function name ends with W AND a path/dir/name suffix.  Avoids wrongly
    # passing buffer args to zero-arg functions like GetCurrentProcessId.
    _BUFFER_SUFFIXES = (
        "directoryw", "pathw", "filenamew", "namew", "pathnamew",
        "longpathw", "shortpathw", "folderw", "volumew",
    )

    lib = None
    try:
        # Choose calling convention: cdecl functions must use CDLL; stdcall uses WinDLL.
        # Using the wrong convention on x86 32-bit corrupts the stack and returns garbage.
        _cc = execution.get("calling_convention", "").lower()
        if _cc in ("cdecl", "__cdecl", "c"):
            lib = ctypes.CDLL(dll_path)
        else:
            lib = ctypes.WinDLL(dll_path)
        fn  = getattr(lib, func_name)
        fn.restype = restype

        c_args = []
        buf    = None  # legacy: wchar buffer for W-suffix no-param functions
        out_str_bufs:   list[tuple[str, Any]] = []  # (name, create_string_buffer)
        out_wchar_bufs: list[tuple[str, Any]] = []  # (name, create_unicode_buffer)
        out_scalars:    list[tuple[str, Any]] = []  # (name, ctypes scalar)

        # Scalar ctypes used to allocate DWORD* / int* output parameters
        _SCALAR_PTR_MAP = {
            "dword": ctypes.c_ulong,   "unsigned long": ctypes.c_ulong,
            "uint":  ctypes.c_uint,    "uint32_t":      ctypes.c_uint,
            "int":   ctypes.c_int,     "int32_t":       ctypes.c_int,
            "long":  ctypes.c_long,    "ulong":         ctypes.c_ulong,
            "word":  ctypes.c_ushort,  "byte":          ctypes.c_ubyte,
            "bool":  ctypes.c_bool,
        }
        # Type names that represent a buffer-length argument following a char* out-buf
        _SIZE_TYPES = {
            "dword", "int", "uint", "unsigned int", "size_t",
            "long", "unsigned long", "uint32_t",
        }

        if params:
            skip_indices: set[int] = set()
            for idx, p in enumerate(params):
                if idx in skip_indices:
                    continue
                pname     = p.get("name", f"param_{idx}")
                ptype_raw = p.get("type", "string")
                ptype_lc  = ptype_raw.lower()
                ptype_base = ptype_lc.replace("const ", "").strip().rstrip(" *")
                val       = args.get(pname)
                direction = p.get("direction", "")

                is_ptr        = "*" in ptype_lc
                is_const_ptr  = is_ptr and "const " in ptype_lc
                is_out_ptr    = is_ptr and not is_const_ptr and (direction == "out" or val is None)
                is_char_out   = is_out_ptr and "char" in ptype_base and "wchar" not in ptype_base
                is_wchar_out  = is_out_ptr and "wchar" in ptype_base
                is_scalar_out = is_out_ptr and not is_char_out and not is_wchar_out

                if is_char_out:
                    # ANSI char* output buffer — auto-detect the following size param
                    buf_size = 4096
                    if idx + 1 < len(params):
                        np      = params[idx + 1]
                        np_name = np.get("name", "")
                        np_type = np.get("type", "").lower().replace("const ", "").strip(" *")
                        np_val  = args.get(np_name)
                        if np_val is not None:
                            try:
                                buf_size = max(64, int(np_val))
                            except (ValueError, TypeError):
                                pass
                        elif np_type in _SIZE_TYPES:
                            # Auto-supply the buffer length; skip that param in the loop
                            skip_indices.add(idx + 1)
                    sbuf = ctypes.create_string_buffer(buf_size)
                    c_args.append(sbuf)
                    if (idx + 1) in skip_indices:
                        c_args.append(ctypes.c_uint(buf_size))
                    out_str_bufs.append((pname, sbuf))

                elif is_wchar_out:
                    # Wide wchar_t* output buffer
                    buf_size = 4096
                    if idx + 1 < len(params):
                        np      = params[idx + 1]
                        np_name = np.get("name", "")
                        np_type = np.get("type", "").lower().replace("const ", "").strip(" *")
                        np_val  = args.get(np_name)
                        if np_val is not None:
                            try:
                                buf_size = max(64, int(np_val))
                            except (ValueError, TypeError):
                                pass
                        elif np_type in _SIZE_TYPES:
                            skip_indices.add(idx + 1)
                    wbuf = ctypes.create_unicode_buffer(buf_size)
                    c_args.append(wbuf)
                    if (idx + 1) in skip_indices:
                        c_args.append(ctypes.c_uint(buf_size))
                    out_wchar_bufs.append((pname, wbuf))

                elif is_scalar_out:
                    # DWORD*, int*, LONG* etc. — allocate a typed scalar and pass byref
                    s_ctype = _SCALAR_PTR_MAP.get(ptype_base, ctypes.c_ulong)
                    scalar  = s_ctype(0)
                    c_args.append(ctypes.byref(scalar))
                    out_scalars.append((pname, scalar))

                elif val is not None:
                    # Normal input parameter
                    atype = _ARGTYPE.get(ptype_base, ctypes.c_char_p)
                    if atype == ctypes.c_char_p:
                        c_args.append(ctypes.c_char_p(str(val).encode()))
                    elif atype == ctypes.c_wchar_p:
                        c_args.append(ctypes.c_wchar_p(str(val)))
                    else:
                        try:
                            c_args.append(atype(int(val)))
                        except (ValueError, TypeError):
                            c_args.append(atype(0))
                # else: no value, not an output pointer — optional / unknown param, skip

        elif not params:
            # No declared params — allocate a wchar output buffer only for
            # well-known wide-string output functions (name ends with W + keyword).
            fn_lower = func_name.lower()
            if any(fn_lower.endswith(sfx) for sfx in _BUFFER_SUFFIXES):
                buf = ctypes.create_unicode_buffer(32767)
                c_args = [buf, ctypes.c_uint(32767)]
                fn.restype = ctypes.c_uint

        result = fn(*c_args)

        # Collect output buffer / out-param values into the response.
        output_parts: list[str] = []
        for _, sbuf in out_str_bufs:
            txt = sbuf.value
            if isinstance(txt, bytes):
                txt = txt.decode(errors="replace")
            if txt:
                output_parts.append(txt)
        for _, wbuf in out_wchar_bufs:
            txt = wbuf.value
            if txt:
                output_parts.append(txt)
        for sc_name, scalar in out_scalars:
            output_parts.append(f"{sc_name}={scalar.value}")
        if buf is not None:
            output_parts.append(str(buf.value))

        if restype == ctypes.c_char_p and isinstance(result, bytes):
            return f"Returned: {result.decode(errors='replace')}"
        if restype == ctypes.c_wchar_p and isinstance(result, str):
            return f"Returned: {result}"
        if output_parts:
            return f"Returned: {result}\n" + "\n".join(output_parts)
        return f"Returned: {result}"
    except Exception as exc:
        return f"DLL call error: {exc}"
    finally:
        # Unload the DLL so the file is not held locked between execute calls.
        # Without this, a subsequent /analyze call that tries to overwrite the
        # same file in C:\mcp-factory\uploads\ would get ERROR_SHARING_VIOLATION
        # and the bridge would return 400, preventing Ghidra from re-analyzing.
        if lib is not None:
            try:
                ctypes.windll.kernel32.FreeLibrary(lib._handle)
            except Exception:
                pass


@app.post("/execute")
async def execute(
    body: ExecuteRequest,
    x_bridge_key: str = Header(default=""),
):
    """Execute a single tool call on this Windows VM and return the result."""
    _check_auth(x_bridge_key)
    inv       = body.invocable
    args      = body.args
    name      = inv.get("name", "")
    execution = inv.get("execution") or inv.get("mcp", {}).get("execution", {})
    method    = execution.get("method", "")
    logger.info("Execute: tool=%s method=%s args=%s execution=%s", name, method, args, execution)
    params = list((inv.get("parameters") or []))
    logger.info("Execute: declared params=%s", [p.get("name") for p in params])
    loop = asyncio.get_running_loop()
    if method == "gui_action":
        result = await loop.run_in_executor(None, _execute_gui_bridge, execution, name, args)
    elif method == "dll_import":
        result = await loop.run_in_executor(None, _execute_dll_bridge, inv, execution, args)
    elif method in ("com_invoke", "com_dispatch"):
        # COM calls may block indefinitely when they open a modal dialog
        # (e.g. BrowseForFolder).  Wrap in a Future with a 60 s timeout so
        # the HTTP handler always returns rather than hanging the event loop.
        try:
            result = await asyncio.wait_for(
                loop.run_in_executor(None, _execute_com_bridge, inv, execution, args),
                timeout=60,
            )
        except asyncio.TimeoutError:
            result = (
                f"COM call '{name}' timed out after 60 s — the method likely opened "
                "a blocking modal dialog (e.g. a folder/file picker). "
                "The dialog may still be visible on the desktop."
            )
    elif method == "dotnet_reflection":
        result = await loop.run_in_executor(None, _execute_dotnet_bridge, execution, name, args)
    elif method in ("python_subprocess", "node", "ts-node", "powershell",
                    "cmd_call", "bash", "ruby", "php", "cscript", "cmd"):
        result = await loop.run_in_executor(None, _execute_script_bridge, execution, name, args)
    elif method in ("http_request", "jsonrpc", "soap"):
        result = await loop.run_in_executor(None, _execute_http_bridge, execution, name, args)
    elif method == "sql_exec":
        result = await loop.run_in_executor(None, _execute_sql_bridge, execution, name, args)
    elif method == "rpc_call":
        # RPC invocables require a running RPC server — execution not supported on bridge.
        # Discovery-only: the model can describe the interface but cannot invoke remotely.
        iface = execution.get("interface_uuid") or execution.get("endpoint") or name
        result = f"RPC interface '{iface}' discovered. Remote invocation requires a live RPC endpoint; describe the interface parameters instead."
    elif method in ("corba_iiop",):
        # CORBA requires an ORB (omniORB, JacORB) — not available on this Windows bridge.
        iface = execution.get("interface") or name
        result = f"CORBA interface '{iface}' discovered. Invocation requires a CORBA ORB over IIOP; describe the interface operations instead."
    elif method in ("jndi_lookup",):
        # JNDI requires a running Java naming provider — not available on this Windows bridge.
        lookup = execution.get("lookup_name") or name
        result = f"JNDI binding '{lookup}' discovered. Invocation requires a running JNDI provider (LDAP/RMI/IIOP); describe the binding instead."
    else:  # cli / subprocess / anything else
        result = await loop.run_in_executor(None, _execute_cli_bridge, execution, name, args)
    return JSONResponse({"result": result})


@app.get("/health")
async def health():
    return {"status": "ok", "platform": "windows"}


@app.get("/debug_session")
async def debug_session(x_bridge_key: str = Header(default="")):
    """Return the bridge process's Windows session info and visible desktop windows.

    GET http://<vm>:8090/debug_session  (with X-Bridge-Key header)

    Use this to confirm:
      - session_id == 1 (interactive desktop, not Session 0 SYSTEM)
      - visible windows include expected apps
    """
    _check_auth(x_bridge_key)
    import ctypes

    info: dict = {}

    # Session ID of the bridge process itself
    try:
        sid = ctypes.c_ulong(0)
        ctypes.windll.kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(sid))
        info["bridge_session_id"] = sid.value
        info["is_session_0"] = sid.value == 0
    except Exception as exc:
        info["bridge_session_id"] = f"error: {exc}"

    # Active console session (the one attached to the physical/RDP display)
    try:
        info["active_console_session"] = ctypes.windll.kernel32.WTSGetActiveConsoleSessionId()
    except Exception as exc:
        info["active_console_session"] = f"error: {exc}"

    # Enumerate visible top-level windows in this session
    try:
        import win32gui  # type: ignore
        windows = []
        def _cb(hwnd, _):
            try:
                if win32gui.IsWindowVisible(hwnd):
                    t = win32gui.GetWindowText(hwnd)
                    if t:
                        windows.append(t)
            except Exception:
                pass
        win32gui.EnumWindows(_cb, None)
        info["visible_windows"] = windows[:40]
        info["visible_window_count"] = len(windows)
    except Exception as exc:
        info["visible_windows"] = f"win32gui error: {exc}"

    # Quick check: is CalculatorApp.exe (the real calc process) running?
    try:
        import psutil  # type: ignore
        calc_procs = [p.name() for p in psutil.process_iter(["name"])
                      if "calc" in (p.info["name"] or "").lower()]
        info["calc_processes"] = calc_procs
    except Exception as exc:
        info["calc_processes"] = f"psutil error: {exc}"

    return JSONResponse(info)


@app.get("/debug_uia")
async def debug_uia(
    stem: str = "calc",
    x_bridge_key: str = Header(default=""),
):
    """Dump UIA Text/Edit descendants of the first window matching 'stem'."""
    _check_auth(x_bridge_key)
    import asyncio
    loop = asyncio.get_running_loop()

    def _dump():
        import psutil
        from pywinauto import Desktop
        from pywinauto.application import Application
        result = {"windows": [], "descendants": []}
        for w in Desktop(backend="uia").windows():
            try:
                title = (w.window_text() or "").lower()
                pid   = w.element_info.process_id
                pname = (psutil.Process(pid).name() or "").lower()
                if stem.lower() in title or stem.lower() in pname:
                    result["windows"].append({
                        "title": w.window_text(),
                        "pid": pid,
                        "process": psutil.Process(pid).name(),
                        "handle": w.handle,
                    })
                    app = Application(backend="uia").connect(handle=w.handle)
                    win = app.top_window()
                    for ctrl in win.descendants(control_type="Text"):
                        try:
                            result["descendants"].append({
                                "type": "Text",
                                "auto_id": ctrl.element_info.automation_id,
                                "text": ctrl.window_text(),
                            })
                        except Exception:
                            pass
                    for ctrl in win.descendants(control_type="Edit"):
                        try:
                            result["descendants"].append({
                                "type": "Edit",
                                "auto_id": ctrl.element_info.automation_id,
                                "text": ctrl.window_text(),
                            })
                        except Exception:
                            pass
                    break
            except Exception:
                continue
        return result

    data = await loop.run_in_executor(None, _dump)
    return JSONResponse(data)


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.getenv("BRIDGE_PORT", "8090"))
    logger.info("Starting GUI bridge on 0.0.0.0:%d", port)
    uvicorn.run(app, host="0.0.0.0", port=port)
