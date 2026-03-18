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


def _execute_dll(inv: dict, execution: dict, args: dict, extra_sentinels: dict | None = None) -> tuple[str, dict]:
    if not IS_WINDOWS:
        return (
            "DLL execution is only supported on Windows.",
            {"backend": "dll", "skipped": "non-windows"},
        )
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

    # Type bases that should be auto-allocated as output scalar slots (never input strings).
    # These match Ghidra's "undefined4 *", "undefined8 *", "uint *" etc.
    _OUT_SCALAR_BASES = frozenset({
        "undefined2", "undefined4", "undefined8",
        "uint", "uint32_t", "int", "int32_t", "dword", "ulong",
        "uint4", "uint8", "long", "ulong32",
    })
    # Bare "undefined *" is treated as a byte output buffer (written back as a string).
    _OUT_BUF_BASES = frozenset({"undefined"})

    _SCALAR_PTR_MAP: dict = {
        "dword": ctypes.c_ulong,   "unsigned long": ctypes.c_ulong,
        "uint":  ctypes.c_uint,    "uint32_t":      ctypes.c_uint,
        "int":   ctypes.c_int,     "int32_t":       ctypes.c_int,
        "long":  ctypes.c_long,    "ulong":         ctypes.c_ulong,
        "word":  ctypes.c_ushort,  "byte":          ctypes.c_ubyte,
        "bool":  ctypes.c_bool,
        "undefined":  ctypes.c_ulong,
        "undefined2": ctypes.c_ushort,
        "undefined4": ctypes.c_uint,
        "undefined8": ctypes.c_ulonglong,
    }

    try:
        lib = ctypes.CDLL(dll_path)
        fn  = getattr(lib, func_name)
        fn.restype = restype

        c_args: list = []
        out_str_bufs:  list = []   # (name, create_string_buffer)
        out_scalars:   list = []   # (name, ctypes scalar)

        _SIZE_TYPES: frozenset[str] = frozenset({
            "dword", "int", "uint", "unsigned int", "size_t",
            "long", "unsigned long", "uint32_t",
        })

        if params:
            params_seq = list(params)
            i_skip: set[int] = set()
            for idx, p in enumerate(params_seq):
                if idx in i_skip:
                    continue
                pname     = p.get("name", "")
                ptype_raw = p.get("type", "string")
                ptype_lc  = ptype_raw.lower()
                ptype_base = ptype_lc.replace("const ", "").strip().rstrip(" *")
                val       = args.get(pname)

                is_ptr   = "*" in ptype_lc
                is_const = is_ptr and "const " in ptype_lc

                # Ghidra undefined*/undefined4*/undefined8* are ALWAYS output slots.
                _val_blank = (
                    val is None or
                    ptype_base in (_OUT_SCALAR_BASES | _OUT_BUF_BASES)
                )
                is_out = is_ptr and not is_const and _val_blank

                if is_out and (ptype_base in _OUT_BUF_BASES or ptype_base == "char"):
                    # undefined* — plain char* output buffer.
                    # Look ahead: the next uint param is the buffer-size arg; always
                    # supply ≥4096 so the DLL doesn't refuse or overflow on size=0.
                    buf_size = 4096
                    if idx + 1 < len(params_seq):
                        np       = params_seq[idx + 1]
                        np_name  = np.get("name", "")
                        np_tbase = np.get("type", "").lower() \
                                     .replace("const ", "").strip().rstrip(" *")
                        np_val   = args.get(np_name)
                        if np_tbase in _SIZE_TYPES:
                            if np_val is not None:
                                try:
                                    buf_size = max(4096, int(np_val))
                                except (ValueError, TypeError):
                                    pass
                            i_skip.add(idx + 1)            # skip; we supply it below
                            sbuf = ctypes.create_string_buffer(buf_size)
                            c_args.append(sbuf)
                            c_args.append(ctypes.c_uint(buf_size))
                            out_str_bufs.append((pname, sbuf))
                            continue
                    sbuf = ctypes.create_string_buffer(buf_size)
                    c_args.append(sbuf)
                    out_str_bufs.append((pname, sbuf))
                elif is_out and (ptype_base in _OUT_SCALAR_BASES or
                                 ptype_base in ("byte", "uchar")):
                    # Scalar output slot (undefined4 *, uint *, etc.)
                    # Note: char* is NOT here — it routes to create_string_buffer above.
                    s_ctype = _SCALAR_PTR_MAP.get(ptype_base, ctypes.c_ulong)
                    scalar  = s_ctype(0)
                    c_args.append(ctypes.byref(scalar))
                    out_scalars.append((pname, scalar))
                elif val is not None:
                    atype = _CTYPES_ARGTYPE.get(ptype_base, ctypes.c_char_p)
                    if is_ptr:
                        if isinstance(val, int):
                            c_args.append(ctypes.c_int64(val))
                        else:
                            c_args.append(ctypes.c_char_p(str(val).encode()))
                    elif atype == ctypes.c_char_p:
                        c_args.append(ctypes.c_char_p(str(val).encode()))
                    else:
                        try:
                            c_args.append(atype(int(val)))
                        except (ValueError, TypeError):
                            c_args.append(atype(0))
                # else: omitted optional param — skip
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

        _t0_call = time.perf_counter()
        result = fn(*c_args)
        _dt_ms = (time.perf_counter() - _t0_call) * 1000.0

        # Build sentinel lookup: hardcoded defaults merged with DLL-specific vocab
        # error_codes so any DLL's custom return codes get annotated, not just the
        # five contoso_cs values.  setdefault keeps hardcoded meanings if vocab
        # repeats the same key with a different phrasing.
        _SENTINEL_NOTES: dict[int, str] = {
            0xFFFFFFFF: "sentinel: not found/invalid",
            0xFFFFFFFE: "sentinel: null argument",
            0xFFFFFFFD: "sentinel: not initialized",
            0xFFFFFFFC: "sentinel: account locked",
            0xFFFFFFFB: "sentinel: write denied",
        }
        if extra_sentinels:
            for _sk, _sv in extra_sentinels.items():
                try:
                    _skey = int(_sk, 16) if isinstance(_sk, str) else int(_sk)
                    _SENTINEL_NOTES.setdefault(_skey, str(_sv))
                except (ValueError, TypeError):
                    pass
        _r32 = result & 0xFFFFFFFF
        _note = _SENTINEL_NOTES.get(_r32, "")
        # When ctypes uses a signed restype (c_int, c_long) and the DLL returns
        # e.g. 0xFFFFFFFC, Python reports -4.  Show the hex form so the model
        # can match against hex vocab entries like error_codes["0xFFFFFFFC"].
        _hex_form = f" (= 0x{_r32:08X})" if result < 0 else ""
        output_parts: list[str] = [
            f"Returned: {result}" + _hex_form + (f", {_note}" if _note else "")
        ]
        for buf_name, sbuf in out_str_bufs:
            txt = sbuf.value
            if isinstance(txt, bytes):
                txt = txt.decode(errors="replace")
            if txt:
                output_parts.append(f"{buf_name}={txt!r}")
        for sc_name, scalar in out_scalars:
            output_parts.append(f"{sc_name}={scalar.value}")
        _trace: dict = {
            "backend":       "dll",
            "dll_path":      dll_path,
            "function_name": func_name,
            "return_type":   ret_str,
            "c_args_count":  len(c_args),
            "out_str_bufs":  [n for n, _ in out_str_bufs],
            "out_scalars":   [n for n, _ in out_scalars],
            "raw_result":    result,
            "latency_ms":    round(_dt_ms, 2),
            "exception":     None,
        }
        return "\n".join(output_parts), _trace
    except Exception as exc:
        import re as _re
        _addr_m = _re.search(r"0x[0-9a-fA-F]+", str(exc))
        _trace = {
            "backend":        "dll",
            "dll_path":       dll_path,
            "function_name":  func_name,
            "exception":      str(exc),
            "exception_class": type(exc).__name__,
            "exception_addr": _addr_m.group() if _addr_m else None,
            "latency_ms":     None,
        }
        return f"DLL call error: {exc}", _trace


def _execute_cli(execution: dict, name: str, args: dict) -> tuple[str, dict]:
    target = (
        execution.get("executable_path")
        or execution.get("target_path")
        or execution.get("dll_path", "")
    )
    if not target:
        return (
            f"CLI error: no executable path configured for '{name}'",
            {"backend": "cli", "exception": "no executable path"},
        )

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
            _msg = (
                f"{Path(target).name} has been launched successfully. "
                "The application is now open. "
                "DO NOT call this launch tool again — it is already running. "
                "Proceed directly to using the other tools to interact with it."
            )
            return _msg, {"backend": "cli", "action": "launch", "target": target, "exception": None}
        except Exception as exc:
            return (
                f"CLI error: {exc}",
                {"backend": "cli", "action": "launch", "target": target, "exception": str(exc)},
            )

    cmd = [target, name] + [str(v) for v in args.values()]
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if IS_WINDOWS else 0
    try:
        _t0 = time.perf_counter()
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=creation_flags,
        )
        _dt_ms = (time.perf_counter() - _t0) * 1000.0
        _trace: dict = {
            "backend":        "cli",
            "cmd":            cmd,
            "exit_code":      r.returncode,
            "stdout_len":     len(r.stdout or ""),
            "stderr_excerpt": (r.stderr or "")[:200] or None,
            "latency_ms":     round(_dt_ms, 2),
            "timeout_hit":    False,
            "exception":      None,
        }
        return r.stdout or r.stderr or f"exit_code={r.returncode}", _trace
    except subprocess.TimeoutExpired:
        _trace = {"backend": "cli", "cmd": cmd, "exit_code": None, "stdout_len": 0,
                  "stderr_excerpt": None, "latency_ms": None, "timeout_hit": True,
                  "exception": "TimeoutExpired"}
        return "CLI error: timed out after 10 s", _trace
    except Exception as exc:
        _trace = {"backend": "cli", "cmd": cmd, "exit_code": None, "stdout_len": 0,
                  "stderr_excerpt": None, "latency_ms": None, "timeout_hit": False,
                  "exception": str(exc)}
        return f"CLI error: {exc}", _trace


def _execute_gui(execution: dict, name: str, args: dict) -> tuple[str, dict]:
    if not IS_WINDOWS:
        return (
            "GUI actions are only supported on Windows.",
            {"backend": "gui", "skipped": "non-windows"},
        )
    try:
        from pywinauto.application import Application  # type: ignore
    except ImportError:
        return (
            "pywinauto is not installed; GUI actions unavailable.",
            {"backend": "gui", "exception": "ImportError: pywinauto"},
        )

    exe_path    = execution.get("exe_path", "")
    action_type = execution.get("action_type", "menu_click")

    if action_type == "close_app":
        try:
            app = Application(backend="uia").connect(path=exe_path, timeout=3)
            app.kill()
            return (
                "App closed.",
                {"backend": "gui", "exe_path": exe_path, "action_type": action_type, "exception": None},
            )
        except Exception as exc:
            return (
                f"GUI close error: {exc}",
                {"backend": "gui", "exe_path": exe_path, "action_type": action_type,
                 "exception": str(exc), "exception_class": type(exc).__name__},
            )

    return (
        f"GUI action '{action_type}' requested for '{exe_path}'. "
        "Full GUI automation requires Windows with pywinauto installed.",
        {"backend": "gui", "exe_path": exe_path, "action_type": action_type, "exception": None},
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


def _call_execute_bridge(inv: dict, args: dict) -> tuple[str | None, dict]:
    """Forward a tool-call to the Windows VM bridge /execute endpoint.

    Returns (result_str, trace) on success, or (None, {}) when the bridge is
    not configured.  On a transport/HTTP failure, returns an error string so
    the caller never silently falls through to Linux execution.
    """
    if not GUI_BRIDGE_URL or not GUI_BRIDGE_SECRET:
        return None, {}
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
        _probe_triggered = False
        if _ERROR_SENTINEL in str(result):
            result = _probe_bridge(client, inv, args, result)
            _probe_triggered = True
        _trace = {
            "backend":         "bridge",
            "http_status":     resp.status_code,
            "latency_ms":      round(dt_ms, 2),
            "probe_triggered": _probe_triggered,
            "exception":       None,
        }
        return result, _trace
    except Exception as exc:
        # Reset the pooled client so the next call gets a fresh connection
        # instead of retrying a dead keepalive socket.
        _bridge_client = None
        logger.warning("[bridge] /execute failed for tool=%s: %s", inv.get("name", "<unknown>"), exc)
        _trace = {
            "backend":         "bridge",
            "exception":       str(exc),
            "exception_class": type(exc).__name__,
        }
        err = f"Bridge /execute error: {exc}"
        return err + " \u2014 the Windows VM bridge is temporarily unreachable. Try again.", _trace


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
        result, _ = _call_execute_bridge(inv, args)
        return result or "Bridge returned an empty result."
    if method == "dll_import":
        result, _ = _execute_dll(inv, execution, args)
        return result
    if method == "gui_action":
        result, _ = _execute_gui(execution, name, args)
        return result
    result, _ = _execute_cli(execution, name, args)
    return result


def _execute_tool_traced(inv: dict, args: dict, extra_sentinels: dict | None = None) -> dict:
    """Like _execute_tool but captures the per-backend execution trace.

    Returns {"result_str": str, "trace": dict | None}.  Synthetic tools
    (record_finding, enrich_invocable) always set trace to None.

    extra_sentinels: optional dict mapping hex-string or int keys to meanings,
    merged with the hardcoded sentinel table so DLL-specific error codes get
    annotated in every tool result string the model sees.
    """
    name      = inv.get("name", "")
    execution = inv.get("execution") or inv.get("mcp", {}).get("execution", {})
    method    = execution.get("method", "")

    # Synthetic tools — delegate to _execute_tool, no trace
    if (
        method in ("findings", "enrich")
        or name in ("record_finding", "enrich_invocable")
    ):
        return {"result_str": _execute_tool(inv, args), "trace": None}

    if GUI_BRIDGE_URL and GUI_BRIDGE_SECRET:
        result, trace = _call_execute_bridge(inv, args)
        return {"result_str": result or "Bridge returned an empty result.", "trace": trace}
    if method == "dll_import":
        result, trace = _execute_dll(inv, execution, args, extra_sentinels)
        return {"result_str": result, "trace": trace}
    if method == "gui_action":
        result, trace = _execute_gui(execution, name, args)
        return {"result_str": result, "trace": trace}
    result, trace = _execute_cli(execution, name, args)
    return {"result_str": result, "trace": trace}
