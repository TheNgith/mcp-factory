"""api/executor.py – Tool execution backends: DLL (ctypes), CLI (subprocess), GUI (pywinauto), bridge.

Exports:
  _CTYPES_RESTYPE, _CTYPES_ARGTYPE – Windows-only ctypes type maps.
  _resolve_dll_path  – search for a DLL relative to the project root.
  _execute_dll       – call a native DLL function via ctypes.
  _execute_cli       – run a CLI tool via subprocess.
  _execute_gui       – drive GUI via pywinauto (Windows only).
  _call_execute_bridge – forward execution to the Windows VM bridge.
  _execute_tool      – top-level dispatch: picks the right backend.
"""

from __future__ import annotations

import ctypes
import logging
import subprocess
import time
from pathlib import Path

from api.config import IS_WINDOWS, GUI_BRIDGE_URL, GUI_BRIDGE_SECRET

logger = logging.getLogger("mcp_factory.api")

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


# Cache bridge reachability briefly to avoid hammering /health on every call,
# but never pin failures forever (transient network blips are common).
_bridge_reachable: bool | None = None  # None = untested
_bridge_checked_at: float = 0.0
_BRIDGE_CACHE_TTL_SECONDS = 20.0


def _check_bridge_alive() -> bool:
    """Quick /health probe with short TTL caching."""
    global _bridge_reachable, _bridge_checked_at
    now = time.monotonic()
    if _bridge_reachable is not None and (now - _bridge_checked_at) < _BRIDGE_CACHE_TTL_SECONDS:
        return _bridge_reachable
    import httpx
    try:
        r = httpx.get(
            f"{GUI_BRIDGE_URL}/health",
            headers={"X-Bridge-Key": GUI_BRIDGE_SECRET},
            timeout=httpx.Timeout(connect=5.0, read=5.0, write=5.0, pool=5.0),
        )
        _bridge_reachable = r.status_code == 200
    except Exception:
        _bridge_reachable = False
    _bridge_checked_at = now
    logger.info("Bridge reachability: %s", _bridge_reachable)
    return _bridge_reachable


def _call_execute_bridge(inv: dict, args: dict) -> str | None:
    """Forward a tool-call to the Windows VM bridge /execute endpoint.

    Returns the result string on success, or None if the bridge is
    unavailable / returns an error (caller falls through to local execution).
    Uses explicit connect/read timeouts so a dead host fails in ~5 s instead
    of the OS default ~2 min TCP timeout.
    """
    global _bridge_reachable, _bridge_checked_at
    if not _check_bridge_alive():
        return None
    import httpx
    try:
        resp = httpx.post(
            f"{GUI_BRIDGE_URL}/execute",
            json={"invocable": inv, "args": args},
            headers={"X-Bridge-Key": GUI_BRIDGE_SECRET},
            timeout=httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0),
        )
        resp.raise_for_status()
        return resp.json().get("result", "")
    except Exception as exc:
        logger.warning("Bridge /execute failed (falling through to local): %s", exc)
        # Mark unreachable for the next short TTL window so we fail fast,
        # then automatically re-probe afterward.
        _bridge_reachable = False
        _bridge_checked_at = time.monotonic()
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
