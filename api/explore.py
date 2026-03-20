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
import os as _os
import re as _re
import threading as _threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor as _TPE
from typing import Any

from api.config import OPENAI_ENDPOINT, OPENAI_DEPLOYMENT, OPENAI_REASONING_DEPLOYMENT, OPENAI_API_KEY, OPENAI_EXPLORE_MODEL, ARTIFACT_CONTAINER
from api.executor import _execute_tool, _execute_tool_traced
from api.storage import _persist_job_status, _get_job_status, _patch_invocable, _save_finding, _patch_finding, _upload_to_blob, _download_blob, _append_transcript, _append_executor_trace, _append_explore_probe_log, _register_invocables, _get_current_invocables
from api.telemetry import _openai_client
from api.explore_phases import (
    _SENTINEL_DEFAULTS, _CAP_PROFILE, _MAX_EXPLORE_ROUNDS_PER_FUNCTION, _MAX_TOOL_CALLS_PER_FUNCTION, _MAX_FUNCTIONS_PER_SESSION,
    _calibrate_sentinels, _probe_write_unlock, _infer_param_desc,
)
from api.explore_vocab import (
    _update_vocabulary, _generate_hypothesis, _backfill_schema_from_synthesis,
    _vocab_block, _uncertainty_score,
)
from api.explore_prompts import (
    _build_explore_system_message, _generate_behavioral_spec, _synthesize, _generate_confidence_gaps,
)
from api.explore_helpers import (
    _GAP_RESOLUTION_ENABLED,
    _WRITE_FN_RE,
    _WRITE_RETRY_BUDGET_BY_CLASS,
    _build_fallback_probe_args,
    _build_tool_schemas,
    _classify_result_text,
    _sentinel_class_from_classification,
    _set_explore_status,
    _snapshot_schema_stage,
    _write_policy_precheck,
)
from api.explore_gap import _attempt_gap_resolution, _run_gap_answer_mini_sessions

logger = logging.getLogger("mcp_factory.api")



def _explore_worker(job_id: str, invocables: list[dict]) -> None:
    """Background worker: explore each invocable with the LLM and enrich the schema."""
    if not OPENAI_ENDPOINT and not OPENAI_API_KEY:
        logger.warning("[%s] explore_worker: neither OPENAI_API_KEY nor AZURE_OPENAI_ENDPOINT configured — aborting", job_id)
        return

    from api.storage import _load_findings

    _job_runtime = (_get_job_status(job_id) or {}).get("explore_runtime") or {}
    _max_functions = int(_job_runtime.get("max_functions") or _MAX_FUNCTIONS_PER_SESSION)
    _max_rounds = int(_job_runtime.get("max_rounds") or _MAX_EXPLORE_ROUNDS_PER_FUNCTION)
    _max_tool_calls = int(_job_runtime.get("max_tool_calls") or _MAX_TOOL_CALLS_PER_FUNCTION)
    _gap_resolution_enabled = bool(_job_runtime.get("gap_resolution_enabled", _GAP_RESOLUTION_ENABLED))
    _clarification_enabled = bool(_job_runtime.get("clarification_questions_enabled", True))
    _min_direct_probes = max(1, int(_job_runtime.get("min_direct_probes_per_function") or 1))
    _skip_documented = bool(_job_runtime.get("skip_documented", True))
    _deterministic_fallback_enabled = bool(_job_runtime.get("deterministic_fallback_enabled", True))
    _effective_cap_profile = str(_job_runtime.get("cap_profile") or _CAP_PROFILE)
    _run_started_at = float((_get_job_status(job_id) or {}).get("explore_started_at") or time.time())

    def _cancel_requested() -> bool:
        _cur = _get_job_status(job_id) or {}
        return bool(_cur.get("explore_cancel_requested"))

    total = min(len(invocables), _max_functions)
    invocables = invocables[:total]

    logger.info("[%s] explore_worker: starting, %d functions to explore", job_id, total)

    # Update status to exploring
    _set_explore_status(job_id, 0, total, "Starting exploration…")

    # Build inv_map for tool dispatch
    inv_map: dict[str, dict] = {}
    for inv in invocables:
        inv_map[inv["name"]] = inv

    # COH-1: Register invocables so enrich_invocable / _patch_invocable can
    # resolve function names during this explore session.
    _register_invocables(job_id, invocables)

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
    tool_schemas = _build_tool_schemas(invocables)

    client = _openai_client()
    # Use the dedicated explore model (gpt-4o-mini by default) for cost efficiency.
    # When using direct OpenAI key, OPENAI_EXPLORE_MODEL controls this.
    # When using Azure, fall back to the reasoning deployment.
    model = OPENAI_EXPLORE_MODEL if OPENAI_API_KEY else (OPENAI_REASONING_DEPLOYMENT or OPENAI_DEPLOYMENT)

    try:
        _upload_to_blob(
            ARTIFACT_CONTAINER,
            f"{job_id}/explore_config.json",
            json.dumps(
                {
                    "mode": _job_runtime.get("mode") or "normal",
                    "cap_profile": _effective_cap_profile,
                    "max_rounds_per_function": _max_rounds,
                    "max_tool_calls_per_function": _max_tool_calls,
                    "max_functions_per_session": _max_functions,
                    "min_direct_probes_per_function": _min_direct_probes,
                    "skip_documented": _skip_documented,
                    "deterministic_fallback_enabled": _deterministic_fallback_enabled,
                    "gap_resolution_enabled": _gap_resolution_enabled,
                    "clarification_questions_enabled": _clarification_enabled,
                },
                indent=2,
            ).encode(),
        )
    except Exception as _cfg_e:
        logger.debug("[%s] explore_worker: explore_config upload failed: %s", job_id, _cfg_e)

    explored = 0
    try:
        prior_findings = _load_findings(job_id)
        already_explored = {f.get("function") for f in prior_findings if f.get("function")}

        # Snapshot the schema before any exploration mutates it.
        # Stored as mcp_schema_t0.json so the session-snapshot endpoint can
        # include it as "pre-enrichment" alongside the final post-explore schema.
        try:
            _raw_schema = _download_blob(ARTIFACT_CONTAINER, f"{job_id}/mcp_schema.json")
            _upload_to_blob(ARTIFACT_CONTAINER, f"{job_id}/mcp_schema_t0.json", _raw_schema)
            logger.info("[%s] explore_worker: pre-enrichment schema snapshot saved", job_id)
        except Exception as _snap_e:
            logger.debug("[%s] explore_worker: schema snapshot failed: %s", job_id, _snap_e)

        # Phase 0.5: auto-calibrate sentinel error codes for this DLL
        sentinels = _SENTINEL_DEFAULTS
        try:
            logger.info("[%s] explore_worker: phase0.5 calibrating sentinels…", job_id)
            _set_explore_status(job_id, 0, total, "Calibrating error codes…")
            sentinels = _calibrate_sentinels(invocables, client, model, job_id=job_id)
            logger.info("[%s] explore_worker: phase0.5 sentinels: %s", job_id,
                        {f"0x{k:08X}": v for k, v in sentinels.items()})
            try:
                _upload_to_blob(
                    ARTIFACT_CONTAINER,
                    f"{job_id}/sentinel_calibration.json",
                    json.dumps({f"0x{k:08X}": v for k, v in sentinels.items()}, indent=2).encode(),
                )
            except Exception as _sce:
                logger.debug("[%s] explore_worker: sentinel artifact upload failed: %s", job_id, _sce)
        except Exception as _se:
            logger.debug("[%s] explore_worker: phase0.5 failed, using defaults: %s", job_id, _se)

        # Shared vocabulary table — grows as we learn things about this DLL.
        # Reload from blob if a previous session already built one (cross-session memory).
        vocab: dict = {}
        try:
            from api.storage import _download_blob as _dl_blob
            _vraw = _dl_blob(ARTIFACT_CONTAINER, f"{job_id}/vocab.json")
            vocab = json.loads(_vraw)
            logger.info("[%s] explore_worker: reloaded vocab from blob (%d keys)", job_id, len(vocab))
        except Exception:
            pass  # normal on first run

        # Persist calibrated error codes from sentinels into vocab so
        # vocab_coverage.json can score against real DLL-specific codes.
        # Merge (not setdefault) so re-runs and newly discovered codes are
        # always incorporated, not silently dropped when error_codes exists.
        if sentinels is not _SENTINEL_DEFAULTS:
            vocab.setdefault("error_codes", {})
            vocab["error_codes"].update({f"0x{k:08X}": v for k, v in sentinels.items()})

        # Seed vocab from user-supplied hints so the LLM starts informed
        # even before Phase 0 extracts strings from the binary.
        _use_cases_text = ""
        try:
            _job_meta = _get_job_status(job_id) or {}
            _user_hints = (_job_meta.get("hints") or "").strip()
            _use_cases_text = (_job_meta.get("use_cases") or "").strip()
            if _user_hints:
                # Extract ID-like patterns (e.g. CUST-001, ORD-20040301-0042)
                _hint_ids = list(dict.fromkeys(_re.findall(r'[A-Z]{2,6}-[\w-]+', _user_hints)))
                if _hint_ids and "id_formats" not in vocab:
                    vocab["id_formats"] = _hint_ids
                # Strip DOMAIN ANSWER entries injected by the answer_gaps endpoint
                # before writing notes so synthetic answers don't pollute the vocab.
                _clean_hints = " | ".join(
                    p.strip() for p in _user_hints.split(" | ")
                    if p.strip() and not p.strip().startswith("DOMAIN ANSWER")
                )
                if _clean_hints and "notes" not in vocab:
                    vocab["notes"] = f"User description: {_clean_hints}"
                logger.info("[%s] explore_worker: seeded vocab from user hints: %s", job_id, _user_hints[:80])
            # Persist use_cases into vocab so the chat phase sees it even after
            # vocab["notes"] may be overwritten by later vocabulary updates.
            if _use_cases_text and "user_context" not in vocab:
                vocab["user_context"] = _use_cases_text
                logger.info("[%s] explore_worker: seeded vocab[user_context] from use_cases", job_id)
        except Exception as _he:
            logger.debug("[%s] explore_worker: hints seed failed: %s", job_id, _he)

        # Phase 0: static enrichment — G-4 IAT, G-7 binary strings, G-8 PE version info, G-9 Capstone sentinels
        _static_hints_block = ""
        _dll_strings: dict = {}
        _static_analysis_result: dict = {}
        try:
            dll_path = next(
                (inv.get("execution", {}).get("dll_path", "") for inv in invocables
                 if inv.get("execution", {}).get("dll_path")),
                "",
            )
            from pathlib import Path as _Path
            _data: bytes | None = None
            # Primary: read from local path (works when running on Windows or
            # when the path is a valid Linux temp path from the upload worker).
            if dll_path:
                try:
                    _data = _Path(dll_path).read_bytes()
                except Exception:
                    pass
            # Fallback: download the original uploaded binary from Blob Storage.
            # This handles the common Azure deployment case where dll_path is a
            # Windows bridge VM path that doesn't exist on the Linux API container.
            if _data is None:
                try:
                    from api.storage import _download_blob as _dl_blob
                    from api.config import UPLOAD_CONTAINER
                    # The upload worker stores the file as {job_id}/input<suffix>
                    # Try common DLL/EXE extensions, then any blob starting with input
                    for _ext in (".dll", ".exe", ".bin", ""):
                        try:
                            _data = _dl_blob(UPLOAD_CONTAINER, f"{job_id}/input{_ext}")
                            break
                        except Exception:
                            pass
                except Exception:
                    pass
            if _data is not None:
                from api.static_analysis import run_static_analysis, build_vocab_seeds, build_static_hints_block as _build_shb
                _dll_name = _Path(dll_path).name if dll_path else "unknown.dll"
                _static_analysis_result = run_static_analysis(_data, _dll_name)

                # G-7: promote binary evidence into vocab as first-class facts
                # (binary strings are ground truth — they override nothing the user
                # set, but fill any vocab key the user left empty)
                _vocab_seeds = build_vocab_seeds(_static_analysis_result, vocab)
                for _k, _v in _vocab_seeds.items():
                    vocab.setdefault(_k, _v)
                    if _k not in vocab or vocab[_k] == _v:
                        vocab[_k] = _v  # ensure seed takes effect even if key exists but is falsy
                if _vocab_seeds:
                    logger.info("[%s] explore_worker: phase0 vocab seeds applied: %s",
                                job_id, list(_vocab_seeds.keys()))

                # Build legacy-compat dll_strings dict for _probe_write_unlock
                _bs = _static_analysis_result.get("binary_strings", {})
                _dll_strings = {
                    "ids":    _bs.get("ids_found", []),
                    "emails": _bs.get("emails_found", []),
                    "all":    _bs.get("ids_found", []) + _bs.get("emails_found", []),
                }

                # Build text block appended to LLM prompt (secondary role after vocab)
                _static_hints_block = _build_shb(_static_analysis_result)
                _static_analysis_result["injected_into_prompt"] = bool(_static_hints_block)
                _static_analysis_result["static_hints_block_length"] = len(_static_hints_block)

                # Persist static_analysis.json to blob so save-session can verify it
                try:
                    _upload_to_blob(
                        ARTIFACT_CONTAINER,
                        f"{job_id}/static_analysis.json",
                        json.dumps(_static_analysis_result, indent=2).encode(),
                    )
                    logger.info("[%s] explore_worker: phase0 static_analysis.json uploaded", job_id)
                except Exception as _sa_err:
                    logger.debug("[%s] explore_worker: static_analysis.json upload failed: %s", job_id, _sa_err)

                logger.info("[%s] explore_worker: phase0 G-4/G-7/G-8/G-9 complete — "
                            "%d IDs, %d sentinels, IAT:%s",
                            job_id,
                            len(_bs.get("ids_found", [])),
                            len(_static_analysis_result.get("sentinel_constants", {}).get("harvested", {})),
                            list(_static_analysis_result.get("iat_capabilities", {}).get("categories", {}).keys()))
        except Exception as _e:
            logger.debug("[%s] explore_worker: phase0 static enrichment failed: %s", job_id, _e)

        # Phase 1: write-unlock probe — mirror of run_local.py --write-probe logic
        write_unlock_block = ""
        unlock_result: dict = {"unlocked": False, "sequence": [], "notes": "not attempted"}
        try:
            logger.info("[%s] explore_worker: phase1 write-unlock probe…", job_id)
            _set_explore_status(job_id, 0, total, "Testing write-mode unlock…")
            unlock_result = _probe_write_unlock(invocables, _dll_strings)
            if unlock_result.get("unlocked"):
                write_unlock_block = (
                    "\nWRITE MODE ACTIVE: The write-unlock sequence has already been executed. "
                    "Write functions (any function whose name implies state changes — Process, Update, Set, Create, Delete, Transfer, Submit, Send, Redeem, Unlock) "
                    "should now succeed. Probe them with real ID values from STATIC ANALYSIS HINTS.\n"
                )
                logger.info("[%s] explore_worker: phase1 UNLOCKED: %s", job_id, unlock_result["notes"])
            else:
                logger.info("[%s] explore_worker: phase1 not unlocked: %s", job_id,
                            unlock_result.get("notes", ""))
        except Exception as _we:
            logger.debug("[%s] explore_worker: phase1 write-unlock probe failed: %s", job_id, _we)

        # Active Learning-style ordering: explore init functions first (unlock state),
        # then sort remaining by uncertainty score ascending (simpler → complex).
        # By the time the LLM reaches ambiguous multi-param functions, the vocab
        # table is rich with cross-function conventions learned from simpler ones.
        _INIT_RE = _re.compile(r"(init(ializ)?|startup|start|setup|open|login|logon|connect)", _re.I)
        _init_invs  = [inv for inv in invocables if _INIT_RE.search(inv["name"])]
        _other_invs = [inv for inv in invocables if not _INIT_RE.search(inv["name"])]
        _other_invs.sort(key=_uncertainty_score)
        invocables = _init_invs + _other_invs
        logger.info("[%s] explore_worker: ordered %d init + %d others by uncertainty",
                    job_id, len(_init_invs), len(_other_invs))

        # ── Per-function exploration (parallel when EXPLORE_CONCURRENCY > 1) ────────
        _CONCURRENCY = int(_os.getenv("EXPLORE_CONCURRENCY", "1"))
        _lock = _threading.Lock()
        _state = {"explored": explored}
        _sentinel_catalog: dict[str, dict] = {}

        def _explore_one(inv: dict) -> None:
            if _cancel_requested():
                return
            fn_name = inv["name"]
            _vocab_snap = dict(vocab)

            # Skip functions already documented in a previous session (thread-safe)
            _skip = False
            with _lock:
                if _skip_documented and fn_name in already_explored:
                    _state["explored"] += 1
                    _skip = True
            if _skip:
                _set_explore_status(job_id, _state["explored"], total, f"Skipped {fn_name} (already documented)")
                return

            _set_explore_status(job_id, _state["explored"], total, f"Exploring {fn_name}…")
            logger.info("[%s] explore_worker: starting %s (%d/%d)", job_id, fn_name, _state["explored"] + 1, total)

            # Build a focused conversation just for this function
            prior = _load_findings(job_id)
            sys_msg = _build_explore_system_message(invocables, prior, sentinels=sentinels, vocab=_vocab_snap, use_cases=_use_cases_text)
            _is_write_fn = bool(_WRITE_FN_RE.search(fn_name))
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
                        + (write_unlock_block if _is_write_fn else "")
                    ),
                },
            ]

            # Track calls that returned 0 for ground-truth consistency enforcement
            _observed_successes: list[dict] = []
            _enrich_called = False
            _best_raw_result: str = ""  # best successful result captured for hypothesis generation
            _p_lookup = {p.get("name", ""): p for p in (inv.get("parameters") or [])}
            _fn_probe_log: list[dict] = []  # accumulate probe entries for explore_probe_log.json
            _fn_tool_call_count = 0  # hard cap on tool calls per function
            _direct_target_tool_calls = 0
            _no_tool_call_rounds = 0
            _finding_recorded = False  # track whether LLM called record_finding
            _policy_stop_reason: str | None = None
            _policy_events: list[dict] = []
            _write_retry_counts: dict[str, int] = defaultdict(int)

            for _round in range(_max_rounds):
                if _cancel_requested():
                    break
                if _fn_tool_call_count >= _max_tool_calls:
                    logger.info("[%s] explore_worker: %s hit tool-call cap (%d), moving on",
                                job_id, fn_name, _max_tool_calls)
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
                    logger.warning(
                        "[%s] explore_worker: OpenAI call failed for %s round %d: %s",
                        job_id, fn_name, _round, exc,
                    )
                    break

                msg = response.choices[0].message

                if not msg.tool_calls:
                    if _direct_target_tool_calls < _min_direct_probes:
                        _no_tool_call_rounds += 1
                        _fn_probe_log.append({
                            "phase": "no_tool_call_round",
                            "function": fn_name,
                            "round": _round,
                            "tool": None,
                            "args": {},
                            "result_excerpt": "assistant returned no tool calls before probe floor met",
                            "trace": None,
                            "classification": {"has_return": False},
                            "policy": None,
                        })
                        conversation.append({
                            "role": "user",
                            "content": (
                                f"You have not directly called '{fn_name}' enough times yet. "
                                f"Make at least {_min_direct_probes} direct call(s) to {fn_name} now "
                                "using concrete test values, then continue analysis."
                            ),
                        })
                        continue
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
                    if _cancel_requested():
                        break
                    _fn = tc.function  # type: ignore[union-attr]
                    tc_name = _fn.name
                    try:
                        tc_args = json.loads(_fn.arguments)
                    except json.JSONDecodeError:
                        tc_args = {}

                    tc_inv = inv_map.get(tc_name)
                    classification = {
                        "has_return": False,
                        "format_guess": "not_executed",
                        "confidence": 0.0,
                        "source": "none",
                    }
                    _policy_block: dict | None = None

                    if _is_write_fn and tc_name == fn_name:
                        _ok, _reason, _detail = _write_policy_precheck(
                            fn_name, tc_args, _vocab_snap, unlock_result,
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
                                _tc_traced = {"result_str": "", "trace": {"backend": "policy", "blocked": True}}
                                tool_result = (
                                    f"Policy blocked write probe: {_policy_block['stop_reason']}"
                                    f" ({_policy_block.get('detail') or 'no additional detail'})"
                                )
                            else:
                                _tc_traced = _execute_tool_traced(tc_inv, tc_args)
                                tool_result = _tc_traced["result_str"]

                            classification = _classify_result_text(str(tool_result))

                            if _is_write_fn and tc_name == fn_name and classification.get("has_return"):
                                _ret_signed = int(classification.get("signed", 0))
                                if _ret_signed != 0:
                                    _cls = _sentinel_class_from_classification(classification)
                                    _write_retry_counts[_cls] += 1
                                    _budget = _WRITE_RETRY_BUDGET_BY_CLASS.get(_cls, _WRITE_RETRY_BUDGET_BY_CLASS["unknown"])
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

                            _fn_probe_log.append({
                                "probe_id": f"{fn_name}:{_round}:{_fn_tool_call_count + 1}:{tc_name}",
                                "phase": "explore",
                                "function": fn_name,
                                "round": _round,
                                "reasoning": (msg.content or "").strip() or None,
                                "tool": tc_name,
                                "args": tc_args,
                                "result_excerpt": str(tool_result)[:200],
                                "trace": _tc_traced.get("trace"),
                                "classification": classification,
                                "policy": _policy_block,
                            })

                            if classification.get("has_return") and classification.get("signed", 0) != 0:
                                _hex = str(classification.get("hex"))
                                _arg_shape = sorted(list(tc_args.keys()))
                                with _lock:
                                    _cat = _sentinel_catalog.setdefault(_hex, {
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
                                        "evidence_count": 0,
                                        "evidence_refs": [],
                                        "functions": [],
                                        "arg_shapes": [],
                                        "phases": [],
                                        "provisional": True,
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
                                    _strong_det = _src_now.startswith("deterministic.") and _conf_now >= 0.95
                                    _stable = (_conf_now >= 0.85 and int(_cat["evidence_count"]) >= 2)
                                    _cat["provisional"] = not (_strong_det or _stable)
                        except Exception as exc:
                            tool_result = f"Tool error: {exc}"
                            _llm_result = tool_result
                    else:
                        tool_result = f"Tool '{tc_name}' not found."
                        _llm_result = tool_result

                    _fn_tool_call_count += 1

                    # Track whether enrich_invocable / record_finding was called this function
                    if tc_name == "enrich_invocable":
                        _enrich_called = True
                    elif tc_name == "record_finding":
                        _finding_recorded = True

                    # Ground-truth tracking: record direct observations of return=0
                    if tc_name == fn_name:
                        _direct_target_tool_calls += 1
                        _ret_m = _re.search(r"Returned:\s*(-?\d+)", tool_result or "")
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
                            _best_raw_result = tool_result  # capture for hypothesis generation

                    logger.info(
                        "[%s] explore_worker: tool=%s result=%s",
                        job_id, tc_name, str(tool_result)[:120],
                    )

                    conversation.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": _llm_result,
                    })

                    if _policy_stop_reason in {"policy_exhausted", "dependency_missing", "schema_missing"}:
                        break

                    # Break out of tool-call processing if we hit the per-function cap
                    if _fn_tool_call_count >= _max_tool_calls:
                        break

                if _policy_stop_reason in {"policy_exhausted", "dependency_missing", "schema_missing"}:
                    logger.info("[%s] explore_worker: %s stopped by write policy (%s)",
                                job_id, fn_name, _policy_stop_reason)
                    break

            def _run_deterministic_fallback(reason: str, max_attempts: int) -> None:
                nonlocal _fn_tool_call_count, _direct_target_tool_calls, _best_raw_result
                _is_floor_enforcement = reason.startswith("direct probe floor unmet")
                if ((not _deterministic_fallback_enabled) and (not _is_floor_enforcement)) or max_attempts <= 0:
                    return
                for _attempt in range(max_attempts):
                    if _fn_tool_call_count >= _max_tool_calls:
                        break
                    _fb_args = _build_fallback_probe_args(inv, _vocab_snap, attempt=_attempt)
                    _policy_block = None
                    if _is_write_fn:
                        _ok, _reason, _detail = _write_policy_precheck(
                            fn_name, _fb_args, _vocab_snap, unlock_result,
                        )
                        if not _ok:
                            _policy_block = {
                                "allowed": False,
                                "stop_reason": _reason,
                                "detail": _detail,
                            }
                    if _policy_block:
                        _fn_probe_log.append({
                            "phase": "deterministic_fallback",
                            "function": fn_name,
                            "round": _attempt,
                            "reasoning": reason,
                            "tool": fn_name,
                            "args": _fb_args,
                            "result_excerpt": f"Policy blocked fallback probe: {_policy_block['stop_reason']}",
                            "trace": {"backend": "policy", "blocked": True},
                            "classification": {"has_return": False},
                            "policy": _policy_block,
                        })
                        continue
                    _tc = _execute_tool_traced(inv_map[fn_name], _fb_args)
                    _res = _tc["result_str"]
                    _cls = _classify_result_text(str(_res))
                    _fn_probe_log.append({
                        "probe_id": f"{fn_name}:fb:{_attempt + 1}",
                        "phase": "deterministic_fallback",
                        "function": fn_name,
                        "round": _attempt,
                        "reasoning": reason,
                        "tool": fn_name,
                        "args": _fb_args,
                        "result_excerpt": str(_res)[:200],
                        "trace": _tc.get("trace"),
                        "classification": _cls,
                        "policy": None,
                    })
                    _fn_tool_call_count += 1
                    _direct_target_tool_calls += 1
                    _ret_m = _re.search(r"Returned:\s*(-?\d+)", _res or "")
                    if _ret_m and int(_ret_m.group(1)) == 0:
                        _observed_successes.append(_fb_args)
                        _best_raw_result = _res
                        break

            _missing_direct_calls = max(0, _min_direct_probes - _direct_target_tool_calls)
            if _missing_direct_calls > 0:
                _run_deterministic_fallback(
                    f"direct probe floor unmet ({_direct_target_tool_calls}/{_min_direct_probes})",
                    _missing_direct_calls,
                )

            if _is_write_fn and not _observed_successes:
                _run_deterministic_fallback("write function fallback coverage", 2)

            # ── Force record_finding if the LLM never called it ──────────────
            if not _finding_recorded and _observed_successes:
                # D-8: Deterministic fallback got return=0 but the LLM never
                # called record_finding.  Save a success finding so the
                # function doesn't fall through the cracks.
                try:
                    _save_finding(job_id, {
                        "function": fn_name,
                        "status": "success",
                        "finding": f"Deterministic fallback probe returned 0.",
                        "working_call": _observed_successes[0],
                        "notes": "Auto-recorded: deterministic fallback succeeded "
                                 "but LLM never called record_finding.",
                        "direct_target_tool_calls": _direct_target_tool_calls,
                        "no_tool_call_rounds": _no_tool_call_rounds,
                        "stop_reason": _policy_stop_reason,
                    })
                    _finding_recorded = True
                    logger.info("[%s] explore_worker: D-8 forced record_finding(success) for %s — working_call=%s",
                                job_id, fn_name, _observed_successes[0])
                except Exception as _frf:
                    logger.debug("[%s] D-8 forced record_finding failed for %s: %s", job_id, fn_name, _frf)
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
                    _save_finding(job_id, {
                        "function": fn_name,
                        "status": "error",
                        "finding": f"All {_fn_tool_call_count} probes returned sentinel codes: {_code_str}. "
                                   f"No working call found.{_policy_note}",
                        "working_call": None,
                        "notes": f"Auto-recorded: LLM exhausted {_fn_tool_call_count} tool calls "
                                 f"without calling record_finding.",
                        "direct_target_tool_calls": _direct_target_tool_calls,
                        "no_tool_call_rounds": _no_tool_call_rounds,
                        "stop_reason": _policy_stop_reason,
                        "write_policy_events": _policy_events,
                    })
                    _finding_recorded = True
                    logger.info("[%s] explore_worker: forced record_finding(error) for %s — codes: %s",
                                job_id, fn_name, _code_str)
                except Exception as _frf:
                    logger.debug("[%s] forced record_finding failed for %s: %s", job_id, fn_name, _frf)

            # Force enrich_invocable if the model skipped it and the function has params
            if not _enrich_called and inv.get("parameters"):
                _cur_findings = _load_findings(job_id)
                _last_f = next(
                    (f for f in reversed(_cur_findings) if f.get("function") == fn_name), None
                )
                _finding_summary = (
                    f"Finding: {_last_f.get('finding', '')}. Notes: {_last_f.get('notes', '')}"
                    if _last_f else "No finding recorded."
                )
                try:
                    from typing import cast, Any as _Any
                    _enrich_resp = client.chat.completions.create(
                        model=model,
                        messages=conversation + [{
                            "role": "user",
                            "content": (
                                f"You did not call enrich_invocable for '{fn_name}'. "
                                f"Based on what you observed, call it now. "
                                f"Rename each param_N to a semantic name (e.g. customer_id, balance, output_buffer). "
                                f"For each parameter description, write what it does AND include an example value from your testing "
                                f"(e.g. 'Input ID string, e.g. the value you used in testing' or 'Output pointer — receives a result value, observed 25000'). "
                                f"Set the function description to a clear one-sentence summary. "
                                f"{_finding_summary}"
                            ),
                        }],
                        tools=cast(_Any, tool_schemas),
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
                        _execute_tool(inv_map["enrich_invocable"], _eargs)
                        logger.info(
                            "[%s] explore_worker: forced enrich_invocable for %s",
                            job_id, fn_name,
                        )
                except Exception as _ee:
                    logger.debug("[%s] forced enrich failed for %s: %s", job_id, fn_name, _ee)

            with _lock:
                _state["explored"] += 1
                already_explored.add(fn_name)
            _set_explore_status(job_id, _state["explored"], total, f"Completed {fn_name}")
            if _observed_successes:
                # Ground-truth override: we observed return=0 directly → force success.
                # D-11: also rewrite the finding text so synthesis doesn't see
                # "All N probes returned sentinel codes" for a function that actually works.
                _gt_text = (
                    f"Returns 0 on success when called with {_observed_successes[0]}. "
                    f"Discovered via deterministic fallback (direct LLM probes: {_direct_target_tool_calls})."
                )
                try:
                    _patch_finding(job_id, fn_name, {
                        "working_call": _observed_successes[0],
                        "status": "success",
                        "stop_reason": "success",
                        "finding": _gt_text,
                    })
                    logger.info(
                        "[%s] explore_worker: ground-truth override for %s working_call=%s",
                        job_id, fn_name, _observed_successes[0],
                    )
                except Exception as _ce:
                    logger.debug("[%s] consistency patch failed for %s: %s", job_id, fn_name, _ce)
            else:
                # Verify the LLM's claimed working_call by re-running it
                _cur = _load_findings(job_id)
                _ff = next((f for f in reversed(_cur) if f.get("function") == fn_name), None)
                if _ff and _ff.get("working_call") is not None:
                    _vi = inv_map.get(fn_name)
                    if _vi:
                        try:
                            _vt = _execute_tool_traced(_vi, _ff["working_call"])
                            _vr = _vt["result_str"]
                            _vr_class = _classify_result_text(str(_vr))
                            _fn_probe_log.append({
                                "phase": "verify",
                                "function": fn_name,
                                "tool": fn_name,
                                "args": _ff["working_call"],
                                "result_excerpt": str(_vr)[:200],
                                "trace": _vt.get("trace"),
                                "classification": _vr_class,
                                "policy": None,
                            })
                            _vm = _re.search(r"Returned:\s*(-?\d+)", _vr or "")
                            if _vm:
                                _vret = int(_vm.group(1)) & 0xFFFFFFFF
                                if _vret not in sentinels and _vret <= 0xFFFFFFF0:
                                    _patch_finding(job_id, fn_name, {
                                        "status": "success",
                                        "stop_reason": "success",
                                    })
                                else:
                                    _patch_finding(job_id, fn_name, {
                                        "working_call": None,
                                        "status": "error",
                                        "stop_reason": _policy_stop_reason or "policy_exhausted",
                                    })
                                    logger.info(
                                        "[%s] explore_worker: discarded hallucinated working_call for %s",
                                        job_id, fn_name,
                                    )
                        except Exception as _ve:
                            logger.debug("[%s] working_call verify failed for %s: %s",
                                         job_id, fn_name, _ve)

            # Hypothesis-driven interpretation: ask LLM what ambiguous output values mean,
            # then optionally cross-validate against an already-explored function.
            if _best_raw_result:
                try:
                    _hyp = _generate_hypothesis(
                        client, model, fn_name, _best_raw_result, _vocab_snap, _load_findings(job_id)
                    )
                    if _hyp.get("interpretations"):
                        _patch_finding(job_id, fn_name, {"interpretation": _hyp["interpretations"]})
                        # Merge into local vocab snapshot so the update is persisted
                        if "value_semantics" not in _vocab_snap:
                            _vocab_snap["value_semantics"] = {}
                        _vocab_snap["value_semantics"].update(_hyp["interpretations"])
                        logger.info("[%s] hypothesis for %s: %s", job_id, fn_name, _hyp["interpretations"])
                    # Run cross-validation if proposed and the target has already been explored
                    _cv = _hyp.get("cross_validation")
                    if (
                        _cv and isinstance(_cv, dict)
                        and _cv.get("function") in inv_map
                        and _cv.get("function") in already_explored
                    ):
                        _cv_inv = inv_map[_cv["function"]]
                        _cvt = _execute_tool_traced(_cv_inv, _cv.get("args") or {})
                        _cv_result = _cvt["result_str"]
                        _cv_class = _classify_result_text(str(_cv_result))
                        _fn_probe_log.append({
                            "phase": "cross_validate",
                            "function": fn_name,
                            "tool": _cv["function"],
                            "args": _cv.get("args") or {},
                            "result_excerpt": str(_cv_result)[:200],
                            "trace": _cvt.get("trace"),
                            "classification": _cv_class,
                            "policy": None,
                        })
                        _patch_finding(
                            job_id, fn_name,
                            {"cross_validation": f"{_cv['function']}({_cv.get('args')}) → {_cv_result}"},
                        )
                        logger.info(
                            "[%s] cross-validated %s via %s: %s",
                            job_id, fn_name, _cv["function"], str(_cv_result)[:80],
                        )
                except Exception as _hyp_e:
                    logger.debug("[%s] hypothesis failed for %s: %s", job_id, fn_name, _hyp_e)

            # Update shared vocabulary from the latest finding for this function
            last_finding = _load_findings(job_id)
            if last_finding:
                last = next(
                    (f for f in reversed(last_finding) if f.get("function") == fn_name), None
                )
                if last:
                    try:
                        # Mutates _vocab_snap in-place; merge learned facts into shared vocab
                        _update_vocabulary(client, model, _vocab_snap, last)
                        with _lock:
                            for _mk, _mv in _vocab_snap.items():
                                if _mk.startswith("_"):
                                    continue
                                if isinstance(_mv, list) and isinstance(vocab.get(_mk), list):
                                    _existing = set(map(str, vocab[_mk]))
                                    vocab[_mk] = vocab[_mk] + [x for x in _mv if str(x) not in _existing]
                                elif isinstance(_mv, dict) and isinstance(vocab.get(_mk), dict):
                                    vocab[_mk].update(_mv)
                                elif _mv and _mk not in vocab:
                                    vocab[_mk] = _mv
                        # Persist updated vocab to blob so next session starts informed
                        try:
                            _upload_to_blob(
                                ARTIFACT_CONTAINER,
                                f"{job_id}/vocab.json",
                                json.dumps(_vocab_snap).encode(),
                            )
                        except Exception as _vpe:
                            logger.debug("[%s] vocab persist failed: %s", job_id, _vpe)
                    except Exception as _ve:
                        logger.debug("[%s] vocab update failed for %s: %s", job_id, fn_name, _ve)

            # Flush probe log for this function to explore_probe_log.json blob
            if _fn_probe_log:
                try:
                    _append_explore_probe_log(job_id, _fn_probe_log)
                except Exception as _ple:
                    logger.debug("[%s] explore probe log flush failed for %s: %s", job_id, fn_name, _ple)

        # Run exploration: parallel if EXPLORE_CONCURRENCY > 1, else sequential
        if _CONCURRENCY > 1:
            logger.info("[%s] explore_worker: running %d functions with concurrency=%d",
                        job_id, len(invocables), _CONCURRENCY)
            with _TPE(max_workers=_CONCURRENCY) as _pool:
                list(_pool.map(_explore_one, invocables))
        else:
            for inv in invocables:
                if _cancel_requested():
                    break
                _explore_one(inv)

        # ── AC-1: Post-loop probe-log reconciliation ─────────────────────
        # Scan the full probe log for functions marked error that actually had
        # a successful probe (return=0).  Override those to success so the
        # findings reflect ground-truth before synthesis sees them.
        try:
            _set_explore_status(job_id, explored, total, "Reconciling probe evidence…")
            _recon_findings = _load_findings(job_id)
            _recon_probe_log: list[dict] = []
            try:
                _recon_probe_raw = _download_blob(ARTIFACT_CONTAINER, f"{job_id}/explore_probe_log.json")
                _recon_probe_log = json.loads(_recon_probe_raw)
            except Exception:
                pass

            if _recon_probe_log:
                # Build map: function -> direct self-call probes where return was 0.
                # This prevents false upgrades from prerequisite calls such as CS_Initialize.
                _success_probes: dict[str, list[dict]] = defaultdict(list)
                for _pe in _recon_probe_log:
                    _clf = _pe.get("classification") or {}
                    _fn = _pe.get("function") or ""
                    _tool = _pe.get("tool") or ""
                    _phase = str(_pe.get("phase") or "")
                    if not (_fn and _tool and _fn == _tool):
                        continue
                    if _phase not in {"explore", "cross_validate"}:
                        continue
                    if _clf.get("has_return") and int(_clf.get("signed", -1)) == 0:
                        _success_probes[_fn].append(_pe)

                _recon_patched = 0
                for _f in _recon_findings:
                    _fn = _f.get("function", "")
                    if _f.get("status") == "error" and _fn in _success_probes:
                        _best_pe = max(
                            _success_probes[_fn],
                            key=lambda _p: len(_p.get("args") or {}),
                        )
                        _wc = _best_pe.get("args") or {}
                        _patch_finding(job_id, _fn, {
                            "status": "success",
                            "working_call": _wc,
                            "stop_reason": "reconciled_from_probe_log",
                            "notes": (
                                f"AC-1 reconciliation: probe "
                                f"{_best_pe.get('probe_id', '?')} returned 0 "
                                f"but finding was error. Overridden to success."
                            ),
                        })
                        _recon_patched += 1
                if _recon_patched:
                    logger.info(
                        "[%s] explore_worker: AC-1 reconciliation patched %d functions from error→success",
                        job_id, _recon_patched,
                    )
        except Exception as _recon_e:
            logger.debug("[%s] explore_worker: AC-1 reconciliation failed: %s", job_id, _recon_e)

        # Persist S1 sentinel evidence catalog for save-session diagnostics.
        try:
            _promoted = 0
            vocab.setdefault("error_codes", {})
            for _hex, _row in _sentinel_catalog.items():
                _conf = float(_row.get("confidence") or 0.0)
                _evi = int(_row.get("evidence_count") or 0)
                _src = str(_row.get("source") or "")
                _meaning = str(_row.get("meaning") or "unknown")
                _strong_det = _src.startswith("deterministic.") and _conf >= 0.95
                _stable = _conf >= 0.85 and _evi >= 2
                if (_strong_det or _stable) and _meaning and _meaning != "unknown":
                    vocab["error_codes"].setdefault(_hex, _meaning)
                    _row["provisional"] = False
                    _row["promotion_reason"] = "deterministic_single" if _strong_det else "repeated_evidence"
                    _promoted += 1

            _upload_to_blob(
                ARTIFACT_CONTAINER,
                f"{job_id}/sentinel_catalog.json",
                json.dumps({
                    "codes": _sentinel_catalog,
                    "promoted_count": _promoted,
                    "total_codes": len(_sentinel_catalog),
                }, indent=2).encode(),
            )
            _upload_to_blob(
                ARTIFACT_CONTAINER,
                f"{job_id}/vocab.json",
                json.dumps(vocab).encode(),
            )
            logger.info("[%s] explore_worker: sentinel_catalog persisted (%d codes, %d promoted)",
                        job_id, len(_sentinel_catalog), _promoted)
        except Exception as _sce:
            logger.debug("[%s] explore_worker: sentinel_catalog persist failed: %s", job_id, _sce)

        explored = _state["explored"]

        # Synthesis: generate API reference Markdown document from all findings
        try:
            _syn_findings = _load_findings(job_id)
            if _syn_findings:
                logger.info("[%s] explore_worker: synthesizing API reference (%d fns)…",
                            job_id, len(_syn_findings))
                _set_explore_status(job_id, explored, total, "Synthesizing API reference…")
                _report = _synthesize(client, model, _syn_findings, vocab=vocab, sentinels=sentinels)
                if _report:
                    _upload_to_blob(
                        ARTIFACT_CONTAINER,
                        f"{job_id}/api_reference.md",
                        _report.encode("utf-8"),
                    )
                    logger.info("[%s] explore_worker: api_reference.md saved to blob", job_id)

                    # Refresh invocables from the in-memory registry so that
                    # backfill and gap resolution see discovery enrichments
                    # instead of the stale snapshot taken at worker start.
                    _refreshed = _get_current_invocables(job_id)
                    if _refreshed:
                        invocables = _refreshed
                        inv_map = {iv["name"]: iv for iv in invocables}

                    # Layer 3: backfill schema descriptions from synthesis document.
                    # Uses the completed synthesis to enrich param descriptions with
                    # proven semantics (units, entity refs, example values).
                    try:
                        logger.info("[%s] explore_worker: layer3 schema backfill…", job_id)
                        _set_explore_status(job_id, explored, total, "Enriching schema from synthesis…")
                        _backfill_schema_from_synthesis(client, model, _report, invocables, job_id)
                    except Exception as _bf_e:
                        logger.debug("[%s] explore_worker: backfill failed: %s", job_id, _bf_e)

                    # Schema checkpoint after initial discovery/backfill but before
                    # gap-resolution retries begin.
                    _snapshot_schema_stage(job_id, "mcp_schema_post_discovery.json")
                    _snapshot_schema_stage(job_id, "mcp_schema_pre_gap_resolution.json")

                    if _cancel_requested():
                        logger.info("[%s] explore_worker: cancellation requested before gap/synthesis tail", job_id)
                    elif _gap_resolution_enabled:
                        # Gap resolution pass: attempt one more focused round on functions
                        # that failed every probe in the main loop, using known-good IDs
                        # and explicit retry strategies.  Resolved functions won't appear
                        # in the gap questions that reach the user.
                        try:
                            logger.info("[%s] explore_worker: gap resolution pass…", job_id)
                            _set_explore_status(job_id, explored, total, "Retrying failed functions…")
                            _attempt_gap_resolution(
                                job_id, invocables, client, model,
                                sentinels, vocab, _use_cases_text,
                                inv_map, tool_schemas,
                            )
                        except Exception as _gr_e:
                            logger.debug("[%s] explore_worker: gap resolution failed: %s", job_id, _gr_e)

                        _snapshot_schema_stage(job_id, "mcp_schema_post_gap_resolution.json")
                        _snapshot_schema_stage(job_id, "mcp_schema_pre_clarification.json")

                        # Self-assessment: generate confidence gap questions for the user.
                        # Runs AFTER gap resolution, so only genuinely unresolvable unknowns
                        # (undocumented error codes, business rules) surface to the user.
                        if _clarification_enabled:
                            try:
                                logger.info("[%s] explore_worker: generating confidence gaps…", job_id)
                                _set_explore_status(job_id, explored, total, "Generating clarification questions…")
                                _gaps = _generate_confidence_gaps(client, model, _syn_findings, invocables)
                                if _gaps:
                                    logger.info("[%s] explore_worker: %d confidence gaps generated", job_id, len(_gaps))
                                # Always persist (even empty list) so UI knows the pass ran
                                _gap_current = _get_job_status(job_id) or {}
                                _persist_job_status(
                                    job_id,
                                    {**_gap_current, "explore_questions": _gaps},
                                    sync=True,
                                )
                            except Exception as _gap_e:
                                logger.debug("[%s] explore_worker: confidence gaps failed: %s", job_id, _gap_e)
                        else:
                            _gap_current = _get_job_status(job_id) or {}
                            _persist_job_status(
                                job_id,
                                {**_gap_current, "explore_questions": []},
                                sync=True,
                            )

                        _snapshot_schema_stage(job_id, "mcp_schema_post_clarification.json")
                    else:
                        logger.info("[%s] explore_worker: gap resolution disabled for this run", job_id)
                        _gap_current = _get_job_status(job_id) or {}
                        _persist_job_status(
                            job_id,
                            {**_gap_current, "explore_questions": []},
                            sync=True,
                        )

                    # Behavioral spec: typed Python stub file capturing the API contract.
                    if not _cancel_requested():
                        try:
                            logger.info("[%s] explore_worker: generating behavioral spec…", job_id)
                            _set_explore_status(job_id, explored, total, "Generating behavioral specification…")
                            _component = (_get_job_status(job_id) or {}).get("component_name", "DLLComponent")
                            _spec_py = _generate_behavioral_spec(
                                client, model, _syn_findings, invocables, _component, _report
                            )
                            if _spec_py:
                                _upload_to_blob(
                                    ARTIFACT_CONTAINER,
                                    f"{job_id}/behavioral_spec.py",
                                    _spec_py.encode("utf-8"),
                                )
                                logger.info("[%s] explore_worker: behavioral_spec.py saved to blob", job_id)
                        except Exception as _spec_e:
                            logger.debug("[%s] explore_worker: behavioral spec failed: %s", job_id, _spec_e)
        except Exception as _syn_e:
            logger.debug("[%s] explore_worker: synthesis failed: %s", job_id, _syn_e)

        # Synthesize a one-sentence domain description from accumulated vocab.
        # Stored as vocab["description"] so the chat phase can prepend it to the
        # system message as domain framing before the mechanical rules.
        # Only generated once — skipped if a previous session already built it.
        if vocab and "description" not in vocab:
            try:
                _desc_seed = vocab.get("user_context") or vocab.get("notes") or ""
                _vs = vocab.get("value_semantics") or {}
                _vs_sample = "; ".join(f"{k}: {v}" for k, v in list(_vs.items())[:8])
                _ids = ", ".join(vocab.get("id_formats") or [])
                _desc_prompt = (
                    "Based on the following accumulated knowledge about a DLL, write ONE sentence "
                    "describing what this component does for a developer integrating it. "
                    "Be specific — name the business domain, key entities, and operations. "
                    "Do not mention 'DLL' or 'function'. Max 25 words.\n\n"
                    + (f"User context: {_desc_seed}\n" if _desc_seed else "")
                    + (f"Known ID formats: {_ids}\n" if _ids else "")
                    + (f"Value semantics: {_vs_sample}\n" if _vs_sample else "")
                )
                _desc_resp = client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": _desc_prompt}],
                    temperature=0,
                    max_tokens=60,
                    timeout=90.0,
                )
                _desc_text = (_desc_resp.choices[0].message.content or "").strip().strip('"')
                if _desc_text:
                    vocab["description"] = _desc_text
                    logger.info("[%s] explore_worker: synthesized description: %s", job_id, _desc_text)
            except Exception as _desc_e:
                logger.debug("[%s] explore_worker: description synthesis failed: %s", job_id, _desc_e)

        # Persist final vocab with description + user_context
        if vocab:
            try:
                _upload_to_blob(
                    ARTIFACT_CONTAINER,
                    f"{job_id}/vocab.json",
                    json.dumps(vocab).encode(),
                )
            except Exception as _vfin_e:
                logger.debug("[%s] explore_worker: final vocab persist failed: %s", job_id, _vfin_e)

        # Final deterministic harmonization pass (non-LLM): enforce a single
        # coherent state from probe evidence + findings + clarification status.
        try:
            _set_explore_status(job_id, explored, total, "Finalizing harmonized state…")
            _hm_findings = _load_findings(job_id)
            _hm_probe_log: list[dict] = []
            try:
                _hm_raw = _download_blob(ARTIFACT_CONTAINER, f"{job_id}/explore_probe_log.json")
                _hm_probe_log = json.loads(_hm_raw)
            except Exception:
                pass

            _direct_successes: dict[str, list[dict]] = defaultdict(list)
            for _pe in _hm_probe_log:
                _fn = _pe.get("function") or ""
                _tool = _pe.get("tool") or ""
                _phase = str(_pe.get("phase") or "")
                _clf = _pe.get("classification") or {}
                if not (_fn and _tool and _fn == _tool):
                    continue
                if _phase not in {"explore", "cross_validate"}:
                    continue
                if _clf.get("has_return") and int(_clf.get("signed", -1)) == 0:
                    _direct_successes[_fn].append(_pe)

            _upgrades: list[dict] = []
            for _f in _hm_findings:
                _fn = _f.get("function") or ""
                if _f.get("status") == "error" and _fn in _direct_successes:
                    _best = max(
                        _direct_successes[_fn],
                        key=lambda _p: len(_p.get("args") or {}),
                    )
                    _patch_finding(
                        job_id,
                        _fn,
                        {
                            "status": "success",
                            "working_call": _best.get("args") or {},
                            "stop_reason": "harmonized_direct_probe_success",
                            "notes": (
                                f"Final harmonization: direct probe {_best.get('probe_id', '?')} "
                                "returned 0; status forced to success."
                            ),
                        },
                    )
                    _upgrades.append({
                        "function": _fn,
                        "probe_id": _best.get("probe_id"),
                        "args": _best.get("args") or {},
                    })

            _post_hm_findings = _load_findings(job_id)
            _status_counts = {
                "success": sum(1 for _f in _post_hm_findings if _f.get("status") == "success"),
                "error": sum(1 for _f in _post_hm_findings if _f.get("status") == "error"),
                "other": sum(1 for _f in _post_hm_findings if _f.get("status") not in {"success", "error"}),
            }

            _cur = _get_job_status(job_id) or {}
            _open_questions = _cur.get("explore_questions") or []
            _unanswered_questions = []
            for _q in _open_questions:
                if not isinstance(_q, dict):
                    continue
                _answered = bool(_q.get("answered")) or bool(str(_q.get("answer") or "").strip())
                if not _answered:
                    _unanswered_questions.append(_q)

            _harmonization = {
                "job_id": job_id,
                "final_phase_suggestion": "awaiting_clarification" if _unanswered_questions else "done",
                "patched_error_to_success": _upgrades,
                "counts": _status_counts,
                "open_questions": len(_open_questions),
                "unanswered_questions": len(_unanswered_questions),
            }
            _upload_to_blob(
                ARTIFACT_CONTAINER,
                f"{job_id}/harmonization_report.json",
                json.dumps(_harmonization, indent=2).encode(),
            )
            logger.info(
                "[%s] explore_worker: harmonization complete (%d upgrades, %d unanswered)",
                job_id, len(_upgrades), len(_unanswered_questions),
            )
        except Exception as _hm_e:
            logger.debug("[%s] explore_worker: harmonization failed: %s", job_id, _hm_e)

        # ── AC-4: Clarification closure gate ────────────────────────────
        # If there are unanswered clarification questions, mark the phase as
        # "awaiting_clarification" instead of "done".  External consumers can
        # then distinguish "complete" from "complete with open questions."
        current = _get_job_status(job_id) or {}
        _open_questions = current.get("explore_questions") or []
        _has_unanswered = any(
            isinstance(q, dict)
            and not (bool(q.get("answered")) or bool(str(q.get("answer") or "").strip()))
            for q in _open_questions
        )
        _elapsed_s = max(0.0, time.time() - _run_started_at)
        _was_canceled = _cancel_requested()
        if _was_canceled:
            _final_phase = "canceled"
        else:
            _final_phase = "awaiting_clarification" if _has_unanswered else "done"
        _persist_job_status(
            job_id,
            {
                **current,
                "explore_phase": _final_phase,
                "explore_progress": f"{explored}/{total}",
                "explore_last_run_seconds": round(_elapsed_s, 2),
                "updated_at": time.time(),
            },
            sync=True,
        )
        logger.info("[%s] explore_worker: finished %d/%d functions (phase=%s)", job_id, explored, total, _final_phase)

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



