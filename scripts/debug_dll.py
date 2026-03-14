#!/usr/bin/env python3
"""
debug_dll.py — Local DLL execution debugger.

Loads invocables from an artifacts JSON (e.g. artifacts/shell32_exports_mcp.json
or a downloaded shell32_dll.json) and calls individual functions via ctypes
directly — no Azure, no bridge, no Docker required.

Usage:
    python scripts/debug_dll.py                          # menu of safe defaults
    python scripts/debug_dll.py --json artifacts/kernel32_exports_mcp.json
    python scripts/debug_dll.py --json artifacts/shell32_exports_mcp.json
    python scripts/debug_dll.py --func GetCurrentProcessId
    python scripts/debug_dll.py --func GetWindowsDirectoryW
    python scripts/debug_dll.py --list   # just print all invocable names
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# ── resolve project root so we can import gui_bridge helpers ─────────────────
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "scripts"))

# Import the ctypes executor we just wrote in gui_bridge.py
from gui_bridge import _execute_dll_bridge, _resolve_exe_path  # noqa: E402

# ── safe test functions (no side effects, no args needed) ────────────────────
_SAFE_DEFAULTS = [
    "GetCurrentProcessId",
    "GetCurrentThreadId",
    "GetTickCount",
    "GetTickCount64",
    "GetWindowsDirectoryW",
    "GetSystemDirectoryW",
    "GetTempPathW",
    "IsDebuggerPresent",
    "GetVersion",
    "GetLastError",
    # shell32 safe ones
    "IsUserAnAdmin",
    "SHGetKnownFolderPath",
]


def _load_invocables(json_path: Path) -> list[dict]:
    data = json.loads(json_path.read_text(encoding="utf-8"))
    # Artifacts format: { "invocables": [...] }
    if "invocables" in data:
        return data["invocables"]
    # Downloaded MCP schema format: { "tools": [{"type":"function","function":{...}}] }
    # These don't have execution blocks — warn and exit
    if "tools" in data:
        print("[!] This JSON is an MCP tool schema (no execution blocks).")
        print("    Use the artifacts JSON instead, e.g.:")
        print("      artifacts/shell32_exports_mcp.json")
        print("      artifacts/kernel32_exports_mcp.json")
        sys.exit(1)
    print(f"[!] Unrecognised JSON format in {json_path}")
    sys.exit(1)


def _find_invocable(invocables: list[dict], func_name: str) -> dict | None:
    for inv in invocables:
        if inv.get("name", "").lower() == func_name.lower():
            return inv
        # Also check nested mcp.execution.function_name
        exec_block = (inv.get("mcp") or {}).get("execution") or inv.get("execution") or {}
        if exec_block.get("function_name", "").lower() == func_name.lower():
            return inv
    return None


def _get_execution(inv: dict) -> dict:
    """Normalise: invocables can store execution at top-level or under mcp.execution."""
    if inv.get("execution"):
        return inv["execution"]
    return (inv.get("mcp") or {}).get("execution", {})


def _run(inv: dict, extra_args: dict | None = None) -> None:
    execution = _get_execution(inv)
    func_name = execution.get("function_name", inv.get("name", "?"))
    dll_path  = execution.get("dll_path", "")
    args      = extra_args or {}
    print(f"\n  → Calling {func_name}() from {Path(dll_path).name}")
    result = _execute_dll_bridge(inv, execution, args)
    print(f"  ← {result}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Debug DLL invocables locally via ctypes")
    ap.add_argument("--json",  default=None, help="Path to artifacts invocables JSON")
    ap.add_argument("--func",  default=None, help="Function name to call (exact or partial match)")
    ap.add_argument("--list",  action="store_true", help="List all invocable names and exit")
    ns = ap.parse_args()

    # Auto-locate a JSON if not specified
    artifacts = _ROOT / "artifacts"
    if ns.json:
        json_path = Path(ns.json)
        if not json_path.is_absolute():
            json_path = _ROOT / json_path
    else:
        # Pick kernel32 by default since it's the richest
        json_path = artifacts / "kernel32_exports_mcp.json"
        if not json_path.exists():
            json_path = artifacts / "shell32_exports_mcp.json"
        if not json_path.exists():
            print("[!] No artifacts JSON found. Pass --json <path>")
            sys.exit(1)
        print(f"[auto] Using {json_path.relative_to(_ROOT)}")

    invocables = _load_invocables(json_path)
    print(f"[info] Loaded {len(invocables)} invocables from {json_path.name}")

    if ns.list:
        for inv in invocables:
            exec_b = _get_execution(inv)
            print(f"  {inv.get('name','?'):50s}  dll={Path(exec_b.get('dll_path','')).name}")
        return

    if ns.func:
        inv = _find_invocable(invocables, ns.func)
        if not inv:
            print(f"[!] '{ns.func}' not found. Use --list to see all names.")
            sys.exit(1)
        _run(inv)
        return

    # Default: run all safe no-arg functions found in the loaded JSON
    print("\n[demo] Running safe no-arg functions found in this DLL:\n")
    ran = 0
    for fname in _SAFE_DEFAULTS:
        inv = _find_invocable(invocables, fname)
        if inv:
            _run(inv)
            ran += 1
    if ran == 0:
        print("  None of the safe defaults found. Try --list then --func <name>")


if __name__ == "__main__":
    main()
