"""
gui_analyzer.py - GUI application capability analyzer using pywinauto.

Strategy overview
-----------------
1. Launch the EXE **hidden** (SW_SHOWMINNOACTIVE via STARTUPINFO) so it never
   appears on the user's desktop — only shows up as an inactive taskbar entry.

2. Enumerate menus **silently** using the Win32 GetMenu / GetMenuString API via
   win32gui.  Pure read — no clicks, no focus changes, no visual state changes.
   Works for classic Win32 apps (notepad ≤ Win10, wordpad, charmap, mspaint, …).

3. UIA fallback for WinUI3 / modern apps (Win11 Notepad 10.x, modern Calculator)
   where GetMenu() returns NULL.  pywinauto's "uia" backend reads the
   accessibility tree; sub-items of collapsed menus ARE present without clicking.

4. Always append four semantic actions: type_text, save_as, get_text, close_app.

Output invocables carry source_type="gui_action" and are executed by
_execute_gui() in the generated server (section4_generate_server.py).
"""

import logging
import re
import subprocess
import time
from pathlib import Path
from typing import List, Optional

from schema import Invocable

logger = logging.getLogger(__name__)

# ── Optional pywinauto ────────────────────────────────────────────────────
try:
    from pywinauto.application import Application  # type: ignore
    PYWINAUTO_AVAILABLE = True
except ImportError:
    PYWINAUTO_AVAILABLE = False
    logger.debug(
        "pywinauto not installed — GUI analysis unavailable. "
        "Install with:  pip install pywinauto"
    )

# ── Optional win32gui (from pywin32) ─────────────────────────────────────
try:
    import win32gui   # type: ignore
    import win32con   # type: ignore
    WIN32GUI_AVAILABLE = True
except ImportError:
    WIN32GUI_AVAILABLE = False

# Windows STARTUPINFO constants (hardcoded — always available on Windows)
_STARTF_USESHOWWINDOW = 0x00000001
_SW_SHOWMINNOACTIVE   = 7  # minimised in taskbar, no focus, no desktop pop-up



# ═══════════════════════════════════════════════════════════════════════
# Hidden launch helpers
# ═══════════════════════════════════════════════════════════════════════

def _launch_hidden(exe_str: str, backend: str = "win32"):
    """Launch *exe_str* minimised and return (proc, app).

    Uses STARTUPINFO SW_SHOWMINNOACTIVE so the window starts as an inactive
    taskbar button rather than popping up on the desktop, then attaches
    pywinauto by process PID.

    Raises on failure — callers should fall back to ``_launch_via_start()``
    for MSIX / WinUI3 apps where the stub exits immediately.
    """
    if not PYWINAUTO_AVAILABLE:
        raise RuntimeError("pywinauto is not installed.  Run:  pip install pywinauto")

    si = subprocess.STARTUPINFO()
    si.dwFlags     = _STARTF_USESHOWWINDOW
    si.wShowWindow = _SW_SHOWMINNOACTIVE

    proc = subprocess.Popen([exe_str], startupinfo=si)
    time.sleep(1.8)

    app = Application(backend=backend).connect(process=proc.pid, timeout=8)
    app.top_window().handle  # raises immediately if no window found under PID
    return proc, app


def _launch_via_start(exe_str: str, backend: str = "uia"):
    """Start the EXE via the shell and connect using HWND-diff (MSIX / WinUI3 safe).

    MSIX-packaged apps (Win11 Notepad, Calculator, etc.) use launcher stubs
    that exit immediately — pywinauto loses the PID and title/path patterns
    fail because the real process has a different name (e.g. CalculatorApp.exe).

    Strategy — generic, works for any MSIX or plain EXE:
      1. Snapshot all visible top-level HWNDs before launch (win32gui).
      2. Shell-launch via ``start ""`` so MSIX package activation fires.
      3. Wait up to ~5 s for a new top-level window to appear.
      4. HWND-diff: new windows are the app.
      5. Prefer a window whose title contains the exe stem; fall back to any new one.
      6. Connect pywinauto via handle — no path/title guessing needed.
      7. Minimise immediately so the window goes to the taskbar.

    Falls back to title-pattern loop when win32gui is unavailable.
    Returns ``(None, app)`` — proc handle is not available; use app.kill().
    """
    if not PYWINAUTO_AVAILABLE:
        raise RuntimeError("pywinauto is not installed.  Run:  pip install pywinauto")

    exe_stem = Path(exe_str).stem.lower()  # e.g. "calc", "notepad"

    # ── Strategy A: HWND-diff via win32gui (reliable for MSIX) ───────────
    if WIN32GUI_AVAILABLE:
        def _snap_hwnds() -> set:
            hwnds: set = set()
            def _cb(h, _):
                try:
                    if win32gui.IsWindowVisible(h) and win32gui.GetWindowText(h):
                        hwnds.add(h)
                except Exception:
                    pass
            try:
                win32gui.EnumWindows(_cb, None)
            except Exception:
                pass
            return hwnds

        before = _snap_hwnds()
        subprocess.Popen(f'start "" "{exe_str}"', shell=True)

        # Poll for up to 8 s in 0.5 s increments — MSIX apps can be slow
        new_hwnds: set = set()
        for _ in range(16):
            time.sleep(0.5)
            after = _snap_hwnds()
            new_hwnds = after - before
            if new_hwnds:
                break

        if new_hwnds:
            # Prefer a window whose title mentions the exe name
            hwnd = None
            for h in new_hwnds:
                try:
                    title = win32gui.GetWindowText(h).lower()
                    if exe_stem in title or "untitled" in title or "new tab" in title:
                        hwnd = h
                        break
                except Exception:
                    pass
            hwnd = hwnd or next(iter(new_hwnds))
            try:
                app = Application(backend=backend).connect(handle=hwnd, timeout=5)
                try:
                    app.top_window().minimize()
                except Exception:
                    pass
                return None, app
            except Exception as exc:
                logger.debug("HWND-diff connect failed (hwnd=%s): %s", hwnd, exc)
                # Fall through to title-pattern loop

    # ── Strategy B: title-pattern loop (win32gui unavailable) ────────────
    subprocess.Popen(f'start "" "{exe_str}"', shell=True)
    time.sleep(4.0)  # longer wait since we have no diff signal

    app = None
    last_exc: Exception = RuntimeError("no connect attempted")
    for connect_kw in [
        {"path": exe_str},
        {"title_re": f"(?i).*{re.escape(exe_stem)}.*"},
        {"title_re": "(?i).*Untitled.*"},
        {"title_re": "(?i).*New Tab.*"},
        {"title_re": "(?i).*Calculator.*"},
    ]:
        try:
            app = Application(backend=backend).connect(timeout=6, **connect_kw)
            break
        except Exception as exc:
            last_exc = exc

    if app is None:
        raise RuntimeError(f"Could not connect after shell-launch of {exe_str!r}: {last_exc}")

    try:
        app.top_window().minimize()
    except Exception:
        pass
    return None, app


# ═══════════════════════════════════════════════════════════════════════
# Silent Win32 HMENU enumeration (no clicks, no focus changes)
# ═══════════════════════════════════════════════════════════════════════

def _enum_hmenu(hmenu, parent_path: List[str], depth: int = 0,
               max_depth: int = 5) -> List[List[str]]:
    """Recursively enumerate a Win32 HMENU handle into path lists.

    Uses only GetMenuItemCount / GetMenuString / GetSubMenu — pure read-only
    Win32 API calls that need no visual state or focus.
    """
    if depth > max_depth:
        return []
    paths: List[List[str]] = []
    try:
        count = win32gui.GetMenuItemCount(hmenu)
    except Exception:
        return []
    if count <= 0:
        return []

    for i in range(count):
        try:
            label = win32gui.GetMenuString(hmenu, i, win32con.MF_BYPOSITION)
        except Exception:
            continue
        label = re.sub(r'&(.)', r'\1', label).strip().rstrip('.')
        if not label or set(label) <= {'-', '\t', '\x00'}:
            continue

        current_path = parent_path + [label]
        submenu = win32gui.GetSubMenu(hmenu, i)
        if submenu:
            children = _enum_hmenu(submenu, current_path, depth + 1, max_depth)
            paths.extend(children) if children else paths.append(current_path)
        else:
            paths.append(current_path)

    return paths


def _walk_menu_silent(hwnd: int) -> List[List[str]]:
    """Return all menu paths from the Win32 HMENU of *hwnd*, silently.

    Returns [] if win32gui is unavailable or the window has no Win32 menu
    (e.g. WinUI3 / UWP apps whose menus are custom controls).
    """
    if not WIN32GUI_AVAILABLE:
        logger.debug("win32gui not available — skipping silent HMENU walk")
        return []
    try:
        hmenu = win32gui.GetMenu(hwnd)
        if not hmenu:
            return []
        return _enum_hmenu(hmenu, [])
    except Exception as exc:
        logger.debug("HMENU enumeration error (hwnd=%s): %s", hwnd, exc)
        return []


# ═══════════════════════════════════════════════════════════════════════
# UIA accessibility-tree walk (WinUI3 / Win11 Notepad)
# ═══════════════════════════════════════════════════════════════════════

def _walk_uia_tree(win) -> List[List[str]]:
    """Walk the UI Automation accessibility tree for menu items.

    Works for WinUI3 / Win11 apps whose menus are NOT backed by a Win32 HMENU.
    Sub-items under a *collapsed* menu are still present in the UIA accessibility
    tree, so we never need to click anything open.

    Note: every Windows window has a "System" menu (window-chrome, Alt+Space).
    We strip it here — it is never an application-level menu item.
    """
    # Labels that belong to the window chrome, not the app
    _SYSTEM_NOISE = {"system", "minimize", "maximize", "restore", "close",
                     "move", "size", "always on top"}
    paths: List[List[str]] = []
    try:
        # Try a named MenuBar control first
        try:
            menu_bar = win.child_window(control_type="MenuBar", found_index=0)
            top_items = menu_bar.children()
        except Exception:
            top_items = []

        # Fallback: search shallow descendants for MenuItem controls
        if not top_items:
            try:
                top_items = win.descendants(control_type="MenuItem", depth=3)
            except Exception:
                top_items = []

        for top in top_items:
            label = re.sub(r'&(.)', r'\1', top.window_text()).strip().rstrip('.')
            if not label or label == '-':
                continue
            # Skip window-chrome system menu items
            if label.lower() in _SYSTEM_NOISE:
                continue

            # Collect sub-items from accessibility tree without clicking
            sub_items = []
            try:
                sub_items = top.children()
            except Exception:
                pass
            if not sub_items:
                try:
                    sub_items = top.descendants(control_type="MenuItem")
                except Exception:
                    pass

            if sub_items:
                for sub in sub_items:
                    sub_label = re.sub(r'&(.)', r'\1', sub.window_text()).strip().rstrip('.')
                    if sub_label and sub_label != '-':
                        paths.append([label, sub_label])
            else:
                paths.append([label])

    except Exception as exc:
        logger.debug("UIA tree walk failed: %s", exc)

    return paths


def _walk_uia_buttons(win) -> List[str]:
    """Enumerate all named Button controls via UIA accessibility tree.

    Used as a fallback when an app has no menu bar at all (e.g. Win11
    Calculator, modern Media Player).  Returns a deduplicated list of
    button labels, filtering out unnamed / icon-only controls.
    """
    seen: set = set()
    labels: List[str] = []
    try:
        buttons = win.descendants(control_type="Button")
        for btn in buttons:
            try:
                label = btn.window_text().strip()
            except Exception:
                continue
            if not label or label in seen:
                continue
            # Skip pure-icon buttons (single non-alphanumeric char like "✕")
            # but keep operator symbols that have meaning (÷ × + - etc.)
            _KEEP = set("+\u2212\u00d7\u00f7*/=%.^()")
            if len(label) == 1 and not label.isalnum() and label not in _KEEP:
                continue
            seen.add(label)
            labels.append(label)
    except Exception as exc:
        logger.debug("UIA button walk failed: %s", exc)
    return labels


# ═══════════════════════════════════════════════════════════════════════
# Name sanitisation
# ═══════════════════════════════════════════════════════════════════════

def _sanitize_name(text: str) -> str:
    """Convert a menu label to a valid Python identifier fragment.

    Examples:
        "Save As..."  → "save_as"
        "&File"       → "file"
        "Font…"       → "font"
    """
    # Strip Unicode ellipsis / ASCII "..."
    cleaned = re.sub(r'[…\.]+$', '', text.strip())
    # Remove accelerator markers (&)
    cleaned = re.sub(r'&(.)', r'\1', cleaned)
    # Replace non-alphanumeric runs with underscore
    cleaned = re.sub(r'[^a-zA-Z0-9]+', '_', cleaned).strip('_').lower()
    return cleaned or "action"


# old _walk_menu removed — replaced by _walk_menu_silent (Win32 HMENU)
# and _walk_uia_tree (UIA accessibility tree) above.

# ── Public API ─────────────────────────────────────────────────────────────

def _kill_app(proc, app) -> None:
    """Best-effort cleanup of a pywinauto app and its subprocess."""
    if app is not None:
        try:
            app.kill()
        except Exception:
            pass
    if proc is not None:
        try:
            proc.terminate()
        except Exception:
            pass


def analyze_gui(exe_path: Path, timeout: int = 10) -> List[Invocable]:
    """Analyze a GUI EXE for invocable menu actions — non-intrusively.

    The EXE is launched **minimised** (taskbar only, never on the desktop).
    Menu discovery uses the Win32 HMENU API directly (zero clicks, zero focus
    changes).  For WinUI3 apps (Win11 Notepad 10.x, modern Calculator) where
    GetMenu() returns NULL, falls back to UIA accessibility-tree walking.

    Args:
        exe_path: Absolute path to the target GUI executable.
        timeout:  Seconds to wait for the main window to become visible.

    Returns:
        List of Invocable records with ``source_type="gui_action"``.
        Empty list when pywinauto is unavailable or launch fails.
    """
    if not PYWINAUTO_AVAILABLE:
        logger.debug("Skipping GUI analysis — pywinauto not installed")
        return []

    exe_str = str(exe_path)
    invocables: List[Invocable] = []
    proc: Optional[subprocess.Popen] = None
    app = None
    win32_ok = False
    menu_paths: List[List[str]] = []

    try:
        # ── Attempt A: Win32 hidden (subprocess + PID connect) ────────────
        # No visual pop-up at all. Works for all classic Win32 apps.
        logger.info("Launching %s hidden for menu discovery…", exe_path.name)
        proc = None
        app = None
        win32_ok = False

        try:
            proc, app = _launch_hidden(exe_str, backend="win32")
            win = app.top_window()
            hwnd = win.handle
            menu_paths = _walk_menu_silent(hwnd)
            if menu_paths:
                logger.info(
                    "Silent HMENU: %d menu paths in %s",
                    len(menu_paths), exe_path.name,
                )
                win32_ok = True
        except Exception as exc:
            logger.debug("win32 hidden launch failed for %s: %s",
                         exe_path.name, exc)

        # Kill win32 app before trying UIA (prevents duplicate windows)
        if not win32_ok:
            _kill_app(proc, app)
            proc, app = None, None

        if not win32_ok:
            # ── Attempt B: UIA via Application.start() + immediate minimise ─
            # Works for WinUI3 / Win11 apps.  Window appears for ~1 frame.
            logger.info(
                "win32 backend yielded nothing for %s — trying UIA (Win11/WinUI3)…",
                exe_path.name,
            )
            try:
                proc, app = _launch_via_start(exe_str, backend="uia")
                win_uia = app.top_window()
                win_uia.wait("exists", timeout=timeout)
                menu_paths = _walk_uia_tree(win_uia)
                if menu_paths:
                    logger.info(
                        "UIA tree: %d menu paths in %s",
                        len(menu_paths), exe_path.name,
                    )
                else:
                    logger.info(
                        "No menu items found via UIA for %s", exe_path.name
                    )
            except Exception as uia_exc:
                logger.warning(
                    "UIA fallback failed for %s: %s", exe_path.name, uia_exc
                )

        # ── Attempt C: UIA button enumeration (apps with no menu bar) ─────
        # Catches Calculator, Media Player, etc. that expose buttons but no
        # real MenuItem controls.  Runs when both menu walks found nothing
        # meaningful (a lone "System" entry does not count as a real menu).
        _real_menus = [p for p in menu_paths if p[0:1] != ["__button__"]]
        if not _real_menus and not win32_ok:
            try:
                if app is None:
                    proc, app = _launch_via_start(exe_str, backend="uia")
                    app.top_window().wait("exists", timeout=timeout)
                button_labels = _walk_uia_buttons(app.top_window())
                if button_labels:
                    logger.info(
                        "UIA buttons: %d buttons in %s",
                        len(button_labels), exe_path.name,
                    )
                    # Store button labels alongside menu_paths using a sentinel
                    # list so the converter below can distinguish them.
                    # We tag each as ["__button__", label] for routing.
                    menu_paths = [["__button__", lbl] for lbl in button_labels]
            except Exception as btn_exc:
                logger.warning(
                    "UIA button walk failed for %s: %s", exe_path.name, btn_exc
                )

    except Exception as exc:
        logger.warning("GUI analysis failed for %s: %s", exe_path.name, exc)

    finally:
        _kill_app(proc, app)

    # ── Step 5: convert paths → Invocables ────────────────────────────────
    # Detected backend: win32 = classic Win32 app (menu_select works),
    #                   uia   = WinUI3/MSIX (keyboard shortcuts needed)
    detected_backend = "win32" if win32_ok else "uia"

    # Known keyboard shortcuts for common menu actions (covers both Win32 and WinUI3)
    _KB_SHORTCUTS: dict = {
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
        ("View", "Zoom In"):    "^+{+}",
        ("View", "Zoom Out"):   "^{-}",
    }

    _COMMON_VERBS = {
        "save", "open", "new", "print", "close", "cut", "copy",
        "paste", "find", "replace", "undo", "redo", "select all",
        "delete", "insert", "format", "view", "font",
    }
    # Calculator-style digit/operator label → high confidence
    _CALC_LABELS = set("0123456789") | {"+", "-", "\u2212", "\u00d7", "\u00f7",
                                         "*", "/", "=", "%", ".", "C", "CE",
                                         "MC", "MR", "M+", "M-", "MS", "sqrt",
                                         "x\u00b2", "1/x", "\u00b11", "<-"}
    for path in menu_paths:
        # ── button_click path (tagged by Attempt C above) ─────────────────
        if path[0] == "__button__":
            btn_label = path[1]
            action_name = "press_" + _sanitize_name(btn_label)
            confidence = "high" if btn_label in _CALC_LABELS else "medium"
            invocables.append(Invocable(
                name=action_name,
                source_type="gui_action",
                signature=f"press_button({btn_label!r})",
                doc_comment=f"Click the '{btn_label}' button in the application.",
                confidence=confidence,
                dll_path=exe_str,
                gui_menu_path=[],
                gui_action_type="button_click",
                gui_backend=detected_backend,
                gui_button_name=btn_label,
            ))
            continue
        path_str = " → ".join(path)
        action_name = "_".join(_sanitize_name(p) for p in path)
        leaf = path[-1].lower()
        confidence = "medium" if any(v in leaf for v in _COMMON_VERBS) else "low"
        kb = _KB_SHORTCUTS.get(tuple(path))
        # For WinUI3 apps, prefer keyboard_shortcut action when we have a known key
        if detected_backend == "uia" and kb:
            action_type = "keyboard_shortcut"
        else:
            action_type = "menu_click"
        invocables.append(Invocable(
            name=action_name,
            source_type="gui_action",
            signature=path_str,
            doc_comment=f"Activate menu: {path_str}",
            confidence=confidence,
            dll_path=exe_str,
            gui_menu_path=path,
            gui_action_type=action_type,
            gui_backend=detected_backend,
            gui_kb_shortcut=kb,
        ))

    # ── Step 6: always append semantic actions ─────────────────────────────
    _add_semantic_actions(invocables, exe_str, detected_backend)
    return invocables


def _add_semantic_actions(invocables: List[Invocable], exe_str: str, gui_backend: str = "win32") -> None:
    """Append well-known semantic GUI actions to the invocables list.

    These are added regardless of whether menu walking succeeded, because
    they represent the highest-value operations an AI agent will need.

    Skips any name that was already discovered via menu walking (de-dup).
    """
    existing_names = {inv.name for inv in invocables}

    semantic = [
        Invocable(
            name="type_text",
            source_type="gui_action",
            signature="type_text(text: str)",
            doc_comment=(
                "Type the given text into the active focused control "
                "(e.g. the text editor area of Notepad). "
                "Preserves spaces and newlines."
            ),
            confidence="high",
            dll_path=exe_str,
            gui_action_type="type_text",
            gui_menu_path=[],
            parameters="text: str",
        ),
        Invocable(
            name="save_as",
            source_type="gui_action",
            signature="save_as(filename: str)",
            doc_comment=(
                "Open the File → Save As dialog and save the document "
                "with the specified filename. Supports relative and absolute paths."
            ),
            confidence="medium",
            dll_path=exe_str,
            gui_action_type="save_as",
            gui_menu_path=["File", "Save As"],
            parameters="filename: str",
        ),
        Invocable(
            name="get_text",
            source_type="gui_action",
            signature="get_text()",
            doc_comment=(
                "Return the full text content of the main editing area "
                "(e.g. all text currently in the Notepad window)."
            ),
            confidence="medium",
            dll_path=exe_str,
            gui_action_type="get_text",
            gui_menu_path=[],
            parameters="",
        ),
        Invocable(
            name="close_app",
            source_type="gui_action",
            signature="close_app()",
            doc_comment=(
                "Close the application. "
                "Sends Alt+F4 and discards unsaved changes."
            ),
            confidence="high",
            dll_path=exe_str,
            gui_action_type="close_app",
            gui_menu_path=[],
            parameters="",
        ),
    ]

    for s in semantic:
        if s.name not in existing_names:
            s.gui_backend = gui_backend
            invocables.append(s)
