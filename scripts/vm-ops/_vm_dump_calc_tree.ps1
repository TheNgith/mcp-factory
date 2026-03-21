$py = "C:\Program Files\Python311\python.exe"
$script = @'
import subprocess, time, psutil
from pywinauto import Desktop
from pywinauto.application import Application

# Kill any existing calc
for p in psutil.process_iter(['name']):
    if 'calc' in p.info['name'].lower():
        p.kill()
time.sleep(1)

# Launch
subprocess.Popen(["C:\\Windows\\System32\\calc.exe"])
time.sleep(4)

# Find window
best = None
for w in Desktop(backend="uia").windows():
    try:
        t = (w.window_text() or "").lower()
        pid = w.element_info.process_id
        pname = (psutil.Process(pid).name() or "").lower()
        if "calc" in t or "calc" in pname:
            best = w.handle
            print(f"Found window: title={w.window_text()!r} pid={pid} proc={psutil.Process(pid).name()!r}")
            break
    except: pass

if not best:
    print("NO CALC WINDOW FOUND")
    exit(1)

app = Application(backend="uia").connect(handle=best)
win = app.top_window()

print("=== ALL TEXT DESCENDANTS ===")
for ctrl in win.descendants(control_type="Text"):
    try:
        t = ctrl.window_text()
        aid = ctrl.element_info.automation_id
        cn = ctrl.element_info.control_type
        print(f"  AutoID={aid!r:30s} text={t!r}")
    except: pass

print("=== EDIT DESCENDANTS ===")
for ctrl in win.descendants(control_type="Edit"):
    try:
        t = ctrl.window_text()
        aid = ctrl.element_info.automation_id
        print(f"  AutoID={aid!r:30s} text={t!r}")
    except: pass
'@
& $py -c $script
