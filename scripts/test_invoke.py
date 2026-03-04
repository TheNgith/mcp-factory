"""
Execution-layer smoke test
==========================
Tests /invoke directly on each generated server — NO LLM, NO API key needed.
Proves whether dll_import and gui_action actually execute, independent of OpenAI.

Usage:  python scripts/test_invoke.py
"""
import os, sys, json, subprocess, time, threading, signal
import urllib.request, urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY   = sys.executable

SERVERS = {
    "dll (zstd)": {
        "dir":   ROOT / "generated" / "zstd",
        "port": 5010,
        # (tool_name, args_dict, pass_if_result_contains)  — empty string = any result counts
        "cases": [
            ("ZSTD_versionNumber", {}, ""),          # returns an integer — any result is fine
            ("ZSTD_versionString", {}, ""),           # returns a version string
            ("ZSTD_isError",       {"code": 0}, ""),  # 0 = not an error
        ],
    },
    "gui (notepad)": {
        "dir":  ROOT / "generated" / "notepad",
        "port": 5011,
        "cases": [
            ("type_text", {"text": "invoke-probe"}, "Typed"),
            ("get_text",  {},                        "invoke-probe"),
            ("save_as",   {"filename": "invoke_test.txt"}, "Saved"),
        ],
    },
}

# ── Server helpers ─────────────────────────────────────────────────────────

def _start(cfg):
    src   = cfg["dir"] / "server.py"
    port  = cfg["port"]
    code  = src.read_text(encoding="utf-8")
    patched = code.replace("app.run(port=5000", f"app.run(port={port}")
    tmp = cfg["dir"] / f"_smoke_{port}.py"
    tmp.write_text(patched, encoding="utf-8")
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    proc = subprocess.Popen(
        [PY, str(tmp)],
        cwd=str(cfg["dir"]),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )
    cfg["_proc"] = proc
    cfg["_tmp"]  = tmp
    return proc

def _stop(cfg):
    proc = cfg.get("_proc")
    tmp  = cfg.get("_tmp")
    if proc:
        try: proc.terminate(); proc.wait(timeout=4)
        except Exception:
            try: proc.kill()
            except Exception: pass
    if tmp:
        try: tmp.unlink(missing_ok=True)
        except Exception: pass

def _wait_up(port, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://localhost:{port}/tools", timeout=2):
                return True
        except Exception:
            time.sleep(0.4)
    return False

def _invoke(port, tool, args):
    body = json.dumps({"tool": tool, "args": args}).encode()
    req  = urllib.request.Request(
        f"http://localhost:{port}/invoke",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read()).get("result", ""), None
    except Exception as exc:
        return "", str(exc)

# ── Colours ────────────────────────────────────────────────────────────────
PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
SKIP = "\033[33mSKIP\033[0m"

# ── Main ───────────────────────────────────────────────────────────────────

def main():
    overall = {}

    for label, cfg in SERVERS.items():
        print(f"\n{'='*60}")
        print(f"  {label}  (port {cfg['port']})")
        print(f"{'='*60}")

        _start(cfg)
        up = _wait_up(cfg["port"])
        if not up:
            print("  server did not start in time — skipping")
            overall[label] = "SKIP"
            _stop(cfg)
            continue

        passed = 0
        for tool, args, must_contain in cfg["cases"]:
            result, err = _invoke(cfg["port"], tool, args)
            # Determine pass: got a result (not empty), no error, and contains expected substring
            is_error = bool(err) or "error" in (result or "").lower()
            has_content = bool((result or "").strip())
            contains_ok = (must_contain == "") or (must_contain.lower() in (result or "").lower())
            ok = has_content and not is_error and contains_ok

            status = PASS if ok else FAIL
            snippet = (result or err or "")[:70].replace("\n", " ")
            print(f"  {status}  {tool}({', '.join(f'{k}={v!r}' for k,v in args.items()) or ''})")
            print(f"         → {snippet}")
            if ok:
                passed += 1

        overall[label] = f"{passed}/{len(cfg['cases'])}"
        _stop(cfg)

    print(f"\n{'='*60}")
    print("  SUMMARY")
    print(f"{'='*60}")
    for label, result in overall.items():
        print(f"  {label:30s}  {result}")

    # Verdict
    dll_r = overall.get("dll (zstd)", "0/0")
    gui_r = overall.get("gui (notepad)", "0/0")
    if dll_r not in ("SKIP", "0/0") and gui_r not in ("SKIP", "0/0"):
        dll_ok = dll_r.split("/")[0] == dll_r.split("/")[1]
        gui_ok = gui_r.split("/")[0] == gui_r.split("/")[1]
        if dll_ok and not gui_ok:
            print("\n  VERDICT: dll_import works. gui_action is the only failing path.")
        elif not dll_ok and not gui_ok:
            print("\n  VERDICT: Both execution paths have failures.")
        elif dll_ok and gui_ok:
            print("\n  VERDICT: Both paths work end-to-end.")
        else:
            print("\n  VERDICT: dll_import has failures; gui_action appears OK.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(1)
