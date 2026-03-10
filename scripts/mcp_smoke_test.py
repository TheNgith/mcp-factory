"""
scripts/mcp_smoke_test.py

Standalone end-to-end smoke test for the generated notepad MCP stdio server.
Launches the server in a subprocess, runs the MCP initialize + tools/list
handshake, and exits 0 on success or 1 on failure.

Used by the gui-tests CI job to produce a clear pass/fail without pytest
overhead.

Usage:
    python scripts/mcp_smoke_test.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

SERVER = Path(__file__).parent.parent / "generated" / "notepad" / "mcp_stdio.py"


def rpc(proc: subprocess.Popen, msg: dict) -> dict:
    assert proc.stdin and proc.stdout
    proc.stdin.write(json.dumps(msg) + "\n")
    proc.stdin.flush()
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        line = proc.stdout.readline()
        if line.strip():
            return json.loads(line)
        time.sleep(0.05)
    raise TimeoutError(f"No response within 15 s for {msg.get('method')!r}")


def main() -> int:
    print(f"Starting MCP server: {SERVER}")
    proc = subprocess.Popen(
        [sys.executable, str(SERVER)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        bufsize=1,
        cwd=str(SERVER.parent),
    )

    try:
        time.sleep(2)  # let the server initialise its event loop

        # ── 1. MCP initialize handshake ─────────────────────────────────────
        init_resp = rpc(proc, {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "ci-smoke", "version": "1.0"},
            },
        })

        if "result" not in init_resp:
            print(f"FAIL: initialize error — {init_resp}", file=sys.stderr)
            return 1

        server_name = init_resp["result"]["serverInfo"]["name"]
        if server_name != "notepad":
            print(f"FAIL: unexpected serverInfo.name {server_name!r}", file=sys.stderr)
            return 1

        print(f"  initialize OK — serverInfo.name={server_name!r}")

        # Send notifications/initialized (required by spec before any other method)
        assert proc.stdin
        proc.stdin.write(
            '{"jsonrpc":"2.0","method":"notifications/initialized","params":{}}\n'
        )
        proc.stdin.flush()

        # ── 2. tools/list ────────────────────────────────────────────────────
        tools_resp = rpc(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})

        if "result" not in tools_resp:
            print(f"FAIL: tools/list error — {tools_resp}", file=sys.stderr)
            return 1

        tools = tools_resp["result"]["tools"]
        names = {t["name"] for t in tools}
        required = {"type_text", "save_as", "get_text", "file_new"}
        missing = required - names
        if missing:
            print(f"FAIL: tools/list missing expected tools: {missing}", file=sys.stderr)
            return 1

        print(f"  tools/list OK — {len(tools)} tools returned, required tools present")

        print(f"\nPASSED — MCP stdio server handshake and tools/list successful.")
        return 0

    finally:
        proc.kill()
        proc.wait()


if __name__ == "__main__":
    sys.exit(main())
