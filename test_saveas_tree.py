"""Test Save As via menu selection + dump full UIA tree to find dialog."""
import os, time, subprocess

os.environ.pop("OPENAI_BASE_URL", None)

from pywinauto.application import Application
from pywinauto import Desktop
import win32gui
import pywinauto.keyboard as kb

# ── kill stale instances ──────────────────────────────────────────────────────
print("[1] killing stale notepads …")
subprocess.run(["taskkill", "/F", "/IM", "notepad.exe"], capture_output=True)
time.sleep(0.8)

hwnds_before: set[int] = set()
def _snap(h, _):
    if win32gui.IsWindowVisible(h) and win32gui.GetWindowText(h):
        hwnds_before.add(h)
win32gui.EnumWindows(_snap, None)

# ── launch ────────────────────────────────────────────────────────────────────
print("[2] launching notepad …")
subprocess.Popen('start "" "C:\\Windows\\notepad.exe"', shell=True)
time.sleep(3.5)

hwnds_after: set[int] = set()
def _snap2(h, _):
    if win32gui.IsWindowVisible(h) and win32gui.GetWindowText(h):
        hwnds_after.add(h)
win32gui.EnumWindows(_snap2, None)

new_hwnds = hwnds_after - hwnds_before
print("[3] new HWNDs:", {h: win32gui.GetWindowText(h) for h in new_hwnds})

if not new_hwnds:
    raise RuntimeError("No new HWND found")

# pick notepad HWND
notepad_hwnd = None
for h in new_hwnds:
    title = win32gui.GetWindowText(h)
    if "untitled" in title.lower() or "notepad" in title.lower():
        notepad_hwnd = h
        break
notepad_hwnd = notepad_hwnd or next(iter(new_hwnds))
print("[4] using HWND:", notepad_hwnd, "->", win32gui.GetWindowText(notepad_hwnd))

app = Application(backend="uia").connect(handle=notepad_hwnd, timeout=5)
win = app.top_window()
win.set_focus()
time.sleep(0.5)

# ── type ──────────────────────────────────────────────────────────────────────
print("[5] typing …")
try:
    doc = win.child_window(control_type="Document")
    doc.set_focus()
    doc.type_keys("Hello from test_menu_saveas", with_spaces=True)
    print("    typed via Document")
except Exception as e:
    print("    fallback type_keys:", e)
    win.type_keys("Hello from test_menu_saveas", with_spaces=True)
time.sleep(0.3)

# ── trigger Save As via menu ──────────────────────────────────────────────────
print("[6] File > Save As via menu …")
win.set_focus()
try:
    win.menu_select("File->Save As...")
    print("    menu_select succeeded")
except Exception as e:
    print("    menu_select failed:", e, "— using Ctrl+Shift+S")
    kb.send_keys("^+s")

time.sleep(2.5)

# ── scan ALL visible windows for Save dialog ───────────────────────────────────
print("[7] scanning all win32 desktop windows:")
def _dump(h, _):
    if win32gui.IsWindowVisible(h):
        t = win32gui.GetWindowText(h)
        if t:
            print(f"    HWND {h} cls={win32gui.GetClassName(h)!r:30s} title={t!r}")
win32gui.EnumWindows(_dump, None)

# ── check all pywinauto UIA Desktop windows ───────────────────────────────────
print("\n[8] Desktop(uia) top-level windows:")
d = Desktop(backend="uia")
for w in d.windows():
    t = w.window_text()
    if t:
        print(f"    {t!r} | class={w.class_name()}")

# ── dump UIA tree of the notepad process ─────────────────────────────────────
print("\n[9] UIA tree of notepad app (depth 3):")
def _walk(elem, depth=0):
    if depth > 3:
        return
    try:
        t = elem.window_text()
        ct = elem.element_info.control_type
        print("  " * depth + f"{ct}: {t!r}")
        for child in elem.children():
            _walk(child, depth + 1)
    except Exception:
        pass

try:
    _walk(app.top_window())
except Exception as e:
    print("  tree walk failed:", e)

# ── try to find file name edit in UIA tree ────────────────────────────────────
print("\n[10] looking for Edit controls anywhere in app:")
try:
    edits = app.top_window().descendants(control_type="Edit")
    for e in edits:
        print(f"    Edit: {e.window_text()!r} auto_id={e.automation_id()!r}")
except Exception as e:
    print("  failed:", e)
