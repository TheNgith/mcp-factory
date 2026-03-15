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


# ── Probe-matrix constants ─────────────────────────────────────────────────
# When a DLL/bridge call returns 4294967295 (0xFFFFFFFF) we run a server-side
# probe matrix so the LLM sees one tool result instead of N serial rounds.
_ERROR_SENTINEL = "4294967295"
_SCALAR_PROBE_SIZES = (64, 256, 512, 1024)


def _is_pointer_type(type_str: str) -> bool:
    """True for pointer-like types: anything with *, or common Windows aliases."""
    t = (type_str or "").lower().strip()
    if "*" in t:
        return True
    return t in {
        "lpvoid", "pvoid", "handle", "lpcstr", "lpstr",
        "lpcwstr", "lpwstr", "lpbyte", "lpctstr", "lptstr", "pointer",
    }


def _is_uint_type(type_str: str) -> bool:
    """True for unsigned scalar types that often act as buffer/size parameters."""
    t = (type_str or "").lower().strip().rstrip("*").rstrip()
    return t in {
        "unsigned", "unsigned int", "uint", "uint32", "uint32_t",
        "dword", "size_t", "ulong", "unsigned long", "word", "uint16_t",
    }


def _probe_bridge(client, inv: dict, args: dict, original_result: str) -> str:
    """Run a probe matrix after an initial call returned 4294967295.

    Builds variants by:
      - Pointer-typed params with a supplied value → try as JSON string *and*
        as plain JSON integer (heap pointer vs register-direct).
      - Uint-typed params → replace with each of [64, 256, 512, 1024].

    Returns immediately on the first successful (non-sentinel) result, or a
    single summary string so the LLM never needs to probe one-at-a-time.
    """
    params    = list(inv.get("parameters") or [])
    execution = inv.get("execution") or {}
    arg_types = execution.get("arg_types") or []

    probes: list[tuple[dict, str]] = []

    for i, p in enumerate(params):
        pname = p.get("name", f"param_{i + 1}")
        ptype = arg_types[i] if i < len(arg_types) else p.get("type", "")
        val   = args.get(pname)

        if _is_pointer_type(ptype) and val is not None:
            # String encoding: pass as JSON string so ctypes allocates a heap
            # buffer; integer encoding: pass value directly into the register.
            try:
                int_val = int(val)
                probes.append(({**args, pname: str(int_val)}, f"{pname}={int_val!r} (string)"))
                probes.append(({**args, pname: int_val},      f"{pname}={int_val} (integer)"))
            except (ValueError, TypeError):
                probes.append(({**args, pname: str(val)}, f"{pname}={val!r} (string)"))
        elif _is_uint_type(ptype):
            for sz in _SCALAR_PROBE_SIZES:
                probes.append(({**args, pname: sz}, f"{pname}={sz}"))

    if not probes:
        return original_result

    tool_name  = inv.get("name", "<unknown>")
    best_label: str | None = None

    for probe_args, label in probes:
        try:
            pr = client.post("/execute", json={"invocable": inv, "args": probe_args})
            pr.raise_for_status()
            probe_result = pr.json().get("result", "")
            result_str = str(probe_result).lower()
            _is_error = (
                _ERROR_SENTINEL in str(probe_result)
                or "error" in result_str
                or "exception" in result_str
                or "violation" in result_str
            )
            if probe_result and not _is_error:
                logger.info(
                    "[bridge] probe succeeded tool=%s label=%s", tool_name, label
                )
                return f"Probe succeeded with {label}: {probe_result}"
            if best_label is None:
                best_label = label
        except Exception as exc:
            logger.debug("[bridge] probe failed tool=%s label=%s: %s", tool_name, label, exc)

    total    = len(probes)
    summary  = f"Tried {total} encodings: all returned access violation. Exhausted."
    logger.info("[bridge] probe exhausted tool=%s: %s", tool_name, summary)
    return summary


# Cache bridge reachability briefly to avoid hammering /health on every call,
# but never pin failures forever (transient network blips are common).
_bridge_reachable: bool | None = None  # None = untested
_bridge_checked_at: float = 0.0
_BRIDGE_CACHE_TTL_SECONDS = 120.0  # 2 min — long enough to skip probes during chat
_BRIDGE_FAIL_TTL_SECONDS = 15.0   # re-probe sooner after a failure

# Persistent httpx client with connection pooling — avoids TCP/TLS handshake
# on every call.  Created lazily so module import doesn't require httpx.
_bridge_client = None  # httpx.Client | None


def _get_bridge_client():
    """Return (or lazily create) a persistent httpx.Client for the bridge."""
    global _bridge_client
    if _bridge_client is None:
        import httpx
        _bridge_client = httpx.Client(
            base_url=GUI_BRIDGE_URL,
            headers={"X-Bridge-Key": GUI_BRIDGE_SECRET},
            timeout=httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0),
            limits=httpx.Limits(max_keepalive_connections=5, keepalive_expiry=120),
        )
    return _bridge_client


def _check_bridge_alive() -> bool:
    """Quick /health probe with TTL caching."""
    global _bridge_reachable, _bridge_checked_at
    now = time.monotonic()
    ttl = _BRIDGE_CACHE_TTL_SECONDS if _bridge_reachable else _BRIDGE_FAIL_TTL_SECONDS
    if _bridge_reachable is not None and (now - _bridge_checked_at) < ttl:
        return _bridge_reachable
    _bridge_reachable = False
    try:
        client = _get_bridge_client()
        t0 = time.perf_counter()
        r = client.get("/health", timeout=5.0)
        dt_ms = (time.perf_counter() - t0) * 1000.0
        _bridge_reachable = r.status_code == 200
        logger.info("[bridge] /health status=%s latency=%.1f ms", r.status_code, dt_ms)
    except Exception:
        logger.info("[bridge] /health probe failed")
    _bridge_checked_at = now
    return _bridge_reachable


def _call_execute_bridge(inv: dict, args: dict) -> str | None:
    """Forward a tool-call to the Windows VM bridge /execute endpoint.

    Returns the result string on success, or None only when the bridge is not
    configured.  On a transport/HTTP failure, returns an error string so the
    caller never silently falls through to Linux execution (which would
    produce misleading 'No such file or directory' errors for Windows paths).
    """
    if not GUI_BRIDGE_URL or not GUI_BRIDGE_SECRET:
        return None
    global _bridge_client
    try:
        client = _get_bridge_client()
        t0 = time.perf_counter()
        resp = client.post(
            "/execute",
            json={"invocable": inv, "args": args},
        )
        dt_ms = (time.perf_counter() - t0) * 1000.0
        resp.raise_for_status()
        logger.info(
            "[bridge] /execute tool=%s status=%s latency=%.1f ms",
            inv.get("name", "<unknown>"),
            resp.status_code,
            dt_ms,
        )
        result = resp.json().get("result", "")
        # Server-side probe batching: when the call returns the Windows error
        # sentinel 0xFFFFFFFF, automatically retry with alternative encodings
        # so the LLM receives one summary instead of issuing N serial rounds.
        if _ERROR_SENTINEL in str(result):
            result = _probe_bridge(client, inv, args, result)
        return result
    except Exception as exc:
        # Reset the pooled client so the next call gets a fresh connection
        # instead of retrying a dead keepalive socket.
        _bridge_client = None
        logger.warning("[bridge] /execute failed for tool=%s: %s", inv.get("name", "<unknown>"), exc)
        return f"Bridge /execute error: {exc} — the Windows VM bridge is temporarily unreachable. Try again."


def _execute_tool(inv: dict, args: dict) -> str:
    """Dispatch a single tool call to the correct backend."""
    name      = inv.get("name", "")
    execution = inv.get("execution") or inv.get("mcp", {}).get("execution", {})
    method    = execution.get("method", "")

    # ── Synthetic findings tool — no bridge/DLL call needed ───────────────
    if method == "findings" or name == "record_finding":
        from api.storage import _save_finding
        job_id = inv.get("_job_id", "")
        entry = {
            "function":    args.get("function_name", ""),
            "param":       args.get("param_name", ""),
            "finding":     args.get("finding", ""),
            "working_call": args.get("working_call"),
        }
        _save_finding(job_id, {k: v for k, v in entry.items() if v is not None})
        fn = entry["function"] or "unknown"
        logger.info("[findings] recorded for %s: %s", fn, entry["finding"])
        return f"Finding recorded for {fn}."

    # ── Synthetic enrich tool — patches the in-memory schema and re-uploads ─
    if method == "enrich" or name == "enrich_invocable":
        from api.storage import _patch_invocable
        job_id        = inv.get("_job_id", "")
        function_name = args.get("function_name", "")
        patch: dict   = {}
        if args.get("function_description"):
            patch["function_description"] = args["function_description"]
        if args.get("params") and isinstance(args["params"], dict):
            patch.update(args["params"])
        if not function_name:
            return "enrich_invocable: function_name is required."
        result = _patch_invocable(job_id, function_name, patch)
        logger.info("[enrich] %s", result)
        return result

    # All Windows-native methods (dll_import, gui_action, cli) must run on the
    # Windows VM.  Forward to the bridge whenever it is configured; only fall
    # back to local execution when the bridge is absent (e.g., dev on Windows).
    if GUI_BRIDGE_URL and GUI_BRIDGE_SECRET:
        return _call_execute_bridge(inv, args) or "Bridge returned an empty result."
    if method == "dll_import":
        return _execute_dll(inv, execution, args)
    if method == "gui_action":
        return _execute_gui(execution, name, args)
    return _execute_cli(execution, name, args)
