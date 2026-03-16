#!/usr/bin/env python3
"""
_dll_call_worker.py — Minimal subprocess worker for safe DLL calls.

Spawned by run_local.py for each ctypes call so that a DLL-level access
violation (0xC0000005) kills only this child process, not the parent.

Called as:
    python scripts/_dll_call_worker.py <base64-encoded JSON>

JSON schema:  {"inv": {...}, "args": {...}}
Output:       prints result string to stdout, one line
"""
import base64
import json
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "scripts"))
os.environ.setdefault("BRIDGE_SECRET", "local-debug")

from gui_bridge import _execute_dll_bridge  # noqa: E402

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("[error] missing argument")
        sys.exit(1)
    payload = json.loads(base64.b64decode(sys.argv[1]).decode("utf-8"))
    inv  = payload["inv"]
    args = payload["args"]
    try:
        result = _execute_dll_bridge(inv, inv.get("execution", {}), args)
    except Exception as exc:
        result = f"DLL call error: {exc}"
    # Write result line, replacing any chars that would break stdout encoding
    sys.stdout.buffer.write((result + "\n").encode("utf-8", errors="replace"))
