"""api/chat.py – Agentic chat completions with tool-call execution loop.

run_chat(body)    – blocking JSON response (kept for backward compat).
stream_chat(body) – async generator yielding SSE events in real time so the
                    UI can render tool calls and results as they happen instead
                    of waiting for all rounds to complete.

Each round the OpenAI call runs in a thread executor so the event loop is
never blocked — SSE events are flushed to the client between rounds.

SSE event format:  data: <json>\n\n
Event types:
  {"type": "token",       "content": "..."}          – final text content
  {"type": "tool_call",   "name": "...", "args": {}}  – tool about to execute
  {"type": "tool_result", "name": "...", "result": "..."} – tool output
  {"type": "done",        "rounds": N}                – final event
  {"type": "error",       "message": "..."}           – fatal error
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any, AsyncGenerator

from fastapi import HTTPException

from api.config import OPENAI_ENDPOINT, OPENAI_DEPLOYMENT, OPENAI_MAX_TOOLS
from api.executor import _execute_tool
from api.storage import _register_invocables, _get_invocable
from api.telemetry import _openai_client

logger = logging.getLogger("mcp_factory.api")

# Keep only the last N non-system conversation turns sent to the model each
# round to bound token growth on long sessions.
_CONTEXT_WINDOW_TURNS = 20


def _sse(event: dict) -> str:
    """Format a dict as a single SSE data line."""
    return f"data: {json.dumps(event)}\n\n"


def _build_system_message(invocables: list) -> dict:
    tool_names = ", ".join(inv["name"] for inv in invocables) if invocables else "the available tools"
    return {
        "role": "system",
        "content": (
            "You are an AI agent with direct control over a Windows application via MCP tools.\n"
            "RULES:\n"
            "1. When asked to perform an action, call the appropriate tool(s) immediately. "
            "You may call multiple tools in a single response to perform sequences faster "
            "(e.g. to press 4, then ×, then 3, issue all three calls at once).\n"
            "2. After all tool calls finish, always write a plain-text sentence summarising "
            "what happened and the result visible on screen.\n"
            "3. Never launch an application that is already open — call the launch tool only once per session.\n"
            "4. If the user asks about your capabilities (e.g. 'list your tools', 'what can you do'), "
            "reply with plain text only — do not call any tools.\n"
            "You have access to these tools: " + tool_names + "."
        ),
    }


def run_chat(body: dict[str, Any]) -> dict[str, Any]:
    """Agentic chat: send messages to Azure OpenAI with MCP tool definitions,
    execute tool_calls returned by the model, feed results back, repeat.

    Body: {messages, tools, invocables?, job_id?}
      invocables: full invocable dicts (with execution metadata) needed to
                  dispatch tool calls. If omitted, execution falls back to
                  job_id lookup.

    Returns the final response dict (caller wraps in JSONResponse).
    """
    messages: list   = body.get("messages", [])
    tools: list      = body.get("tools", [])
    invocables: list = body.get("invocables", [])
    job_id: str      = body.get("job_id", "")

    if not messages:
        raise HTTPException(400, "No messages provided")
    if not OPENAI_ENDPOINT:
        raise HTTPException(503, "Azure OpenAI endpoint not configured")

    # Build a local invocable registry for this request
    inv_map: dict[str, dict] = {inv["name"]: inv for inv in invocables}
    if job_id and invocables:
        _register_invocables(job_id, invocables)

    MAX_TOOL_ROUNDS = 50  # hard safety cap only — loop detection stops earlier
    conversation = list(messages)  # working copy
    _all_tool_results: list[dict] = []  # accumulated across all rounds for response
    _last_call_signature: str = ""    # for loop detection
    # actually call tools instead of narrating what the user should do.
    if not any(m.get("role") == "system" for m in conversation):
        conversation.insert(0, _build_system_message(invocables))

    try:
        client = _openai_client()
        msg = None
        _tool_calls_total = 0
        _chat_t0 = time.perf_counter()

        # ── P5: Semantic tool selection ─────────────────────────────────────
        # Only filter when the tool list exceeds the model's hard API limit.
        # Below that ceiling every tool is passed directly so the model always
        # has the full set available.
        _AI_SEARCH_TOP_K = OPENAI_MAX_TOOLS
        _active_tools = list(tools)  # per-turn tool subset
        _last_user_message = next(
            (m.get("content", "") for m in reversed(conversation) if m.get("role") == "user"),
            "",
        )
        if len(tools) > _AI_SEARCH_TOP_K and job_id and _last_user_message:
            try:
                from search import retrieve_tools as _retrieve_tools  # type: ignore
                _semantic_tools = _retrieve_tools(job_id, _last_user_message, client, top_k=_AI_SEARCH_TOP_K)
                if _semantic_tools:
                    _active_tools = _semantic_tools
                    logger.info(
                        "[%s] Semantic retrieval: %d/%d tools selected",
                        job_id, len(_active_tools), len(tools),
                    )
            except Exception as _se:
                logger.warning("[%s] Semantic tool retrieval failed: %s", job_id, _se)

        # Track which launcher tools have already been called this session
        # so semantic retrieval can exclude them from subsequent rounds.
        _called_launchers: set[str] = set()

        for _round in range(MAX_TOOL_ROUNDS):
            # After round 0, if the tool list is large enough to need filtering,
            # re-run semantic retrieval using the model's last assistant message
            # as the query — this keeps the retrieved set aligned with whatever
            # step the model is currently working on rather than the original
            # user prompt (critical for long multi-step tasks over large tool sets).
            if _round > 0 and len(tools) > _AI_SEARCH_TOP_K and job_id:
                _rolling_query = _last_user_message
                # Use the last assistant content as a better query if available
                for m in reversed(conversation):
                    if m.get("role") == "assistant" and m.get("content"):
                        _rolling_query = m["content"]
                        break
                try:
                    from search import retrieve_tools as _retrieve_tools  # type: ignore
                    _semantic_tools = _retrieve_tools(job_id, _rolling_query, client, top_k=_AI_SEARCH_TOP_K)
                    if _semantic_tools:
                        _active_tools = [t for t in _semantic_tools
                                         if t.get("function", {}).get("name") not in _called_launchers]
                except Exception as exc:
                    logger.warning("[%s] Semantic tool retrieval refresh failed: %s", job_id, exc)
                    # Fall back to filtering in-memory without a new retrieval
                    _active_tools = [t for t in _active_tools
                                     if t.get("function", {}).get("name") not in _called_launchers]
            elif _round > 0 and _called_launchers:
                _active_tools = [t for t in _active_tools
                                 if t.get("function", {}).get("name") not in _called_launchers]

            kwargs: dict = {
                "model": OPENAI_DEPLOYMENT,
                "messages": conversation,
                "temperature": 0.2,
            }
            if _active_tools:
                kwargs["tools"] = _active_tools
                kwargs["tool_choice"] = "auto"

            response = client.chat.completions.create(**kwargs)
            msg = response.choices[0].message

            # No tool calls → final answer
            if not msg.tool_calls:
                logger.info(
                    "[chat] completed in %d round(s), %d tool call(s)",
                    _round + 1, _tool_calls_total,
                    extra={"custom_dimensions": {
                        "event": "chat_complete",
                        "job_id": job_id,
                        "rounds": _round + 1,
                        "tool_calls_total": _tool_calls_total,
                        "duration_ms": int((time.perf_counter() - _chat_t0) * 1000),
                    }},
                )
                return {
                    "role": msg.role,
                    "content": msg.content,
                    "tool_calls": [],
                    "tool_results": _all_tool_results,
                    "rounds": _round + 1,
                }

            # Append assistant turn with tool_calls to conversation
            assistant_turn: dict[str, Any] = {
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in msg.tool_calls
                ],
            }
            conversation.append(assistant_turn)

            _tool_calls_total += len(msg.tool_calls)

            # Loop detection: if every tool call this round is identical to last round, stop.
            _this_sig = "|".join(f"{tc.function.name}:{tc.function.arguments}" for tc in msg.tool_calls)
            if _this_sig == _last_call_signature:
                logger.warning("[chat] Loop detected (same calls twice) — forcing summary")
                break
            _last_call_signature = _this_sig

            # Execute each tool call and append tool result messages
            for tc in msg.tool_calls:
                fn_name = tc.function.name
                try:
                    fn_args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    fn_args = {}

                inv = inv_map.get(fn_name)
                if inv is None and job_id:
                    inv = _get_invocable(job_id, fn_name)

                if inv is not None:
                    tool_result = _execute_tool(inv, fn_args)
                    # Track launcher invocables (CLI tools whose name == exe stem)
                    # so they are excluded from semantic retrieval in subsequent rounds.
                    if inv.get("source_type") == "cli" and Path(inv.get("dll_path", "")).stem.lower() == fn_name.lower():
                        _called_launchers.add(fn_name)
                    logger.info(f"[chat/{_round}] Executed {fn_name}: {tool_result[:120]}")
                else:
                    tool_result = (
                        f"Tool '{fn_name}' executed (no invocable metadata "
                        f"available — pass 'invocables' in the request body "
                        f"or call /api/generate first). "
                        f"Raw arguments: {json.dumps(fn_args)}"
                    )
                    logger.warning(f"[chat/{_round}] No invocable for {fn_name}")

                _all_tool_results.append({"name": fn_name, "arguments": fn_args, "result": tool_result})
                conversation.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": tool_result,
                })

        # Exceeded MAX_TOOL_ROUNDS — force one final text-only summary from the model
        if msg is None:
            return {"role": "assistant", "content": "", "tool_calls": [], "tool_results": [], "rounds": 0}
        summary_content = "All steps completed."
        try:
            _summary_resp = client.chat.completions.create(
                model=OPENAI_DEPLOYMENT,
                messages=conversation,
                temperature=0.2,
                tools=_active_tools,
                tool_choice="none",
            )
            summary_content = _summary_resp.choices[0].message.content or summary_content
        except Exception as _se:
            logger.warning("[chat] Final summary call failed: %s", _se)
        return {
            "role": "assistant",
            "content": summary_content,
            "tool_calls": [],
            "tool_results": _all_tool_results,
            "rounds": MAX_TOOL_ROUNDS,
        }

    except Exception as e:
        logger.error(f"Chat error: {e}")
        raise HTTPException(500, f"Chat failed: {e}")


async def stream_chat(body: dict[str, Any]) -> AsyncGenerator[str, None]:
    """Async generator: runs the agentic tool-call loop yielding SSE events
    between rounds so the browser sees progress in real time.

    Each OpenAI call runs in a thread executor so the async event loop is
    never blocked — SSE events flush to the client immediately after each
    yield.  No OpenAI stream=True is used; we get per-round feedback instead
    of per-token, which is much more reliable with the sync AzureOpenAI client.
    """
    messages: list   = body.get("messages", [])
    tools: list      = body.get("tools", [])
    invocables: list = body.get("invocables", [])
    job_id: str      = body.get("job_id", "")

    if not messages:
        yield _sse({"type": "error", "message": "No messages provided"})
        return
    if not OPENAI_ENDPOINT:
        yield _sse({"type": "error", "message": "Azure OpenAI endpoint not configured"})
        return

    inv_map: dict[str, dict] = {inv["name"]: inv for inv in invocables}
    if job_id and invocables:
        _register_invocables(job_id, invocables)

    MAX_TOOL_ROUNDS = 10
    _last_call_signature = ""

    # Build conversation: system prompt first, then last N user/assistant turns.
    sys_msgs  = [m for m in messages if m.get("role") == "system"]
    user_msgs = [m for m in messages if m.get("role") != "system"]
    if not sys_msgs:
        sys_msgs = [_build_system_message(invocables)]
    conversation = sys_msgs + user_msgs[-_CONTEXT_WINDOW_TURNS:]

    _active_tools = list(tools)
    _called_launchers: set[str] = set()
    _tools_executed: list[str] = []  # names of tool calls that actually ran

    loop = asyncio.get_event_loop()

    try:
        client = _openai_client()

        for _round in range(MAX_TOOL_ROUNDS):
            # Exclude already-fired launcher tools so the model can't re-launch.
            if _called_launchers:
                _active_tools = [t for t in _active_tools
                                 if t.get("function", {}).get("name") not in _called_launchers]

            kwargs: dict = {
                "model":       OPENAI_DEPLOYMENT,
                "messages":    conversation,
                "temperature": 0,
            }
            if _active_tools:
                kwargs["tools"]       = _active_tools
                kwargs["tool_choice"] = "auto"

            # Run the blocking OpenAI call in a thread so the event loop stays
            # free to flush already-yielded SSE events to the client.
            response = await loop.run_in_executor(
                None,
                lambda kw=kwargs: client.chat.completions.create(**kw),
            )
            msg = response.choices[0].message

            # ── No tool calls → final text answer ─────────────────────────
            if not msg.tool_calls:
                if msg.content:
                    yield _sse({"type": "token", "content": msg.content})
                elif _tools_executed:
                    # Model finished all tool calls but returned no summary text
                    # (common Azure OpenAI behaviour at temperature=0).  Emit a
                    # synthetic token so the UI shows something useful instead
                    # of a placeholder.
                    names = ", ".join(_tools_executed[-3:])
                    yield _sse({"type": "token", "content": f"Done — ran: {names}."})
                yield _sse({"type": "done", "rounds": _round + 1})
                return

            # ── Loop detection ─────────────────────────────────────────────
            _this_sig = "|".join(
                f"{tc.function.name}:{tc.function.arguments}" for tc in msg.tool_calls
            )
            if _this_sig == _last_call_signature:
                logger.warning("[stream_chat] Loop detected — stopping")
                yield _sse({"type": "done", "rounds": _round + 1})
                return
            _last_call_signature = _this_sig

            # Append assistant turn with tool_calls to conversation
            conversation.append({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in msg.tool_calls
                ],
            })

            # ── Execute each tool call, streaming result events immediately ─
            for tc in msg.tool_calls:
                fn_name = tc.function.name
                try:
                    fn_args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    fn_args = {}

                yield _sse({"type": "tool_call", "name": fn_name, "args": fn_args})

                inv = inv_map.get(fn_name)
                if inv is None and job_id:
                    inv = _get_invocable(job_id, fn_name)

                if inv is not None:
                    # Tool execution can be slow (pywinauto, GUI interaction) —
                    # run in executor so the SSE response stays live.
                    tool_result = await loop.run_in_executor(
                        None, lambda i=inv, a=fn_args: _execute_tool(i, a)
                    )
                    if inv.get("source_type") == "cli" and \
                            Path(inv.get("dll_path", "")).stem.lower() == fn_name.lower():
                        _called_launchers.add(fn_name)
                else:
                    tool_result = (
                        f"Tool '{fn_name}' not found — pass 'invocables' in the "
                        f"request body or call /api/generate first."
                    )

                yield _sse({"type": "tool_result", "name": fn_name, "result": tool_result})
                logger.info("[stream_chat/%d] %s → %s", _round, fn_name, tool_result[:120])
                _tools_executed.append(fn_name)

                conversation.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": tool_result,
                })

            # Trim conversation to bound token size each round.
            _sys  = [m for m in conversation if m.get("role") == "system"]
            _rest = [m for m in conversation if m.get("role") != "system"]
            conversation = _sys + _rest[-_CONTEXT_WINDOW_TURNS:]

        yield _sse({"type": "done", "rounds": MAX_TOOL_ROUNDS})

    except Exception as exc:
        logger.error("stream_chat error: %s", exc)
        yield _sse({"type": "error", "message": str(exc)})
