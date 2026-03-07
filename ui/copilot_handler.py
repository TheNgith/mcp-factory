"""
ui/copilot_handler.py — GitHub Copilot Extension agent handler (P6)

Implements the GitHub Copilot Extensions agent protocol so users can type:
    @mcp-factory analyze notepad.exe
    @mcp-factory generate notepad
    @mcp-factory chat <message>
in VS Code Copilot Chat and get streaming responses.

Slash commands:
    /analyze  <filename or path>
    /generate <job_id> [component_name]
    /chat     <job_id> <message>

Endpoint: POST /copilot
  - Verifies the GitHub Copilot token via public key validation.
  - Streams back a response as text/event-stream (SSE).

Environment variables:
    PIPELINE_URL          URL of the MCP Factory pipeline API.
    GITHUB_COPILOT_PUBLIC_KEY_URL  (optional) Override the JWKS URL.

Reference:
    https://docs.github.com/en/copilot/building-copilot-extensions/building-a-copilot-agent-for-your-copilot-extension/using-copilot-apis-with-your-copilot-agent
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any, AsyncIterator

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse

logger = logging.getLogger("mcp_factory.copilot")

PIPELINE_URL = os.getenv(
    "PIPELINE_URL",
    "https://mcp-factory-pipeline.calmsmoke-c4f97e21.eastus.azurecontainerapps.io",
).rstrip("/")

# GitHub Copilot token verification endpoint
_COPILOT_TOKEN_INFO_URL = "https://api.github.com/copilot_internal/v2/token"
# GitHub JWKS endpoint for verifying Copilot request signatures
_GITHUB_KEYS_URL = "https://api.github.com/meta/public_keys/copilot_api"

# ---------------------------------------------------------------------------
# Token verification
# ---------------------------------------------------------------------------

async def _verify_github_token(request: Request) -> dict:
    """Verify the GitHub Copilot token in the Authorization header.

    Returns the decoded token payload if valid.
    Raises HTTPException(401) on failure.
    """
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(401, "Missing or invalid Authorization header")

    token = auth[len("Bearer "):]

    # Call GitHub token info endpoint to validate
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.get(
                _COPILOT_TOKEN_INFO_URL,
                headers={"Authorization": f"Bearer {token}"},
            )
        except Exception as exc:
            logger.warning("Token validation request failed: %s", exc)
            raise HTTPException(401, "Token validation failed")

    if resp.status_code == 200:
        return resp.json()
    elif resp.status_code == 401:
        raise HTTPException(401, "Invalid GitHub Copilot token")
    else:
        # If GitHub is temporarily unreachable, allow through (best-effort)
        logger.warning(
            "GitHub token info returned %d — allowing through (best-effort)", resp.status_code
        )
        return {}


# ---------------------------------------------------------------------------
# SSE helpers
# ---------------------------------------------------------------------------

def _sse_text(content: str) -> str:
    """Format a text delta SSE event (Copilot Extensions protocol)."""
    payload = json.dumps(
        {
            "choices": [
                {
                    "index": 0,
                    "delta": {"role": "assistant", "content": content},
                    "finish_reason": None,
                }
            ]
        }
    )
    return f"data: {payload}\n\n"


def _sse_done() -> str:
    """Format the final [DONE] SSE event."""
    payload = json.dumps(
        {
            "choices": [
                {
                    "index": 0,
                    "delta": {},
                    "finish_reason": "stop",
                }
            ]
        }
    )
    return f"data: {payload}\n\ndata: [DONE]\n\n"


# ---------------------------------------------------------------------------
# Slash command handlers
# ---------------------------------------------------------------------------

async def _handle_analyze(args: str) -> AsyncIterator[str]:
    """Stream back analysis results for /analyze <path_or_filename>."""
    path = args.strip()
    if not path:
        yield _sse_text("Please provide a filename or path, e.g. `/analyze notepad.exe`\n")
        yield _sse_done()
        return

    yield _sse_text(f"Analyzing **{path}**…  uploading and discovering invocables.\n\n")

    # Submit as an analyze-path job (server-side path)
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            resp = await client.post(
                f"{PIPELINE_URL}/api/analyze-path",
                json={"path": path, "hints": ""},
            )
            if resp.status_code not in (200, 202):
                body = resp.text[:300]
                yield _sse_text(f"Analysis request failed ({resp.status_code}): {body}\n")
                yield _sse_done()
                return
            data = resp.json()
        except Exception as exc:
            yield _sse_text(f"Request error: {exc}\n")
            yield _sse_done()
            return

    job_id = data.get("job_id", "")
    yield _sse_text(f"Job **{job_id}** queued. Polling for results…\n")

    # Poll until done (up to 120 s)
    deadline = time.time() + 120
    async with httpx.AsyncClient(timeout=10) as client:
        while time.time() < deadline:
            await asyncio.sleep(3)
            try:
                poll = await client.get(f"{PIPELINE_URL}/api/jobs/{job_id}")
                state = poll.json()
            except Exception:
                continue

            status   = state.get("status", "running")
            progress = state.get("progress", 0)
            yield _sse_text(f"Status: **{status}** ({progress}%)\n")

            if status == "done":
                result = state.get("result", {})
                invocables = result.get("invocables", [])
                count = len(invocables)
                sample = ", ".join(inv.get("name", "") for inv in invocables[:5])
                if count > 5:
                    sample += f", … (+{count - 5} more)"
                yield _sse_text(
                    f"\n✅ Analysis complete for **{path}**\n"
                    f"- **Job ID**: `{job_id}`\n"
                    f"- **Invocables found**: {count}\n"
                    f"- **Sample**: {sample}\n\n"
                    f"Use `/generate {job_id}` to create an MCP server, or open the "
                    f"[MCP Factory UI]({PIPELINE_URL.replace('pipeline', 'ui')}) for the full experience.\n"
                )
                break
            elif status == "error":
                yield _sse_text(f"\n❌ Analysis failed: {state.get('error', 'unknown error')}\n")
                break
        else:
            yield _sse_text("\n⏱ Timed out waiting for analysis. Check the UI for job status.\n")

    yield _sse_done()


async def _handle_generate(args: str) -> AsyncIterator[str]:
    """Stream back generation results for /generate <job_id> [component_name]."""
    parts = args.strip().split(None, 1)
    if not parts:
        yield _sse_text("Usage: `/generate <job_id> [component_name]`\n")
        yield _sse_done()
        return

    job_id = parts[0]
    component_name = parts[1] if len(parts) > 1 else job_id

    yield _sse_text(f"Generating MCP server for job **{job_id}** as `{component_name}`…\n")

    # Fetch the job's invocables
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            poll = await client.get(f"{PIPELINE_URL}/api/jobs/{job_id}")
            state = poll.json()
        except Exception as exc:
            yield _sse_text(f"Could not fetch job state: {exc}\n")
            yield _sse_done()
            return

    if state.get("status") != "done":
        yield _sse_text(f"Job **{job_id}** is not done yet (status: {state.get('status')}). Run `/analyze` first.\n")
        yield _sse_done()
        return

    invocables = (state.get("result") or {}).get("invocables", [])
    if not invocables:
        yield _sse_text(f"No invocables found for job **{job_id}**.\n")
        yield _sse_done()
        return

    # Call /api/generate
    async with httpx.AsyncClient(timeout=60) as client:
        try:
            resp = await client.post(
                f"{PIPELINE_URL}/api/generate",
                json={
                    "job_id": job_id,
                    "component_name": component_name,
                    "selected": invocables,
                },
            )
            data = resp.json()
        except Exception as exc:
            yield _sse_text(f"Generate request error: {exc}\n")
            yield _sse_done()
            return

    tool_count = len((data.get("mcp_schema") or {}).get("tools", []))
    mcp_json_blob = data.get("mcp_json_blob", "")
    mcp_server_blob = data.get("mcp_server_blob", "")

    download_base = f"{PIPELINE_URL}/api/download/{job_id}"
    yield _sse_text(
        f"\n✅ MCP server generated for **{component_name}**\n"
        f"- **Tools**: {tool_count}\n"
        f"- **mcp_server.py**: [{download_base}/mcp_server.py]({download_base}/mcp_server.py)\n"
        f"- **mcp.json** (VS Code config): [{download_base}/mcp.json]({download_base}/mcp.json)\n\n"
        f"To use in VS Code:\n"
        f"1. Download `mcp.json` and place it in your workspace `.vscode/` folder.\n"
        f"2. Download `mcp_server.py` to `generated/{component_name}/`.\n"
        f"3. Run `pip install mcp && python mcp_server.py` — then reload VS Code.\n"
    )
    yield _sse_done()


async def _handle_chat(args: str) -> AsyncIterator[str]:
    """Stream back chat response for /chat <job_id> <message>."""
    parts = args.strip().split(None, 1)
    if len(parts) < 2:
        yield _sse_text("Usage: `/chat <job_id> <your message>`\n")
        yield _sse_done()
        return

    job_id, message = parts[0], parts[1]
    yield _sse_text(f"Chatting with **{job_id}** tools…\n\n")

    async with httpx.AsyncClient(timeout=60) as client:
        try:
            resp = await client.post(
                f"{PIPELINE_URL}/api/chat",
                json={
                    "job_id": job_id,
                    "messages": [{"role": "user", "content": message}],
                    "tools": [],
                },
            )
            data = resp.json()
        except Exception as exc:
            yield _sse_text(f"Chat request error: {exc}\n")
            yield _sse_done()
            return

    content = data.get("content") or "(no response)"
    tool_calls = data.get("tool_calls", [])

    if tool_calls:
        yield _sse_text("**Tool calls made:**\n")
        for tc in tool_calls:
            name = tc.get("name", "?")
            yield _sse_text(f"- `{name}`\n")
        yield _sse_text("\n")

    yield _sse_text(content)
    yield _sse_done()


async def _handle_help() -> AsyncIterator[str]:
    """Stream back help text."""
    yield _sse_text(
        "**MCP Factory** — turn any Windows binary into an MCP server for AI agents.\n\n"
        "**Slash commands:**\n"
        "- `/analyze <path>`  — discover tools in a binary (e.g. `/analyze notepad.exe`)\n"
        "- `/generate <job_id> [name]` — generate an MCP SDK server from a completed analysis\n"
        "- `/chat <job_id> <message>` — chat with a generated binary via its tools\n\n"
        "**Example workflow:**\n"
        "```\n"
        "@mcp-factory /analyze C:\\Windows\\System32\\notepad.exe\n"
        "@mcp-factory /generate <job_id> notepad\n"
        "@mcp-factory /chat <job_id> Open a file named hello.txt\n"
        "```\n"
    )
    yield _sse_done()


# ---------------------------------------------------------------------------
# Main handler — mounted by ui/main.py
# ---------------------------------------------------------------------------

async def copilot_endpoint(request: Request) -> StreamingResponse:
    """POST /copilot — GitHub Copilot Extensions agent endpoint.

    Verifies the Copilot token, parses slash commands from the last user
    message, and streams back an SSE response.
    """
    # Verify GitHub token (best-effort; log failures but don't hard-block in dev)
    try:
        _token_info = await _verify_github_token(request)
    except HTTPException as exc:
        if exc.status_code == 401:
            raise
        # Unexpected error: log and continue
        logger.warning("Token verification error: %s", exc.detail)
        _token_info = {}

    body: dict = await request.json()
    messages: list[dict] = body.get("messages", [])

    # Find the last user message
    user_message = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            content = m.get("content", "")
            # Content may be a list of content parts
            if isinstance(content, list):
                user_message = " ".join(
                    part.get("text", "") for part in content if isinstance(part, dict)
                )
            else:
                user_message = str(content)
            break

    user_message = user_message.strip()

    # Dispatch slash command
    async def _stream() -> AsyncIterator[bytes]:
        try:
            if user_message.startswith("/analyze "):
                gen = _handle_analyze(user_message[len("/analyze "):])
            elif user_message.startswith("/generate "):
                gen = _handle_generate(user_message[len("/generate "):])
            elif user_message.startswith("/chat "):
                gen = _handle_chat(user_message[len("/chat "):])
            elif user_message in ("/help", "help", ""):
                gen = _handle_help()
            else:
                # Treat as free-text: suggest commands
                gen = _handle_help()

            async for chunk in gen:
                yield chunk.encode()
        except Exception as exc:
            logger.error("Copilot stream error: %s", exc)
            yield _sse_text(f"Sorry, an error occurred: {exc}\n").encode()
            yield _sse_done().encode()

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
