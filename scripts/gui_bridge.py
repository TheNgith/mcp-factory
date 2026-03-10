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
import time
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
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
    types: list[str] = ["gui", "com", "cli", "registry"]    # base64-encoded binary content (optional): lets the bridge analyze an
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


def _inv_to_dict(inv: Any) -> dict:
    """Convert an Invocable dataclass (or dict) to a plain dict.

    Uses to_dict() when available so parameters is always a list
    (not the raw Optional[str] dataclass field) and all fields are
    in the canonical pipeline format.
    """
    if isinstance(inv, dict):
        return inv
    if hasattr(inv, "to_dict"):
        return inv.to_dict()
    return {k: v for k, v in vars(inv).items() if not k.startswith("_")}


# How long (seconds) to allow the GUI analyzer before giving up.
# pywinauto can hang for 90+ s on UWP stubs (e.g. calc.exe on Server 2022).
# Raised to 60 s to give Win11 UWP apps enough time to expose their UIA tree.
GUI_ANALYZE_TIMEOUT = 60


def _run_analysis_sync(target: Path, requested: set, hints: str) -> dict:
    """Synchronous analysis worker — runs in a thread pool executor.

    Keeping all blocking I/O (pywinauto, COM, subprocess) in a plain function
    avoids stalling the uvicorn async event loop.
    """
    invocables: list[dict] = []
    errors: dict[str, str] = {}

    # ── GUI ──────────────────────────────────────────────────────────────────
    if "gui" in requested and target.suffix.lower() == ".exe":
        try:
            analyze_gui_fn = _import_gui()
            # Enforce a hard time-box: pywinauto retries can add up to 90+ s on
            # headless Server 2022 where UWP windows never appear.
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as _gui_pool:
                _gui_future = _gui_pool.submit(analyze_gui_fn, target, 20)
                try:
                    gui_results = _gui_future.result(timeout=GUI_ANALYZE_TIMEOUT)
                except concurrent.futures.TimeoutError:
                    logger.warning(
                        "GUI analysis timed out after %ds for %s — skipping",
                        GUI_ANALYZE_TIMEOUT, target.name,
                    )
                    errors["gui"] = f"timed out after {GUI_ANALYZE_TIMEOUT}s"
                    gui_results = []
            invocables.extend(_inv_to_dict(i) for i in gui_results)
            logger.info("GUI: %d invocables from %s", len(gui_results), target.name)
        except Exception as exc:
            logger.warning("GUI analysis failed: %s", exc)
            errors["gui"] = str(exc)

    # ── COM / Type Library ───────────────────────────────────────────────────
    if "com" in requested:
        try:
            scan_type_library = _import_tlb()
            tlb_results = scan_type_library(target)
            # tlb_analyzer returns raw dicts — convert to Invocable-style dicts
            for entry in tlb_results:
                for method in entry.get("methods", []):
                    invocables.append({
                        "name":        method.get("name", "unknown"),
                        "source_type": "com",
                        "signature":   method.get("signature", method.get("name", "")),
                        "confidence":  "high",
                        "dll_path":    str(target),
                        "doc_comment": method.get("doc", ""),
                        "parameters":  method.get("parameters", []),
                        "return_type": method.get("return_type", ""),
                        "execution": {
                            "method":   "com_invoke",
                            "dll_path": str(target),
                            "interface": entry.get("name", ""),
                            "member":   method.get("name", ""),
                        },
                    })
            logger.info("COM/TLB: %d methods from %s", len(tlb_results), target.name)
        except Exception as exc:
            logger.warning("COM/TLB analysis failed: %s", exc)
            errors["com"] = str(exc)

    # ── CLI ──────────────────────────────────────────────────────────────────
    if "cli" in requested and target.suffix.lower() == ".exe":
        try:
            analyze_cli = _import_cli()
            results = analyze_cli(target)
            invocables.extend(_inv_to_dict(i) for i in results)
            logger.info("CLI: %d invocables from %s", len(results), target.name)
        except Exception as exc:
            logger.warning("CLI analysis failed: %s", exc)
            errors["cli"] = str(exc)

    # ── Registry ─────────────────────────────────────────────────────────────
    if "registry" in requested:
        try:
            analyze_registry = _import_registry()
            results = analyze_registry(hints=hints or target.stem)
            invocables.extend(_inv_to_dict(i) for i in results)
            logger.info("Registry: %d invocables", len(results))
        except Exception as exc:
            logger.warning("Registry analysis failed: %s", exc)
            errors["registry"] = str(exc)

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

    if body.content:
        # The pipeline sent base64-encoded file bytes — decode to a temp file.
        # This handles uploaded binaries whose Linux temp path doesn't exist on Windows.
        try:
            import shutil
            tmp_dir = Path(tempfile.mkdtemp(prefix="bridge_"))
            target  = tmp_dir / Path(body.path).name
            target.write_bytes(base64.b64decode(body.content))
            logger.info(
                "Decoded uploaded binary to %s (%d bytes)",
                target, target.stat().st_size,
            )
            # Prefer the real system-path binary for GUI analysis — temp copies
            # of UWP stubs (e.g. calc.exe) won't launch a visible window.
            sys_candidate = _resolve_exe_path(str(target))
            if sys_candidate != str(target) and Path(sys_candidate).exists():
                logger.info("Using system path %s for analysis instead of temp copy", sys_candidate)
                target = Path(sys_candidate)
                # Clean up the temp dir immediately since we won't use it
                try:
                    shutil.rmtree(tmp_dir, ignore_errors=True)
                except Exception:
                    pass
                tmp_dir = None
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
            return JSONResponse(_res)

    try:
        # Run all blocking analysis in a thread pool — keeps the uvicorn async
        # event loop free to service health checks and concurrent requests.
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,  # default ThreadPoolExecutor
            _run_analysis_sync,
            target,
            requested,
            body.hints,
        )
    finally:
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
    return JSONResponse(result)


# ── Execution helpers (mirror of api/main.py — runs on Windows) ──────────────

def _resolve_exe_path(path: str) -> str:
    """Resolve a possibly-Linux temp path to a real Windows executable path."""
    p = Path(path)
    if p.exists():
        return str(p)
    filename = p.name
    for candidate in [
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
    logger.info("Execute: tool=%s method=%s args=%s", name, method, list(args.keys()))
    loop = asyncio.get_running_loop()
    if method == "gui_action":
        result = await loop.run_in_executor(None, _execute_gui_bridge, execution, name, args)
    else:
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
