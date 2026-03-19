"""api/chat.py – Agentic chat completions with tool-call execution loop.

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
    {"type": "status",      "stage": "...", "message": "..."} – keepalive/progress
  {"type": "done",        "rounds": N}                – final event
  {"type": "error",       "message": "..."}           – fatal error
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from pathlib import Path
from typing import Any, AsyncGenerator

from fastapi import HTTPException

from api.config import OPENAI_ENDPOINT, OPENAI_DEPLOYMENT, OPENAI_REASONING_DEPLOYMENT, OPENAI_MAX_TOOLS, OPENAI_API_KEY, OPENAI_MODEL, OPENAI_CHAT_MODEL
from api.executor import _execute_tool, _execute_tool_traced
from api.storage import _register_invocables, _get_invocable, _load_findings, _save_finding, _patch_invocable, _append_diagnosis_raw, _append_executor_trace
from api.telemetry import _openai_client

logger = logging.getLogger("mcp_factory.api")

# Keep only the last N non-system conversation turns sent to the model each
# round to bound token growth on long sessions.
_CONTEXT_WINDOW_TURNS = 20

_GENERIC_PARAM = re.compile(r'^param_\d+$')


# ── Synthetic record_finding tool definition (always injected) ────────────
_RECORD_FINDING_TOOL = {
    "type": "function",
    "function": {
        "name": "record_finding",
        "description": (
            "Record a discovered fact about a function's calling convention or parameter semantics. "
            "Call this ONLY when you have conclusive evidence: either a call succeeded (non-error return) "
            "or you have definitively exhausted all encoding options for a parameter. "
            "Findings persist across sessions so future calls start informed."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "function_name": {"type": "string", "description": "The exact function name from the schema"},
                "param_name":    {"type": "string", "description": "The parameter this finding relates to, e.g. param_1"},
                "finding":       {"type": "string", "description": "Plain English description: what works, what fails, and why"},
                "working_call":  {"type": "object", "description": "The exact args dict that produced a non-error return, if any"},
            },
            "required": ["function_name", "finding"],
        },
    },
}

# ── Synthetic enrich_invocable tool definition (always injected) ─────────
_ENRICH_INVOCABLE_TOOL = {
    "type": "function",
    "function": {
        "name": "enrich_invocable",
        "description": (
            "Write discovered semantics back into the schema for a function. "
            "Call this when you know what a parameter actually means — e.g. after a successful call "
            "reveals that param_1 is a customer_id buffer. "
            "Pair with record_finding so the same discovery is both documented and schema-enriched."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "function_name": {"type": "string", "description": "The exact function name from the schema"},
                "function_description": {"type": "string", "description": "Human-readable description of what this function does"},
                "params": {
                    "type": "object",
                    "description": (
                        "Mapping of old generic param names to semantic info. "
                        "e.g. {\"param_1\": {\"name\": \"customer_id_buffer\", \"description\": \"Pointer to output buffer receiving the customer ID string\"}}"
                    ),
                },
            },
            "required": ["function_name"],
        },
    },
}


def _schema_quality(invocables: list) -> str:
    """Return 'basic' when every parameter across all invocables has a generic
    name (param_1, param_2…), 'rich' otherwise.  'basic' signals a black-box
    binary where the reasoning model should be preferred."""
    for inv in invocables:
        for param in inv.get("parameters", []):
            if not _GENERIC_PARAM.match(param.get("name", "param_0")):
                return "rich"
    return "basic"


def _keyword_filter_tools(query: str, tools: list, top_k: int) -> list:
    """Score tools by keyword overlap with query; return up to top_k.

    Used as a zero-cost fallback when the semantic embedding cache is cold
    (container restart, no Azure AI Search, embed_and_index failure).
    Exact tool-name matches score highest so that direct requests like
    'Use IsBrowsable' always surface the right tool.
    """
    q_lower = query.lower()
    words = [w.strip("\"'().,;:!?") for w in q_lower.split() if len(w) > 2]

    scored: list[tuple[float, dict]] = []
    for t in tools:
        fn   = t.get("function", {})
        name = fn.get("name", "").lower()
        desc = (fn.get("description", "") or "").lower()
        score = 0.0
        for w in words:
            if w == name:
                score += 10.0       # exact name match — highest weight
            elif w in name:
                score += 4.0        # partial name match
            elif w in desc:
                score += 1.0        # description mention
        if score > 0:
            scored.append((score, t))

    scored.sort(key=lambda x: x[0], reverse=True)
    result = [t for _, t in scored[:top_k]]

    # Pad with first tools if we don't have enough keyword matches
    if len(result) < top_k:
        seen = {t.get("function", {}).get("name") for t in result}
        for t in tools:
            if len(result) >= top_k:
                break
            if t.get("function", {}).get("name") not in seen:
                result.append(t)
    return result


def _sse(event: dict) -> str:
    """Format a dict as a single SSE data line."""
    return f"data: {json.dumps(event)}\n\n"


def _build_system_message(invocables: list, job_id: str = "") -> dict:
    # Load vocab accumulated during exploration (id formats, conventions, etc.)
    vocab_block = ""
    _domain_preamble = ""
    _id_formats: list = []
    if job_id:
        try:
            from api.storage import _download_blob
            from api.config import ARTIFACT_CONTAINER
            import json as _json
            _vocab = _json.loads(_download_blob(ARTIFACT_CONTAINER, f"{job_id}/vocab.json"))
            if _vocab:
                from api.explore import _vocab_block
                # Strip description/user_context before passing to _vocab_block —
                # they're already hoisted into _domain_preamble above the rules.
                _vocab_for_block = {k: v for k, v in _vocab.items()
                                    if k not in ("description", "user_context")}
                vocab_block = "\n" + _vocab_block(_vocab_for_block) + "\n"
                _id_formats = list(_vocab.get("id_formats") or [])
                # Extract domain framing to inject BEFORE the rules so the model
                # reads business context first, then interprets rules through that lens.
                _desc = (_vocab.get("description") or "").strip()
                _uctx = (_vocab.get("user_context") or "").strip()
                if _desc or _uctx:
                    _preamble_lines = ["COMPONENT BEING CONTROLLED:"]
                    if _desc:
                        _preamble_lines.append(f"  {_desc}")
                    if _uctx and _uctx != _desc:
                        _preamble_lines.append(f"  Integration intent: {_uctx}")
                    _domain_preamble = "\n".join(_preamble_lines) + "\n\n"
        except Exception:
            pass  # vocab not yet built or blob miss — fine

    # Build DLL-specific enforcement rules from vocab.
    _id_format_rule = ""
    if _id_formats:
        _fmts = ", ".join(str(f) for f in _id_formats)
        _id_format_rule = (
            "ID FORMAT RULE:\n"
            f"   Valid ID patterns for this component: {_fmts}. "
            "Before passing any string argument to a DLL function, verify it matches one of these. "
            "If the value does not match (e.g. 'ABC', 'LOCKED', 'test', or a bare number), "
            "do NOT call the function — tell the user the value is invalid and show the correct formats.\n"
        )
    _error_code_rule = (
        "ERROR CODE RULE:\n"
        "   Before interpreting ANY non-zero integer return value — whether from a live call or a "
        "user question like 'what does 0xFFFFFFFC mean?' — ALWAYS consult error_codes in vocab first. "
        "NEVER describe a return value as 'access violation' or guess its meaning without first "
        "looking it up. If it is listed in error_codes, state that exact meaning.\n"
    )

    # Load any findings from previous sessions for this job.
    prior = _load_findings(job_id) if job_id else []
    findings_block = ""
    if prior:
        lines = []
        for f in prior:
            fn   = f.get("function", "?")
            pm   = f.get("param", "")
            note = f.get("finding", "")
            wc   = f.get("working_call")
            line = f"  - {fn}{(' ' + pm) if pm else ''}: {note}"
            if wc:
                line += f" (working call: {wc})"
            lines.append(line)
        findings_block = (
            "\nKNOWN WORKING PATTERNS (discovered in previous sessions — use these first):\n"
            + "\n".join(lines)
            + "\n"
        )
    # Detect prerequisite/initialisation functions — first from schema criticality labels,
    # then fall back to naming convention heuristics.
    required_first = [
        inv["name"] for inv in invocables
        if inv.get("criticality") == "required_first"
    ]
    if not required_first:
        _INIT_SUFFIXES = ("initialize", "init", "startup", "start", "setup", "open", "login", "logon", "connect")
        required_first = [
            inv["name"] for inv in invocables
            if any(inv["name"].lower() == s or inv["name"].lower().endswith(s) or f"_{s}" in inv["name"].lower()
                   for s in _INIT_SUFFIXES)
        ]

    # Build criticality summary block — surfaces write vs read classification
    _crit_lines = []
    for inv in invocables:
        c = inv.get("criticality")
        if c and c != "unknown":
            deps = inv.get("depends_on") or []
            dep_str = f" (requires: {', '.join(deps)})" if deps else ""
            _crit_lines.append(f"  {inv['name']}: {c}{dep_str}")
    criticality_block = ""
    if _crit_lines:
        criticality_block = (
            "\nFUNCTION ROLES (from enrichment analysis):\n"
            + "\n".join(_crit_lines)
            + "\n"
        )

    init_rule = ""
    if required_first:
        names = ", ".join(required_first)
        init_rule = (
            f"\n6. This session includes setup/initialisation functions: {names}. "
            "Call ALL of them silently before any other function from the same library. "
            "Never mention this to the user unless initialisation itself fails."
        )

    return {
        "role": "system",
        "content": (
            _domain_preamble
            + "You are an AI agent that calls DLL functions via MCP tools to service user requests.\n"
            "RULES:\n"
            "1. When asked to perform an action, call the appropriate DLL function tool(s) immediately. "
            "You may batch multiple independent calls in a single response to perform sequences faster — "
            "never make one call per response when several can be issued together.\n"
            "2. After all tool calls finish, write a plain-text summary of what was done and the result. "
            "Always decode return values for the user (e.g. convert cents to dollars, resolve error codes by name).\n"
            "3. If the user asks about your capabilities (e.g. 'list your tools', 'what can you do'), "
            "reply with plain text only — do not call any tools.\n"
            + _id_format_rule
            + _error_code_rule
            + "OUTPUT BUFFER RULE (critical for DLL functions):\n"
            "   Params typed as undefined*, undefined4*, undefined8*, uint*, or int* that are output "
            "buffers must NEVER be included in tool call arguments. Omit them entirely; the executor "
            "auto-allocates the buffer and returns their value as 'param_N=<value>' in the result. "
            "Any function with an output-buffer param (typed as undefined*, undefined4*, undefined8*, uint*, int*) must have that param omitted — do NOT pass it.\n"
            "ZERO-OUTPUT RETRY RULE:\n"
            "   If a call returns 0 (success) but every output param shows value=0, the inputs were "
            "too small. Retry once with LARGER values before concluding the output is always zero: "
            "for financial/calculation functions use principal=10000, rate=500, period=12; "
            "for general numeric functions try 1000, 10000, 100000.\n"
            "5. If a tool call returns 4294967295 or -1 (error sentinel), follow this EXACT probing protocol:\n"
            "   STEP A — pointer encoding (execute in ONE round before anything else):\n"
            "     For every pointer-typed param (byte*, undefined*, BYTE*, char*) with a user-supplied value, "
            "call the function TWICE in the SAME round: once with the value as a JSON string (e.g. \"1042\") "
            "AND once with the value as a plain JSON integer (e.g. 1042). "
            "Strings become heap pointers; integers land directly in the register. Stop when one succeeds.\n"
            "   STEP B — scalar size probe (only after STEP A still fails, batch ALL in ONE round):\n"
            "     For uint/scalar parameters that may be buffer sizes, issue these as SEPARATE tool calls "
            "in the SAME response: param_3=64, param_3=256, param_3=512, param_3=1024. Stop when one succeeds.\n"
            "   STEP C — cross-product (only if both above fail):\n"
            "     Combine integer encoding for pointer params WITH each scalar probe value. Batch them.\n"
            "   NEVER probe one value per round when you can batch multiple calls in a single response.\n"
            "   When any call succeeds, immediately call record_finding with the exact working args.\n"
            "   - 'access violation': a pointer argument was missing — infer from user context and retry.\n"
            "   - If a prerequisite function (Initialize, Open, Login) exists, call it first.\n"
            "   - Only report failure to the user after exhausting all steps above.\n"
            "7. MANDATORY PERSISTENCE: after ANY tool call that returns 0 (success), you MUST call "
            "record_finding in the SAME response round with status='success' and working_call set to "
            "the exact args that produced 0. This is not optional — skipping it means the discovery "
            "is permanently lost. Similarly, after exhausting all probes on a failing function, call "
            "record_finding with status='error' and note the exact error code(s) observed. "
            "Do NOT call record_finding speculatively or for intermediate/uncertain results — only "
            "for definitive success (return=0) or confirmed failure (all probes returned sentinels)."
            + init_rule
            + criticality_block
            + vocab_block
            + findings_block
        ),
    }


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
    if not OPENAI_ENDPOINT and not OPENAI_API_KEY:
        yield _sse({"type": "error", "message": "OpenAI not configured — set OPENAI_API_KEY or AZURE_OPENAI_ENDPOINT"})
        return

    inv_map: dict[str, dict] = {inv["name"]: inv for inv in invocables}

    # ── Server-side invocable + tool-schema fallback ───────────────────────
    # When the caller passes only job_id (headless test runner, CI pipeline)
    # and omits the invocables/tools arrays, load them from the in-memory
    # registry or blob storage so the chat session is always self-sufficient.
    # This removes the fragile "pass 25 KB of JSON through the PS serializer"
    # dependency and survives container restarts that wipe in-memory state.
    if not invocables and job_id:
        from api.storage import _JOB_INVOCABLE_MAPS as _jimap  # type: ignore
        _loaded: dict = {}
        if job_id in _jimap:
            _loaded = dict(_jimap[job_id])
        else:
            try:
                import json as _json_fb
                from api.storage import _download_blob as _dl_fb
                from api.config import ARTIFACT_CONTAINER as _AC_fb
                _raw_inv = _dl_fb(_AC_fb, f"{job_id}/invocables_map.json")
                _loaded = _json_fb.loads(_raw_inv)
                _jimap[job_id] = _loaded
                logger.info("[%s] stream_chat: loaded %d invocables from blob fallback", job_id, len(_loaded))
            except Exception as _fb_e:
                logger.debug("[%s] stream_chat: blob fallback failed: %s", job_id, _fb_e)
        if _loaded:
            invocables = list(_loaded.values())
            inv_map.update(_loaded)

    # ── Tool-schema fallback ───────────────────────────────────────────────
    # If the caller sent no tools array (or it was empty), build OpenAI tool
    # schemas from the invocables so the LLM always has the DLL functions.
    if not tools and invocables:
        import re as _re_chat
        for _inv in invocables:
            _props: dict = {}
            _required: list = []
            for _p in (_inv.get("parameters") or []):
                if isinstance(_p, str):
                    _p = {"name": _p, "type": "string"}
                _pname = _p.get("name", "arg")
                _json_type = _p.get("json_type") or "string"
                _props[_pname] = {
                    "type": _json_type,
                    "description": _p.get("description") or _p.get("type", "string"),
                }
                if _p.get("direction", "in") != "out":
                    _required.append(_pname)
            _safe = _re_chat.sub(r"[^a-zA-Z0-9_.\-]", "_", _inv["name"])[:64]
            _desc = _inv.get("doc") or _inv.get("description") or _inv["name"]
            tools.append({
                "type": "function",
                "function": {
                    "name": _safe,
                    "description": _desc,
                    "parameters": {"type": "object", "properties": _props, "required": _required},
                },
            })
            # Ensure inv_map lookup works whether the model uses the raw or sanitised name
            if _safe not in inv_map:
                inv_map[_safe] = _inv

    MAX_TOOL_ROUNDS = 14
    _last_call_signature = ""

    # ── Dynamic model selection ────────────────────────────────────────────
    # Use the reasoning model when schema quality is low (all generic param
    # names like param_1, param_2) — this signals a black-box binary where
    # the model needs more reasoning capacity to probe parameter semantics.
    # When using direct OpenAI (OPENAI_API_KEY set), use OPENAI_MODEL instead
    # of the Azure deployment name.
    # OPENAI_CHAT_MODEL lets you cheaply override chat only (e.g. gpt-4o-mini)
    # without affecting the explore model.  Falls back to OPENAI_MODEL / deployment.
    _resolved_chat_model = OPENAI_CHAT_MODEL or (OPENAI_MODEL if OPENAI_API_KEY else OPENAI_DEPLOYMENT)
    _base_model = _resolved_chat_model
    _reasoning_model = OPENAI_CHAT_MODEL or (OPENAI_MODEL if OPENAI_API_KEY else OPENAI_REASONING_DEPLOYMENT)
    _schema_q = _schema_quality(invocables) if invocables else "rich"
    _active_model = _reasoning_model if _schema_q == "basic" else _base_model
    _failure_count = 0  # consecutive 0xFFFFFFFF / -1 returns; triggers mid-session escalation
    if _schema_q == "basic" and invocables:
        logger.info(
            "[%s] stream_chat: generic schema (black-box binary) — using reasoning model %s",
            job_id, _active_model,
        )

    # Build conversation: system prompt first, then last N user/assistant turns.
    sys_msgs  = [m for m in messages if m.get("role") == "system"]
    user_msgs = [m for m in messages if m.get("role") != "system"]
    if not sys_msgs:
        sys_msgs = [_build_system_message(invocables, job_id)]
    conversation = sys_msgs + user_msgs[-_CONTEXT_WINDOW_TURNS:]

    _AI_SEARCH_TOP_K = OPENAI_MAX_TOOLS
    _active_tools = list(tools)
    # Always inject the synthetic findings tool so the LLM can record
    # discoveries regardless of which DLL tools are in scope.
    _findings_inv = {
        "name": "record_finding",
        "source_type": "findings",
        "_job_id": job_id,
        "execution": {"method": "findings"},
        "parameters": [],
    }
    inv_map["record_finding"] = _findings_inv
    if not any(t.get("function", {}).get("name") == "record_finding" for t in _active_tools):
        _active_tools.append(_RECORD_FINDING_TOOL)
    _enrich_inv = {
        "name": "enrich_invocable",
        "source_type": "enrich",
        "_job_id": job_id,
        "execution": {"method": "enrich"},
        "parameters": [],
    }
    inv_map["enrich_invocable"] = _enrich_inv
    if not any(t.get("function", {}).get("name") == "enrich_invocable" for t in _active_tools):
        _active_tools.append(_ENRICH_INVOCABLE_TOOL)
    _called_launchers: set[str] = set()
    _tools_executed: list[str] = []  # names of tool calls that actually ran
    _tool_log: list[dict] = []       # full call+result log for transcript
    _last_user_message = next(
        (m.get("content", "") for m in reversed(conversation) if m.get("role") == "user"),
        "",
    )

    loop = asyncio.get_event_loop()

    # Only re-register (and re-upload to blob) when the server doesn't already
    # have this job's invocables in memory.  The UI re-sends the full list on
    # every message (~2 MB for 1000 tools), so skipping this on subsequent
    # turns avoids a redundant 2 MB blob upload that would block the async
    # generator before the first SSE event is yielded.
    if job_id and invocables:
        from api.storage import _JOB_INVOCABLE_MAPS as _jimap  # type: ignore
        if job_id not in _jimap:
            loop.run_in_executor(None, _register_invocables, job_id, invocables)

    try:
        client = _openai_client()

        # ── P5: Initial semantic tool selection (mirrors run_chat) ─────────
        # stream_chat had no filtering at all — every tool was sent on every
        # round, which blows the token budget for large schemas like shell32
        # and causes the model to hang on round 2+ (after tool execution).
        if len(tools) > _AI_SEARCH_TOP_K and job_id and _last_user_message:
            try:
                from api.search import retrieve_tools as _retrieve_tools  # type: ignore
                _semantic_tools = await loop.run_in_executor(
                    None,
                    lambda: _retrieve_tools(job_id, _last_user_message, client, top_k=_AI_SEARCH_TOP_K),
                )
                if _semantic_tools:
                    _active_tools = _semantic_tools
                    logger.info(
                        "[%s] stream_chat: semantic selected %d/%d tools",
                        job_id, len(_active_tools), len(tools),
                    )
                else:
                    _active_tools = _keyword_filter_tools(_last_user_message, tools, _AI_SEARCH_TOP_K)
                    logger.warning(
                        "[%s] stream_chat: semantic empty; keyword fallback %d→%d",
                        job_id, len(tools), _AI_SEARCH_TOP_K,
                    )
            except Exception as _se:
                logger.warning("[%s] stream_chat: semantic retrieval failed: %s", job_id, _se)
                _active_tools = _keyword_filter_tools(_last_user_message, tools, _AI_SEARCH_TOP_K)

        # Load vocab error_codes once so the executor can annotate DLL-specific
        # return codes beyond the five hardcoded sentinels (e.g. a DLL with its
        # own error table distinct from the contoso_cs defaults).
        _vocab_sentinels: dict | None = None
        if job_id:
            try:
                from api.storage import _download_blob as _dvb
                from api.config import ARTIFACT_CONTAINER as _ac
                import json as _jv
                _vraw = _jv.loads(_dvb(_ac, f"{job_id}/vocab.json"))
                _vocab_sentinels = _vraw.get("error_codes") if _vraw else None
            except Exception:
                pass

        for _round in range(MAX_TOOL_ROUNDS):
            # ── Per-round semantic re-selection ────────────────────────────
            # After round 0, re-query using the model's last assistant content
            # as the search string so the retrieved set tracks what the model
            # is currently working on rather than the original user prompt.
            if _round > 0 and len(tools) > _AI_SEARCH_TOP_K and job_id:
                _rolling_query = _last_user_message
                for m in reversed(conversation):
                    if m.get("role") == "assistant" and m.get("content"):
                        _rolling_query = m["content"]
                        break
                try:
                    from api.search import retrieve_tools as _retrieve_tools  # type: ignore
                    _semantic_tools = await loop.run_in_executor(
                        None,
                        lambda q=_rolling_query: _retrieve_tools(job_id, q, client, top_k=_AI_SEARCH_TOP_K),
                    )
                    if _semantic_tools:
                        _active_tools = [t for t in _semantic_tools
                                         if t.get("function", {}).get("name") not in _called_launchers]
                    else:
                        _kw = _keyword_filter_tools(_rolling_query, tools, _AI_SEARCH_TOP_K)
                        _active_tools = [t for t in _kw
                                         if t.get("function", {}).get("name") not in _called_launchers]
                except Exception as _re:
                    logger.warning(
                        "[%s] stream_chat: semantic refresh failed round %d: %s", job_id, _round, _re
                    )
                    _kw = _keyword_filter_tools(_rolling_query, tools, _AI_SEARCH_TOP_K)
                    _active_tools = [t for t in _kw
                                     if t.get("function", {}).get("name") not in _called_launchers]
            elif _called_launchers:
                # Exclude already-fired launcher tools so the model can't re-launch.
                _active_tools = [t for t in _active_tools
                                 if t.get("function", {}).get("name") not in _called_launchers]

            kwargs: dict = {
                "model":       _active_model,
                "messages":    conversation,
                "temperature": 0,
            }
            if _active_tools:
                kwargs["tools"]       = _active_tools
                kwargs["tool_choice"] = "auto"

            # Run the blocking OpenAI call in a thread so the event loop stays
            # free to flush already-yielded SSE events to the client.
            _OPENAI_HARD_TIMEOUT = 120  # seconds before we give up and surface an error
            _openai_t0 = time.perf_counter()
            _openai_future = loop.run_in_executor(
                None,
                lambda kw=kwargs: client.chat.completions.create(**kw),
            )
            while True:
                try:
                    response = await asyncio.wait_for(asyncio.shield(_openai_future), timeout=5.0)
                    break
                except asyncio.TimeoutError:
                    if time.perf_counter() - _openai_t0 > _OPENAI_HARD_TIMEOUT:
                        yield _sse({"type": "error", "message": "Model took too long to respond — try again."})
                        return
                    yield _sse({
                        "type": "status",
                        "stage": "openai",
                        "message": "Waiting for model response...",
                    })
            _openai_ms = (time.perf_counter() - _openai_t0) * 1000.0
            msg = response.choices[0].message
            logger.info(
                "[stream_chat/%d] openai latency=%.1f ms tool_calls=%d content=%s",
                _round,
                _openai_ms,
                len(msg.tool_calls or []),
                bool(msg.content),
            )

            # ── No tool calls → final text answer ─────────────────────────
            if not msg.tool_calls:
                _final_text = ""
                if msg.content:
                    _final_text = msg.content
                    yield _sse({"type": "token", "content": msg.content})
                elif _tools_executed:
                    # Model returned no summary text (common at temperature=0 after
                    # tool calls).  Force one final text-only completion so the user
                    # gets a real conversational response instead of a terse fallback.
                    try:
                        # Don't include tools here — tool_choice=none means they
                        # can never fire; they only waste context tokens on round 2+.
                        _summary_kw = {
                            "model":       _active_model,
                            "messages":    conversation,
                            "temperature": 0.3,
                        }
                        _summary_future = loop.run_in_executor(
                            None,
                            lambda kw=_summary_kw: client.chat.completions.create(**kw),
                        )
                        while True:
                            try:
                                _summary_resp = await asyncio.wait_for(
                                    asyncio.shield(_summary_future), timeout=5.0
                                )
                                break
                            except asyncio.TimeoutError:
                                yield _sse({"type": "status", "stage": "openai",
                                            "message": "Generating response..."})
                        _summary_text = _summary_resp.choices[0].message.content
                        if _summary_text:
                            _final_text = _summary_text
                            yield _sse({"type": "token", "content": _summary_text})
                        else:
                            names = ", ".join(_tools_executed[-3:])
                            _final_text = f"Done — executed {len(_tools_executed)} step(s): {names}."
                            yield _sse({"type": "token", "content": _final_text})
                    except Exception as _se:
                        logger.warning("[stream_chat] Summary call failed: %s", _se)
                        names = ", ".join(_tools_executed[-3:])
                        _final_text = f"Done — executed {len(_tools_executed)} step(s): {names}."
                        yield _sse({"type": "token", "content": _final_text})
                # Persist this exchange to the per-job blob transcript
                if job_id and _last_user_message and _final_text:
                    try:
                        from api.storage import _append_transcript as _at
                        _tl_snap = list(_tool_log)
                        loop.run_in_executor(
                            None,
                            lambda u=_last_user_message, a=_final_text, tl=_tl_snap: _at(job_id, u, a, tl),
                        )
                        # Persist structured executor traces
                        _trace_entries = [e["trace"] for e in _tl_snap if e.get("trace")]
                        if _trace_entries:
                            loop.run_in_executor(
                                None,
                                lambda te=_trace_entries: _append_executor_trace(job_id, te),
                            )
                        # Build and persist a per-message diagnosis record
                        _diag_record = {
                            "user_message":  (_last_user_message or "")[:200],
                            "tools_called":  [e["call"] for e in _tl_snap],
                            "sentinel_hits": sum(1 for e in _tl_snap if "4294967295" in str(e.get("result", ""))),
                            "dll_errors":    sum(1 for e in _tl_snap if "DLL call error" in str(e.get("result", ""))),
                            "round_count":   _round + 1,
                            "recorded_at":   time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        }
                        loop.run_in_executor(
                            None,
                            lambda d=_diag_record: _append_diagnosis_raw(job_id, d),
                        )
                    except Exception:
                        pass
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
                _tool_log.append({"call": fn_name, "args": fn_args, "result": None, "trace": None})

                inv = inv_map.get(fn_name)
                if inv is None and job_id:
                    inv = _get_invocable(job_id, fn_name)

                if inv is not None:
                    # Tool execution can be slow (pywinauto, GUI interaction) —
                    # run in executor so the SSE response stays live.
                    _TOOL_HARD_TIMEOUT = 30  # seconds; DLL/COM/GUI calls can hang
                    _tool_t0 = time.perf_counter()
                    _tool_trace: dict | None = None
                    _tool_future = loop.run_in_executor(
                        None, lambda i=inv, a=fn_args: _execute_tool_traced(i, a, _vocab_sentinels)
                    )
                    while True:
                        try:
                            _traced = await asyncio.wait_for(
                                asyncio.shield(_tool_future), timeout=5.0
                            )
                            tool_result = _traced["result_str"]
                            _tool_trace = _traced.get("trace")
                            break
                        except asyncio.TimeoutError:
                            if time.perf_counter() - _tool_t0 > _TOOL_HARD_TIMEOUT:
                                tool_result = f"Tool '{fn_name}' timed out after {_TOOL_HARD_TIMEOUT}s — the call may have hung (COM/DLL deadlock or blocking dialog)."
                                _tool_trace = {"backend": "timeout", "latency_ms": _TOOL_HARD_TIMEOUT * 1000}
                                break
                            yield _sse({
                                "type": "status",
                                "stage": "tool",
                                "name": fn_name,
                                "message": f"Waiting for tool '{fn_name}'...",
                            })
                    _tool_ms = (time.perf_counter() - _tool_t0) * 1000.0
                    if inv.get("source_type") == "cli" and \
                            Path(inv.get("dll_path", "")).stem.lower() == fn_name.lower():
                        _called_launchers.add(fn_name)
                else:
                    tool_result = (
                        f"Tool '{fn_name}' not found — pass 'invocables' in the "
                        f"request body or call /api/generate first."
                    )
                    _tool_ms = 0.0

                yield _sse({"type": "tool_result", "name": fn_name, "result": tool_result})
                if _tool_log:
                    _tool_log[-1]["result"] = tool_result
                    _tool_log[-1]["trace"] = _tool_trace
                logger.info(
                    "[stream_chat/%d] tool=%s latency=%.1f ms result=%s",
                    _round,
                    fn_name,
                    _tool_ms,
                    tool_result[:120],
                )
                _tools_executed.append(fn_name)

                conversation.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": tool_result,
                })

                # Track error sentinels; escalate to reasoning model after 3 failures
                # if a more capable model is configured.
                if "4294967295" in tool_result or tool_result.strip() == "-1":
                    _failure_count += 1
                    if (_failure_count >= 3
                            and _active_model != _reasoning_model
                            and _base_model != _reasoning_model):
                        _active_model = _reasoning_model
                        logger.info(
                            "[stream_chat/%d] Escalating to reasoning model %s after %d failures",
                            _round, _active_model, _failure_count,
                        )

            # Trim conversation to bound token size each round.
            _sys  = [m for m in conversation if m.get("role") == "system"]
            _rest = [m for m in conversation if m.get("role") != "system"]
            _rest = _rest[-_CONTEXT_WINDOW_TURNS:]
            # Drop any leading role:tool messages that have no preceding
            # assistant message with tool_calls — OpenAI rejects these with 400.
            while _rest and _rest[0].get("role") == "tool":
                _rest.pop(0)
            conversation = _sys + _rest

        yield _sse({"type": "done", "rounds": MAX_TOOL_ROUNDS})

    except Exception as exc:
        logger.error("stream_chat error: %s", exc)
        yield _sse({"type": "error", "message": str(exc)})
