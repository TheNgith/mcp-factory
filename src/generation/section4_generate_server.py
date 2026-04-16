"""
section4_generate_server.py
Generates a Flask-based MCP server with /tools, /invoke, and /chat endpoints
from a selected-invocables.json file. The /chat endpoint uses OpenAI function
calling (compatible with api.openai.com and Azure OpenAI via .env variables).
"""

import json
import os
import textwrap

INPUT_PATH = os.path.join("artifacts", "selected-invocables.json")
OUTPUT_BASE = "generated"

# ---------------------------------------------------------------------------
# Server template — placeholders are replaced by _inject() below.
# __COMPONENT_NAME__  → component name string (no quotes, used in comments)
# __INVOCABLES_JSON__ → JSON-serialised list of invocable dicts
# ---------------------------------------------------------------------------
SERVER_TEMPLATE = r'''# Generated MCP server — __COMPONENT_NAME__
# Run:  pip install -r requirements.txt  &&  cp .env.example .env  (fill values)
#       python server.py

import os
import json
import ctypes
import subprocess
import threading
from pathlib import Path
from flask import Flask, request, jsonify, send_from_directory
from openai import OpenAI
from dotenv import load_dotenv

# Vendored error enrichment (copied next to this file at generation time).
try:
    from error_enrichment import build_error_payload as _build_error_payload
except Exception:
    def _build_error_payload(*_a, **_kw):
        return None

# Always load .env from the server's own directory, regardless of cwd
load_dotenv(dotenv_path=Path(__file__).parent / ".env")
# openai 2.x reads OPENAI_BASE_URL directly from os.environ;
# if it's set to "" (e.g. by a system env var) it overrides base_url=None.
# Remove it entirely so the library defaults to api.openai.com.
if not os.environ.get("OPENAI_BASE_URL"):
    os.environ.pop("OPENAI_BASE_URL", None)

app = Flask(__name__, static_folder="static")

# ── Tool registry (injected by generator) ──────────────────────────────────
INVOCABLES = json.loads(r"""__INVOCABLES_JSON__""")

INVOCABLE_MAP = {inv["name"]: inv for inv in INVOCABLES}


def _format_error(
    function_name: str,
    raw_result=None,
    trace=None,
    exception=None,
    inv=None,
) -> tuple[str, dict | None]:
    """Build a structured error payload + human/JSON composite string.

    The returned string is safe to return directly to a plain-text MCP client
    (it contains the human paragraph followed by a ```json``` code block with
    the full payload), while JSON-parsing clients can extract the embedded
    block.  Also returned as the machine-readable dict so /invoke can ship it
    in a separate field.
    """
    findings = []
    if isinstance(inv, dict):
        _summary = inv.get("findings_summary") or {}
        findings = [
            {
                "working_call": entry.get("args"),
                "confidence": entry.get("confidence"),
                "recorded_at": entry.get("recorded_at"),
                "status": "success",
            }
            for entry in (_summary.get("working_calls") or [])
        ]
    payload = _build_error_payload(
        function_name, raw_result, trace, exception, findings, None,
    )
    if not payload:
        # The enrichment call decided there was no error; surface whatever the
        # caller passed through unchanged.
        return (str(raw_result if raw_result is not None else exception or ""), None)
    human = payload.get("human") or f"{function_name} failed."
    block = "```json\n" + json.dumps(payload, indent=2, default=str) + "\n```"
    return f"{human}\n\n{block}", payload


_C_TO_JSON_TYPE = {
    "int": "integer", "unsigned": "integer", "unsigned int": "integer",
    "long": "integer", "unsigned long": "integer", "size_t": "integer",
    "uint32_t": "integer", "uint64_t": "integer", "int32_t": "integer",
    "int64_t": "integer", "short": "integer", "unsigned short": "integer",
    "float": "number", "double": "number",
    "bool": "boolean",
}


def _c_type_to_json_type(c_type: str) -> str:
    """Map a C type string to the closest JSON schema primitive type."""
    t = c_type.lower().strip().rstrip("*").strip()
    if t in _C_TO_JSON_TYPE:
        return _C_TO_JSON_TYPE[t]
    # pointer / char* / string variants → string
    if "char" in t or "string" in t or "str" == t:
        return "string"
    # anything containing 'int' or 'long' → integer
    if "int" in t or "long" in t:
        return "integer"
    return "string"


def _build_openai_functions():
    """Convert the invocable list into OpenAI function-calling schema objects."""
    fns = []
    for inv in INVOCABLES:
        props = {}
        required = []
        for p in inv.get("parameters", []):
            json_type = _c_type_to_json_type(p.get("type", "string"))
            props[p["name"]] = {
                "type": json_type,
                "description": p.get("description", p.get("type", "")),
            }
            if p.get("required", False):
                required.append(p["name"])
        fns.append({
            "type": "function",
            "function": {
                "name": inv["name"],
                "description": inv.get("description") or f"Invoke {inv['name']} from __COMPONENT_NAME__",
                "parameters": {
                    "type": "object",
                    "properties": props,
                    "required": required,
                },
            },
        })
    return fns


OPENAI_FUNCTIONS = _build_openai_functions()


# ── Semantic tool retrieval ─────────────────────────────────────────────────
# Lazily embeds all tool descriptions on first chat request, then returns the
# top-K tools most semantically relevant to the user's message.  This keeps
# every binary's full export list discoverable while respecting OpenAI's
# 128-tool hard cap.  Falls back to OPENAI_FUNCTIONS[:128] on any error.
_TOOL_EMBED_CACHE: list = []
_TOOL_EMBED_LOCK  = threading.Lock()
_TOOL_EMBED_K     = 60   # tools sent to OpenAI per request


def _retrieve_tools(user_message: str, client, k: int = _TOOL_EMBED_K) -> list:
    """Return the top-k tools most relevant to user_message (semantic search)."""
    global _TOOL_EMBED_CACHE
    if not OPENAI_FUNCTIONS:
        return []
    if len(OPENAI_FUNCTIONS) <= k:
        return OPENAI_FUNCTIONS  # small enough — no retrieval needed
    try:
        import math

        def _norm(v: list) -> list:
            mag = math.sqrt(sum(x * x for x in v))
            return [x / mag for x in v] if mag else v

        def _dot(a: list, b: list) -> float:
            return sum(x * y for x, y in zip(a, b))

        with _TOOL_EMBED_LOCK:
            if not _TOOL_EMBED_CACHE:
                texts = [
                    f"{fn['function']['name']}: {fn['function']['description']}"
                    for fn in OPENAI_FUNCTIONS
                ]
                resp = client.embeddings.create(
                    model="text-embedding-3-small", input=texts
                )
                _TOOL_EMBED_CACHE[:] = [
                    _norm(item.embedding)
                    for item in sorted(resp.data, key=lambda x: x.index)
                ]

        q_resp = client.embeddings.create(
            model="text-embedding-3-small", input=[user_message]
        )
        q = _norm(q_resp.data[0].embedding)
        scores = [(_dot(q, vec), i) for i, vec in enumerate(_TOOL_EMBED_CACHE)]
        scores.sort(reverse=True)
        return [OPENAI_FUNCTIONS[i] for _, i in scores[:k]]
    except Exception:
        return OPENAI_FUNCTIONS[:128]  # graceful fallback


# ── Execution helpers ───────────────────────────────────────────────────────

def _execute_tool(name: str, args: dict) -> tuple[str, dict | None]:
    """Dispatch a single tool call.  Returns (result_str, error | None)."""
    inv = INVOCABLE_MAP.get(name)
    if not inv:
        return _format_error(
            name, raw_result=None,
            trace={"backend": "unknown_tool"},
            exception=f"Unknown tool: {name}",
            inv=None,
        )
    # Support both flat {"execution": {...}} and rich MCP {"mcp": {"execution": {...}}}
    execution = inv.get("execution") or inv.get("mcp", {}).get("execution", {})
    method = execution.get("method", "")
    if method == "dll_import":
        return _execute_dll(inv, execution, args)
    if method == "gui_action":
        # _execute_gui returns a plain string; classify post-hoc so callers
        # still get the (str, error|None) contract.
        _gui_result = _execute_gui(execution, name, args)
        _lower = _gui_result.lower()
        if " error" in _lower or _lower.startswith(("error", "unknown gui", "no ", "could not")):
            return _format_error(
                name, raw_result=None,
                trace={"backend": "gui", "exception": _gui_result[:200]},
                exception=_gui_result, inv=inv,
            )
        return _gui_result, None
    return _execute_cli(execution, name, args, inv)


# C type map used by _execute_dll
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


def _resolve_dll_path(raw: str) -> str:
    """Return an absolute path for *raw*, searching likely anchors."""
    from pathlib import Path as _P
    p = _P(raw)
    if p.is_absolute() and p.exists():
        return str(p)
    # server lives at generated/<component>/server.py → project root is 2 dirs up
    project_root = _P(__file__).resolve().parent.parent.parent
    candidate = project_root / raw
    if candidate.exists():
        return str(candidate)
    # also try relative to server's own directory
    local = _P(__file__).resolve().parent / raw
    if local.exists():
        return str(local)
    return raw  # best-effort; let ctypes give the real error


def _execute_dll(inv: dict, execution: dict, args: dict) -> tuple[str, dict | None]:
    dll_path  = _resolve_dll_path(execution.get("dll_path", ""))
    func_name = execution.get("function_name", "")

    # Return type: flat "return_type" field takes priority; fall back to
    # signature.return_type from the rich MCP schema.
    ret_str = (
        inv.get("return_type")
        or (inv.get("signature") or {}).get("return_type", "unknown")
        or "unknown"
    ).strip()
    restype = _CTYPES_RESTYPE.get(ret_str.lower(), ctypes.c_size_t)

    # Parameter list: flat "parameters" list or parsed from signature string
    params = list(inv.get("parameters") or [])
    if not params:
        sig_str = (inv.get("signature") or {}).get("parameters", "")
        if sig_str:
            for chunk in sig_str.split(","):
                tokens = chunk.strip().split()
                if len(tokens) >= 2:
                    # e.g. ["const", "char*", "buf"] or ["size_t", "n"]
                    raw_type = " ".join(tokens[:-1]).lower().strip("*").rstrip()
                    pname    = tokens[-1].lstrip("*")
                    params.append({"name": pname, "type": raw_type})

    try:
        lib = ctypes.CDLL(dll_path)
        fn  = getattr(lib, func_name)
        fn.restype = restype

        # Build ctypes arg list from the named args dict
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
            # No type metadata — guess from Python value type
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

        # Decode bytes result from char* functions
        if restype == ctypes.c_char_p:
            if isinstance(result, bytes):
                return f"Returned: {result.decode(errors='replace')}", None
        # Let the enrichment helper classify numeric results.  Returns (str, None)
        # if the code is not a known failure, or (err_str, payload) if it is.
        _raw_str = f"Returned: {result}"
        _err_str, _payload = _format_error(
            func_name or inv.get("name", ""),
            raw_result=result,
            trace={"backend": "dll", "raw_result": result, "exception": None},
            exception=None,
            inv=inv,
        )
        if _payload:
            return _err_str, _payload
        return _raw_str, None
    except Exception as exc:
        return _format_error(
            func_name or inv.get("name", ""),
            raw_result=None,
            trace={"backend": "dll", "exception": str(exc)},
            exception=str(exc),
            inv=inv,
        )


def _execute_cli(execution: dict, name: str, args: dict, inv: dict | None = None) -> tuple[str, dict | None]:
    target = (
        execution.get("executable_path")
        or execution.get("target_path")
        or execution.get("dll_path", "")
    )
    if not target:
        return _format_error(
            name, raw_result=None,
            trace={"backend": "cli", "exception": "no executable path"},
            exception="no executable path configured", inv=inv,
        )
    # If the invocable name matches the exe stem this is a "launch the app"
    # invocable (e.g. calc, notepad).  Run it with Popen so its GUI window
    # is NOT suppressed, and don't pass the name as a spurious CLI argument.
    from pathlib import Path as _Path
    exe_stem = _Path(target).stem.lower()
    if exe_stem == name.lower():
        # Route through _ensure_gui_app so the window is cached immediately;
        # subsequent button_click calls will reuse it instead of re-launching.
        # Peek at sibling invocables to detect WinUI3/MSIX apps (gui_backend=uia)
        # so _ensure_gui_app can skip the win32 Attempt A that would open a
        # wasted extra window before failing over to UIA.
        _preferred = ""
        for _inv in INVOCABLES:
            _exec = _inv.get("execution") or {}
            if (_exec.get("exe_path", "").lower() == target.lower()
                    and _exec.get("gui_backend") == "uia"):
                _preferred = "uia"
                break
        try:
            _ensure_gui_app(target, preferred_backend=_preferred)
            return f"Launched {_Path(target).name}", None
        except Exception as exc:
            return _format_error(
                name, raw_result=None,
                trace={"backend": "cli", "action": "launch", "exception": str(exc)},
                exception=str(exc), inv=inv,
            )
    # Standard CLI invocation: target_exe subcommand [args...]
    cmd = [target, name] + [str(v) for v in args.values()]
    # Suppress any GUI window the binary might open (Windows only).
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=creation_flags,
        )
        if r.returncode == 0:
            return r.stdout or r.stderr or f"exit_code={r.returncode}", None
        return _format_error(
            name, raw_result=None,
            trace={"backend": "cli", "exit_code": r.returncode,
                   "stderr_excerpt": (r.stderr or "")[:200], "exception": "nonzero exit"},
            exception=f"exit_code={r.returncode}", inv=inv,
        )
    except subprocess.TimeoutExpired:
        return _format_error(
            name, raw_result=None,
            trace={"backend": "cli", "timeout_hit": True, "exception": "TimeoutExpired"},
            exception="TimeoutExpired", inv=inv,
        )
    except Exception as exc:
        return _format_error(
            name, raw_result=None,
            trace={"backend": "cli", "exception": str(exc)},
            exception=str(exc), inv=inv,
        )


# ── GUI app state (persistent across HTTP requests) ─────────────────────────
# A single-user demo stores one live pywinauto Application per exe_path.
# The app stays alive between calls so "type hello" → "save as test.txt"
# works as two separate HTTP requests against the same window.

_GUI_APP_LOCK = threading.Lock()
_GUI_APP_INSTANCES: dict = {}  # exe_path -> (Application, main_window)


def _ensure_gui_app(exe_path: str, preferred_backend: str = ""):
    """Return (app, win) for exe_path, launching the app if not already running.

    Tries the win32 backend first (classic Win32 apps).  If no window is found,
    automatically retries with the uia backend (WinUI3 / Win11 apps like
    Notepad 10.x).

    Pass preferred_backend='uia' to skip the win32 attempt entirely — this
    avoids opening a wasted extra window for MSIX/WinUI3 apps like Calculator.
    """
    import time
    import subprocess
    import re
    from pathlib import Path
    try:
        from pywinauto.application import Application  # type: ignore
    except ImportError:
        raise RuntimeError(
            "pywinauto is not installed. "
            "Run: pip install pywinauto"
        )

    with _GUI_APP_LOCK:
        entry = _GUI_APP_INSTANCES.get(exe_path)
        if entry is not None:
            app, win, backend = entry
            try:
                win.exists()
                return app, win
            except Exception:
                _GUI_APP_INSTANCES.pop(exe_path, None)

        last_exc = None

        # ── Attempt A: win32 backend (classic Win32 apps) ─────────────────
        # Skipped for known WinUI3/MSIX apps (preferred_backend='uia') because
        # the stub process exits immediately, causing a wasted second window.
        import concurrent.futures as _cf

        if preferred_backend != "uia":
            def _launch_win32():
                _app = Application(backend="win32")
                si = subprocess.STARTUPINFO()
                si.dwFlags = 0x00000001    # STARTF_USESHOWWINDOW
                si.wShowWindow = 7         # SW_SHOWMINNOACTIVE
                _proc = subprocess.Popen([exe_path], startupinfo=si)
                time.sleep(1.8)
                _app = Application(backend="win32").connect(process=_proc.pid, timeout=3)
                _win = _app.top_window()
                _win.handle  # raises immediately if no window under this PID
                return _app, _win

            with _cf.ThreadPoolExecutor(max_workers=1) as _pool:
                _fut = _pool.submit(_launch_win32)
                try:
                    app, win = _fut.result(timeout=7)
                    _GUI_APP_INSTANCES[exe_path] = (app, win, "win32")
                    return app, win
                except Exception as exc:
                    last_exc = exc  # win32 failed — fall through to UIA

        # ── Attempt B: UIA + shell-launch (MSIX / WinUI3 apps) ───────────
        # Win11 Notepad (and modern Calculator) use MSIX stubs that exit
        # immediately — pywinauto loses the PID.  Strategy:
        #
        #   1. Snapshot existing top-level HWNDs with win32gui (preferred).
        #   2. Shell-launch so Windows resolves the MSIX package.
        #   3. Wait for the real process to create its window.
        #   4. Diff HWNDs → the new window IS the app; connect by handle.
        #
        # This bypasses all title/path guessing and works regardless of
        # what title or process-name Win11 Notepad registers.
        # Falls back to title-pattern loop if win32gui is unavailable.
        try:
            # ── B1: HWND-diff via win32gui (reliable) ─────────────────────
            try:
                import win32gui as _w32gui  # type: ignore

                def _snap():
                    _hwnds: set = set()
                    def _cb(h, _):
                        if _w32gui.IsWindowVisible(h) and _w32gui.GetWindowText(h):
                            _hwnds.add(h)
                    _w32gui.EnumWindows(_cb, None)
                    return _hwnds

                before = _snap()
                subprocess.Popen(f'start "" "{exe_path}"', shell=True)
                time.sleep(4.0)
                after = _snap()
                new_hwnds = after - before
                if not new_hwnds:
                    raise RuntimeError(
                        "No new top-level windows appeared after launching "
                        f"{exe_path!r} — HWND diff came up empty"
                    )
                hwnd = next(iter(new_hwnds))
                app = Application(backend="uia").connect(handle=hwnd, timeout=5)
            except ImportError:
                # win32gui not available — fall back to title patterns
                subprocess.Popen(f'start "" "{exe_path}"', shell=True)
                time.sleep(3.5)
                exe_stem = Path(exe_path).stem
                app = None
                for connect_kw in [
                    {"path": exe_path},
                    {"title_re": f"(?i).*{re.escape(exe_stem)}.*"},
                    {"title_re": "(?i).*Untitled.*"},
                    {"title_re": "(?i).*New Tab.*"},
                ]:
                    try:
                        app = Application(backend="uia").connect(timeout=5, **connect_kw)
                        break
                    except Exception as exc:
                        last_exc = exc
                if app is None:
                    raise RuntimeError(
                        f"UIA connect failed after shell-launch: {last_exc}"
                    )

            win = app.top_window()
            _GUI_APP_INSTANCES[exe_path] = (app, win, "uia")
            return app, win
        except Exception as exc:
            last_exc = exc

        raise RuntimeError(
            f"Could not start GUI app {exe_path!r}: {last_exc}"
        )


def _execute_gui(execution: dict, name: str, args: dict) -> str:
    """Execute a GUI action against a live pywinauto-controlled window.

    Supported action_type values:
        menu_click      - navigate a menu path, e.g. ["File", "Save As"]
        keyboard_shortcut - send keystrokes directly (WinUI3 apps)
        button_click    - click a named UIA Button control (e.g. Calculator)
        type_text       - type text into the active edit control
        get_text        - return text content of the main edit control
        save_as         - open File > Save As dialog and save with given filename
        open_file       - open File > Open dialog and load a specific filename
        close_app       - kill the application process
    """
    import re
    import time
    exe_path = execution.get("exe_path", "")
    action_type = execution.get("action_type", "menu_click")
    menu_path = execution.get("menu_path", [])

    try:
        app, win = _ensure_gui_app(exe_path)
    except Exception as exc:
        return f"GUI launch error: {exc}"

    try:
        # ── type_text ────────────────────────────────────────────────────────
        if action_type == "type_text":
            text = args.get("text", "")
            win.set_focus()
            typed = False
            # Try progressively broader strategies to reach the edit control
            for class_name in ("RichEditD2DPT", "Edit", "RichEdit20W", "RICHEDIT50W"):
                try:
                    edit = win.child_window(class_name=class_name)
                    edit.set_focus()
                    edit.type_keys(text, with_spaces=True)
                    typed = True
                    break
                except Exception:
                    pass
            if not typed:
                # UIA fallback — find Document or text area by control type
                try:
                    doc = win.child_window(control_type="Document")
                    doc.set_focus()
                    doc.type_keys(text, with_spaces=True)
                    typed = True
                except Exception:
                    pass
            if not typed:
                # Last resort: type into the top-level window
                win.type_keys(text, with_spaces=True)
            return f"Typed: {repr(text)}"

        # ── get_text ─────────────────────────────────────────────────────────
        elif action_type == "get_text":
            for class_name in ("RichEditD2DPT", "Edit", "RichEdit20W", "RICHEDIT50W"):
                try:
                    edit = win.child_window(class_name=class_name)
                    return edit.window_text()
                except Exception:
                    pass
            try:
                doc = win.child_window(control_type="Document")
                return doc.window_text()
            except Exception:
                pass
            return win.window_text()

        # ── open_file ────────────────────────────────────────────────────────
        elif action_type == "open_file":
            filename = (
                args.get("filename")
                or args.get("name")
                or args.get("file")
                or ""
            )
            if not filename:
                return "open_file requires a filename argument"
            win.set_focus()
            time.sleep(0.2)
            # Trigger File > Open
            triggered = False
            for _trigger in (
                lambda: win.menu_select("File->Open"),
                lambda: win.type_keys("^o"),
                lambda: win.type_keys("%fo"),
            ):
                try:
                    _trigger()
                    triggered = True
                    break
                except Exception:
                    pass
            if not triggered:
                return "Could not trigger Open dialog"
            time.sleep(1.2)
            # Locate the Open dialog
            dlg = None
            for _title in ("Open", "Open.*", ".*Open.*"):
                try:
                    dlg = app.window(title_re=_title)
                    dlg.wait("visible", timeout=4)
                    break
                except Exception:
                    dlg = None
            if dlg is None:
                try:
                    dlg = app.top_window()
                except Exception as exc:
                    return f"Open dialog not found: {exc}"
            try:
                fn_ctrl = None
                for _lookup in (
                    lambda: dlg.child_window(title="File name:", control_type="Edit"),
                    lambda: dlg.child_window(class_name="Edit", found_index=0),
                    lambda: dlg.child_window(control_type="ComboBox").child_window(class_name="Edit"),
                    lambda: dlg.child_window(auto_id="1001"),
                ):
                    try:
                        fn_ctrl = _lookup()
                        fn_ctrl.wrapper_object()
                        break
                    except Exception:
                        fn_ctrl = None
                if fn_ctrl is not None:
                    fn_ctrl.set_focus()
                    fn_ctrl.set_text("")
                    fn_ctrl.type_keys(filename, with_spaces=True)
                else:
                    import pywinauto.keyboard as _kb  # type: ignore
                    _kb.send_keys(filename)
                time.sleep(0.3)
                try:
                    dlg.child_window(title="Open", control_type="Button").click()
                except Exception:
                    try:
                        dlg.child_window(title_re="Open.*", control_type="Button").click()
                    except Exception:
                        import pywinauto.keyboard as _kb  # type: ignore
                        _kb.send_keys("{ENTER}")
                time.sleep(0.8)
                return f"Opened file: {filename}"
            except Exception as dlg_exc:
                return f"Open dialog interaction error: {dlg_exc}"

        # ── save_as ──────────────────────────────────────────────────────────
        elif action_type == "save_as":
            filename = (
                args.get("filename")
                or args.get("name")
                or args.get("file")
                or "output.txt"
            )
            win.set_focus()
            time.sleep(0.2)

            # Trigger Save As — try multiple methods in order
            triggered = False
            for _trigger in (
                lambda: win.menu_select("File->Save As"),       # classic Win32
                lambda: win.type_keys("^+s"),                   # Ctrl+Shift+S (Win11 Notepad)
                lambda: win.type_keys("%fa"),                   # Alt+F A
                lambda: win.type_keys("{VK_MENU}fa"),           # same via VK
            ):
                try:
                    _trigger()
                    triggered = True
                    break
                except Exception:
                    pass
            if not triggered:
                return "Could not trigger Save As dialog"

            time.sleep(1.2)  # Wait for the shell dialog to appear

            # Locate the Save As dialog — try multiple title patterns
            dlg = None
            for _title in ("Save As", "Save as", "Save As.*", ".*Save.*"):
                try:
                    dlg = app.window(title_re=_title)
                    dlg.wait("visible", timeout=4)
                    break
                except Exception:
                    dlg = None

            if dlg is None:
                try:
                    dlg = app.top_window()
                except Exception as exc:
                    return f"Save As dialog not found: {exc}"

            try:
                fn_ctrl = None
                for _lookup in (
                    lambda: dlg.child_window(title="File name:", control_type="Edit"),
                    lambda: dlg.child_window(class_name="Edit", found_index=0),
                    lambda: dlg.child_window(control_type="ComboBox").child_window(class_name="Edit"),
                    lambda: dlg.child_window(auto_id="1001"),
                ):
                    try:
                        fn_ctrl = _lookup()
                        fn_ctrl.wrapper_object()
                        break
                    except Exception:
                        fn_ctrl = None

                if fn_ctrl is not None:
                    fn_ctrl.set_focus()
                    fn_ctrl.set_text("")
                    fn_ctrl.type_keys(filename, with_spaces=True)
                else:
                    import pywinauto.keyboard as _kb  # type: ignore
                    _kb.send_keys(filename)

                time.sleep(0.3)
                try:
                    dlg.child_window(title="Save", control_type="Button").click()
                except Exception:
                    try:
                        dlg.child_window(title_re="Save.*", control_type="Button").click()
                    except Exception:
                        import pywinauto.keyboard as _kb  # type: ignore
                        _kb.send_keys("{ENTER}")

                time.sleep(0.6)
                return f"Saved as: {filename}"
            except Exception as dlg_exc:
                return f"Save As dialog interaction error: {dlg_exc}"

        # ── close_app ────────────────────────────────────────────────────────
        elif action_type == "close_app":
            with _GUI_APP_LOCK:
                _GUI_APP_INSTANCES.pop(exe_path, None)
            try:
                app.kill()
            except Exception:
                pass
            return "Application closed"

        # ── button_click ─────────────────────────────────────────────────────
        elif action_type == "button_click":
            button_name = execution.get("button_name", "")
            if not button_name:
                return "No button_name specified for button_click action"
            win.set_focus()
            time.sleep(0.15)
            # Try UIA by title first (most reliable for WinUI3 Calculator)
            for _lookup in (
                lambda: win.child_window(title=button_name, control_type="Button"),
                lambda: win.child_window(title_re=f"(?i)^{re.escape(button_name)}$", control_type="Button"),
                lambda: win.child_window(title=button_name),
            ):
                try:
                    btn = _lookup()
                    btn.click_input()
                    time.sleep(0.25)  # WinUI3 needs time between clicks; 0.1 drops rapid repeats
                    return f"Clicked button: {button_name!r}"
                except Exception:
                    pass
            return f"Button not found: {button_name!r}"

        # ── keyboard_shortcut ─────────────────────────────────────────────────
        elif action_type == "keyboard_shortcut":
            keys = execution.get("keys") or execution.get("kb_shortcut", "")
            if not keys:
                return "No keys specified for keyboard_shortcut action"
            win.set_focus()
            time.sleep(0.2)
            win.type_keys(keys)
            time.sleep(0.3)
            return f"Sent keys: {keys}"

        # ── menu_click ───────────────────────────────────────────────────────
        elif action_type == "menu_click":
            if not menu_path:
                return "No menu_path specified for menu_click action"
            # For WinUI3/MSIX apps the Win32 menu bar is absent; use keyboard
            # shortcuts for well-known actions rather than menu_select.
            gui_backend = execution.get("gui_backend", "win32")
            kb_shortcut = execution.get("kb_shortcut")
            if gui_backend == "uia" and kb_shortcut:
                win.set_focus()
                time.sleep(0.2)
                win.type_keys(kb_shortcut)
                time.sleep(0.3)
                sep = " -> "
                return f"Clicked menu: {sep.join(menu_path)}"
            # Classic Win32: try menu_select first, keyboard fallback second
            _KB_FALLBACKS = {
                ("File", "New"):        "^n",
                ("File", "New window"): "^+n",
                ("File", "Open"):       "^o",
                ("File", "Save"):       "^s",
                ("File", "Save As"):    "^+s",
                ("File", "Print"):      "^p",
                ("Edit", "Undo"):       "^z",
                ("Edit", "Redo"):       "^y",
                ("Edit", "Cut"):        "^x",
                ("Edit", "Copy"):       "^c",
                ("Edit", "Paste"):      "^v",
                ("Edit", "Select All"): "^a",
                ("Edit", "Find"):       "^f",
                ("Edit", "Replace"):    "^h",
            }
            try:
                menu_str = "->".join(menu_path)  # pywinauto separator
                win.menu_select(menu_str)
                time.sleep(0.3)
                sep = " -> "
                return f"Clicked menu: {sep.join(menu_path)}"
            except Exception as menu_exc:
                # Graceful fallback: keyboard shortcut if we know it
                kb = _KB_FALLBACKS.get(tuple(menu_path))
                if kb:
                    win.set_focus()
                    time.sleep(0.2)
                    win.type_keys(kb)
                    time.sleep(0.3)
                    sep = " -> "
                    return f"Clicked menu (kb fallback): {sep.join(menu_path)}"
                return f"Menu click error for {menu_path}: {menu_exc}"

        else:
            return f"Unknown GUI action_type: {action_type!r}"

    except Exception as exc:
        # If something went wrong (window died, etc.) clear the cached instance
        with _GUI_APP_LOCK:
            _GUI_APP_INSTANCES.pop(exe_path, None)
        return f"GUI action error ({action_type}): {exc}"


# ── Routes ──────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/tools", methods=["GET"])
def list_tools():
    return jsonify([inv["name"] for inv in INVOCABLES])


@app.route("/invoke", methods=["POST"])
def invoke():
    body = request.json or {}
    name = body.get("tool", "")
    args = body.get("args", {})
    result, error = _execute_tool(name, args)
    return jsonify({"result": result, "error": error})


@app.route("/chat", methods=["POST"])
def chat():
    body = request.json or {}
    history = body.get("history", [])          # [{role, content}, ...]
    user_message = body.get("message", "").strip()
    if not user_message:
        return jsonify({"error": "message is required"}), 400

    client = OpenAI(
        api_key=os.getenv("OPENAI_API_KEY"),
        base_url=os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1",
    )
    model = os.getenv("OPENAI_DEPLOYMENT", "gpt-4o-mini")

    system_prompt = (
        "You are a helpful assistant with access to binary tools from __COMPONENT_NAME__. "
        "When a user asks you to perform a multi-step task, issue as many tool calls as possible in a single response — only wait for results when a later step strictly depends on the output of an earlier one. "
        "Explain what each tool does and report results clearly."
    )

    messages = (
        [{"role": "system", "content": system_prompt}]
        + history
        + [{"role": "user", "content": user_message}]
    )

    _tools = _retrieve_tools(user_message, client) or None
    response = client.chat.completions.create(
        model=model,
        messages=messages,
        tools=_tools or None,
        tool_choice="auto" if _tools else "none",
    )

    msg = response.choices[0].message
    tool_outputs = []

    if msg.tool_calls:
        tool_messages = []
        for tc in msg.tool_calls:
            fn_name = tc.function.name
            try:
                fn_args = json.loads(tc.function.arguments)
            except json.JSONDecodeError:
                fn_args = {}
            result, error = _execute_tool(fn_name, fn_args)
            tool_outputs.append({
                "name": fn_name, "args": fn_args,
                "result": result, "error": error,
            })
            tool_messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result,
            })

        # Feed tool results back for a natural-language summary.
        messages.append(msg.model_dump(exclude_none=True))
        messages.extend(tool_messages)
        followup = client.chat.completions.create(model=model, messages=messages)
        reply = followup.choices[0].message.content or ""
    else:
        reply = msg.content or ""

    # Return updated history (strip system message so the client can replay it).
    updated_history = messages[1:]

    return jsonify({
        "reply": reply,
        "tool_outputs": tool_outputs,
        "updated_history": updated_history,
    })


@app.route("/download/invocables")
def download_invocables():
    """Serve the raw invocables list as a JSON download."""
    resp = app.response_class(
        response=json.dumps(INVOCABLES, indent=2),
        status=200,
        mimetype="application/json",
    )
    resp.headers["Content-Disposition"] = "attachment; filename=selected-invocables.json"
    return resp


if __name__ == "__main__":
    app.run(port=5000, debug=False)
'''

# ---------------------------------------------------------------------------
# Chat UI template
# ---------------------------------------------------------------------------
HTML_TEMPLATE = r'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>MCP Factory — __COMPONENT_NAME__</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  :root {
    --bg: #0d1117; --surface: #161b22; --border: #30363d;
    --accent: #58a6ff; --user-bg: #1f6feb; --tool-bg: #1a3a1a;
    --text: #c9d1d9; --muted: #8b949e; --error: #f85149;
    --radius: 10px; --font: system-ui, -apple-system, sans-serif;
  }
  body { background: var(--bg); color: var(--text); font-family: var(--font);
         display: flex; flex-direction: column; height: 100dvh; }
  header { background: var(--surface); border-bottom: 1px solid var(--border);
           padding: 12px 20px; display: flex; align-items: center;
           justify-content: space-between; flex-shrink: 0; }
  header h1 { font-size: 1rem; font-weight: 600; color: var(--accent); }
  header span { font-size: .8rem; color: var(--muted); }
  #download-btn {
    background: var(--surface); border: 1px solid var(--border); color: var(--accent);
    padding: 5px 14px; border-radius: 6px; cursor: pointer; font-size: .8rem;
    text-decoration: none; transition: background .15s;
  }
  #download-btn:hover { background: var(--border); }
  #chat-window { flex: 1; overflow-y: auto; padding: 20px;
                 display: flex; flex-direction: column; gap: 12px; }
  .bubble { max-width: 75%; padding: 10px 14px; border-radius: var(--radius);
            line-height: 1.5; font-size: .9rem; white-space: pre-wrap; word-break: break-word; }
  .bubble.user { background: var(--user-bg); align-self: flex-end;
                 border-bottom-right-radius: 3px; }
  .bubble.assistant { background: var(--surface); border: 1px solid var(--border);
                       align-self: flex-start; border-bottom-left-radius: 3px; }
  .bubble.error { background: #2d1117; border: 1px solid var(--error);
                   color: var(--error); align-self: flex-start; }
  .tool-block { background: var(--tool-bg); border: 1px solid #2e5c2e;
                border-radius: var(--radius); padding: 10px 14px; font-size: .8rem;
                align-self: flex-start; max-width: 75%; }
  .tool-block .tool-name { color: #3fb950; font-weight: 600; margin-bottom: 4px; }
  .tool-block .tool-args { color: var(--muted); margin-bottom: 4px; }
  .tool-block .tool-result { color: var(--text); white-space: pre-wrap; word-break: break-word; }
  .typing { align-self: flex-start; color: var(--muted); font-size: .85rem;
            padding: 6px 14px; animation: blink .9s infinite; }
  @keyframes blink { 0%,100% { opacity:.4 } 50% { opacity:1 } }
  footer { background: var(--surface); border-top: 1px solid var(--border);
           padding: 12px 16px; display: flex; gap: 10px; flex-shrink: 0; }
  #msg-input { flex: 1; background: var(--bg); border: 1px solid var(--border);
               color: var(--text); border-radius: 8px; padding: 10px 14px;
               font-size: .9rem; resize: none; height: 44px; line-height: 1.4;
               outline: none; transition: border .15s; }
  #msg-input:focus { border-color: var(--accent); }
  #send-btn { background: var(--accent); color: #000; border: none; border-radius: 8px;
              padding: 0 20px; font-size: .9rem; font-weight: 600; cursor: pointer;
              transition: opacity .15s; }
  #send-btn:disabled { opacity: .4; cursor: not-allowed; }
  ::-webkit-scrollbar { width: 6px; } ::-webkit-scrollbar-track { background: transparent; }
  ::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }
</style>
</head>
<body>
<header>
  <h1>MCP Factory &mdash; __COMPONENT_NAME__</h1>
  <div style="display:flex;align-items:center;gap:16px">
    <span id="model-label">model: gpt-4o-mini</span>
    <a id="download-btn" href="/download/invocables" download="selected-invocables.json">
      &#8595; Download invocables.json
    </a>
  </div>
</header>
<div id="chat-window">
  <div class="bubble assistant">
    Hi! I have access to the tools exported by <strong>__COMPONENT_NAME__</strong>.
    Ask me to call a function, describe what one does, or list available tools.
  </div>
</div>
<footer>
  <textarea id="msg-input" placeholder="Ask me to call a function…" rows="1"></textarea>
  <button id="send-btn">Send</button>
</footer>

<script>
  const chatWindow = document.getElementById('chat-window');
  const msgInput   = document.getElementById('msg-input');
  const sendBtn    = document.getElementById('send-btn');
  let history = [];

  function scrollToBottom() {
    chatWindow.scrollTop = chatWindow.scrollHeight;
  }

  function appendBubble(cls, text) {
    const d = document.createElement('div');
    d.className = `bubble ${cls}`;
    d.textContent = text;
    chatWindow.appendChild(d);
    scrollToBottom();
    return d;
  }

  function appendToolBlock(t) {
    const d = document.createElement('div');
    d.className = 'tool-block';
    const argsStr = Object.keys(t.args || {}).length
      ? JSON.stringify(t.args, null, 2) : '(no args)';
    d.innerHTML =
      `<div class="tool-name">&#10551; ${t.name}</div>` +
      `<div class="tool-args">args: ${argsStr}</div>` +
      `<div class="tool-result">${t.result}</div>`;
    chatWindow.appendChild(d);
    scrollToBottom();
  }

  function setLoading(v) {
    sendBtn.disabled = v;
    msgInput.disabled = v;
  }

  async function sendMessage() {
    const text = msgInput.value.trim();
    if (!text) return;
    msgInput.value = '';
    msgInput.style.height = '44px';

    appendBubble('user', text);
    setLoading(true);

    const typing = document.createElement('div');
    typing.className = 'typing';
    typing.textContent = 'Thinking…';
    chatWindow.appendChild(typing);
    scrollToBottom();

    try {
      const res = await fetch('/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text, history }),
      });
      const data = await res.json();
      chatWindow.removeChild(typing);

      if (data.error) {
        appendBubble('error', 'Error: ' + data.error);
      } else {
        (data.tool_outputs || []).forEach(t => appendToolBlock(t));
        appendBubble('assistant', data.reply);
        history = data.updated_history || history;
      }
    } catch (err) {
      chatWindow.removeChild(typing);
      appendBubble('error', 'Network error: ' + err.message);
    }

    setLoading(false);
    msgInput.focus();
  }

  sendBtn.addEventListener('click', sendMessage);
  msgInput.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
  });
  msgInput.addEventListener('input', () => {
    msgInput.style.height = 'auto';
    msgInput.style.height = Math.min(msgInput.scrollHeight, 120) + 'px';
  });

  // Fetch and display model from server env
  fetch('/tools').then(r => r.json()).then(tools => {
    document.getElementById('model-label').textContent =
      `${tools.length} tools loaded`;
  }).catch(() => {});
</script>
</body>
</html>
'''

ENV_EXAMPLE = """\
# Copy to .env and fill in your credentials.
# Works with both api.openai.com (set OPENAI_BASE_URL=) and Azure OpenAI.

OPENAI_API_KEY=sk-...
OPENAI_BASE_URL=
OPENAI_DEPLOYMENT=gpt-4o-mini
"""

REQUIREMENTS = """\
flask>=3.0
openai>=1.0
python-dotenv>=1.0
"""


# ---------------------------------------------------------------------------
# Vendored error-enrichment modules
#
# Both generated servers (Flask and MCP SDK) import build_error_payload from a
# vendored copy of api/error_enrichment.py (which itself imports from
# api/sentinel_codes.py).  We read those two source files at generation time
# and strip the `api.` import prefix so the copies work standalone next to
# mcp_server.py / server.py.  Read once at module load so generate_* functions
# don't hit disk per-call.
# ---------------------------------------------------------------------------

def _read_vendored(rel_path: str) -> str:
    src = os.path.join(os.path.dirname(__file__), "..", "..", rel_path)
    src = os.path.abspath(src)
    with open(src, "r", encoding="utf-8") as fh:
        text = fh.read()
    # Drop the api. package prefix so the vendored copy imports its sibling
    # module by bare name (the generated server sits at the import root).
    return text.replace("from api.sentinel_codes", "from sentinel_codes")


try:
    ERROR_ENRICHMENT_PY = _read_vendored("api/error_enrichment.py")
    SENTINEL_CODES_PY = _read_vendored("api/sentinel_codes.py")
except FileNotFoundError:
    # Fallback when generator is invoked from a packaged install without the
    # source tree — produce minimal stubs that never crash but return None.
    ERROR_ENRICHMENT_PY = (
        "def build_error_payload(*a, **kw):\n    return None\n"
    )
    SENTINEL_CODES_PY = (
        "SENTINEL_DEFAULTS = {}\n"
        "COMMON_WIN32_ERRORS = {}\n"
        "COMMON_HRESULTS = {}\n"
        "COMMON_NTSTATUS = {}\n"
        "def classify_common_result_code(code):\n    return None\n"
    )

# ---------------------------------------------------------------------------
# MCP SDK server template (P1)
# Emits a True MCP-protocol server using the official mcp Python SDK.
# Supports stdio transport (VS Code Copilot, Claude Desktop) and SSE.
# Each invocable is registered as an @mcp.tool().
# ---------------------------------------------------------------------------
MCP_SERVER_TEMPLATE = r'''# Generated MCP SDK server — __COMPONENT_NAME__
# True MCP protocol (Model Context Protocol spec) — stdio + SSE transports.
#
# Run (stdio — VS Code / Claude Desktop):
#   pip install -r mcp_requirements.txt
#   python mcp_server.py
#
# Run (SSE — browser / HTTP client):
#   python mcp_server.py --transport sse --port 8080
#
# VS Code: open mcp.json, which auto-configures Copilot Chat to use this server.

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

# ---------------------------------------------------------------------------
# Bootstrap: install mcp package if missing (useful when running generated
# server directly on a fresh machine).
# ---------------------------------------------------------------------------
try:
    from mcp.server.fastmcp import FastMCP  # type: ignore
except ImportError:
    import subprocess as _sp
    _sp.check_call([sys.executable, "-m", "pip", "install", "mcp", "-q"])
    from mcp.server.fastmcp import FastMCP  # type: ignore

# Vendored error enrichment (copied next to this file at generation time).
try:
    from error_enrichment import build_error_payload as _build_error_payload
except Exception:
    def _build_error_payload(*_a, **_kw):
        return None

# ---------------------------------------------------------------------------
# Invocable registry (injected by generator)
# ---------------------------------------------------------------------------
INVOCABLES = json.loads(r"""__INVOCABLES_JSON__""")
INVOCABLE_MAP = {inv["name"]: inv for inv in INVOCABLES}


def _format_error(function_name, raw_result=None, trace=None, exception=None, inv=None):
    """Return a human + ```json payload``` string classifying the failure."""
    findings = []
    if isinstance(inv, dict):
        _summary = inv.get("findings_summary") or {}
        for entry in (_summary.get("working_calls") or []):
            findings.append({
                "working_call": entry.get("args"),
                "confidence": entry.get("confidence"),
                "recorded_at": entry.get("recorded_at"),
                "status": "success",
            })
    payload = _build_error_payload(function_name, raw_result, trace, exception, findings, None)
    if not payload:
        return str(raw_result if raw_result is not None else exception or "")
    human = payload.get("human") or f"{function_name} failed."
    return f"{human}\n\n```json\n{json.dumps(payload, indent=2, default=str)}\n```"

# ---------------------------------------------------------------------------
# Type maps for ctypes dispatch
# ---------------------------------------------------------------------------
_CTYPES_RESTYPE = {
    "void": None, "bool": ctypes.c_bool, "int": ctypes.c_int,
    "unsigned": ctypes.c_uint, "unsigned int": ctypes.c_uint,
    "long": ctypes.c_long, "unsigned long": ctypes.c_ulong,
    "size_t": ctypes.c_size_t, "float": ctypes.c_float,
    "double": ctypes.c_double, "char*": ctypes.c_char_p,
    "const char*": ctypes.c_char_p, "char *": ctypes.c_char_p,
    "const char *": ctypes.c_char_p,
}
_CTYPES_ARGTYPE = {
    "int": ctypes.c_int, "unsigned": ctypes.c_uint,
    "unsigned int": ctypes.c_uint, "long": ctypes.c_long,
    "unsigned long": ctypes.c_ulong, "size_t": ctypes.c_size_t,
    "float": ctypes.c_float, "double": ctypes.c_double,
    "bool": ctypes.c_bool, "string": ctypes.c_char_p,
    "str": ctypes.c_char_p, "char*": ctypes.c_char_p,
    "const char*": ctypes.c_char_p, "char *": ctypes.c_char_p,
    "const char *": ctypes.c_char_p,
}


def _resolve_dll_path(raw: str) -> str:
    p = Path(raw)
    if p.is_absolute() and p.exists():
        return str(p)
    local = Path(__file__).resolve().parent / raw
    if local.exists():
        return str(local)
    return raw


def _execute_dll(inv: dict, execution: dict, args: dict) -> str:
    dll_path = _resolve_dll_path(execution.get("dll_path", ""))
    func_name = execution.get("function_name", "")
    ret_str = (inv.get("return_type") or (inv.get("signature") or {}).get("return_type", "unknown") or "unknown").strip()
    restype = _CTYPES_RESTYPE.get(ret_str.lower(), ctypes.c_size_t)
    params = list(inv.get("parameters") or [])
    if not params:
        sig_str = (inv.get("signature") or {}).get("parameters", "")
        if sig_str:
            for chunk in sig_str.split(","):
                tokens = chunk.strip().split()
                if len(tokens) >= 2:
                    raw_type = " ".join(tokens[:-1]).lower().strip("*").rstrip()
                    pname = tokens[-1].lstrip("*")
                    params.append({"name": pname, "type": raw_type})
    try:
        lib = ctypes.CDLL(dll_path)
        fn = getattr(lib, func_name)
        fn.restype = restype
        c_args = []
        if params and args:
            for p in params:
                pname = p.get("name", "")
                ptype = p.get("type", "string").lower().strip("*").rstrip()
                val = args.get(pname)
                if val is None:
                    continue
                atype = _CTYPES_ARGTYPE.get(ptype, ctypes.c_char_p)
                c_args.append(ctypes.c_char_p(str(val).encode()) if atype == ctypes.c_char_p else atype(int(val)))
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
        if restype == ctypes.c_char_p and isinstance(result, bytes):
            return f"Returned: {result.decode(errors='replace')}"
        # Classify numeric returns so sentinel codes reach the MCP client as
        # human + structured JSON instead of a bare "Returned: 4294967295".
        _payload = _build_error_payload(
            func_name or inv.get("name", ""),
            result,
            {"backend": "dll", "raw_result": result, "exception": None},
            None,
            [],
            None,
        )
        if _payload:
            return f"{_payload['human']}\n\n```json\n{json.dumps(_payload, indent=2, default=str)}\n```"
        return f"Returned: {result}"
    except Exception as exc:
        return _format_error(
            func_name or inv.get("name", ""),
            trace={"backend": "dll", "exception": str(exc)},
            exception=str(exc),
            inv=inv,
        )


def _execute_cli(execution: dict, name: str, args: dict, inv: dict | None = None) -> str:
    target = execution.get("executable_path") or execution.get("target_path") or execution.get("dll_path", "")
    if not target:
        return _format_error(
            name,
            trace={"backend": "cli", "exception": "no executable path"},
            exception="no executable path configured",
            inv=inv,
        )
    exe_stem = Path(target).stem.lower()
    if exe_stem == name.lower():
        try:
            if sys.platform == "win32":
                subprocess.Popen([target], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            else:
                subprocess.Popen([target])
            return f"Launched {Path(target).name}"
        except Exception as exc:
            return _format_error(
                name,
                trace={"backend": "cli", "action": "launch", "exception": str(exc)},
                exception=str(exc),
                inv=inv,
            )
    cmd = [target, name] + [str(v) for v in args.values()]
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10, creationflags=creation_flags)
        if r.returncode == 0:
            return r.stdout or r.stderr or f"exit_code={r.returncode}"
        return _format_error(
            name,
            trace={"backend": "cli", "exit_code": r.returncode,
                   "stderr_excerpt": (r.stderr or "")[:200], "exception": "nonzero exit"},
            exception=f"exit_code={r.returncode}",
            inv=inv,
        )
    except subprocess.TimeoutExpired:
        return _format_error(
            name,
            trace={"backend": "cli", "timeout_hit": True, "exception": "TimeoutExpired"},
            exception="TimeoutExpired",
            inv=inv,
        )
    except Exception as exc:
        return _format_error(
            name,
            trace={"backend": "cli", "exception": str(exc)},
            exception=str(exc),
            inv=inv,
        )


def _execute_tool(inv: dict, args: dict) -> str:
    name = inv.get("name", "")
    execution = inv.get("execution") or inv.get("mcp", {}).get("execution", {})
    method = execution.get("method", "")
    if method == "dll_import":
        return _execute_dll(inv, execution, args)
    return _execute_cli(execution, name, args, inv)


# ---------------------------------------------------------------------------
# MCP server — one @mcp.tool() per invocable
# ---------------------------------------------------------------------------
mcp = FastMCP("__COMPONENT_NAME__")


def _make_tool_fn(inv: dict):
    """Return a callable suitable for FastMCP.tool() registration."""
    _name = inv["name"]
    _desc = (inv.get("description") or inv.get("doc") or f"Invoke {_name} from __COMPONENT_NAME__").strip()
    _params = [p for p in (inv.get("parameters") or []) if p.get("name")]

    if not _params:
        def _tool_fn() -> str:
            return _execute_tool(inv, {})
    else:
        # Build a function with explicit keyword args so FastMCP can infer the JSON schema.
        param_list = ", ".join(f"{p['name']}: str = ''" for p in _params)
        args_dict  = "{" + ", ".join(f'"{p["name"]}": {p["name"]}' for p in _params) + "}"
        src = (
            f"def _tool_fn({param_list}) -> str:\n"
            f"    return _execute_tool(_inv, {args_dict})\n"
        )
        ns: dict = {"_execute_tool": _execute_tool, "_inv": inv}
        exec(src, ns)  # noqa: S102
        _tool_fn = ns["_tool_fn"]

    _tool_fn.__name__ = _name
    _tool_fn.__qualname__ = _name
    _tool_fn.__doc__ = _desc
    return _tool_fn


for _inv in INVOCABLES:
    _fn = _make_tool_fn(_inv)
    mcp.tool(name=_fn.__name__, description=_fn.__doc__)(_fn)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="MCP server for __COMPONENT_NAME__")
    parser.add_argument("--transport", default="stdio", choices=["stdio", "sse"],
                        help="Transport type (default: stdio)")
    parser.add_argument("--port", type=int, default=8080,
                        help="Port for SSE transport (default: 8080)")
    args_ns = parser.parse_args()
    if args_ns.transport == "sse":
        mcp.run(transport="sse", port=args_ns.port)
    else:
        mcp.run(transport="stdio")
'''

MCP_JSON_TEMPLATE = """\
{
  "servers": {
    "__COMPONENT_NAME__": {
      "command": "python",
      "args": ["mcp_server.py"],
      "cwd": "${workspaceFolder}/generated/__COMPONENT_NAME__",
      "transportType": "stdio",
      "env": {}
    }
  }
}
"""

MCP_REQUIREMENTS = """\
mcp>=1.0
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _inject(template: str, component_name: str, invocables_json: str) -> str:
    """Replace all placeholder tokens in a template string."""
    return (
        template
        .replace("__COMPONENT_NAME__", component_name)
        .replace("__INVOCABLES_JSON__", invocables_json)
    )


def _write(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)
    print(f"  wrote {path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    if not os.path.exists(INPUT_PATH):
        raise FileNotFoundError(f"Missing {INPUT_PATH}")

    with open(INPUT_PATH, "r", encoding="utf-8") as fh:
        data = json.load(fh)

    component_name = data["component_name"]
    invocables = data["selected_invocables"]

    project_path = os.path.join(OUTPUT_BASE, component_name)
    static_path = os.path.join(project_path, "static")
    os.makedirs(static_path, exist_ok=True)

    invocables_json = json.dumps(invocables, indent=2)

    print(f"Generating MCP server for '{component_name}' ({len(invocables)} tools) …")

    # ── Legacy Flask chat-UI server (backward compat) ──────────────────────
    _write(
        os.path.join(project_path, "server.py"),
        _inject(SERVER_TEMPLATE, component_name, invocables_json),
    )
    _write(
        os.path.join(static_path, "index.html"),
        _inject(HTML_TEMPLATE, component_name, invocables_json),
    )
    _write(os.path.join(project_path, "requirements.txt"), REQUIREMENTS)
    _write(os.path.join(project_path, ".env.example"), ENV_EXAMPLE)

    # ── True MCP SDK server (P1) ───────────────────────────────────────────
    _write(
        os.path.join(project_path, "mcp_server.py"),
        _inject(MCP_SERVER_TEMPLATE, component_name, invocables_json),
    )
    _write(os.path.join(project_path, "mcp_requirements.txt"), MCP_REQUIREMENTS)
    _write(
        os.path.join(project_path, "mcp.json"),
        _inject(MCP_JSON_TEMPLATE, component_name, invocables_json),
    )

    # ── Vendored error-enrichment modules (shared by server.py and
    # mcp_server.py so both surface the same classified failure payload).
    _write(os.path.join(project_path, "error_enrichment.py"), ERROR_ENRICHMENT_PY)
    _write(os.path.join(project_path, "sentinel_codes.py"), SENTINEL_CODES_PY)

    print(f"\nDone!  cd {project_path}")
    print("  # Flask chat UI:")
    print("  pip install -r requirements.txt")
    print("  cp .env.example .env   # then fill in your API key")
    print("  python server.py")
    print("  open http://localhost:5000")
    print()
    print("  # True MCP SDK server (VS Code Copilot / Claude Desktop):")
    print("  pip install -r mcp_requirements.txt")
    print("  python mcp_server.py         # stdio transport")
    print("  python mcp_server.py --transport sse --port 8080  # SSE transport")
    print("  # Then in VS Code: add mcp.json to .vscode/ or workspace settings")


# ---------------------------------------------------------------------------
# Public API — called from api/main.py /api/generate (P1)
# ---------------------------------------------------------------------------

def generate_mcp_sdk_artifacts(
    component_name: str,
    invocables: list,
    output_base: str = OUTPUT_BASE,
) -> dict:
    """Generate mcp_server.py + mcp.json for the given component.

    Returns a dict with keys:
        mcp_server_py  – content of mcp_server.py (str)
        mcp_json       – content of mcp.json (str)
        mcp_requirements_txt – content of mcp_requirements.txt (str)
    """
    invocables_json = json.dumps(invocables, indent=2)
    return {
        "mcp_server_py": _inject(MCP_SERVER_TEMPLATE, component_name, invocables_json),
        "mcp_json": _inject(MCP_JSON_TEMPLATE, component_name, invocables_json),
        "mcp_requirements_txt": MCP_REQUIREMENTS,
        "error_enrichment_py": ERROR_ENRICHMENT_PY,
        "sentinel_codes_py": SENTINEL_CODES_PY,
    }


if __name__ == "__main__":
    main()
