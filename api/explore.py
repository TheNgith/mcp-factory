"""api/explore.py – Autonomous reverse-engineering exploration worker.

_explore_worker(job_id, invocables) runs the LLM in a structured loop with a
reverse-engineering system prompt.  For each function it:
  1. Calls the function with probing arguments to observe behaviour.
  2. Calls enrich_invocable to write semantic names back to the schema.
  3. Calls record_finding to persist what was learned.

Job status is updated continuously with phase="exploring" and a progress
counter so the UI can display "Exploring functions… (3/12)".
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from api.config import OPENAI_ENDPOINT, OPENAI_DEPLOYMENT, OPENAI_REASONING_DEPLOYMENT, OPENAI_API_KEY, OPENAI_EXPLORE_MODEL
from api.executor import _execute_tool
from api.storage import _persist_job_status, _get_job_status, _patch_invocable, _save_finding
from api.telemetry import _openai_client

logger = logging.getLogger("mcp_factory.api")

_MAX_EXPLORE_ROUNDS_PER_FUNCTION = 3   # 3 rounds catches >95% of cases; 6 was wasteful
_MAX_FUNCTIONS_PER_SESSION = 50  # safety cap


def _build_explore_system_message(invocables: list, findings: list) -> dict:
    """System message for the autonomous exploration agent."""
    fn_names = ", ".join(inv["name"] for inv in invocables)
    findings_block = ""
    if findings:
        lines = [
            f"  - {f.get('function','?')}: {f.get('finding','')}"
            + (f" (working call: {f['working_call']})" if f.get("working_call") else "")
            for f in findings
        ]
        findings_block = (
            "\nALREADY DISCOVERED (skip re-probing these):\n"
            + "\n".join(lines)
            + "\n"
        )
    return {
        "role": "system",
        "content": (
            "You are a reverse-engineering agent. Your job is to systematically explore "
            "an undocumented Windows DLL and document what each function does.\n\n"
            "AVAILABLE FUNCTIONS: " + fn_names + "\n\n"
            "PROTOCOL:\n"
            "1. If init functions exist (Initialize, Init, Open, Login), call them FIRST.\n"
            "2. Call each function with safe probe values:\n"
            "   - integer params (uint, int, ushort): try 0, 1, 64, 256\n"
            "   - input string/byte* params: try '', 'test', and any STATIC ANALYSIS HINTS provided\n"
            "   - output pointer params (undefined4*, undefined8*, uint*): OMIT from the call — "
            "the executor auto-allocates these. Their values appear as 'param_N=<value>' in the result.\n"
            "   - undefined* output buffer + adjacent uint size param: OMIT BOTH — "
            "executor allocates 4096-byte buffer and supplies size=4096 automatically.\n"
            "3. Classify the return value:\n"
            "   - 0 = success for action functions\n"
            "   - 0xFFFFFFFF (4294967295) = error sentinel (not found / invalid input)\n"
            "   - 0xFFFFFFFE (4294967294) = secondary error (null argument)\n"
            "   - For GetVersion/GetBuild/GetRevision functions: the return value IS the version "
            "number as a packed UINT — any non-zero integer is a valid result, NOT an error. "
            "Decode as (val>>16)&0xFF . (val>>8)&0xFF . val&0xFF for major.minor.patch.\n"
            "4. Once you have a working call OR have exhausted safe probes, call BOTH:\n"
            "   a. enrich_invocable — rename generic params (param_1 → semantic_name), set description.\n"
            "   b. record_finding   — persist what you discovered in plain English.\n"
            "5. Move on to the next function. Stop when every function has been attempted.\n\n"
            "CONSTRAINTS:\n"
            "- The PRIMARY indicator of success/failure is ALWAYS the integer return value, "
            "not the output param values.\n"
            "- Never call dangerous functions (format, delete, write) with real data.\n"
            "- Keep probe values small and safe.\n"
            "- Be concise — after each function, proceed immediately to the next.\n"
            "- Do not ask for confirmation; work autonomously.\n"
            + findings_block
        ),
    }


def _explore_worker(job_id: str, invocables: list[dict]) -> None:
    """Background worker: explore each invocable with the LLM and enrich the schema."""
    if not OPENAI_ENDPOINT and not OPENAI_API_KEY:
        logger.warning("[%s] explore_worker: neither OPENAI_API_KEY nor AZURE_OPENAI_ENDPOINT configured — aborting", job_id)
        return

    from api.storage import _load_findings

    total = min(len(invocables), _MAX_FUNCTIONS_PER_SESSION)
    invocables = invocables[:total]

    logger.info("[%s] explore_worker: starting, %d functions to explore", job_id, total)

    # Update status to exploring
    _set_explore_status(job_id, 0, total, "Starting exploration…")

    # Build inv_map for tool dispatch
    inv_map: dict[str, dict] = {}
    for inv in invocables:
        inv_map[inv["name"]] = inv

    # Inject synthetic tools into inv_map
    _enrich_inv = {
        "name": "enrich_invocable",
        "source_type": "enrich",
        "_job_id": job_id,
        "execution": {"method": "enrich"},
        "parameters": [],
    }
    _findings_inv = {
        "name": "record_finding",
        "source_type": "findings",
        "_job_id": job_id,
        "execution": {"method": "findings"},
        "parameters": [],
    }
    inv_map["enrich_invocable"] = _enrich_inv
    inv_map["record_finding"] = _findings_inv

    # Build tool schemas list for the LLM
    from api.generate import run_generate as _run_gen  # noqa: F401
    from api.chat import _RECORD_FINDING_TOOL, _ENRICH_INVOCABLE_TOOL  # type: ignore

    # Build tools from invocables
    tool_schemas: list[dict] = []
    import re as _re
    for inv in invocables:
        props: dict = {}
        required: list = []
        for p in (inv.get("parameters") or []):
            if isinstance(p, str):
                p = {"name": p, "type": "string"}
            pname = p.get("name", "arg")
            json_type = p.get("json_type") or "string"
            props[pname] = {
                "type": json_type,
                "description": p.get("description") or p.get("type", "string"),
            }
            if p.get("direction", "in") != "out":
                required.append(pname)
        safe_name = _re.sub(r"[^a-zA-Z0-9_.\-]", "_", inv["name"])[:64]
        desc = inv.get("doc") or inv.get("description") or inv.get("signature") or inv["name"]
        tool_schemas.append({
            "type": "function",
            "function": {
                "name": safe_name,
                "description": desc,
                "parameters": {
                    "type": "object",
                    "properties": props,
                    "required": required,
                },
            },
        })
    tool_schemas.append(_RECORD_FINDING_TOOL)
    tool_schemas.append(_ENRICH_INVOCABLE_TOOL)

    client = _openai_client()
    # Use the dedicated explore model (gpt-4o-mini by default) for cost efficiency.
    # When using direct OpenAI key, OPENAI_EXPLORE_MODEL controls this.
    # When using Azure, fall back to the reasoning deployment.
    model = OPENAI_EXPLORE_MODEL if OPENAI_API_KEY else (OPENAI_REASONING_DEPLOYMENT or OPENAI_DEPLOYMENT)

    explored = 0
    try:
        prior_findings = _load_findings(job_id)
        already_explored = {f.get("function") for f in prior_findings if f.get("function")}

        # Phase 0: extract static hints from the DLL binary (best-effort)
        _static_hints_block = ""
        try:
            dll_path = next(
                (inv.get("execution", {}).get("dll_path", "") for inv in invocables
                 if inv.get("execution", {}).get("dll_path")),
                "",
            )
            if dll_path:
                import re as _re2
                from pathlib import Path as _Path
                _data = _Path(dll_path).read_bytes()
                _text = _data.decode("ascii", errors="ignore")
                _raw  = sorted(set(m.group(0).strip() for m in _re2.finditer(r"[ -~]{6,}", _text) if m.group(0).strip()))
                _ids     = [s for s in _raw if _re2.match(r"[A-Z]{2,6}-[\w-]+", s) and len(s) < 40]
                _emails  = [s for s in _raw if _re2.match(r"[\w.+-]+@[\w.-]+\.[a-z]{2,}", s, _re2.I)]
                _fmts    = [s for s in _raw if "%" in s and any(c in s for c in ("s", "d", "u", "f", "lu")) and len(s) < 120]
                _status  = [s for s in _raw if s.isupper() and 4 <= len(s) <= 16 and s.isalpha()
                            and s.lower() in {"active","inactive","pending","shipped","delivered",
                                              "cancelled","suspended","complete","unknown","locked","unlocked"}]
                parts = []
                if _ids:    parts.append("Known IDs/codes: " + ", ".join(_ids[:20]))
                if _emails: parts.append("Known emails: " + ", ".join(_emails[:10]))
                if _status: parts.append("Known status values: " + ", ".join(_status[:15]))
                if _fmts:   parts.append("Output format strings: " + " | ".join(_fmts[:5]))
                if parts:
                    _static_hints_block = (
                        "\nSTATIC ANALYSIS HINTS (strings extracted from DLL binary):\n"
                        + "\n".join(parts)
                        + "\nUse these as probe values for string params before trying generic ones.\n"
                    )
                    logger.info("[%s] explore_worker: phase0 found %d IDs, %d emails, %d formats",
                                job_id, len(_ids), len(_emails), len(_fmts))
        except Exception as _e:
            logger.debug("[%s] explore_worker: phase0 string extraction failed: %s", job_id, _e)

        for inv in invocables:
            fn_name = inv["name"]

            # Skip functions already documented in a previous session
            if fn_name in already_explored:
                explored += 1
                _set_explore_status(job_id, explored, total, f"Skipped {fn_name} (already documented)")
                continue

            _set_explore_status(job_id, explored, total, f"Exploring {fn_name}…")
            logger.info("[%s] explore_worker: starting %s (%d/%d)", job_id, fn_name, explored + 1, total)

            # Build a focused conversation just for this function
            prior = _load_findings(job_id)
            sys_msg = _build_explore_system_message(invocables, prior)
            conversation = [
                sys_msg,
                {
                    "role": "user",
                    "content": (
                        f"Explore the function '{fn_name}'. "
                        "Call it with safe probe values, observe the result, "
                        "then call enrich_invocable and record_finding with what you learned. "
                        "Be brief — one summary sentence after you're done."
                        + _static_hints_block
                    ),
                },
            ]

            for _round in range(_MAX_EXPLORE_ROUNDS_PER_FUNCTION):
                try:
                    from typing import cast, Any as _Any
                    response = client.chat.completions.create(
                        model=model,
                        messages=conversation,
                        tools=cast(_Any, tool_schemas),
                        tool_choice="auto",
                        temperature=0,
                    )
                except Exception as exc:
                    logger.warning(
                        "[%s] explore_worker: OpenAI call failed for %s round %d: %s",
                        job_id, fn_name, _round, exc,
                    )
                    break

                msg = response.choices[0].message

                if not msg.tool_calls:
                    # Model finished — no more tool calls needed
                    break

                # Append assistant turn
                conversation.append({
                    "role": "assistant",
                    "content": msg.content or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,  # type: ignore[union-attr]
                                "arguments": tc.function.arguments,  # type: ignore[union-attr]
                            },
                        }
                        for tc in msg.tool_calls
                    ],
                })

                # Execute each tool call
                for tc in msg.tool_calls:
                    _fn = tc.function  # type: ignore[union-attr]
                    tc_name = _fn.name
                    try:
                        tc_args = json.loads(_fn.arguments)
                    except json.JSONDecodeError:
                        tc_args = {}

                    tc_inv = inv_map.get(tc_name)
                    if tc_inv is not None:
                        try:
                            tool_result = _execute_tool(tc_inv, tc_args)
                        except Exception as exc:
                            tool_result = f"Tool error: {exc}"
                    else:
                        tool_result = f"Tool '{tc_name}' not found."

                    logger.info(
                        "[%s] explore_worker: tool=%s result=%s",
                        job_id, tc_name, str(tool_result)[:120],
                    )

                    conversation.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": tool_result,
                    })

            explored += 1
            already_explored.add(fn_name)
            _set_explore_status(job_id, explored, total, f"Completed {fn_name}")

        # Mark exploration done — update job status back to previous terminal state
        # or set a new "explore_done" sub-status so the UI knows it finished.
        current = _get_job_status(job_id) or {}
        _persist_job_status(
            job_id,
            {
                **current,
                "explore_phase": "done",
                "explore_progress": f"{explored}/{total}",
                "updated_at": time.time(),
            },
            sync=True,
        )
        logger.info("[%s] explore_worker: finished %d/%d functions", job_id, explored, total)

    except Exception as exc:
        logger.error("[%s] explore_worker: fatal error: %s", job_id, exc)
        current = _get_job_status(job_id) or {}
        _persist_job_status(
            job_id,
            {
                **current,
                "explore_phase": "error",
                "explore_error": str(exc),
                "updated_at": time.time(),
            },
            sync=True,
        )


def _set_explore_status(job_id: str, explored: int, total: int, message: str) -> None:
    current = _get_job_status(job_id) or {}
    _persist_job_status(
        job_id,
        {
            **current,
            "explore_phase": "exploring",
            "explore_progress": f"{explored}/{total}",
            "explore_message": message,
            "updated_at": time.time(),
        },
    )
