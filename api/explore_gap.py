from __future__ import annotations

import json
import logging
import re as _re
import time

from api.config import ARTIFACT_CONTAINER, OPENAI_API_KEY, OPENAI_DEPLOYMENT, OPENAI_EXPLORE_MODEL, OPENAI_REASONING_DEPLOYMENT, OPENAI_ENDPOINT
from api.executor import _execute_tool, _execute_tool_traced
from api.explore_helpers import (
    _GAP_RESOLUTION_ENABLED,
    _build_tool_schemas,
    _set_explore_status,
    _snapshot_schema_stage,
)
from api.explore_phases import _MAX_EXPLORE_ROUNDS_PER_FUNCTION, _MAX_TOOL_CALLS_PER_FUNCTION, _SENTINEL_DEFAULTS
from api.explore_prompts import _build_explore_system_message, _generate_confidence_gaps
from api.storage import (
    _append_executor_trace,
    _append_transcript,
    _download_blob,
    _get_job_status,
    _patch_finding,
    _persist_job_status,
    _register_invocables,
    _upload_to_blob,
)
from api.telemetry import _openai_client

logger = logging.getLogger("mcp_factory.api")


def _attempt_gap_resolution(
    job_id: str,
    invocables: list[dict],
    client,
    model: str,
    sentinels: dict,
    vocab: dict,
    use_cases_text: str,
    inv_map: dict,
    tool_schemas: list[dict],
) -> None:
    """Targeted second-pass re-probe of functions that failed every probe in the main loop."""
    from api.storage import _load_findings

    all_findings = _load_findings(job_id)

    failed_invs = [
        inv for inv in invocables
        if not any(
            f.get("function") == inv["name"] and f.get("status") == "success"
            for f in all_findings
        )
    ]
    if not failed_invs:
        logger.info("[%s] gap_resolution: no failed functions — skipping", job_id)
        return

    fn_list = [inv["name"] for inv in failed_invs]
    logger.info("[%s] gap_resolution: targeted retry of %d function(s): %s", job_id, len(failed_invs), fn_list)

    successful_findings = [f for f in all_findings if f.get("status") == "success" and f.get("working_call")]
    kb_lines = [f"  - {sf['function']}({sf['working_call']})" for sf in successful_findings[:6]]
    kb_block = (
        "\nKNOWN-GOOD CALLS (reuse these IDs/values as inputs):\n" + "\n".join(kb_lines) + "\n"
    ) if kb_lines else ""

    for i, inv in enumerate(failed_invs):
        fn_name = inv["name"]
        _set_explore_status(job_id, i, len(failed_invs), f"Gap resolution: retrying {fn_name}…")

        prev_finding = next((f for f in reversed(all_findings) if f.get("function") == fn_name), None)
        prev_ctx = (
            f"Previous attempt: {prev_finding.get('finding', 'no finding')}.\n"
            if prev_finding else ""
        )

        sys_msg = _build_explore_system_message(
            invocables, _load_findings(job_id),
            sentinels=sentinels, vocab=vocab, use_cases=use_cases_text,
        )
        conversation = [
            sys_msg,
            {
                "role": "user",
                "content": (
                    f"SECOND-PASS RETRY for '{fn_name}'.\n"
                    f"{prev_ctx}"
                    f"{kb_block}\n"
                    "This function failed every probe in the first pass. "
                    "Try these strategies in order:\n"
                    "1. Call the init function first (even if called before), then call this function.\n"
                    "2. Use the customer/order IDs from the KNOWN-GOOD CALLS above.\n"
                    "3. Permute numeric parameters: try 0, 1, 100, 1000, 10000.\n"
                    "4. For string params: try empty string, then each known-good ID format.\n"
                    "Goal: find ANY call that returns 0. Once found, call record_finding with "
                    "status='success' and working_call set to the exact args that worked.\n"
                    "If still failing after all strategies, call record_finding with "
                    "status='error' and note the exact error code(s) observed."
                ),
            },
        ]

        _observed_successes: list[dict] = []
        _p_lookup = {p.get("name", ""): p for p in (inv.get("parameters") or [])}
        _fn_tool_call_count = 0

        for _round in range(_MAX_EXPLORE_ROUNDS_PER_FUNCTION):
            if _fn_tool_call_count >= _MAX_TOOL_CALLS_PER_FUNCTION:
                logger.info("[%s] gap_resolution: %s hit tool-call cap (%d)",
                            job_id, fn_name, _MAX_TOOL_CALLS_PER_FUNCTION)
                break
            try:
                from typing import cast, Any as _Any
                response = client.chat.completions.create(
                    model=model,
                    messages=conversation,
                    tools=cast(_Any, tool_schemas),
                    tool_choice="auto",
                    temperature=0,
                    timeout=90.0,
                )
            except Exception as exc:
                logger.warning("[%s] gap_resolution: OpenAI call failed for %s round %d: %s",
                               job_id, fn_name, _round, exc)
                break

            msg = response.choices[0].message
            if not msg.tool_calls:
                break

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

            for tc in msg.tool_calls:
                if _fn_tool_call_count >= _MAX_TOOL_CALLS_PER_FUNCTION:
                    break
                _fn = tc.function  # type: ignore[union-attr]
                tc_name = _fn.name
                try:
                    tc_args = json.loads(_fn.arguments)
                except json.JSONDecodeError:
                    tc_args = {}

                tc_inv = inv_map.get(tc_name)
                tool_result = _execute_tool(tc_inv, tc_args) if tc_inv else f"Tool '{tc_name}' not found."
                _fn_tool_call_count += 1

                if tc_name == fn_name:
                    _ret_m = _re.match(r"Returned:\s*(\d+)", tool_result or "")
                    if _ret_m and int(_ret_m.group(1)) == 0:
                        _out_bases = frozenset({
                            "undefined", "undefined2", "undefined4", "undefined8",
                            "uint", "uint32_t", "int", "int32_t", "dword",
                            "ulong", "uint4", "uint8", "long", "ulong32",
                        })
                        _clean: dict = {}
                        for _k, _v in tc_args.items():
                            _p = _p_lookup.get(_k, {})
                            _pt = _p.get("type", "").lower().replace("const ", "").strip().rstrip(" *")
                            _is_out = "*" in _p.get("type", "") and _pt in _out_bases
                            if not _is_out and _p.get("direction", "in") != "out":
                                _clean[_k] = _v
                        _observed_successes.append(_clean)

                logger.info("[%s] gap_resolution: tool=%s result=%s", job_id, tc_name, str(tool_result)[:120])
                conversation.append({"role": "tool", "tool_call_id": tc.id, "content": tool_result})

        if _observed_successes:
            _patch_finding(job_id, fn_name, {"working_call": _observed_successes[0], "status": "success"})
            logger.info("[%s] gap_resolution: resolved %s → success %s", job_id, fn_name, _observed_successes[0])
        else:
            _cur = _load_findings(job_id)
            _ff = next((f for f in reversed(_cur) if f.get("function") == fn_name), None)
            if _ff and _ff.get("working_call") is not None:
                _vi = inv_map.get(fn_name)
                if _vi:
                    try:
                        _vr = _execute_tool(_vi, _ff["working_call"])
                        _vm = _re.match(r"Returned:\s*(\d+)", _vr or "")
                        if _vm:
                            _vret = int(_vm.group(1))
                            if _vret not in sentinels and _vret <= 0xFFFFFFF0:
                                _patch_finding(job_id, fn_name, {"status": "success"})
                                logger.info("[%s] gap_resolution: resolved %s via verification", job_id, fn_name)
                            else:
                                _patch_finding(job_id, fn_name, {"working_call": None, "status": "error"})
                    except Exception as _ve:
                        logger.debug("[%s] gap_resolution: verify failed for %s: %s", job_id, fn_name, _ve)


def _run_gap_answer_mini_sessions(job_id: str, invocables: list[dict]) -> None:
    """Targeted mini-sessions driven by user gap answers."""
    from api.storage import _load_findings

    if not _GAP_RESOLUTION_ENABLED:
        logger.info("[%s] gap_mini_sessions: skipped (EXPLORE_ENABLE_GAP_RESOLUTION=0)", job_id)
        return

    if not OPENAI_ENDPOINT and not OPENAI_API_KEY:
        return

    try:
        vocab: dict = {}
        try:
            raw = _download_blob(ARTIFACT_CONTAINER, f"{job_id}/vocab.json")
            vocab = json.loads(raw)
        except Exception:
            pass

        gap_answers: dict = vocab.get("gap_answers") or {}
        targeted = {fn: ans for fn, ans in gap_answers.items() if fn != "general" and (ans or "").strip()}
        if not targeted:
            logger.info("[%s] gap_mini_sessions: no function-specific answers — skipping", job_id)
            return

        _snapshot_schema_stage(job_id, "mcp_schema_pre_mini_session.json")

        client = _openai_client()
        model = OPENAI_EXPLORE_MODEL if OPENAI_API_KEY else (OPENAI_REASONING_DEPLOYMENT or OPENAI_DEPLOYMENT)

        _job_meta = _get_job_status(job_id) or {}
        _use_cases_text = _job_meta.get("use_cases", "")
        sentinels = _SENTINEL_DEFAULTS

        inv_map: dict[str, dict] = {inv["name"]: inv for inv in invocables}

        # COH-2: Register invocables so enrich_invocable / _patch_invocable can
        # resolve function names during gap mini-sessions.
        _register_invocables(job_id, invocables)

        inv_map["enrich_invocable"] = {
            "name": "enrich_invocable", "source_type": "enrich", "_job_id": job_id,
            "execution": {"method": "enrich"}, "parameters": [],
        }
        inv_map["record_finding"] = {
            "name": "record_finding", "source_type": "findings", "_job_id": job_id,
            "execution": {"method": "findings"}, "parameters": [],
        }
        tool_schemas = _build_tool_schemas(invocables)

        explore_questions = _job_meta.get("explore_questions") or []

        logger.info("[%s] gap_mini_sessions: %d answered function(s) to retry: %s",
                    job_id, len(targeted), list(targeted))

        for i, (fn_name, answer_text) in enumerate(targeted.items()):
            inv = inv_map.get(fn_name)
            if not inv:
                logger.debug("[%s] gap_mini_sessions: no invocable for %s — skipping", job_id, fn_name)
                continue

            _set_explore_status(job_id, i, len(targeted), f"Gap mini-session: {fn_name}…")
            logger.info("[%s] gap_mini_sessions: starting mini-session for %s", job_id, fn_name)

            all_findings = _load_findings(job_id)
            prev_finding = next((f for f in reversed(all_findings) if f.get("function") == fn_name), None)
            prev_ctx = (
                f"Previous attempt: {prev_finding.get('finding', 'no finding')}.\n"
                if prev_finding else ""
            )

            fn_gap = next((g for g in explore_questions if g.get("function") == fn_name), {})
            technical_q = fn_gap.get("technical_question", "")
            technical_ctx = f"Technical context: {technical_q}\n" if technical_q else ""

            sys_msg = _build_explore_system_message(
                invocables, all_findings,
                sentinels=sentinels, vocab=vocab, use_cases=_use_cases_text,
            )
            conversation = [
                sys_msg,
                {
                    "role": "user",
                    "content": (
                        f"DOMAIN EXPERT ANSWER for '{fn_name}'.\n"
                        f"{technical_ctx}"
                        f"{prev_ctx}"
                        f"A domain expert answered: {answer_text!r}\n\n"
                        f"Use this information to re-probe '{fn_name}' now. "
                        "Apply the expert's answer to determine the correct prerequisite calls, "
                        "argument formats, or conditions needed for a successful call. "
                        "Goal: find a call that returns 0 (success). "
                        "When done, call enrich_invocable and record_finding with what you found. "
                        "If every probe still fails after applying the answer, call "
                        "record_finding(status='error') with exact codes seen."
                    ),
                },
            ]

            _p_lookup = {p.get("name", ""): p for p in (inv.get("parameters") or [])}
            _observed_successes: list[dict] = []
            _mini_tool_log: list[dict] = []
            _mini_traces: list[dict] = []
            _fn_tool_call_count = 0

            for _round in range(_MAX_EXPLORE_ROUNDS_PER_FUNCTION):
                if _fn_tool_call_count >= _MAX_TOOL_CALLS_PER_FUNCTION:
                    logger.info("[%s] gap_mini_sessions: %s hit tool-call cap (%d)",
                                job_id, fn_name, _MAX_TOOL_CALLS_PER_FUNCTION)
                    break
                try:
                    from typing import cast, Any as _Any
                    response = client.chat.completions.create(
                        model=model,
                        messages=conversation,
                        tools=cast(_Any, tool_schemas),
                        tool_choice="auto",
                        temperature=0,
                        timeout=90.0,
                    )
                except Exception as exc:
                    logger.warning("[%s] gap_mini_sessions: OpenAI call failed for %s round %d: %s",
                                   job_id, fn_name, _round, exc)
                    break

                msg = response.choices[0].message
                if not msg.tool_calls:
                    break

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

                _round_reasoning = (msg.content or "").strip()
                _first_in_round = True
                for tc in msg.tool_calls:
                    if _fn_tool_call_count >= _MAX_TOOL_CALLS_PER_FUNCTION:
                        break
                    _fn = tc.function  # type: ignore[union-attr]
                    tc_name = _fn.name
                    try:
                        tc_args = json.loads(_fn.arguments)
                    except json.JSONDecodeError:
                        tc_args = {}

                    tc_inv = inv_map.get(tc_name)
                    if tc_inv:
                        _traced = _execute_tool_traced(tc_inv, tc_args)
                        tool_result = _traced["result_str"]
                        _tc_trace = _traced.get("trace")
                    else:
                        tool_result = f"Tool '{tc_name}' not found."
                        _tc_trace = None
                    _fn_tool_call_count += 1

                    _mini_tool_log.append({
                        "call": tc_name,
                        "args": tc_args,
                        "result": tool_result,
                        "trace": _tc_trace,
                        "reasoning": _round_reasoning if _first_in_round else None,
                    })
                    if _tc_trace:
                        _mini_traces.append(_tc_trace)
                    _first_in_round = False

                    if tc_name == fn_name:
                        _ret_m = _re.match(r"Returned:\s*(\d+)", tool_result or "")
                        if _ret_m and int(_ret_m.group(1)) == 0:
                            _out_bases = frozenset({
                                "undefined", "undefined2", "undefined4", "undefined8",
                                "uint", "uint32_t", "int", "int32_t", "dword",
                                "ulong", "uint4", "uint8", "long", "ulong32",
                            })
                            _clean: dict = {}
                            for _k, _v in tc_args.items():
                                _p = _p_lookup.get(_k, {})
                                _pt = _p.get("type", "").lower().replace("const ", "").strip().rstrip(" *")
                                _is_out = "*" in _p.get("type", "") and _pt in _out_bases
                                if not _is_out and _p.get("direction", "in") != "out":
                                    _clean[_k] = _v
                            _observed_successes.append(_clean)

                    logger.info("[%s] gap_mini_sessions: tool=%s result=%s",
                                job_id, tc_name, str(tool_result)[:120])
                    conversation.append({"role": "tool", "tool_call_id": tc.id, "content": tool_result})

            try:
                _mini_user_msg = (
                    f"[GAP MINI-SESSION: {fn_name}]\n"
                    f"Domain expert answer: {answer_text!r}\n"
                    f"{technical_ctx}"
                    f"{prev_ctx}"
                )
                _mini_final = "(mini-session complete)"
                for _turn in reversed(conversation):
                    if _turn.get("role") == "assistant" and _turn.get("content"):
                        _mini_final = _turn["content"]
                        break
                _append_transcript(job_id, _mini_user_msg, _mini_final, _mini_tool_log,
                                   transcript_blob="mini_session_transcript.txt")
                if _mini_traces:
                    _append_executor_trace(job_id, _mini_traces)
            except Exception as _tr_e:
                logger.debug("[%s] gap_mini_sessions: transcript write failed for %s: %s",
                              job_id, fn_name, _tr_e)

            if _observed_successes:
                _patch_finding(job_id, fn_name, {"working_call": _observed_successes[0], "status": "success"})
                logger.info("[%s] gap_mini_sessions: resolved %s → success %s",
                            job_id, fn_name, _observed_successes[0])
            else:
                _cur = _load_findings(job_id)
                _ff = next((f for f in reversed(_cur) if f.get("function") == fn_name), None)
                if _ff and _ff.get("working_call") is not None:
                    _vi = inv_map.get(fn_name)
                    if _vi:
                        try:
                            _vr = _execute_tool(_vi, _ff["working_call"])
                            _vm = _re.match(r"Returned:\s*(\d+)", _vr or "")
                            if _vm:
                                _vret = int(_vm.group(1))
                                if _vret not in sentinels and _vret <= 0xFFFFFFF0:
                                    _patch_finding(job_id, fn_name, {"status": "success"})
                                else:
                                    _patch_finding(job_id, fn_name, {"working_call": None, "status": "error"})
                        except Exception as _ve:
                            logger.debug("[%s] gap_mini_sessions: verify failed for %s: %s",
                                         job_id, fn_name, _ve)

        try:
            logger.info("[%s] gap_mini_sessions: re-generating confidence gaps after mini-sessions…", job_id)
            _set_explore_status(job_id, len(targeted), len(targeted), "Re-assessing gaps…")
            updated_findings = _load_findings(job_id)
            new_gaps = _generate_confidence_gaps(client, model, updated_findings, invocables)
            resolved = {f.get("function") for f in updated_findings if f.get("status") == "success"}
            new_gaps = [g for g in new_gaps if g.get("function") not in resolved]
            _gap_current = _get_job_status(job_id) or {}
            _persist_job_status(job_id, {**_gap_current, "explore_questions": new_gaps}, sync=True)
            logger.info("[%s] gap_mini_sessions: %d gap(s) remain after re-assessment", job_id, len(new_gaps))
        except Exception as _re_e:
            logger.debug("[%s] gap_mini_sessions: re-gap generation failed: %s", job_id, _re_e)

        try:
            _targeted_fns = set(targeted)
            _grl_findings = _load_findings(job_id)
            _grl = [
                {
                    "function": f.get("function"),
                    "status": f.get("status"),
                    "working_call": f.get("working_call"),
                    "confidence": f.get("confidence"),
                    "successes": f.get("successes", 0),
                    "attempts": f.get("attempts", 0),
                }
                for f in _grl_findings
                if f.get("function") in _targeted_fns
            ]
            _upload_to_blob(
                ARTIFACT_CONTAINER,
                f"{job_id}/gap_resolution_log.json",
                json.dumps(_grl, indent=2).encode(),
            )
            logger.info("[%s] gap_mini_sessions: gap_resolution_log written (%d entries)", job_id, len(_grl))
        except Exception as _grl_e:
            logger.debug("[%s] gap_mini_sessions: gap_resolution_log upload failed: %s", job_id, _grl_e)

        _snapshot_schema_stage(job_id, "mcp_schema_post_mini_session.json")

        _cur_status = _get_job_status(job_id) or {}
        # AC-4: Use clarification closure gate — only mark "done" if no
        # unanswered questions remain after mini-session resolution.
        _open_q = _cur_status.get("explore_questions") or []
        _has_unanswered = any(
            isinstance(q, dict)
            and not (bool(q.get("answered")) or bool(str(q.get("answer") or "").strip()))
            for q in _open_q
        )
        _final_phase = "awaiting_clarification" if _has_unanswered else "done"
        _persist_job_status(
            job_id,
            {**_cur_status, "explore_phase": _final_phase, "updated_at": time.time()},
            sync=True,
        )
        logger.info("[%s] gap_mini_sessions: complete (phase=%s)", job_id, _final_phase)

    except Exception as exc:
        logger.error("[%s] gap_mini_sessions: fatal error: %s", job_id, exc)