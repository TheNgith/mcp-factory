"""api/chat.py – Agentic chat completions with tool-call execution loop.

run_chat(body) implements everything inside the /api/chat handler beyond
routing: system-prompt injection, semantic tool selection, the tool-call loop,
loop detection, and the forced summary on MAX_TOOL_ROUNDS.

Returns a plain dict suitable for wrapping in JSONResponse by the caller.
Raises fastapi.HTTPException on validation errors or Azure OpenAI failures.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from api.config import OPENAI_ENDPOINT, OPENAI_DEPLOYMENT
from api.executor import _execute_tool
from api.storage import _register_invocables, _get_invocable
from api.telemetry import _openai_client

logger = logging.getLogger("mcp_factory.api")


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
        tool_names = ", ".join(inv["name"] for inv in invocables) if invocables else "the available tools"
        conversation.insert(0, {
            "role": "system",
            "content": (
                "You are an AI agent with direct control over a Windows application via MCP tools. "
                "RULES YOU MUST FOLLOW:\n"
                "1. When asked to perform actions, call tools immediately — never describe what you would do.\n"
                "2. Do NOT launch an application that is already open. Only call the launch tool once. "
                "If the tool result says the app was launched or is already running, "
                "NEVER call that launch tool again in this session under any circumstances.\n"
                "3. You can call MULTIPLE tools in a single response — do this to perform sequences faster. "
                "For example, to press 4 then × then 3, issue all three tool calls at once.\n"
                "4. After completing all actions, report the final result shown on screen.\n"
                "5. If the user asks a question about your tools or capabilities (e.g. 'list your tools', "
                "'what can you do'), respond with a plain text answer — do NOT call any tools.\n"
                "You have access to these tools: " + tool_names + "."
            ),
        })

    try:
        client = _openai_client()
        msg = None
        _tool_calls_total = 0
        _chat_t0 = time.perf_counter()

        # ── P5: Semantic tool selection ─────────────────────────────────────
        # If the tool list is large (> 15), retrieve only the top-15 most
        # semantically relevant tools per user turn to stay inside the GPT-4o
        # 128-tool limit and reduce prompt tokens.
        _AI_SEARCH_TOP_K = 15
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
            # After round 0, refresh semantic tool selection with launchers excluded
            if _round > 0 and len(tools) > _AI_SEARCH_TOP_K and _called_launchers:
                try:
                    from search import retrieve_tools as _retrieve_tools  # type: ignore
                    _semantic_tools = _retrieve_tools(job_id, _last_user_message, client, top_k=_AI_SEARCH_TOP_K)
                    if _semantic_tools:
                        _active_tools = [t for t in _semantic_tools
                                         if t.get("function", {}).get("name") not in _called_launchers]
                except Exception as exc:
                    logger.warning("[%s] Semantic tool retrieval refresh failed: %s", job_id, exc)

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
