# Generated MCP server — notepad
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

load_dotenv(dotenv_path=Path(__file__).parent / ".env")
# openai 2.x reads OPENAI_BASE_URL directly from os.environ;
# if it's set to "" (e.g. by a system env var) it overrides base_url=None.
# Remove it entirely so the library defaults to api.openai.com.
if not os.environ.get("OPENAI_BASE_URL"):
    os.environ.pop("OPENAI_BASE_URL", None)

app = Flask(__name__, static_folder="static")

# ── Tool registry (injected by generator) ──────────────────────────────────
INVOCABLES = json.loads(r"""[
  {
    "name": "type_text",
    "kind": "gui_action",
    "confidence": "high",
    "description": "Type the given text into the active focused control (e.g. the text editor area of Notepad). Preserves spaces and newlines.",
    "return_type": "unknown",
    "parameters": [
      {
        "name": "text",
        "type": "string",
        "required": true,
        "description": "str"
      }
    ],
    "execution": {
      "method": "gui_action",
      "exe_path": "C:\\Windows\\notepad.exe",
      "action_type": "type_text",
      "menu_path": []
    }
  },
  {
    "name": "save_as",
    "kind": "gui_action",
    "confidence": "medium",
    "description": "Open the File \u00e2\u2020\u2019 Save As dialog and save the document with the specified filename. Supports relative and absolute paths.",
    "return_type": "unknown",
    "parameters": [
      {
        "name": "filename",
        "type": "string",
        "required": true,
        "description": "str"
      }
    ],
    "execution": {
      "method": "gui_action",
      "exe_path": "C:\\Windows\\notepad.exe",
      "action_type": "save_as",
      "menu_path": [
        "File",
        "Save As"
      ]
    }
  },
  {
    "name": "get_text",
    "kind": "gui_action",
    "confidence": "medium",
    "description": "Return the full text content of the main editing area (e.g. all text currently in the Notepad window).",
    "return_type": "unknown",
    "parameters": [],
    "execution": {
      "method": "gui_action",
      "exe_path": "C:\\Windows\\notepad.exe",
      "action_type": "get_text",
      "menu_path": []
    }
  },
  {
    "name": "close_app",
    "kind": "gui_action",
    "confidence": "high",
    "description": "Close the application. Sends Alt+F4 and discards unsaved changes.",
    "return_type": "unknown",
    "parameters": [],
    "execution": {
      "method": "gui_action",
      "exe_path": "C:\\Windows\\notepad.exe",
      "action_type": "close_app",
      "menu_path": []
    }
  },
  {
    "name": "file_new",
    "kind": "gui_action",
    "confidence": "high",
    "description": "Create a new empty document (Ctrl+N). Clears the editing area.",
    "return_type": "unknown",
    "parameters": [],
    "execution": {
      "method": "gui_action",
      "exe_path": "C:\\Windows\\notepad.exe",
      "action_type": "keyboard_shortcut",
      "keys": "^n"
    }
  },
  {
    "name": "file_new_window",
    "kind": "gui_action",
    "confidence": "low",
    "description": "Open a new Notepad window (File > New window).",
    "return_type": "unknown",
    "parameters": [],
    "execution": {
      "method": "gui_action",
      "exe_path": "C:\\Windows\\notepad.exe",
      "action_type": "menu_click",
      "menu_path": [
        "File",
        "New window"
      ]
    }
  },
  {
    "name": "file_open",
    "kind": "gui_action",
    "confidence": "high",
    "description": "Open an existing file by name via the File > Open dialog. Pass the filename (e.g. 'demo.txt' or full path). The file replaces the current Notepad buffer.",
    "return_type": "unknown",
    "parameters": [
      {"name": "filename", "type": "string", "description": "Filename or full path to open"}
    ],
    "execution": {
      "method": "gui_action",
      "exe_path": "C:\\Windows\\notepad.exe",
      "action_type": "open_file",
      "menu_path": []
    }
  },
  {
    "name": "file_save",
    "kind": "gui_action",
    "confidence": "high",
    "description": "Save the current document (Ctrl+S).",
    "return_type": "unknown",
    "parameters": [],
    "execution": {
      "method": "gui_action",
      "exe_path": "C:\\Windows\\notepad.exe",
      "action_type": "keyboard_shortcut",
      "keys": "^s"
    }
  },
  {
    "name": "file_page_setup",
    "kind": "gui_action",
    "confidence": "low",
    "description": "Open the Page Setup dialog (File > Page setup).",
    "return_type": "unknown",
    "parameters": [],
    "execution": {
      "method": "gui_action",
      "exe_path": "C:\\Windows\\notepad.exe",
      "action_type": "menu_click",
      "menu_path": [
        "File",
        "Page setup"
      ]
    }
  },
  {
    "name": "file_print",
    "kind": "gui_action",
    "confidence": "medium",
    "description": "Open the Print dialog (File > Print).",
    "return_type": "unknown",
    "parameters": [],
    "execution": {
      "method": "gui_action",
      "exe_path": "C:\\Windows\\notepad.exe",
      "action_type": "menu_click",
      "menu_path": [
        "File",
        "Print"
      ]
    }
  },
  {
    "name": "edit_undo",
    "kind": "gui_action",
    "confidence": "high",
    "description": "Undo the last action (Ctrl+Z).",
    "return_type": "unknown",
    "parameters": [],
    "execution": {
      "method": "gui_action",
      "exe_path": "C:\\Windows\\notepad.exe",
      "action_type": "keyboard_shortcut",
      "keys": "^z"
    }
  },
  {
    "name": "edit_cut",
    "kind": "gui_action",
    "confidence": "medium",
    "description": "Cut the selected text to clipboard (Edit > Cut).",
    "return_type": "unknown",
    "parameters": [],
    "execution": {
      "method": "gui_action",
      "exe_path": "C:\\Windows\\notepad.exe",
      "action_type": "menu_click",
      "menu_path": [
        "Edit",
        "Cut"
      ]
    }
  },
  {
    "name": "edit_copy",
    "kind": "gui_action",
    "confidence": "medium",
    "description": "Copy the selected text to clipboard (Edit > Copy).",
    "return_type": "unknown",
    "parameters": [],
    "execution": {
      "method": "gui_action",
      "exe_path": "C:\\Windows\\notepad.exe",
      "action_type": "menu_click",
      "menu_path": [
        "Edit",
        "Copy"
      ]
    }
  },
  {
    "name": "edit_paste",
    "kind": "gui_action",
    "confidence": "medium",
    "description": "Paste clipboard content at cursor position (Edit > Paste).",
    "return_type": "unknown",
    "parameters": [],
    "execution": {
      "method": "gui_action",
      "exe_path": "C:\\Windows\\notepad.exe",
      "action_type": "menu_click",
      "menu_path": [
        "Edit",
        "Paste"
      ]
    }
  },
  {
    "name": "edit_find",
    "kind": "gui_action",
    "confidence": "medium",
    "description": "Open the Find dialog (Edit > Find) to search for text.",
    "return_type": "unknown",
    "parameters": [],
    "execution": {
      "method": "gui_action",
      "exe_path": "C:\\Windows\\notepad.exe",
      "action_type": "menu_click",
      "menu_path": [
        "Edit",
        "Find"
      ]
    }
  },
  {
    "name": "edit_replace",
    "kind": "gui_action",
    "confidence": "medium",
    "description": "Open the Find and Replace dialog (Edit > Replace).",
    "return_type": "unknown",
    "parameters": [],
    "execution": {
      "method": "gui_action",
      "exe_path": "C:\\Windows\\notepad.exe",
      "action_type": "menu_click",
      "menu_path": [
        "Edit",
        "Replace"
      ]
    }
  },
  {
    "name": "edit_select_all",
    "kind": "gui_action",
    "confidence": "high",
    "description": "Select all text in the document (Ctrl+A).",
    "return_type": "unknown",
    "parameters": [],
    "execution": {
      "method": "gui_action",
      "exe_path": "C:\\Windows\\notepad.exe",
      "action_type": "keyboard_shortcut",
      "keys": "^a"
    }
  },
  {
    "name": "edit_insert_time_date",
    "kind": "gui_action",
    "confidence": "low",
    "description": "Insert the current time and date at the cursor position (Edit > Insert time/date). F5 shortcut.",
    "return_type": "unknown",
    "parameters": [],
    "execution": {
      "method": "gui_action",
      "exe_path": "C:\\Windows\\notepad.exe",
      "action_type": "menu_click",
      "menu_path": [
        "Edit",
        "Insert time/date"
      ]
    }
  },
  {
    "name": "view_word_wrap",
    "kind": "gui_action",
    "confidence": "medium",
    "description": "Toggle word wrap on or off (View > Word wrap).",
    "return_type": "unknown",
    "parameters": [],
    "execution": {
      "method": "gui_action",
      "exe_path": "C:\\Windows\\notepad.exe",
      "action_type": "menu_click",
      "menu_path": [
        "View",
        "Word wrap"
      ]
    }
  },
  {
    "name": "view_zoom_in",
    "kind": "gui_action",
    "confidence": "medium",
    "description": "Increase the text zoom level (View > Zoom In). Ctrl+Plus shortcut.",
    "return_type": "unknown",
    "parameters": [],
    "execution": {
      "method": "gui_action",
      "exe_path": "C:\\Windows\\notepad.exe",
      "action_type": "menu_click",
      "menu_path": [
        "View",
        "Zoom In"
      ]
    }
  },
  {
    "name": "view_zoom_out",
    "kind": "gui_action",
    "confidence": "medium",
    "description": "Decrease the text zoom level (View > Zoom Out). Ctrl+Minus shortcut.",
    "return_type": "unknown",
    "parameters": [],
    "execution": {
      "method": "gui_action",
      "exe_path": "C:\\Windows\\notepad.exe",
      "action_type": "menu_click",
      "menu_path": [
        "View",
        "Zoom Out"
      ]
    }
  },
  {
    "name": "view_status_bar",
    "kind": "gui_action",
    "confidence": "low",
    "description": "Toggle the status bar visibility (View > Status bar).",
    "return_type": "unknown",
    "parameters": [],
    "execution": {
      "method": "gui_action",
      "exe_path": "C:\\Windows\\notepad.exe",
      "action_type": "menu_click",
      "menu_path": [
        "View",
        "Status bar"
      ]
    }
  }
]""")

INVOCABLE_MAP = {inv["name"]: inv for inv in INVOCABLES}


def _build_openai_functions():
    """Convert the invocable list into OpenAI function-calling schema objects."""
    fns = []
    for inv in INVOCABLES:
        props = {}
        required = []
        for p in inv.get("parameters", []):
            props[p["name"]] = {
                "type": "string",
                "description": p.get("description", ""),
            }
            if p.get("required", False):
                required.append(p["name"])
        fns.append({
            "type": "function",
            "function": {
                "name": inv["name"],
                "description": inv.get("description") or f"Invoke {inv['name']} from notepad",
                "parameters": {
                    "type": "object",
                    "properties": props,
                    "required": required,
                },
            },
        })
    return fns


OPENAI_FUNCTIONS = _build_openai_functions()


# ── Execution helpers ───────────────────────────────────────────────────────

def _execute_tool(name: str, args: dict) -> str:
    inv = INVOCABLE_MAP.get(name)
    if not inv:
        return f"Unknown tool: {name}"
    execution = inv.get("execution", {})
    method = execution.get("method", "")
    if method == "dll_import":
        return _execute_dll(execution, args)
    if method == "gui_action":
        return _execute_gui(execution, name, args)
    return _execute_cli(execution, name, args)


def _execute_dll(execution: dict, args: dict) -> str:
    dll_path = execution.get("dll_path", "")
    func_name = execution.get("function_name", "")
    try:
        lib = ctypes.CDLL(dll_path)
        fn = getattr(lib, func_name)
        fn.restype = ctypes.c_size_t
        # Pass positional string args if any were supplied.
        c_args = [ctypes.c_char_p(v.encode()) for v in args.values() if isinstance(v, str)]
        result = fn(*c_args) if c_args else fn()
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
    from pathlib import Path as _Path
    exe_stem = _Path(target).stem.lower()
    if exe_stem == name.lower():
        try:
            subprocess.Popen([target])
            return f"Launched {_Path(target).name}"
        except Exception as exc:
            return f"CLI error: {exc}"
    cmd = [target, name] + [str(v) for v in args.values()]
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
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


# ── GUI app state (persistent across HTTP requests) ─────────────────────────
# A single-user demo stores one live pywinauto Application per exe_path.
# The app stays alive between calls so "type hello" → "save as test.txt"
# works as two separate HTTP requests against the same window.

_GUI_APP_LOCK = threading.Lock()
_GUI_APP_INSTANCES: dict = {}  # exe_path -> (Application, main_window)


def _ensure_gui_app(exe_path: str):
    """Return (app, win) for exe_path, launching the app if not already running.

    Tries the win32 backend first (classic Win32 apps).  If no window is found,
    automatically retries with the uia backend (WinUI3 / Win11 apps like
    Notepad 10.x).
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
        # Run inside a thread so a hung top_window() gets a hard deadline.
        # STARTUPINFO SW_SHOWMINNOACTIVE keeps the window minimised in taskbar.
        import concurrent.futures as _cf

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
                # Prefer a HWND whose title looks like the app (more robust
                # when unrelated windows also pop up at the same moment).
                exe_stem = Path(exe_path).stem.lower()
                hwnd = None
                for _h in new_hwnds:
                    _t = _w32gui.GetWindowText(_h).lower()
                    if exe_stem in _t or "untitled" in _t or "new tab" in _t:
                        hwnd = _h
                        break
                hwnd = hwnd or next(iter(new_hwnds))
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
        menu_click  - navigate a menu path, e.g. ["File", "Save As"]
        type_text   - type text into the active edit control
        get_text    - return text content of the main edit control
        save_as     - open File→Save As dialog and save with given filename
        close_app   - kill the application process
    """
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

        # ── open_file ─────────────────────────────────────────────────────────
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
                # Last resort — use the most-recently-opened top window
                try:
                    dlg = app.top_window()
                except Exception as exc:
                    return f"Save As dialog not found: {exc}"

            try:
                # Win11 file dialog filename field is inside a ComboBoxEx32 → Edit
                # Classic Win32 dialog has a plain Edit with class_name="Edit"
                fn_ctrl = None
                for _lookup in (
                    lambda: dlg.child_window(title="File name:", control_type="Edit"),
                    lambda: dlg.child_window(class_name="Edit", found_index=0),
                    lambda: dlg.child_window(control_type="ComboBox").child_window(class_name="Edit"),
                    lambda: dlg.child_window(auto_id="1001"),   # common shell dialog control ID
                ):
                    try:
                        fn_ctrl = _lookup()
                        fn_ctrl.wrapper_object()  # raises if not found
                        break
                    except Exception:
                        fn_ctrl = None

                if fn_ctrl is not None:
                    fn_ctrl.set_focus()
                    fn_ctrl.set_text("")
                    fn_ctrl.type_keys(filename, with_spaces=True)
                else:
                    # Absolute last resort: just type the filename raw and hope
                    import pywinauto.keyboard as _kb  # type: ignore
                    _kb.send_keys(filename)

                time.sleep(0.3)
                # Press Enter or click the Save button
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

        # ── keyboard_shortcut ─────────────────────────────────────────────────
        elif action_type == "keyboard_shortcut":
            keys = execution.get("keys", "")
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
            # Try keyboard shortcut fallbacks first for Win11 WinUI3 Notepad
            _kb_fallbacks = {
                ("File", "New"): "^n",
                ("File", "Save"): "^s",
                ("File", "Open"): "^o",
                ("Edit", "Undo"): "^z",
                ("Edit", "Cut"): "^x",
                ("Edit", "Copy"): "^c",
                ("Edit", "Paste"): "^v",
                ("Edit", "Select All"): "^a",
                ("View", "Zoom In"): "^{+}",
                ("View", "Zoom Out"): "^{-}",
            }
            kb = _kb_fallbacks.get(tuple(menu_path))
            if kb:
                win.set_focus()
                time.sleep(0.2)
                win.type_keys(kb)
                time.sleep(0.3)
                sep = " -> "
                return f"Clicked menu: {sep.join(menu_path)}"
            try:
                menu_str = "->".join(menu_path)  # pywinauto separator
                win.menu_select(menu_str)
                time.sleep(0.3)
                sep = " -> "
                return f"Clicked menu: {sep.join(menu_path)}"
            except Exception as menu_exc:
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
    result = _execute_tool(name, args)
    return jsonify({"result": result})


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
        "You are a helpful assistant with access to binary tools from notepad. "
        "When a user asks you to perform a multi-step task, issue as many tool calls as possible in a single response — only wait for results when a later step strictly depends on the output of an earlier one. "
        "Explain what each tool does and report results clearly."
    )

    messages = (
        [{"role": "system", "content": system_prompt}]
        + history
        + [{"role": "user", "content": user_message}]
    )

    response = client.chat.completions.create(
        model=model,
        messages=messages,
        tools=OPENAI_FUNCTIONS or None,
        tool_choice="auto" if OPENAI_FUNCTIONS else "none",
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
            result = _execute_tool(fn_name, fn_args)
            tool_outputs.append({"name": fn_name, "args": fn_args, "result": result})
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
