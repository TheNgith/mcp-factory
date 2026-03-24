"""api.pipeline.s02_probe.probe_loop – Per-function LLM probe loop.

Contains _classify_arg_source, _explore_one, and _run_phase_3_probe_loop.
"""
from __future__ import annotations

import json
import logging
import re as _re
import threading as _threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor as _TPE
from typing import Any

from api.config import ARTIFACT_CONTAINER
from api.executor import _execute_tool, _execute_tool_traced
from api.storage import (
    _persist_job_status, _get_job_status, _patch_invocable,
    _save_finding, _patch_finding, _upload_to_blob, _download_blob,
    _append_transcript, _append_executor_trace, _append_explore_probe_log,
    _register_invocables, _merge_invocables, _get_current_invocables,
)
from api.telemetry import _openai_client
from api.pipeline.helpers import (
    _SENTINEL_DEFAULTS, _CAP_PROFILE,
    _MAX_EXPLORE_ROUNDS_PER_FUNCTION, _MAX_TOOL_CALLS_PER_FUNCTION, _MAX_FUNCTIONS_PER_SESSION,
    _GAP_RESOLUTION_ENABLED,
    _INIT_RE,
    _VERSION_FN_RE,
    _WRITE_FN_RE,
    _WRITE_RETRY_BUDGET_BY_CLASS,
    _build_ranked_fallback_probe_args,
    _build_tool_schemas,
    _cancel_requested,
    _classify_result_text,
    _infer_param_desc,
    _save_stage_context,
    _sentinel_class_from_classification,
    _set_explore_status,
    _snapshot_schema_stage,
    _strip_output_buffer_params,
    _write_policy_precheck,
)
from api.pipeline.s00_setup.calibration import (
    _calibrate_sentinels,
    _name_sentinel_candidates,
)
from api.pipeline.s01_unlock.write_unlock import _probe_write_unlock
from api.pipeline.vocab import (
    _update_vocabulary, _generate_hypothesis, _backfill_schema_from_synthesis,
    _vocab_block, _uncertainty_score,
)
from api.pipeline.prompts import (
    _build_explore_system_message, _generate_behavioral_spec,
    _synthesize, _generate_confidence_gaps,
)
from api.pipeline.s06_gaps.gap_resolution import _attempt_gap_resolution, _run_gap_answer_mini_sessions
from api.pipeline.types import ExploreContext, ExploreRuntime

logger = logging.getLogger("mcp_factory.api")

def _classify_arg_source(
    arg_name: str, arg_value, inv: dict, ctx_vocab: dict
) -> str:
    """Return a source tag for a single probe argument value.

    Tags: static_id | known_good_replay | default_numeric | fallback_string
    """
    id_fmts = ctx_vocab.get("id_formats") or []
    if isinstance(arg_value, str):
        for fmt in id_fmts:
            if "-" in fmt:
                prefix = fmt[: fmt.index("-") + 1]
                if arg_value.startswith(prefix):
                    return "static_id"
    known_good = ctx_vocab.get("known_good_args") or {}
    if arg_name in known_good and known_good[arg_name] == arg_value:
        return "known_good_replay"
    if isinstance(arg_value, (int, float)):
        return "default_numeric"
    return "fallback_string"


# ══════════════════════════════════════════════════════════════════════════════
#  Phase 3 – Per-function probe loop
# ══════════════════════════════════════════════════════════════════════════════

def _explore_one(inv: dict, ctx: ExploreContext) -> None:
    """Run the full LLM probe sequence for a single function.

    Module-level (not a closure) so it can be called directly in unit tests.
    All shared mutable state is accessed through *ctx*; thread-safety is managed
    via ctx._lock around vocab/sentinel_catalog/already_explored/_state updates.
    """
    from api.storage import _load_findings

    if _cancel_requested(ctx.job_id):
        return

    fn_name = inv["name"]

    # Snapshot vocab at function start so this worker sees a consistent view
    # even when parallel workers are concurrently updating the shared vocab.
    _vocab_snap = dict(ctx.vocab)
    # T-05: inject binary-string IDs from static analysis so fallback probe args
    # can use real ID values (e.g. "CUST-001") rather than generic placeholders.
    _sa_ids = (
        (ctx.static_analysis_result or {}).get("binary_strings") or {}
    ).get("ids_found") or []
    if _sa_ids:
        _vocab_snap["binary_string_ids"] = list(_sa_ids)

    # Skip functions already documented in a previous session (thread-safe)
    _skip = False
    with ctx._lock:
        if ctx.runtime.skip_documented and fn_name in ctx.already_explored:
            ctx._state["explored"] += 1
            _skip = True
    if _skip:
        _set_explore_status(ctx.job_id, ctx._state["explored"], ctx.total,
                            f"Skipped {fn_name} (already documented)")
        return

    # Q-5: Re-initialise DLL state before each function's probe sequence to
    # reset balance/points/etc. so probes don't fail due to state drained by
    # previous function probing.
    if not _INIT_RE.search(fn_name) and ctx.init_invocables:
        for _rinit in ctx.init_invocables:
            try:
                _execute_tool(_rinit, {})
            except Exception:
                pass

    _set_explore_status(ctx.job_id, ctx._state["explored"], ctx.total, f"Exploring {fn_name}…")
    logger.info("[%s] explore_worker: starting %s (%d/%d)",
                ctx.job_id, fn_name, ctx._state["explored"] + 1, ctx.total)

    # Build a focused conversation for this function.
    # context_density controls how much prior knowledge is injected:
    #   full    — all prior findings (default, validated overnight)
    #   minimal — only the current function's prior finding (if any)
    #   none    — no prior findings (cold start)
    _all_prior = _load_findings(ctx.job_id)
    if ctx.runtime.context_density == "none":
        prior: list = []
    elif ctx.runtime.context_density == "minimal":
        prior = [f for f in _all_prior if f.get("function") == fn_name]
    else:
        prior = _all_prior
    sys_msg = _build_explore_system_message(
        ctx.invocables, prior, sentinels=ctx.sentinels,
        vocab=_vocab_snap, use_cases=ctx.use_cases_text,
    )
    _is_write_fn = bool(_WRITE_FN_RE.search(fn_name))
    _is_version_fn = bool(_VERSION_FN_RE.search(fn_name))

    # Extract decompiled code for this function (if available from Ghidra)
    _decompiled = inv.get("doc_comment") or inv.get("doc") or ""
    _decompiled_block = ""
    if _decompiled and _is_write_fn:
        _trunc = _decompiled[:3000]
        _decompiled_block = (
            f"\n\nDECOMPILED SOURCE CODE for {fn_name}:\n```c\n{_trunc}\n```\n"
            "READ THIS CODE CAREFULLY. It tells you exactly what conditions make "
            "this function return 0 (success) vs a sentinel error code.\n"
        )

    # For write functions, also gather decompiled code from related functions
    # (init functions and any functions this one might depend on)
    _dependency_block = ""
    if _is_write_fn:
        _dep_parts = []
        for other_inv in ctx.invocables:
            if other_inv["name"] == fn_name:
                continue
            other_doc = other_inv.get("doc_comment") or other_inv.get("doc") or ""
            if not other_doc:
                continue
            other_name = other_inv["name"]
            is_init = bool(_INIT_RE.search(other_name))
            is_unlock = "unlock" in other_name.lower()
            if is_init or is_unlock:
                _dep_parts.append(
                    f"\nRELATED FUNCTION — {other_name}:\n```c\n{other_doc[:2000]}\n```\n"
                )
        if _dep_parts:
            _dependency_block = (
                "\nDEPENDENCY ANALYSIS — These functions may need to be called "
                "before the target function will succeed:\n"
                + "".join(_dep_parts[:3])
            )

    # Build known ID hints from vocab
    _known_ids = []
    if ctx.vocab and ctx.vocab.get("id_formats"):
        _known_ids = [str(f) for f in ctx.vocab["id_formats"] if f][:4]
    _id_hint = ", ".join(_known_ids) if _known_ids else "CUST-001, ORD-001, ACCT-001"

    # Find init function names dynamically (not hardcoded to CS_Initialize)
    _init_names = [i["name"] for i in ctx.init_invocables] if ctx.init_invocables else ["the init function"]
    _init_call_hint = _init_names[0] if _init_names else "the init function"

    if ctx.runtime.instruction_fragment:
        _instruction = ctx.runtime.instruction_fragment
    elif _is_write_fn:
        _instruction = (
            "Your PRIMARY GOAL is to make this function return 0 (success).\n\n"
            "STEP 1 — READ THE DECOMPILED CODE below. It shows you:\n"
            "  - What conditions cause error returns (sentinel codes)\n"
            "  - What conditions lead to return 0 (success path)\n"
            "  - Whether this function depends on another function being called first\n"
            "  - Whether there are checksums, XOR validations, or flag checks\n\n"
            "STEP 2 — REASON about what inputs satisfy the success path:\n"
            "  - If the code checks a flag/bit set by another function, call that function first\n"
            "  - If the code has a checksum (XOR, hash), compute the correct input value\n"
            "  - If the code validates string format, use the format shown in the code\n"
            "  - If the code checks a balance/count, ensure the prerequisite state exists\n\n"
            "STEP 3 — EXECUTE your plan:\n"
            f"  a. Call {_init_call_hint}() first (always)\n"
            "  b. Call any prerequisite functions identified in Step 2\n"
            "  c. Call this function with the inputs you reasoned about\n"
            f"  Use these IDs as args: {_id_hint}. For amounts try 100, 500, 1000.\n\n"
            "STEP 4 — If still failing, try variations:\n"
            f"  - Different init modes: {_init_call_hint}(param_1=0), (param_1=1), (param_1=2)\n"
            "  - Different ID values from the static analysis hints\n"
            "  - If you see 0xFFFFFFFC (-4), that means 'locked' — look for an Unlock function\n"
            "  - If you see 0xFFFFFFFB (-5), that means 'insufficient balance' — the amount is too high\n\n"
            "After EACH attempt, check: did the function return 0?\n"
            "  - If YES: call record_finding(status='success', working_call=<the args that worked>)\n"
            "  - If NO after all attempts: call record_finding(status='error') with the sentinel codes seen.\n"
            "Do NOT waste tool calls on enrich_invocable until you find a working call.\n"
            + _decompiled_block
            + _dependency_block
        )
    else:
        _instruction = (
            "Call it with safe probe values, observe the result, "
            "then call enrich_invocable and record_finding with what you learned. "
            "Be brief — one summary sentence after you're done."
        )

    conversation = [
        sys_msg,
        {
            "role": "user",
            "content": (
                f"Explore the function '{fn_name}'. "
                + _instruction
                + ctx.static_hints_block
                + (ctx.write_unlock_block if _is_write_fn else "")
            ),
        },
    ]

    # T-04: persist first probe user message per job as observability artifact.
    # Only written once (first function processed) — proves static hints reached
    # the probe user message and were not silently dropped.
    try:
        _sample_blob = f"{ctx.job_id}/probe_user_message_sample.txt"
        _sample_exists = True
        try:
            _download_blob(ARTIFACT_CONTAINER, _sample_blob)
        except Exception:
            _sample_exists = False
        if not _sample_exists:
            _sample_content = conversation[1]["content"]
            _upload_to_blob(
                ARTIFACT_CONTAINER,
                _sample_blob,
                _sample_content.encode(),
            )
    except Exception as _t04_err:
        logger.debug("[%s] T-04: probe user message sample write failed: %s",
                     ctx.job_id, _t04_err)

    # Reasoning artifact: snapshot the vocab state at this function's probe start.
    # Captures what domain knowledge the model had available before probing began.
    # Writes to evidence/stage-01-pre-probe/probe-vocab-snapshot.json (appended per function).
    try:
        _vocab_snap_entry = {
            "function": fn_name,
            "vocab_keys": list(_vocab_snap.keys()),
            "vocab": _vocab_snap,
        }
        _vocab_snap_blob = (
            f"{ctx.job_id}/evidence/stage-01-pre-probe/probe-vocab-snapshot.json"
        )
        try:
            _existing_vsnaps = json.loads(
                _download_blob(ARTIFACT_CONTAINER, _vocab_snap_blob)
            )
        except Exception:
            _existing_vsnaps = []
        _existing_vsnaps.append(_vocab_snap_entry)
        _upload_to_blob(
            ARTIFACT_CONTAINER, _vocab_snap_blob,
            json.dumps(_existing_vsnaps, indent=2).encode(),
        )
    except Exception as _vsne:
        logger.debug("[%s] probe-vocab-snapshot write failed for %s: %s",
                     ctx.job_id, fn_name, _vsne)

    # Write functions get a larger tool budget — they need init+write+retry cycles
    _effective_max_tool_calls = (
        max(ctx.runtime.max_tool_calls, 14) if _is_write_fn
        else ctx.runtime.max_tool_calls
    )

    # Per-function local state (not shared; no lock required)
    _observed_successes: list[dict] = []
    _enrich_called = False
    _best_raw_result: str = ""
    _p_lookup = {p.get("name", ""): p for p in (inv.get("parameters") or [])}
    _fn_probe_log: list[dict] = []
    _fn_tool_call_count = 0
    _direct_target_tool_calls = 0
    _no_tool_call_rounds = 0
    _finding_recorded = False
    _policy_stop_reason: str | None = None
    _policy_events: list[dict] = []
    _write_retry_counts: dict[str, int] = defaultdict(int)

    # ── Main LLM round loop ──────────────────────────────────────────────────

    for _round in range(ctx.runtime.max_rounds):
        if _cancel_requested(ctx.job_id):
            break
        if _fn_tool_call_count >= _effective_max_tool_calls:
            logger.info("[%s] explore_worker: %s hit tool-call cap (%d), moving on",
                        ctx.job_id, fn_name, _effective_max_tool_calls)
            break

        _llm_ok = False
        for _retry in range(4):
            try:
                from typing import cast, Any as _Any
                response = ctx.client.chat.completions.create(
                    model=ctx.model,
                    messages=conversation,
                    tools=cast(_Any, ctx.tool_schemas),
                    tool_choice="auto",
                    temperature=0,
                    timeout=90.0,
                )
                _llm_ok = True
                break
            except Exception as exc:
                _exc_type = type(exc).__name__
                _exc_status = getattr(exc, "status_code", None)
                _exc_body = str(getattr(exc, "body", None) or "")[:200]
                _is_rate_limit = (
                    _exc_status in (429, "429")
                    or "rate" in str(exc).lower()
                    or "too many" in str(exc).lower()
                )
                if _is_rate_limit and _retry < 3:
                    # Respect Retry-After header if present; otherwise exponential backoff
                    _retry_after = getattr(exc, "headers", {})
                    _ra_val = (_retry_after or {}).get("retry-after") or (_retry_after or {}).get("Retry-After")
                    _backoff = int(float(_ra_val)) + 1 if _ra_val else (2 ** _retry) * 8  # 8s, 16s, 32s
                    logger.info(
                        "[%s] %s: 429 rate limit, retry %d/3 after %ds",
                        ctx.job_id, fn_name, _retry + 1, _backoff,
                    )
                    time.sleep(_backoff)
                    continue
                logger.warning(
                    "[%s] explore_worker: OpenAI call failed for %s round %d: "
                    "type=%s status=%s body=%s msg=%s",
                    ctx.job_id, fn_name, _round,
                    _exc_type, _exc_status, _exc_body, exc,
                )
                _fn_probe_log.append({
                    "phase": "llm_error", "function": fn_name, "round": _round,
                    "tool": None, "args": {},
                    "result_excerpt": f"LLM error: {_exc_type} status={_exc_status}: {exc}",
                    "trace": None, "classification": {"has_return": False}, "policy": None,
                })
                break
        if not _llm_ok:
            break

        msg = response.choices[0].message

        if not msg.tool_calls:
            if _direct_target_tool_calls < ctx.runtime.min_direct_probes:
                _no_tool_call_rounds += 1
                _fn_probe_log.append({
                    "phase": "no_tool_call_round", "function": fn_name, "round": _round,
                    "tool": None, "args": {},
                    "result_excerpt": "assistant returned no tool calls before probe floor met",
                    "trace": None, "classification": {"has_return": False}, "policy": None,
                })
                conversation.append({
                    "role": "user",
                    "content": (
                        f"You have not directly called '{fn_name}' enough times yet. "
                        f"Make at least {ctx.runtime.min_direct_probes} direct call(s) to "
                        f"{fn_name} now using concrete test values, then continue analysis."
                    ),
                })
                continue
            break  # model finished — no more tool calls needed

        # Append assistant turn to conversation
        conversation.append({
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": [
                {
                    "id": tc.id, "type": "function",
                    "function": {
                        "name": tc.function.name,       # type: ignore[union-attr]
                        "arguments": tc.function.arguments,  # type: ignore[union-attr]
                    },
                }
                for tc in msg.tool_calls
            ],
        })

        # Execute each tool call in this round
        for tc in msg.tool_calls:
            if _cancel_requested(ctx.job_id):
                break
            _fn = tc.function  # type: ignore[union-attr]
            tc_name = _fn.name
            try:
                tc_args = json.loads(_fn.arguments)
            except json.JSONDecodeError:
                tc_args = {}

            tc_inv = ctx.inv_map.get(tc_name)
            classification: dict = {
                "has_return": False, "format_guess": "not_executed",
                "confidence": 0.0, "source": "none",
            }
            _policy_block: dict | None = None

            if _is_write_fn and tc_name == fn_name:
                _ok, _reason, _detail = _write_policy_precheck(
                    fn_name, tc_args, _vocab_snap, ctx.unlock_result,
                )
                if not _ok:
                    _policy_stop_reason = _reason or "policy_exhausted"
                    _policy_block = {
                        "allowed": False,
                        "stop_reason": _policy_stop_reason,
                        "detail": _detail,
                    }
                    _policy_events.append(_policy_block)

            if tc_inv is not None:
                try:
                    if _policy_block:
                        _tc_traced = {
                            "result_str": "",
                            "trace": {"backend": "policy", "blocked": True},
                        }
                        tool_result = (
                            f"Policy blocked write probe: {_policy_block['stop_reason']}"
                            f" ({_policy_block.get('detail') or 'no additional detail'})"
                        )
                    else:
                        _tc_traced = _execute_tool_traced(tc_inv, tc_args)
                        tool_result = _tc_traced["result_str"]

                    classification = _classify_result_text(str(tool_result))

                    if (
                        _is_write_fn and tc_name == fn_name
                        and classification.get("has_return")
                    ):
                        _ret_signed = int(classification.get("signed", 0))
                        if _ret_signed != 0:
                            _cls = _sentinel_class_from_classification(classification)
                            _write_retry_counts[_cls] += 1
                            _budget = _WRITE_RETRY_BUDGET_BY_CLASS.get(
                                _cls, _WRITE_RETRY_BUDGET_BY_CLASS["unknown"]
                            )
                            if _write_retry_counts[_cls] > _budget:
                                _policy_stop_reason = "policy_exhausted"
                                _pe = {
                                    "allowed": False,
                                    "stop_reason": _policy_stop_reason,
                                    "detail": (
                                        f"retry budget exceeded for class={_cls} "
                                        f"({_write_retry_counts[_cls]}/{_budget})"
                                    ),
                                    "sentinel_class": _cls,
                                }
                                _policy_events.append(_pe)

                    _llm_result = tool_result
                    if classification.get("has_return"):
                        _llm_result += (
                            "\n[CLASSIFICATION] "
                            f"format={classification.get('format_guess')} "
                            f"confidence={classification.get('confidence')} "
                            f"source={classification.get('source')} "
                            f"signed={classification.get('signed')} "
                            f"unsigned={classification.get('unsigned')} "
                            f"hex={classification.get('hex')} "
                            f"meaning={classification.get('meaning') or 'unknown'}"
                        )

                    _arg_sources = {
                        k: _classify_arg_source(k, v, inv, _vocab_snap)
                        for k, v in tc_args.items()
                    }
                    _fn_probe_log.append({
                        "probe_id": f"{fn_name}:{_round}:{_fn_tool_call_count + 1}:{tc_name}",
                        "phase": "explore", "function": fn_name, "round": _round,
                        "reasoning": (msg.content or "").strip() or None,
                        "tool": tc_name, "args": tc_args,
                        "arg_sources": _arg_sources,
                        "result_excerpt": str(tool_result)[:200],
                        "trace": _tc_traced.get("trace"),
                        "classification": classification, "policy": _policy_block,
                    })

                    # Accumulate non-zero return codes into the cross-function sentinel catalog
                    if classification.get("has_return") and classification.get("signed", 0) != 0:
                        _hex = str(classification.get("hex"))
                        _arg_shape = sorted(list(tc_args.keys()))
                        with ctx._lock:
                            _cat = ctx.sentinel_catalog.setdefault(_hex, {
                                "hex": _hex,
                                "unsigned": classification.get("unsigned"),
                                "format_guess": classification.get("format_guess"),
                                "confidence": classification.get("confidence"),
                                "source": classification.get("source"),
                                "meaning": classification.get("meaning") or "unknown",
                                "rationale_summary": (
                                    f"{classification.get('format_guess')} from "
                                    f"{classification.get('source')}; "
                                    f"meaning={classification.get('meaning') or 'unknown'}"
                                ),
                                "evidence_count": 0, "evidence_refs": [],
                                "functions": [], "arg_shapes": [],
                                "phases": [], "provisional": True,
                            })
                            _cat["evidence_count"] = int(_cat.get("evidence_count", 0)) + 1
                            if fn_name not in _cat["functions"]:
                                _cat["functions"].append(fn_name)
                            _shape_s = ",".join(_arg_shape)
                            if _shape_s not in _cat["arg_shapes"]:
                                _cat["arg_shapes"].append(_shape_s)
                            if "explore" not in _cat["phases"]:
                                _cat["phases"].append("explore")
                            _probe_ref = f"{fn_name}:{_round}:{_fn_tool_call_count + 1}:{tc_name}"
                            if _probe_ref not in _cat["evidence_refs"]:
                                _cat["evidence_refs"].append(_probe_ref)
                            _conf_now = float(classification.get("confidence") or 0.0)
                            _src_now = str(classification.get("source") or "")
                            _strong_det = (
                                _src_now.startswith("deterministic.") and _conf_now >= 0.95
                            )
                            _stable = _conf_now >= 0.85 and int(_cat["evidence_count"]) >= 2
                            _cat["provisional"] = not (_strong_det or _stable)

                except Exception as exc:
                    tool_result = f"Tool error: {exc}"
                    _llm_result = tool_result
            else:
                tool_result = f"Tool '{tc_name}' not found."
                _llm_result = tool_result

            _fn_tool_call_count += 1

            if tc_name == "enrich_invocable":
                _enrich_called = True
            elif tc_name == "record_finding":
                _finding_recorded = True

            # Ground-truth tracking: record direct observations of return=0.
            # FIX-1: use _strip_output_buffer_params to exclude Ghidra output-buffer
            # parameters (undefined4* etc.) but keep string inputs (byte* etc.)
            if tc_name == fn_name:
                _direct_target_tool_calls += 1
                _ret_m = _re.search(r"Returned:\s*(-?\d+)", tool_result or "")
                if _ret_m:
                    _ret_val = int(_ret_m.group(1))
                    _is_version_success = (
                        _is_version_fn
                        and _ret_val > 0
                        and (_ret_val & 0xFFFFFFFF) not in ctx.sentinels
                    )
                    if _ret_val == 0 or _is_version_success:
                        _observed_successes.append(
                            _strip_output_buffer_params(tc_args, _p_lookup)
                        )
                        _best_raw_result = tool_result

            logger.info("[%s] explore_worker: tool=%s result=%s",
                        ctx.job_id, tc_name, str(tool_result)[:120])

            conversation.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": _llm_result,
            })

            if _policy_stop_reason in {"policy_exhausted", "dependency_missing", "schema_missing"}:
                break
            if _fn_tool_call_count >= _effective_max_tool_calls:
                break

        if _policy_stop_reason in {"policy_exhausted", "dependency_missing", "schema_missing"}:
            logger.info("[%s] explore_worker: %s stopped by write policy (%s)",
                        ctx.job_id, fn_name, _policy_stop_reason)
            break

    # ── Deterministic fallback (defined after main loop so it can mutate locals above) ──

    def _run_deterministic_fallback(reason: str, max_attempts: int) -> None:
        nonlocal _fn_tool_call_count, _direct_target_tool_calls, _best_raw_result
        _is_floor_enforcement = reason.startswith("direct probe floor unmet")
        if (
            (not ctx.runtime.deterministic_fallback_enabled and not _is_floor_enforcement)
            or max_attempts <= 0
        ):
            return
        for _attempt in range(max_attempts):
            if _fn_tool_call_count >= _effective_max_tool_calls:
                break
            _fb_args, _fb_selection = _build_ranked_fallback_probe_args(
                inv, _vocab_snap, attempt=_attempt
            )
            _pb: dict | None = None
            if _is_write_fn:
                _ok, _reason, _detail = _write_policy_precheck(
                    fn_name, _fb_args, _vocab_snap, ctx.unlock_result,
                )
                if not _ok:
                    _pb = {"allowed": False, "stop_reason": _reason, "detail": _detail}
            if _pb:
                _arg_sources = {
                    k: _classify_arg_source(k, v, inv, _vocab_snap)
                    for k, v in _fb_args.items()
                }
                _fn_probe_log.append({
                    "phase": "deterministic_fallback", "function": fn_name,
                    "round": _attempt, "reasoning": reason, "tool": fn_name,
                    "args": _fb_args,
                    "arg_sources": _arg_sources,
                    "arg_selection": _fb_selection,
                    "result_excerpt": f"Policy blocked fallback probe: {_pb['stop_reason']}",
                    "trace": {"backend": "policy", "blocked": True},
                    "classification": {"has_return": False}, "policy": _pb,
                })
                continue
            _tc = _execute_tool_traced(ctx.inv_map[fn_name], _fb_args)
            _res = _tc["result_str"]
            _cls = _classify_result_text(str(_res))
            _arg_sources = {
                k: _classify_arg_source(k, v, inv, _vocab_snap)
                for k, v in _fb_args.items()
            }
            _fn_probe_log.append({
                "probe_id": f"{fn_name}:fb:{_attempt + 1}",
                "phase": "deterministic_fallback", "function": fn_name,
                "round": _attempt, "reasoning": reason, "tool": fn_name,
                "args": _fb_args, "result_excerpt": str(_res)[:200],
                "arg_sources": _arg_sources,
                "arg_selection": _fb_selection,
                "trace": _tc.get("trace"), "classification": _cls, "policy": None,
            })
            _fn_tool_call_count += 1
            _direct_target_tool_calls += 1
            _ret_m = _re.search(r"Returned:\s*(-?\d+)", _res or "")
            if _ret_m:
                _ret_val = int(_ret_m.group(1))
                # Version functions return a packed UINT (e.g. 131841 = 2.3.1).
                # Any positive non-sentinel result counts as success.
                _is_version_success = (
                    _is_version_fn
                    and _ret_val > 0
                    and (_ret_val & 0xFFFFFFFF) not in ctx.sentinels
                )
                if _ret_val == 0 or _is_version_success:
                    _observed_successes.append(_fb_args)
                    _best_raw_result = _res
                    break

    # ── Post-loop: direct probe floor enforcement ────────────────────────────

    _missing_direct_calls = max(0, ctx.runtime.min_direct_probes - _direct_target_tool_calls)
    if _missing_direct_calls > 0:
        _run_deterministic_fallback(
            f"direct probe floor unmet ({_direct_target_tool_calls}/{ctx.runtime.min_direct_probes})",
            _missing_direct_calls,
        )

    if _is_write_fn and not _observed_successes:
        _run_deterministic_fallback("write function fallback coverage", 2)

    # Safety net: scan probe log for "Returned: 0" from the target function
    # that _observed_successes might have missed (e.g., 429 killed the round
    # between the tool execution and the success tracking).
    if not _observed_successes:
        for _pe in _fn_probe_log:
            if _pe.get("tool") != fn_name:
                continue
            _rex = _pe.get("result_excerpt") or ""
            _rm = _re.search(r"Returned:\s*0(?:\s|,|$)", _rex)
            if _rm:
                _recovered_args = _pe.get("args") or {}
                _observed_successes.append(_recovered_args)
                _best_raw_result = _rex
                logger.info(
                    "[%s] explore_worker: recovered success for %s from probe log (429 safety net)",
                    ctx.job_id, fn_name,
                )
                break

    # ── Force record_finding if LLM never called it ──────────────────────────

    if not _finding_recorded and _observed_successes:
        # D-8: Deterministic fallback got return=0 but LLM never called record_finding.
        try:
            _save_finding(ctx.job_id, {
                "function": fn_name, "status": "success",
                "finding": "Deterministic fallback probe returned 0.",
                "working_call": _observed_successes[0],
                "notes": (
                    "Auto-recorded: deterministic fallback succeeded "
                    "but LLM never called record_finding."
                ),
                "direct_target_tool_calls": _direct_target_tool_calls,
                "no_tool_call_rounds": _no_tool_call_rounds,
                "stop_reason": _policy_stop_reason,
            })
            _finding_recorded = True
            logger.info("[%s] explore_worker: D-8 forced record_finding(success) for %s — working_call=%s",
                        ctx.job_id, fn_name, _observed_successes[0])
        except Exception as _frf:
            logger.debug("[%s] D-8 forced record_finding failed for %s: %s",
                         ctx.job_id, fn_name, _frf)
    elif not _finding_recorded and not _observed_successes:
        # Collect sentinel codes seen across all probes for this function
        _seen_codes = set()
        for _pe in _fn_probe_log:
            _rex = (_pe.get("result_excerpt") or "")
            _rm = _re.search(r"Returned:\s*(\d+)", _rex)
            if _rm:
                _rv = int(_rm.group(1))
                if _rv != 0:
                    _seen_codes.add(hex(_rv) if _rv > 0xFFFFFFF0 else str(_rv))
        _code_str = ", ".join(sorted(_seen_codes)) or "unknown"
        try:
            _policy_note = (
                f" Stop reason: {_policy_stop_reason}." if _policy_stop_reason else ""
            )
            _save_finding(ctx.job_id, {
                "function": fn_name, "status": "error",
                "finding": (
                    f"All {_fn_tool_call_count} probes returned sentinel codes: "
                    f"{_code_str}. No working call found.{_policy_note}"
                ),
                "working_call": None,
                "notes": (
                    f"Auto-recorded: LLM exhausted {_fn_tool_call_count} tool calls "
                    "without calling record_finding."
                ),
                "direct_target_tool_calls": _direct_target_tool_calls,
                "no_tool_call_rounds": _no_tool_call_rounds,
                "stop_reason": _policy_stop_reason,
                "write_policy_events": _policy_events,
            })
            _finding_recorded = True
            logger.info("[%s] explore_worker: forced record_finding(error) for %s — codes: %s",
                        ctx.job_id, fn_name, _code_str)
        except Exception as _frf:
            logger.debug("[%s] forced record_finding failed for %s: %s",
                         ctx.job_id, fn_name, _frf)

    # Force enrich_invocable if the model skipped it and the function has params
    if not _enrich_called and inv.get("parameters"):
        _cur_findings = _load_findings(ctx.job_id)
        _last_f = next(
            (f for f in reversed(_cur_findings) if f.get("function") == fn_name), None
        )
        _finding_summary = (
            f"Finding: {_last_f.get('finding', '')}. Notes: {_last_f.get('notes', '')}"
            if _last_f else "No finding recorded."
        )
        try:
            from typing import cast, Any as _Any
            _enrich_resp = ctx.client.chat.completions.create(
                model=ctx.model,
                messages=conversation + [{
                    "role": "user",
                    "content": (
                        f"You did not call enrich_invocable for '{fn_name}'. "
                        f"Based on what you observed, call it now. "
                        "Rename each param_N to a semantic name (e.g. customer_id, balance, "
                        "output_buffer). For each parameter description, write what it does AND "
                        "include an example value from your testing. "
                        "Set the function description to a clear one-sentence summary. "
                        f"{_finding_summary}"
                    ),
                }],
                tools=cast(_Any, ctx.tool_schemas),
                tool_choice={"type": "function", "function": {"name": "enrich_invocable"}},
                temperature=0,
                timeout=90.0,
            )
            _em = _enrich_resp.choices[0].message
            if _em.tool_calls:
                _etc = _em.tool_calls[0]
                try:
                    _eargs = json.loads(_etc.function.arguments)  # type: ignore[union-attr]
                except json.JSONDecodeError:
                    _eargs = {}
                _execute_tool(ctx.inv_map["enrich_invocable"], _eargs)
                logger.info("[%s] explore_worker: forced enrich_invocable for %s",
                            ctx.job_id, fn_name)
                # Reasoning artifact: record param rename decisions from forced enrich.
                # Maps original Ghidra param names to what the model proposed.
                # Writes to evidence/stage-02-probe-loop/param-rename-decisions.json.
                try:
                    _original_params = {
                        p.get("name", ""): p.get("type", "")
                        for p in (inv.get("parameters") or [])
                        if isinstance(p, dict)
                    }
                    _rename_entry = {
                        "function": fn_name,
                        "original_params": _original_params,
                        "proposed_description": _eargs.get("description", ""),
                        "proposed_enrich_args": _eargs,
                    }
                    _rename_blob = (
                        f"{ctx.job_id}/evidence/stage-02-probe-loop/param-rename-decisions.json"
                    )
                    try:
                        _existing_renames = json.loads(
                            _download_blob(ARTIFACT_CONTAINER, _rename_blob)
                        )
                    except Exception:
                        _existing_renames = []
                    _existing_renames.append(_rename_entry)
                    _upload_to_blob(
                        ARTIFACT_CONTAINER, _rename_blob,
                        json.dumps(_existing_renames, indent=2).encode(),
                    )
                except Exception as _rde:
                    logger.debug("[%s] param-rename-decisions write failed for %s: %s",
                                 ctx.job_id, fn_name, _rde)
        except Exception as _ee:
            logger.debug("[%s] forced enrich failed for %s: %s", ctx.job_id, fn_name, _ee)

    # Update shared progress counter (thread-safe)
    with ctx._lock:
        ctx._state["explored"] += 1
        ctx.already_explored.add(fn_name)
    _set_explore_status(ctx.job_id, ctx._state["explored"], ctx.total, f"Completed {fn_name}")

    # ── Ground-truth override (D-11) ─────────────────────────────────────────

    if _observed_successes:
        # We observed return=0 or a valid version uint → force success and rewrite
        # finding text so synthesis doesn't see "All N probes returned sentinel codes"
        # for a function that actually works.
        _success_val_m = _re.search(r"Returned:\s*(-?\d+)", _best_raw_result or "")
        _success_val = int(_success_val_m.group(1)) if _success_val_m else 0
        if _is_version_fn and _success_val > 0:
            # Decode packed version UINT: (val>>16)&0xFF . (val>>8)&0xFF . val&0xFF
            _v_uval = _success_val & 0xFFFFFFFF
            _v_str = f"{(_v_uval >> 16) & 0xFF}.{(_v_uval >> 8) & 0xFF}.{_v_uval & 0xFF}"
            _gt_text = (
                f"Returns packed version UINT {_success_val} (decoded: {_v_str}) "
                f"when called with {_observed_successes[0]}. Non-zero return is SUCCESS for version functions."
            )
        else:
            _gt_text = (
                f"Returns 0 on success when called with {_observed_successes[0]}. "
                f"Discovered via deterministic fallback (direct LLM probes: {_direct_target_tool_calls})."
            )
        try:
            _patch_finding(ctx.job_id, fn_name, {
                "working_call": _observed_successes[0],
                "status": "success",
                "stop_reason": "success",
                "finding": _gt_text,
            })
            logger.info("[%s] explore_worker: ground-truth override for %s working_call=%s",
                        ctx.job_id, fn_name, _observed_successes[0])
        except Exception as _ce:
            logger.debug("[%s] consistency patch failed for %s: %s", ctx.job_id, fn_name, _ce)
    else:
        # Reasoning artifact: sentinel calibration decisions with coverage metadata.
        # Writes to evidence/stage-00-calibrate/sentinel-calibration-decisions.json.
        try:
            _calib_decision = {
                "functions_calibrated": len(ctx.invocables),
                "sentinel_count": len(ctx.sentinels),
                "used_defaults": ctx.sentinels == _SENTINEL_DEFAULTS,
                "sentinels_resolved": {
                    f"0x{k:08X}": v for k, v in ctx.sentinels.items()
                },
            }
            _upload_to_blob(
                ARTIFACT_CONTAINER,
                f"{ctx.job_id}/evidence/stage-00-calibrate/sentinel-calibration-decisions.json",
                json.dumps(_calib_decision, indent=2).encode(),
            )
        except Exception as _cde:
            logger.debug("[%s] phase0.5: sentinel-calibration-decisions write failed: %s",
                         ctx.job_id, _cde)
        # Verify the LLM's claimed working_call by re-running it
        _cur = _load_findings(ctx.job_id)
        _ff = next((f for f in reversed(_cur) if f.get("function") == fn_name), None)
        if _ff and _ff.get("working_call") is not None:
            _vi = ctx.inv_map.get(fn_name)
            if _vi:
                try:
                    _vt = _execute_tool_traced(_vi, _ff["working_call"])
                    _vr = _vt["result_str"]
                    _vr_class = _classify_result_text(str(_vr))
                    _verify_args = _ff["working_call"]
                    _arg_sources = {
                        k: _classify_arg_source(k, v, _vi, _vocab_snap)
                        for k, v in _verify_args.items()
                    }
                    _fn_probe_log.append({
                        "phase": "verify", "function": fn_name, "tool": fn_name,
                        "args": _verify_args,
                        "arg_sources": _arg_sources,
                        "result_excerpt": str(_vr)[:200],
                        "trace": _vt.get("trace"), "classification": _vr_class, "policy": None,
                    })
                    _vm = _re.search(r"Returned:\s*(-?\d+)", _vr or "")
                    if _vm:
                        _vret = int(_vm.group(1)) & 0xFFFFFFFF
                        if _vret not in ctx.sentinels and _vret <= 0xFFFFFFF0:
                            _patch_finding(ctx.job_id, fn_name,
                                           {"status": "success", "stop_reason": "success"})
                        else:
                            _patch_finding(ctx.job_id, fn_name, {
                                "working_call": None, "status": "error",
                                "stop_reason": _policy_stop_reason or "policy_exhausted",
                            })
                            logger.info("[%s] explore_worker: discarded hallucinated working_call for %s",
                                        ctx.job_id, fn_name)
                except Exception as _ve:
                    logger.debug("[%s] working_call verify failed for %s: %s",
                                 ctx.job_id, fn_name, _ve)

    # ── D-12: Auto-enrich schema when LLM skipped enrich_invocable ──────────
    # If the LLM never called enrich_invocable (e.g. all LLM rounds failed and
    # only the deterministic fallback ran), synthesise param descriptions from
    # Ghidra type info + probe findings so the schema isn't left as raw Ghidra
    # boilerplate.  Only fires when the description is still the default Ghidra
    # annotation — never overwrites a description the LLM set.

    if not _enrich_called:
        try:
            from api.explore_phases import _infer_param_desc
            _cur_findings = _load_findings(ctx.job_id)
            _fn_findings = [f for f in _cur_findings if f.get("function") == fn_name]
            _auto_patch: dict = {}
            _cur_desc = (inv.get("description") or inv.get("doc") or "").strip()
            _is_ghidra = (
                _cur_desc.lower().startswith("recovered by ghidra")
                or not _cur_desc
            )
            # Build param-level patches first
            for _p in (inv.get("parameters") or []):
                _pname = _p.get("name", "")
                _ptype = _p.get("type", "")
                _pdesc = (_p.get("description") or "").strip()
                _pdesc_generic = (
                    not _pdesc
                    or _pdesc.lower().startswith("input ")
                    or _pdesc.lower().startswith("output ")
                    or _pdesc.lower().startswith("parameter of type")
                    or len(_pdesc) < 8
                )
                if _pdesc_generic:
                    _inferred = _infer_param_desc(_pname, _ptype, _fn_findings)
                    if _inferred and not _inferred.startswith("Parameter of type"):
                        _auto_patch[_pname] = {"description": _inferred}
            # Set a minimal function description from the finding text if still Ghidra
            if _is_ghidra and _fn_findings:
                _latest = _fn_findings[-1]
                _finding_text = (_latest.get("finding") or "").strip()
                if _finding_text and len(_finding_text) > 20:
                    _auto_patch["function_description"] = _finding_text[:300]
            if _auto_patch:
                _patch_invocable(ctx.job_id, fn_name, _auto_patch)
                logger.info("[%s] D-12: auto-enriched %s (%d fields)",
                            ctx.job_id, fn_name, len(_auto_patch))
        except Exception as _ae:
            logger.debug("[%s] D-12 auto-enrich failed for %s: %s",
                         ctx.job_id, fn_name, _ae)

    # ── Hypothesis-driven interpretation ─────────────────────────────────────

    if _best_raw_result:
        try:
            _hyp = _generate_hypothesis(
                ctx.client, ctx.model, fn_name,
                _best_raw_result, _vocab_snap, _load_findings(ctx.job_id),
            )
            if _hyp.get("interpretations"):
                _patch_finding(ctx.job_id, fn_name,
                               {"interpretation": _hyp["interpretations"]})
                if "value_semantics" not in _vocab_snap:
                    _vocab_snap["value_semantics"] = {}
                _vocab_snap["value_semantics"].update(_hyp["interpretations"])
                logger.info("[%s] hypothesis for %s: %s",
                            ctx.job_id, fn_name, _hyp["interpretations"])
            _cv = _hyp.get("cross_validation")
            if (
                _cv and isinstance(_cv, dict)
                and _cv.get("function") in ctx.inv_map
                and _cv.get("function") in ctx.already_explored
            ):
                _cv_inv = ctx.inv_map[_cv["function"]]
                _cvt = _execute_tool_traced(_cv_inv, _cv.get("args") or {})
                _cv_result = _cvt["result_str"]
                _cv_class = _classify_result_text(str(_cv_result))
                _cv_args = _cv.get("args") or {}
                _arg_sources = {
                    k: _classify_arg_source(k, v, _cv_inv, _vocab_snap)
                    for k, v in _cv_args.items()
                }
                _fn_probe_log.append({
                    "phase": "cross_validate", "function": fn_name,
                    "tool": _cv["function"], "args": _cv_args,
                    "arg_sources": _arg_sources,
                    "result_excerpt": str(_cv_result)[:200],
                    "trace": _cvt.get("trace"), "classification": _cv_class, "policy": None,
                })
                _patch_finding(ctx.job_id, fn_name, {
                    "cross_validation": (
                        f"{_cv['function']}({_cv.get('args')}) → {_cv_result}"
                    ),
                })
                logger.info("[%s] cross-validated %s via %s: %s",
                            ctx.job_id, fn_name, _cv["function"], str(_cv_result)[:80])
        except Exception as _hyp_e:
            logger.debug("[%s] hypothesis failed for %s: %s", ctx.job_id, fn_name, _hyp_e)

    # ── Vocab update from this function's finding ────────────────────────────

    last_finding = _load_findings(ctx.job_id)
    if last_finding:
        last = next((f for f in reversed(last_finding) if f.get("function") == fn_name), None)
        if last:
            try:
                # Mutates _vocab_snap in-place; merge learned facts into shared vocab
                _update_vocabulary(ctx.client, ctx.model, _vocab_snap, last)
                with ctx._lock:
                    for _mk, _mv in _vocab_snap.items():
                        if _mk.startswith("_"):
                            continue
                        if isinstance(_mv, list) and isinstance(ctx.vocab.get(_mk), list):
                            _existing = set(map(str, ctx.vocab[_mk]))
                            ctx.vocab[_mk] = ctx.vocab[_mk] + [
                                x for x in _mv if str(x) not in _existing
                            ]
                        elif isinstance(_mv, dict) and isinstance(ctx.vocab.get(_mk), dict):
                            ctx.vocab[_mk].update(_mv)
                        elif _mv and _mk not in ctx.vocab:
                            ctx.vocab[_mk] = _mv
                # Persist updated vocab to blob so next session starts informed
                try:
                    _upload_to_blob(
                        ARTIFACT_CONTAINER,
                        f"{ctx.job_id}/vocab.json",
                        json.dumps(_vocab_snap).encode(),
                    )
                except Exception as _vpe:
                    logger.debug("[%s] vocab persist failed: %s", ctx.job_id, _vpe)
            except Exception as _ve:
                logger.debug("[%s] vocab update failed for %s: %s", ctx.job_id, fn_name, _ve)

    # ── Flush probe log for this function ────────────────────────────────────

    # Reasoning artifact: persist model's per-round reasoning text for this function.
    # Extracts all assistant messages from the conversation that preceded tool calls.
    # Writes to evidence/stage-02-probe-loop/probe-round-reasoning.json (appended per function).
    try:
        _round_reasoning_entries = []
        _conv_round = 0
        for _cm in conversation:
            if _cm.get("role") == "assistant":
                _reasoning_text = (_cm.get("content") or "").strip()
                if _reasoning_text:
                    _round_reasoning_entries.append({
                        "function": fn_name,
                        "round": _conv_round,
                        "reasoning": _reasoning_text,
                    })
                _conv_round += 1
        if _round_reasoning_entries:
            _reasoning_blob = (
                f"{ctx.job_id}/evidence/stage-02-probe-loop/probe-round-reasoning.json"
            )
            try:
                _existing_raw = _download_blob(ARTIFACT_CONTAINER, _reasoning_blob)
                _existing = json.loads(_existing_raw)
            except Exception:
                _existing = []
            _existing.extend(_round_reasoning_entries)
            _upload_to_blob(
                ARTIFACT_CONTAINER,
                _reasoning_blob,
                json.dumps(_existing, indent=2).encode(),
            )
    except Exception as _rre:
        logger.debug("[%s] probe-round-reasoning write failed for %s: %s",
                     ctx.job_id, fn_name, _rre)

    # Reasoning artifact: persist probe stop reason for this function.
    # Writes to evidence/stage-02-probe-loop/probe-stop-reasons.json (appended per function).
    try:
        # Determine stop reason from local state
        if _policy_stop_reason:
            _computed_stop_reason = _policy_stop_reason
        elif _fn_tool_call_count >= _effective_max_tool_calls:
            _computed_stop_reason = "cap_hit_tool_calls"
        elif _cancel_requested(ctx.job_id):
            _computed_stop_reason = "cancel"
        else:
            _computed_stop_reason = "natural"

        # Get model's final summary sentence (last assistant message)
        _final_summary = ""
        for _cm in reversed(conversation):
            if _cm.get("role") == "assistant" and (_cm.get("content") or "").strip():
                _final_summary = (_cm.get("content") or "").strip()
                break

        _stop_entry = {
            "function": fn_name,
            "stop_reason": _computed_stop_reason,
            "rounds_used": sum(1 for _cm in conversation if _cm.get("role") == "assistant"),
            "tool_calls_used": _fn_tool_call_count,
            "direct_target_calls": _direct_target_tool_calls,
            "final_summary": _final_summary[:300] if _final_summary else None,
        }
        _stop_blob = (
            f"{ctx.job_id}/evidence/stage-02-probe-loop/probe-stop-reasons.json"
        )
        try:
            _existing_stops_raw = _download_blob(ARTIFACT_CONTAINER, _stop_blob)
            _existing_stops = json.loads(_existing_stops_raw)
        except Exception:
            _existing_stops = []
        _existing_stops.append(_stop_entry)
        _upload_to_blob(
            ARTIFACT_CONTAINER,
            _stop_blob,
            json.dumps(_existing_stops, indent=2).encode(),
        )
    except Exception as _sre:
        logger.debug("[%s] probe-stop-reasons write failed for %s: %s",
                     ctx.job_id, fn_name, _sre)

    # Reasoning artifact: probe strategy summary — which strategies fired per function.
    # Writes to evidence/stage-02-probe-loop/probe-strategy-summary.json (appended per function).
    try:
        _phases_used = sorted({e.get("phase", "unknown") for e in _fn_probe_log})
        _strategy_entry = {
            "function": fn_name,
            "phases_used": _phases_used,
            "rounds_used": sum(1 for m in conversation if m.get("role") == "assistant"),
            "tool_calls_used": _fn_tool_call_count,
            "direct_target_calls": _direct_target_tool_calls,
            "no_tool_call_rounds": _no_tool_call_rounds,
            "deterministic_fallback_fired": any(
                e.get("phase") == "deterministic_fallback" for e in _fn_probe_log
            ),
            "cross_validation_ran": any(
                e.get("phase") == "cross_validate" for e in _fn_probe_log
            ),
            "hypothesis_ran": bool(_best_raw_result),
            "is_write_fn": _is_write_fn,
            "enrich_called": _enrich_called,
            "finding_recorded": _finding_recorded,
            "stop_reason": _policy_stop_reason or (
                "cap_hit_tool_calls"
                if _fn_tool_call_count >= _effective_max_tool_calls
                else "natural"
            ),
        }
        _strategy_blob = (
            f"{ctx.job_id}/evidence/stage-02-probe-loop/probe-strategy-summary.json"
        )
        try:
            _existing_strats = json.loads(
                _download_blob(ARTIFACT_CONTAINER, _strategy_blob)
            )
        except Exception:
            _existing_strats = []
        _existing_strats.append(_strategy_entry)
        _upload_to_blob(
            ARTIFACT_CONTAINER, _strategy_blob,
            json.dumps(_existing_strats, indent=2).encode(),
        )
    except Exception as _psume:
        logger.debug("[%s] probe-strategy-summary write failed for %s: %s",
                     ctx.job_id, fn_name, _psume)

    if _fn_probe_log:
        try:
            _append_explore_probe_log(ctx.job_id, _fn_probe_log)
        except Exception as _ple:
            logger.debug("[%s] explore probe log flush failed for %s: %s",
                         ctx.job_id, fn_name, _ple)


def _run_phase_3_probe_loop(ctx: ExploreContext) -> None:
    """Dispatch _explore_one for each invocable — parallel if concurrency > 1.

    READS:  ctx.invocables, ctx.runtime.concurrency
    WRITES: ctx._state, ctx.sentinel_catalog, ctx.already_explored (via _explore_one)
    INVARIANT: every invocable is processed exactly once (cancel may abort early)
    HANDOFF: explore_probe_log.json continuously flushed to blob by _explore_one
    """
    # Save probe-loop model context: the full system message the LLM will receive.
    # This captures variable parts (vocab, findings, sentinels) at probe-loop start.
    try:
        from api.storage import _load_findings
        _ctx_prior = _load_findings(ctx.job_id)
        _ctx_sys = _build_explore_system_message(
            ctx.invocables, _ctx_prior,
            sentinels=ctx.sentinels, vocab=ctx.vocab,
            use_cases=ctx.use_cases_text,
        )
        _save_stage_context(ctx.job_id, "model_context_phase_01_probe_loop.txt",
                            _ctx_sys.get("content", ""))
    except Exception as _mce:
        logger.debug("[%s] phase3: model context save failed: %s", ctx.job_id, _mce)

    # Small inter-function delay to spread LLM calls and avoid 429 bursts.
    # Write functions get longer delays since they use more LLM rounds.
    _INTER_FN_DELAY = 1.5  # seconds between non-write functions
    _WRITE_FN_DELAY = 3.0  # seconds between write functions

    if ctx.runtime.concurrency > 1:
        logger.info("[%s] phase3: running %d functions with concurrency=%d",
                    ctx.job_id, len(ctx.invocables), ctx.runtime.concurrency)
        with _TPE(max_workers=ctx.runtime.concurrency) as _pool:
            list(_pool.map(lambda inv: _explore_one(inv, ctx), ctx.invocables))
    else:
        for _fn_idx, inv in enumerate(ctx.invocables):
            if _cancel_requested(ctx.job_id):
                break
            if _fn_idx > 0:
                _delay = _WRITE_FN_DELAY if _WRITE_FN_RE.search(inv["name"]) else _INTER_FN_DELAY
                time.sleep(_delay)
            _explore_one(inv, ctx)


# ══════════════════════════════════════════════════════════════════════════════
#  Phase 4 – AC-1 probe-log reconciliation
