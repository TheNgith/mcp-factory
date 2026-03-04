"""Quick sanity test: can we launch Win11 MSIX Notepad via HWND-diff and trigger Save As?"""
import os, time, subprocess

os.environ.pop("OPENAI_BASE_URL", None)

from pywinauto.application import Application
import win32gui
import pywinauto.keyboard as kb

# ── kill stale instances ─────────────────────────────────────────────────────
print("[1] killing stale notepads …")
subprocess.run(["taskkill", "/F", "/IM", "notepad.exe"], capture_output=True)
time.sleep(0.8)

# ── snapshot HWNDs before launch ─────────────────────────────────────────────
hwnds_before: set[int] = set()
def _snap(h, _):
    if win32gui.IsWindowVisible(h) and win32gui.GetWindowText(h):
        hwnds_before.add(h)
win32gui.EnumWindows(_snap, None)

# ── launch (UIA / MSIX safe) ─────────────────────────────────────────────────
print("[2] launching notepad …")
subprocess.Popen('start "" "C:\\Windows\\notepad.exe"', shell=True)
time.sleep(3.5)

# ── find new HWND ─────────────────────────────────────────────────────────────
hwnds_after: set[int] = set()
def _snap2(h, _):
    if win32gui.IsWindowVisible(h) and win32gui.GetWindowText(h):
        hwnds_after.add(h)
win32gui.EnumWindows(_snap2, None)

new_hwnds = hwnds_after - hwnds_before
print("[3] new HWNDs:", {h: win32gui.GetWindowText(h) for h in new_hwnds})

if not new_hwnds:
    raise RuntimeError("No new HWND found — notepad didn't open?")

# pick the one that looks like Notepad
notepad_hwnd = None
for h in new_hwnds:
    title = win32gui.GetWindowText(h)
    if "notepad" in title.lower() or "untitled" in title.lower():
        notepad_hwnd = h
        break
notepad_hwnd = notepad_hwnd or next(iter(new_hwnds))
print("[4] using HWND:", notepad_hwnd, "->", win32gui.GetWindowText(notepad_hwnd))

# ── connect via UIA ───────────────────────────────────────────────────────────
app = Application(backend="uia").connect(handle=notepad_hwnd, timeout=5)
win = app.top_window()
win.set_focus()
time.sleep(0.3)

# ── type text ─────────────────────────────────────────────────────────────────
print("[5] typing text …")
try:
    doc = win.child_window(control_type="Document")
    doc.set_focus()
    doc.type_keys("I am having a good day today", with_spaces=True)
    print("    → typed via Document control")
except Exception as e:
    print("    → Document fail:", e, "— falling back to win.type_keys")
    win.type_keys("I am having a good day today", with_spaces=True)

time.sleep(0.3)

# ── trigger Save As  (Ctrl+Shift+S in newer Notepad, or File > Save As) ──────
print("[6] triggering Ctrl+S …")
win.set_focus()
kb.send_keys("^s")
time.sleep(2.0)

# ── inspect toplevel windows ─────────────────────────────────────────────────
print("[7] windows now:")
for w in app.windows():
    print("  ", w.window_text(), "|", w.class_name())

# also check desktop-level windows for a Save dialog
print("[8] all visible desktop windows containing 'save' (case-insensitive):")
def _find_save(h, _):
    t = win32gui.GetWindowText(h)
    if "save" in t.lower() and win32gui.IsWindowVisible(h):
        print("  HWND", h, "->", t)
win32gui.EnumWindows(_find_save, None)

# ── try to locate the filename field and type ─────────────────────────────────
print("[9] looking for filename field inside app …")
try:
    dlg = app.window(title_re="(?i)save")
    print("   found dialog:", dlg.window_text())
    fname_edit = dlg.child_window(class_name="Edit")
    fname_edit.set_edit_text(r"C:\Users\evanw\Desktop\test_uia.txt")
    time.sleep(0.3)
    kb.send_keys("{ENTER}")
    time.sleep(1.0)
    print("[10] file saved successfully? Check Desktop for test_uia.txt")
except Exception as e:
    print("[9] no dialog found via app.window(title_re='save'):", e)

# also try Desktop (ApplicationWrapper)
try:
    from pywinauto import Desktop
    dlg2 = Desktop(backend="uia").window(title_re="(?i)save")
    print("[9b] Desktop found:", dlg2.window_text())
    fname_edit2 = dlg2.child_window(control_type="Edit", found_index=0)
    fname_edit2.set_edit_text(r"C:\Users\evanw\Desktop\test_uia.txt")
    time.sleep(0.3)
    kb.send_keys("{ENTER}")
    print("[10b] sent via Desktop dlg")
except Exception as e:
    print("[9b] Desktop no dialog:", e)
