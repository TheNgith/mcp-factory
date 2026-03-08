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
    # If the exe stem matches the invocable name, treat it as a launch invocable
    if Path(target).stem.lower() == name.lower():
        try:
            subprocess.Popen([target], creationflags=no_window)
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
    try:
        win = app.top_window()
        # Calculator display control name
        for ctrl_name in ("CalculatorResults", "Display", "Result", "output"):
            try:
                return win[ctrl_name].window_text().strip()
            except Exception:
                pass
        # Generic fallback — first Edit or Static that has content
        for ctrl in win.descendants(control_type="Text"):
            try:
                t = ctrl.window_text().strip()
                if t:
                    return t
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
            # Don't hide the window — GUI apps need a visible desktop window
            # so subsequent connect() calls can find them by title.
            subprocess.Popen([exe_path])
            import time; time.sleep(3)  # UWP apps need ~3s to render UIA tree
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
            win[button].click()
            import time; time.sleep(0.1)
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


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.getenv("BRIDGE_PORT", "8090"))
    logger.info("Starting GUI bridge on 0.0.0.0:%d", port)
    uvicorn.run(app, host="0.0.0.0", port=port)
