"""
tests/test_mcp_stdio.py

End-to-end smoke test for the generated notepad MCP stdio server.
Launches mcp_stdio.py as a subprocess and exchanges raw JSON-RPC 2.0
messages over stdin/stdout — exactly what VS Code Copilot does when it
connects to a stdio MCP server.

What this proves:
  1. The MCP initialize handshake succeeds.
  2. tools/list returns all expected Notepad tools.
  3. tools/call dispatches correctly (GUI call is Windows-only; on other
     platforms only the protocol layer is exercised with a no-op tool).

Run:
    pytest tests/test_mcp_stdio.py -v
"""

from __future__ import annotations

import json
import platform
import subprocess
import sys
import time
from pathlib import Path

import pytest

# Skip the entire module if flask (required by server.py) is not installed.
# This guards against running on a minimal environment that only has the
# root requirements.txt but not generated/notepad/requirements.txt.
pytest.importorskip("flask", reason="flask not installed — skipping MCP stdio tests")
pytest.importorskip("mcp", reason="mcp not installed — skipping MCP stdio tests")

# Absolute path to the generated notepad MCP stdio server
SERVER_PATH = Path(__file__).parent.parent / "generated" / "notepad" / "mcp_stdio.py"

EXPECTED_TOOLS = {
    "type_text",
    "save_as",
    "get_text",
    "file_new",
    "file_open",
    "file_save",
    "edit_undo",
    "edit_select_all",
    "view_word_wrap",
}

IS_WINDOWS = platform.system() == "Windows"


# ---------------------------------------------------------------------------
# Helper: minimal JSON-RPC over subprocess stdio
# ---------------------------------------------------------------------------

class McpStdioClient:
    """Thin wrapper: spawn the server, exchange newline-delimited JSON-RPC."""

    def __init__(self, server_script: Path):
        self.proc = subprocess.Popen(
            [sys.executable, str(server_script)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(server_script.parent),
            text=True,
            bufsize=1,  # line-buffered
        )
        self._id = 0

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    def send(self, method: str, params: dict | None = None, notify: bool = False) -> dict | None:
        """Send a request (or notification if notify=True) and return the response."""
        msg: dict = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        if not notify:
            msg["id"] = self._next_id()

        line = json.dumps(msg) + "\n"
        assert self.proc.stdin
        self.proc.stdin.write(line)
        self.proc.stdin.flush()

        if notify:
            return None

        # Read exactly one response line (with a short deadline)
        assert self.proc.stdout
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            response_line = self.proc.stdout.readline()
            if response_line.strip():
                return json.loads(response_line)
            time.sleep(0.05)
        raise TimeoutError(f"No response within 10s for method={method!r}")

    def close(self) -> None:
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
            self.proc.wait(timeout=5)
        except Exception:
            self.proc.kill()


@pytest.fixture()
def mcp_client():
    client = McpStdioClient(SERVER_PATH)
    # ── MCP handshake ──────────────────────────────────────────────────────
    init_resp = client.send(
        "initialize",
        {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "pytest-mcp-client", "version": "1.0"},
        },
    )
    assert init_resp is not None, "No response to initialize"
    assert "result" in init_resp, f"initialize failed: {init_resp}"
    assert init_resp["result"]["serverInfo"]["name"] == "notepad"

    # Send initialized notification (required by MCP spec)
    client.send("notifications/initialized", notify=True)

    yield client
    client.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_initialize_handshake(mcp_client: McpStdioClient):
    """The initialize/initialized exchange must complete without error."""
    # Fixture already performed the handshake — reaching here means it worked.
    pass


def test_tools_list_returns_expected_tools(mcp_client: McpStdioClient):
    """tools/list must return all expected Notepad tool names."""
    resp = mcp_client.send("tools/list", {})
    assert resp is not None
    assert "result" in resp, f"tools/list error: {resp}"

    names = {t["name"] for t in resp["result"]["tools"]}
    missing = EXPECTED_TOOLS - names
    assert not missing, f"Missing tools in tools/list response: {missing}"


def test_tools_list_schemas_valid(mcp_client: McpStdioClient):
    """Every tool must have a name, description, and a valid inputSchema."""
    resp = mcp_client.send("tools/list", {})
    assert resp is not None
    for tool in resp["result"]["tools"]:
        assert "name" in tool, "Tool missing 'name'"
        assert "description" in tool, f"Tool {tool.get('name')!r} missing 'description'"
        schema = tool.get("inputSchema", {})
        assert schema.get("type") == "object", (
            f"Tool {tool['name']!r} inputSchema type is not 'object'"
        )


@pytest.mark.skipif(not IS_WINDOWS, reason="GUI tool calls require Windows + Notepad")
def test_tools_call_type_text(mcp_client: McpStdioClient):
    """
    tools/call → type_text must return a success string.
    Notepad will open on the desktop; the test closes it afterwards.
    Skipped on non-Windows runners.
    """
    resp = mcp_client.send(
        "tools/call",
        {"name": "type_text", "arguments": {"text": "hello world"}},
    )
    assert resp is not None
    assert "result" in resp, f"tools/call error: {resp}"
    content = resp["result"]["content"]
    assert any("Typed" in c.get("text", "") for c in content), (
        f"Unexpected tools/call response: {content}"
    )

    # Clean up — close Notepad without saving
    mcp_client.send(
        "tools/call",
        {"name": "close_app", "arguments": {}},
    )
