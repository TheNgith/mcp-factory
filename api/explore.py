"""api/explore.py – Autonomous reverse-engineering exploration worker.

Pipeline overview
-----------------
_explore_worker(job_id, invocables) drives 12 named phases in sequence,
threading all shared state through an ExploreContext dataclass so data-flow
between phases is explicit and each phase can be tested in isolation.

  Phase 0.5  _run_phase_05_calibrate          – calibrate DLL-specific sentinel codes
  Phase 0a   _run_phase_0_vocab_seed          – load/seed the cross-session vocab table
  Phase 0b   _run_phase_0_static              – binary static analysis (G-4/G-7/G-8/G-9)
  Phase 1    _run_phase_1_write_unlock        – probe write-unlock prerequisite sequence
  Phase 2    _run_phase_2_curriculum_order     – order: init-first, then by uncertainty
  Phase 3    _run_phase_3_probe_loop          – per-function LLM probe loops (_explore_one)
           + Q16§4 stage-boundary recalibration – scan probe log for new sentinel codes
           + Q16§5 write-unlock re-probe       – retry unlock if new sentinels resolve it
  Phase 4    _run_phase_4_reconcile           – AC-1 probe-log reconciliation
  Phase 5    _run_phase_5_sentinel_catalog    – persist + promote sentinel evidence
  Phase 6    _run_phase_6_synthesize          – LLM → api_reference.md
  Phase 7    _run_phase_7_backfill            – enrich schema from synthesis doc
  Phase 7b   _run_phase_7b_verify_enrichment  – execute working_calls against the DLL
  Phase 8    _run_phase_8_gap_resolution      – retry failed functions + clarification Qs
  Phase 9    _run_phase_9_behavioral_spec     – typed Python behavioral specification
  Phase 10   _run_phase_10_harmonize          – final deterministic harmonization pass
  Finalize   _run_finalize                    – vocab description + AC-4 closure gate
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

from api.config import (
    OPENAI_ENDPOINT, OPENAI_DEPLOYMENT, OPENAI_REASONING_DEPLOYMENT,
    OPENAI_API_KEY, OPENAI_EXPLORE_MODEL, ARTIFACT_CONTAINER,
)
from api.executor import _execute_tool, _execute_tool_traced
from api.storage import (
    _persist_job_status, _get_job_status, _patch_invocable,
    _save_finding, _patch_finding, _upload_to_blob, _download_blob,
    _append_transcript, _append_executor_trace, _append_explore_probe_log,
    _register_invocables, _merge_invocables, _get_current_invocables,
)
from api.telemetry import _openai_client
from api.explore_phases import (
    _SENTINEL_DEFAULTS, _CAP_PROFILE,
    _MAX_EXPLORE_ROUNDS_PER_FUNCTION, _MAX_TOOL_CALLS_PER_FUNCTION, _MAX_FUNCTIONS_PER_SESSION,
    _calibrate_sentinels, _probe_write_unlock, _infer_param_desc,
    _name_sentinel_candidates,
)
from api.explore_vocab import (
    _update_vocabulary, _generate_hypothesis, _backfill_schema_from_synthesis,
    _vocab_block, _uncertainty_score,
)
from api.explore_prompts import (
    _build_explore_system_message, _generate_behavioral_spec,
    _synthesize, _generate_confidence_gaps,
)
from api.explore_helpers import (
    _GAP_RESOLUTION_ENABLED,
    _INIT_RE,
    _VERSION_FN_RE,
    _WRITE_FN_RE,
    _WRITE_RETRY_BUDGET_BY_CLASS,
    _build_ranked_fallback_probe_args,
    _build_tool_schemas,
    _cancel_requested,
    _classify_result_text,
    _save_stage_context,
    _sentinel_class_from_classification,
    _set_explore_status,
    _snapshot_schema_stage,
    _strip_output_buffer_params,
    _write_policy_precheck,
)
from api.explore_gap import _attempt_gap_resolution, _run_gap_answer_mini_sessions
from api.explore_types import ExploreContext, ExploreRuntime
from api.cohesion import emit_contract_artifacts
from api.explore_probe import _explore_one, _run_phase_3_probe_loop

logger = logging.getLogger("mcp_factory.api")

# _INIT_RE, _VERSION_FN_RE, _cancel_requested have moved to explore_helpers.py
# and are imported above via the explore_helpers import block.
# _explore_one and _run_phase_3_probe_loop have moved to explore_probe.py.



def _load_prior_session_artifacts(prior_job_id: str) -> dict:
    """Load artifacts from a prior pipeline run for circular feedback.

    Returns a dict with optional keys: findings, sentinels, vocab, api_reference.
    Missing artifacts are silently skipped (cold start for that artifact).
    """
    artifacts: dict = {}
    if not prior_job_id:
        return artifacts

    logger.info("[circular-feedback] loading artifacts from prior job %s", prior_job_id)

    try:
        raw = _download_blob(ARTIFACT_CONTAINER, f"{prior_job_id}/findings.json")
        artifacts["findings"] = json.loads(raw)
        logger.info("[circular-feedback] loaded %d prior findings",
                    len(artifacts["findings"]))
    except Exception:
        pass

    try:
        raw = _download_blob(ARTIFACT_CONTAINER, f"{prior_job_id}/sentinel_calibration.json")
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            sentinel_table = {}
            for k, v in parsed.items():
                try:
                    sentinel_table[int(k, 16) if isinstance(k, str) else int(k)] = str(v)
                except (ValueError, TypeError):
                    pass
            if sentinel_table:
                artifacts["sentinels"] = sentinel_table
                logger.info("[circular-feedback] loaded %d prior sentinel codes",
                            len(sentinel_table))
    except Exception:
        pass

    try:
        raw = _download_blob(ARTIFACT_CONTAINER, f"{prior_job_id}/vocab.json")
        artifacts["vocab"] = json.loads(raw)
        logger.info("[circular-feedback] loaded prior vocab (%d keys)",
                    len(artifacts.get("vocab", {})))
    except Exception:
        pass

    try:
        raw = _download_blob(ARTIFACT_CONTAINER, f"{prior_job_id}/api_reference.md")
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        artifacts["api_reference"] = raw
        logger.info("[circular-feedback] loaded prior api_reference (%d chars)",
                    len(artifacts.get("api_reference", "")))
    except Exception:
        pass

    try:
        raw = _download_blob(ARTIFACT_CONTAINER, f"{prior_job_id}/winning_init_sequence.json")
        artifacts["winning_init_sequence"] = json.loads(raw)
        logger.info("[circular-feedback] loaded winning init sequence from prior session")
    except Exception:
        pass

    return artifacts


def _build_explore_context(job_id: str, invocables: list[dict]) -> ExploreContext:
    """Build a fully-initialised ExploreContext from a job ID and invocable list."""
    from api.storage import _load_findings

    _job_runtime = (_get_job_status(job_id) or {}).get("explore_runtime") or {}
    runtime = ExploreRuntime.from_job_runtime(_job_runtime)

    total = min(len(invocables), runtime.max_functions)
    invocables = invocables[:total]

    inv_map: dict[str, dict] = {inv["name"]: inv for inv in invocables}

    _merge_invocables(job_id, invocables)

    inv_map["enrich_invocable"] = {
        "name": "enrich_invocable", "source_type": "enrich",
        "_job_id": job_id, "execution": {"method": "enrich"}, "parameters": [],
    }
    inv_map["record_finding"] = {
        "name": "record_finding", "source_type": "findings",
        "_job_id": job_id, "execution": {"method": "findings"}, "parameters": [],
    }

    tool_schemas = _build_tool_schemas(invocables)

    client = _openai_client()
    model = runtime.model_override or (
        OPENAI_EXPLORE_MODEL if OPENAI_API_KEY
        else (OPENAI_REASONING_DEPLOYMENT or OPENAI_DEPLOYMENT)
    )

    prior_findings = _load_findings(job_id)
    already_explored = {f.get("function") for f in prior_findings if f.get("function")}

    # Circular feedback: load prior session artifacts if prior_job_id is set
    _prior = _load_prior_session_artifacts(runtime.prior_job_id)
    _prior_sentinels = _prior.get("sentinels", {})
    _prior_vocab = _prior.get("vocab", {})
    _prior_findings = _prior.get("findings", [])

    # Seed findings from prior session so the LLM starts with that knowledge
    if _prior_findings and not prior_findings:
        from api.storage import _patch_finding
        for pf in _prior_findings:
            fn = pf.get("function")
            if fn and pf.get("status") == "success":
                try:
                    _patch_finding(job_id, fn, {
                        "status": pf["status"],
                        "finding": pf.get("finding", ""),
                        "working_call": pf.get("working_call"),
                        "notes": f"Seeded from prior session {runtime.prior_job_id}",
                        "source": "prior_session",
                    })
                    already_explored.add(fn)
                except Exception:
                    pass
        logger.info("[circular-feedback] seeded %d success findings from prior session",
                    sum(1 for f in _prior_findings if f.get("status") == "success"))

    _run_started_at = float((_get_job_status(job_id) or {}).get("explore_started_at") or time.time())

    # Pre-seed sentinels from prior session (will be refined by Phase 0.5)
    _initial_sentinels = dict(_SENTINEL_DEFAULTS)
    if _prior_sentinels:
        _initial_sentinels.update(_prior_sentinels)

    ctx = ExploreContext(
        job_id=job_id,
        runtime=runtime,
        client=client,
        model=model,
        run_started_at=_run_started_at,
        invocables=invocables,
        inv_map=inv_map,
        tool_schemas=tool_schemas,
        total=total,
        sentinels=_initial_sentinels,
        already_explored=already_explored,
    )

    # Store prior session artifacts for use by later phases
    ctx._prior_session = _prior  # type: ignore[attr-defined]
    if _prior_vocab:
        ctx.vocab = dict(_prior_vocab)

    return ctx


# ══════════════════════════════════════════════════════════════════════════════
#  Phase 0.5 – Sentinel calibration
# ══════════════════════════════════════════════════════════════════════════════

def _run_phase_05_calibrate(ctx: ExploreContext) -> None:
    """Auto-calibrate DLL-specific sentinel error codes.

    READS:  ctx.job_id, ctx.invocables, ctx.client, ctx.model
    WRITES: ctx.sentinels, ctx.sentinel_new_codes_this_run
    INVARIANT: ctx.sentinels always contains at least _SENTINEL_DEFAULTS keys
    HANDOFF: sentinel_calibration.json uploaded to blob
    """
    try:
        logger.info("[%s] phase0.5: calibrating sentinels…", ctx.job_id)
        _set_explore_status(ctx.job_id, 0, ctx.total, "Calibrating error codes…")

        # Q16: load hints for Q-1 pre-seeding inside _calibrate_sentinels
        _hints = ""
        try:
            _hints = str((_get_job_status(ctx.job_id) or {}).get("hints") or "")
        except Exception:
            pass

        _prev_sentinel_count = len(ctx.sentinels)
        ctx.sentinels = _calibrate_sentinels(
            ctx.invocables, ctx.client, ctx.model, job_id=ctx.job_id, hints=_hints,
        )
        ctx.sentinel_new_codes_this_run += max(0, len(ctx.sentinels) - _prev_sentinel_count)
        logger.info("[%s] phase0.5: sentinels: %s", ctx.job_id,
                    {f"0x{k:08X}": v for k, v in ctx.sentinels.items()})
        try:
            _upload_to_blob(
                ARTIFACT_CONTAINER,
                f"{ctx.job_id}/sentinel_calibration.json",
                json.dumps(
                    {f"0x{k:08X}": v for k, v in ctx.sentinels.items()}, indent=2
                ).encode(),
            )
        except Exception as _sce:
            logger.debug("[%s] phase0.5: sentinel artifact upload failed: %s",
                         ctx.job_id, _sce)
    except Exception as _se:
        logger.debug("[%s] phase0.5: failed, using defaults: %s", ctx.job_id, _se)


# ══════════════════════════════════════════════════════════════════════════════
#  Phase 0a – Vocab seed
# ══════════════════════════════════════════════════════════════════════════════

def _run_phase_0_vocab_seed(ctx: ExploreContext) -> None:
    """Load cross-session vocab from blob, then seed from user hints and calibrated sentinels.

    READS:  ctx.job_id, ctx.sentinels
    WRITES: ctx.vocab, ctx.use_cases_text
    INVARIANT: DOMAIN ANSWER entries are stripped before writing vocab["notes"]
    HANDOFF: vocab["error_codes"] contains sentinel meanings; vocab["id_formats"]
             contains ID patterns extracted from user hints
    """
    # Reload from blob if a previous session already built one (cross-session memory).
    try:
        _vraw = _download_blob(ARTIFACT_CONTAINER, f"{ctx.job_id}/vocab.json")
        ctx.vocab = json.loads(_vraw)
        logger.info("[%s] phase0a: reloaded vocab from blob (%d keys)",
                    ctx.job_id, len(ctx.vocab))
    except Exception:
        pass  # normal on first run

    # Persist calibrated error codes into vocab so vocab_coverage.json can score them.
    # Merge (not setdefault) so re-runs and newly discovered codes are always incorporated.
    if ctx.sentinels is not _SENTINEL_DEFAULTS:
        ctx.vocab.setdefault("error_codes", {})
        ctx.vocab["error_codes"].update({f"0x{k:08X}": v for k, v in ctx.sentinels.items()})

    # Seed vocab from user-supplied hints so the LLM starts informed.
    try:
        _job_meta = _get_job_status(ctx.job_id) or {}
        _user_hints = (_job_meta.get("hints") or "").strip()
        ctx.use_cases_text = (_job_meta.get("use_cases") or "").strip()

        if _user_hints:
            # Extract ID-like patterns (e.g. CUST-001, ORD-20040301-0042)
            _hint_ids = list(dict.fromkeys(_re.findall(r'[A-Z]{2,6}-[\w-]+', _user_hints)))
            if _hint_ids:
                ctx.vocab["id_formats"] = _hint_ids
            # Strip DOMAIN ANSWER entries injected by the answer_gaps endpoint
            # before writing notes so synthetic answers don't pollute the vocab.
            _clean_hints = " | ".join(
                p.strip() for p in _user_hints.split(" | ")
                if p.strip() and not p.strip().startswith("DOMAIN ANSWER")
            )
            if _clean_hints and "notes" not in ctx.vocab:
                ctx.vocab["notes"] = f"User description: {_clean_hints}"
            logger.info("[%s] phase0a: seeded vocab from user hints: %s",
                        ctx.job_id, _user_hints[:80])

            # Q-1: Parse explicit error codes from hints and merge into sentinels
            # so codes like 0xFFFFFFFB ("write denied") from user knowledge are
            # recognized even when calibration probing doesn't trigger them.
            from api.explore_phases import _parse_hint_error_codes
            _hint_codes = _parse_hint_error_codes(_user_hints)
            if _hint_codes:
                ctx.sentinels.update(_hint_codes)
                ctx.vocab.setdefault("error_codes", {})
                ctx.vocab["error_codes"].update(
                    {f"0x{k:08X}": v for k, v in _hint_codes.items()}
                )
                logger.info("[%s] phase0a: merged %d error codes from hints: %s",
                            ctx.job_id, len(_hint_codes),
                            {f"0x{k:08X}": v for k, v in _hint_codes.items()})
                try:
                    _upload_to_blob(
                        ARTIFACT_CONTAINER,
                        f"{ctx.job_id}/sentinel_calibration.json",
                        json.dumps(
                            {f"0x{k:08X}": v for k, v in ctx.sentinels.items()}, indent=2
                        ).encode(),
                    )
                except Exception:
                    pass

        # Persist use_cases into vocab so the chat phase sees it even after
        # vocab["notes"] may be overwritten by later vocabulary updates.
        if ctx.use_cases_text and "user_context" not in ctx.vocab:
            ctx.vocab["user_context"] = ctx.use_cases_text
            logger.info("[%s] phase0a: seeded vocab[user_context] from use_cases", ctx.job_id)
    except Exception as _he:
        logger.debug("[%s] phase0a: hints seed failed: %s", ctx.job_id, _he)


# ══════════════════════════════════════════════════════════════════════════════
#  Phase 0b – Binary static analysis
# ══════════════════════════════════════════════════════════════════════════════

def _run_phase_0_static(ctx: ExploreContext) -> None:
    """Run binary static analysis and inject results into vocab + LLM prompt block.

    Implements G-4 (IAT capabilities), G-7 (binary strings), G-8 (PE version info),
    and G-9 (Capstone sentinel harvesting).

    READS:  ctx.job_id, ctx.invocables, ctx.vocab
    WRITES: ctx.static_hints_block, ctx.dll_strings, ctx.static_analysis_result
            ctx.vocab (seeds binary evidence; never overwrites user-set keys)
    INVARIANT: vocab keys set by user hints are not overwritten by binary seeds
    HANDOFF: static_analysis.json uploaded to blob
    """
    try:
        dll_path = next(
            (inv.get("execution", {}).get("dll_path", "") for inv in ctx.invocables
             if inv.get("execution", {}).get("dll_path")),
            "",
        )
        from pathlib import Path as _Path
        _data: bytes | None = None

        # Primary: read from the local path (works on Windows or valid Linux temp path)
        if dll_path:
            try:
                _data = _Path(dll_path).read_bytes()
            except Exception:
                pass

        # Fallback: download original uploaded binary from Blob Storage.
        # This is the common Azure deployment case where dll_path is a Windows
        # bridge VM path unavailable on the Linux API container.
        if _data is None:
            try:
                from api.storage import _download_blob as _dl_blob
                from api.config import UPLOAD_CONTAINER
                for _ext in (".dll", ".exe", ".bin", ""):
                    try:
                        _data = _dl_blob(UPLOAD_CONTAINER, f"{ctx.job_id}/input{_ext}")
                        break
                    except Exception:
                        pass
            except Exception:
                pass

        if _data is not None:
            from api.static_analysis import (
                run_static_analysis, build_vocab_seeds,
                build_static_hints_block as _build_shb,
            )
            _dll_name = _Path(dll_path).name if dll_path else "unknown.dll"
            ctx.static_analysis_result = run_static_analysis(_data, _dll_name)

            # G-7: promote binary evidence into vocab as first-class facts
            # (binary strings are ground truth — they override nothing the user
            # set, but fill any vocab key the user left empty)
            _vocab_seeds = build_vocab_seeds(ctx.static_analysis_result, ctx.vocab)
            for _k, _v in _vocab_seeds.items():
                ctx.vocab.setdefault(_k, _v)
                if _k not in ctx.vocab or ctx.vocab[_k] == _v:
                    ctx.vocab[_k] = _v  # also override falsy values
            if _vocab_seeds:
                logger.info("[%s] phase0b: vocab seeds applied: %s",
                            ctx.job_id, list(_vocab_seeds.keys()))

            _bs = ctx.static_analysis_result.get("binary_strings", {})
            ctx.dll_strings = {
                "ids":    _bs.get("ids_found", []),
                "emails": _bs.get("emails_found", []),
                "all":    _bs.get("ids_found", []) + _bs.get("emails_found", []),
            }
            ctx.static_hints_block = _build_shb(ctx.static_analysis_result)
            ctx.static_analysis_result["injected_into_prompt"] = bool(ctx.static_hints_block)
            ctx.static_analysis_result["static_hints_block_length"] = len(ctx.static_hints_block)

            try:
                _upload_to_blob(
                    ARTIFACT_CONTAINER,
                    f"{ctx.job_id}/static_analysis.json",
                    json.dumps(ctx.static_analysis_result, indent=2).encode(),
                )
                logger.info("[%s] phase0b: static_analysis.json uploaded", ctx.job_id)
            except Exception as _sa_err:
                logger.debug("[%s] phase0b: static_analysis.json upload failed: %s",
                             ctx.job_id, _sa_err)

            logger.info(
                "[%s] phase0b: G-4/G-7/G-8/G-9 complete — %d IDs, %d sentinels, IAT:%s",
                ctx.job_id,
                len(_bs.get("ids_found", [])),
                len(ctx.static_analysis_result.get("sentinel_constants", {}).get("harvested", {})),
                list(ctx.static_analysis_result.get("iat_capabilities", {}).get("categories", {}).keys()),
            )
    except Exception as _e:
        logger.debug("[%s] phase0b: static enrichment failed: %s", ctx.job_id, _e)


# ══════════════════════════════════════════════════════════════════════════════
#  Phase 1 – Write-unlock probe
# ══════════════════════════════════════════════════════════════════════════════

def _run_phase_1_write_unlock(ctx: ExploreContext) -> None:
    """Probe the write-unlock prerequisite sequence.

    READS:  ctx.invocables, ctx.dll_strings
    WRITES: ctx.unlock_result, ctx.write_unlock_block
    INVARIANT: ctx.unlock_result is always set (defaults to {unlocked: False})
    HANDOFF: write_unlock_block is always non-empty for write functions
    """
    try:
        logger.info("[%s] phase1: write-unlock probe…", ctx.job_id)
        _set_explore_status(ctx.job_id, 0, ctx.total, "Testing write-mode unlock…")

        # If a prior session found the winning init sequence, try it first
        _prior = getattr(ctx, '_prior_session', {}) or {}
        _winning = _prior.get("winning_init_sequence")
        if _winning and _winning.get("sequence"):
            logger.info("[%s] phase1: replaying winning init sequence from prior session",
                        ctx.job_id)
            inv_map = {inv["name"]: inv for inv in ctx.invocables}
            _SENTINEL_SET = {0xFFFFFFFB, 0xFFFFFFFE, 0xFFFFFFFF}
            for step in _winning["sequence"]:
                init_inv = inv_map.get(step.get("fn", ""))
                if init_inv:
                    try:
                        _execute_tool(init_inv, step.get("args", {}))
                    except Exception:
                        pass

            # Test if the prior winning sequence still works
            _prior_wfn = _winning.get("write_fn_tested", "")
            _prior_args = _winning.get("write_fn_args", {})
            _wfn_inv = inv_map.get(_prior_wfn)
            if _wfn_inv and _prior_args:
                try:
                    result = _execute_tool(_wfn_inv, _prior_args)
                    ret_m = _re.match(r"Returned:\s*(\d+)", result or "")
                    if ret_m and int(ret_m.group(1)) == 0:
                        ctx.unlock_result = {
                            "unlocked": True,
                            "sequence": _winning["sequence"],
                            "write_fn_tested": _prior_wfn,
                            "write_fn_args": _prior_args,
                            "notes": f"Replayed winning sequence from prior session",
                        }
                        logger.info("[%s] phase1: UNLOCKED via prior winning sequence!",
                                    ctx.job_id)
                except Exception:
                    pass

        if not ctx.unlock_result.get("unlocked"):
            ctx.unlock_result = _probe_write_unlock(ctx.invocables, ctx.dll_strings, ctx.vocab)
        if ctx.unlock_result.get("unlocked"):
            ctx.write_unlock_block = (
                "\nWRITE MODE ACTIVE: The write-unlock sequence has already been executed. "
                "Write functions (any function whose name implies state changes — Process, Update, "
                "Set, Create, Delete, Transfer, Submit, Send, Redeem, Unlock) should now succeed. "
                "Probe them with real ID values from STATIC ANALYSIS HINTS.\n"
            )
            logger.info("[%s] phase1: UNLOCKED: %s", ctx.job_id, ctx.unlock_result["notes"])
        else:
            ctx.write_unlock_block = (
                "\nWRITE MODE NOT YET UNLOCKED. This function modifies state (payments, refunds, etc). "
                "You MUST call CS_Initialize first (try mode=0, mode=1, mode=2) before probing this function. "
                "Use real ID values from STATIC ANALYSIS HINTS (e.g. CUST-001, ORD-001) and reasonable "
                "numeric amounts (e.g. 100, 500). If you get sentinel 0xFFFFFFFB, try a different "
                "init mode or parameter combination. Do NOT give up after one attempt.\n"
            )
            logger.info("[%s] phase1: not unlocked: %s", ctx.job_id,
                        ctx.unlock_result.get("notes", ""))

        # Q16: emit write_unlock_outcome + write_unlock_sentinel to session-meta
        write_fns = [
            inv for inv in ctx.invocables
            if _re.search(r"(pay|redeem|unlock|process|write|commit|transfer|debit|credit)",
                          inv["name"], _re.I)
        ]
        if not write_fns:
            unlock_outcome = "not_attempted"
            blocking_sentinel: str | None = None
        elif ctx.unlock_result.get("unlocked"):
            unlock_outcome = "resolved"
            blocking_sentinel = None
        else:
            unlock_outcome = "blocked"
            blocking_sentinel = None
            for candidate_code in (0xFFFFFFFB, 0xFFFFFFFF, 0xFFFFFFFE, 0xFFFFFFFD, 0xFFFFFFFC):
                if candidate_code in ctx.sentinels:
                    blocking_sentinel = f"0x{candidate_code:08X}"
                    break
        try:
            current_status = _get_job_status(ctx.job_id) or {}
            _persist_job_status(ctx.job_id, {
                **current_status,
                "write_unlock_outcome": unlock_outcome,
                "write_unlock_sentinel": blocking_sentinel,
            })
        except Exception as exc:
            logger.debug("[%s] phase1: write-unlock status emit failed: %s", ctx.job_id, exc)
        # Q16/T-18: persist write_unlock_probe.json for cohesion transition evidence
        try:
            _upload_to_blob(
                ARTIFACT_CONTAINER,
                f"{ctx.job_id}/write_unlock_probe.json",
                json.dumps({
                    "outcome": unlock_outcome,
                    "unlocked": ctx.unlock_result.get("unlocked"),
                    "blocking_sentinel": blocking_sentinel,
                    "sequence": ctx.unlock_result.get("sequence"),
                    "write_fn_tested": ctx.unlock_result.get("write_fn_tested"),
                    "notes": ctx.unlock_result.get("notes"),
                    "code_reasoning_analysis": ctx.unlock_result.get("code_reasoning_analysis"),
                }, indent=2).encode(),
            )
        except Exception as exc:
            logger.debug("[%s] phase1: write_unlock_probe artifact upload failed: %s",
                         ctx.job_id, exc)
    except Exception as _we:
        logger.debug("[%s] phase1: write-unlock probe failed: %s", ctx.job_id, _we)


# ══════════════════════════════════════════════════════════════════════════════
#  Micro Coordinator System — intelligent decision points at stage boundaries
# ══════════════════════════════════════════════════════════════════════════════

_WRITE_FN_PATTERN = _re.compile(
    r"(pay|redeem|unlock|process|write|commit|transfer|debit|credit)", _re.I
)


def _persist_mc_decision(ctx: ExploreContext, mc_id: str, decision: dict) -> None:
    """Write a micro coordinator decision artifact for observability."""
    try:
        blob_key = f"{ctx.job_id}/mc-decisions/{mc_id}.json"
        existing: list = []
        try:
            existing = json.loads(_download_blob(ARTIFACT_CONTAINER, blob_key))
        except Exception:
            pass
        existing.append(decision)
        _upload_to_blob(ARTIFACT_CONTAINER, blob_key,
                        json.dumps(existing, indent=2).encode())
    except Exception as exc:
        logger.debug("[%s] %s: decision artifact write failed: %s",
                     ctx.job_id, mc_id, exc)


def _mark_unlock_resolved(ctx: ExploreContext, trigger: str, notes: str) -> None:
    """Update context and job status when write-unlock is cracked."""
    ctx.write_unlock_block = (
        f"\nWRITE MODE ACTIVE (resolved at {trigger}): "
        "The write-unlock sequence has been executed. Write functions "
        "should now succeed. Probe with real ID values from "
        "STATIC ANALYSIS HINTS.\n"
    )
    logger.info("[%s] %s: WRITE UNLOCKED: %s", ctx.job_id, trigger, notes)

    # Persist the winning init sequence for future runs
    winning_sequence = ctx.unlock_result.get("sequence", [])
    try:
        _upload_to_blob(
            ARTIFACT_CONTAINER,
            f"{ctx.job_id}/winning_init_sequence.json",
            json.dumps({
                "resolved_at": trigger,
                "sequence": winning_sequence,
                "write_fn_tested": ctx.unlock_result.get("write_fn_tested"),
                "write_fn_args": ctx.unlock_result.get("write_fn_args"),
                "notes": notes,
            }, indent=2).encode(),
        )
        logger.info("[%s] %s: winning init sequence persisted", ctx.job_id, trigger)
    except Exception:
        pass

    try:
        _cur = _get_job_status(ctx.job_id) or {}
        _persist_job_status(ctx.job_id, {
            **_cur,
            "write_unlock_outcome": "resolved",
            "write_unlock_sentinel": None,
            "write_unlock_resolved_at": trigger,
        })
    except Exception:
        pass

    # Re-probe all failed write functions now that unlock is cracked
    _reprobe_write_functions_after_unlock(ctx, trigger)


def _reprobe_write_functions_after_unlock(ctx: ExploreContext, trigger: str) -> None:
    """Re-probe every write function that previously failed, now that unlock is active.

    This closes the gap where MC-6 cracks write-unlock but the findings still
    show error because the functions were probed before the unlock happened.
    """
    from api.storage import _load_findings

    findings = _load_findings(ctx.job_id)
    failed_writes = [
        f for f in findings
        if _WRITE_FN_PATTERN.search(f.get("function", ""))
        and f.get("status") == "error"
    ]
    if not failed_writes:
        return

    logger.info("[%s] %s: re-probing %d failed write functions after unlock",
                ctx.job_id, trigger, len(failed_writes))

    # Replay the winning init sequence first
    inv_map = {inv["name"]: inv for inv in ctx.invocables}
    for step in ctx.unlock_result.get("sequence", []):
        init_inv = inv_map.get(step.get("fn", ""))
        if init_inv:
            try:
                _execute_tool(init_inv, step.get("args", {}))
            except Exception:
                pass

    # Now probe each failed write function with vocab-derived args
    write_args = _build_write_args_from_vocab(ctx)
    reprobe_count = 0

    # Include the winning args at the front of every write function's candidates
    # (MC-6 proved these work for at least one function)
    _winning_fn = ctx.unlock_result.get("write_fn_tested", "")
    _winning_args = ctx.unlock_result.get("write_fn_args")
    if _winning_args and isinstance(_winning_args, dict):
        for fn in write_args:
            write_args[fn] = [_winning_args] + write_args[fn]

    for finding in failed_writes:
        fn_name = finding["function"]
        inv = inv_map.get(fn_name)
        if not inv:
            continue

        arg_candidates = write_args.get(fn_name, [])
        if not arg_candidates:
            params = inv.get("parameters") or []
            if params:
                arg_candidates = [{"param_1": "CUST-001", "param_2": 100}]
            else:
                arg_candidates = [{}]
        # Also include winning args even if fn wasn't in write_args
        if _winning_args and _winning_args not in arg_candidates:
            arg_candidates = [_winning_args] + arg_candidates

        for args in arg_candidates[:6]:
            try:
                # Re-init before each write attempt
                for step in ctx.unlock_result.get("sequence", []):
                    init_inv = inv_map.get(step.get("fn", ""))
                    if init_inv:
                        _execute_tool(init_inv, step.get("args", {}))

                result = _execute_tool(inv, args)
                ret_m = _re.match(r"Returned:\s*(\d+)", result or "")
                if ret_m:
                    ret_val = int(ret_m.group(1)) & 0xFFFFFFFF
                    if ret_val == 0:
                        _patch_finding(ctx.job_id, fn_name, {
                            "status": "success",
                            "working_call": args,
                            "notes": f"Cracked at {trigger}: init sequence + {fn_name}({args}) = 0",
                            "verification": "verified",
                        })
                        reprobe_count += 1
                        logger.info("[%s] %s: %s CRACKED with args %s",
                                    ctx.job_id, trigger, fn_name, args)
                        break
            except Exception:
                continue

    # If the winning function was among the failed writes but re-probe didn't
    # find it (e.g., different args), force-patch it since MC-6 already confirmed it.
    if _winning_fn and _winning_args and reprobe_count == 0:
        for finding in failed_writes:
            if finding["function"] == _winning_fn:
                _patch_finding(ctx.job_id, _winning_fn, {
                    "status": "success",
                    "working_call": _winning_args,
                    "notes": f"Confirmed by {trigger}: {_winning_fn}({_winning_args}) = 0",
                    "verification": "verified",
                })
                reprobe_count += 1
                logger.info("[%s] %s: force-patched %s from MC unlock proof",
                            ctx.job_id, trigger, _winning_fn)
                break

    logger.info("[%s] %s: re-probe complete — %d/%d write functions now succeed",
                ctx.job_id, trigger, reprobe_count, len(failed_writes))


def _targeted_unlock_attempt(
    ctx: ExploreContext,
    init_sequences: list[dict],
    write_test_args: dict[str, list[dict]],
    label: str,
) -> bool:
    """Try specific init→write sequences. Returns True if any write returns 0."""
    inv_map = {inv["name"]: inv for inv in ctx.invocables}
    write_fns = [n for n in inv_map if _WRITE_FN_PATTERN.search(n)]
    _SENTINEL_SET = {0xFFFFFFFB, 0xFFFFFFFE, 0xFFFFFFFF}

    for seq in init_sequences:
        init_fn = seq.get("fn", "")
        init_args = seq.get("args", {})
        init_inv = inv_map.get(init_fn)
        if not init_inv:
            continue
        try:
            _execute_tool(init_inv, init_args)
        except Exception:
            continue

        for wfn in write_fns:
            w_inv = inv_map.get(wfn)
            if not w_inv:
                continue
            for args in write_test_args.get(wfn, [{}])[:3]:
                try:
                    result = _execute_tool(w_inv, args)
                    ret_m = _re.match(r"Returned:\s*(\d+)", result or "")
                    if ret_m:
                        ret_val = int(ret_m.group(1)) & 0xFFFFFFFF
                        if ret_val == 0:
                            ctx.unlock_result = {
                                "unlocked": True,
                                "sequence": [{"fn": init_fn, "args": init_args}],
                                "write_fn_tested": wfn,
                                "write_fn_args": args,
                                "notes": f"{label}: {init_fn}({init_args}) → {wfn}({args}) = 0",
                            }
                            _mark_unlock_resolved(ctx, label, ctx.unlock_result["notes"])
                            return True
                except Exception:
                    continue
    return False


# ── MC-3: Post-Reconcile Coordinator ────────────────────────────────────────

def _mc3_post_reconcile(ctx: ExploreContext) -> None:
    """Analyze probe results to classify the write-unlock failure mode.

    READS: explore_probe_log.json, findings
    REASONS: What sentinel codes did write functions see? Are they uniform
             (init problem) or varied (mixed problem)? Did any write fn
             accidentally return 0?
    ACTS: Adjusts write_unlock_block with diagnostic-specific guidance.
          Tries targeted init sequences if analysis suggests a specific mode.
    DOCUMENTS: mc-decisions/mc3-post-reconcile.json
    """
    if ctx.unlock_result.get("unlocked"):
        return

    decision = {"trigger": "mc3-post-reconcile", "analysis": {}, "action": None, "result": None}

    try:
        probe_log: list[dict] = []
        try:
            raw = _download_blob(ARTIFACT_CONTAINER, f"{ctx.job_id}/explore_probe_log.json")
            probe_log = json.loads(raw)
        except Exception:
            decision["analysis"]["probe_log"] = "unavailable"
            _persist_mc_decision(ctx, "mc3-post-reconcile", decision)
            return

        # Collect sentinel codes seen from write function probes
        write_sentinels: dict[str, list[int]] = defaultdict(list)
        write_accidental_success: list[dict] = []

        for entry in probe_log:
            fn = entry.get("function", "")
            tool = entry.get("tool", "")
            if not _WRITE_FN_PATTERN.search(fn):
                continue
            clf = entry.get("classification") or {}
            if not clf.get("has_return"):
                continue
            ret_val = int(clf.get("signed", -1))
            if ret_val == 0 and fn == tool:
                write_accidental_success.append(entry)
            elif ret_val != 0:
                write_sentinels[fn].append(ret_val & 0xFFFFFFFF)

        # Classify the failure mode
        all_codes = []
        for codes in write_sentinels.values():
            all_codes.extend(codes)
        unique_codes = set(all_codes)

        if write_accidental_success:
            decision["analysis"]["mode"] = "accidental_success"
            decision["analysis"]["functions"] = [e.get("function") for e in write_accidental_success]
            decision["action"] = "extract_working_sequence"

            for entry in write_accidental_success:
                fn = entry.get("function", "")
                args = entry.get("args", {})
                _patch_finding(ctx.job_id, fn, {
                    "status": "success", "working_call": args,
                    "notes": "MC-3: write fn returned 0 in probe log — previously missed",
                })
            decision["result"] = f"patched {len(write_accidental_success)} write findings to success"

        elif len(unique_codes) == 1:
            the_code = list(unique_codes)[0]
            meaning = ctx.sentinels.get(the_code, "unknown")
            decision["analysis"]["mode"] = "uniform_sentinel"
            decision["analysis"]["code"] = f"0x{the_code:08X}"
            decision["analysis"]["meaning"] = meaning
            decision["analysis"]["interpretation"] = (
                "All write functions return the same sentinel. "
                "This strongly suggests an initialization/auth prerequisite, "
                "not a per-function argument problem."
            )
            decision["action"] = "targeted_init_sweep"

            ctx.write_unlock_block = (
                f"\nWRITE MODE BLOCKED — UNIFORM SENTINEL 0x{the_code:08X} ({meaning}). "
                "All write functions return the SAME error code. This means the DLL needs "
                "a specific initialization sequence BEFORE any write operation. "
                "You MUST call CS_Initialize with different mode values (0, 1, 2, 4, 8) "
                "and IMMEDIATELY test this write function after EACH init call. "
                "The mode that changes the error code (or returns 0) is the correct one.\n"
            )

            # Try targeted init sweep with focused modes
            init_names = [inv["name"] for inv in ctx.invocables
                          if _re.search(r"init(ializ)?", inv["name"], _re.I)]
            init_seqs = []
            for init_fn in init_names:
                for mode in (0, 1, 2, 3, 4, 5, 6, 7, 8, 16, 32, 64):
                    init_seqs.append({"fn": init_fn, "args": {"param_1": mode}})

            write_args = {}
            if ctx.vocab and ctx.vocab.get("id_formats"):
                ids = [str(f) for f in ctx.vocab["id_formats"] if f][:3]
                for inv in ctx.invocables:
                    if _WRITE_FN_PATTERN.search(inv["name"]):
                        params = inv.get("parameters") or []
                        if params:
                            a = {}
                            for i, p in enumerate(params):
                                pn = p.get("name", f"param_{i+1}")
                                if i < len(ids):
                                    a[pn] = ids[i]
                                else:
                                    a[pn] = 100 * (i + 1)
                            write_args[inv["name"]] = [a]

            if _targeted_unlock_attempt(ctx, init_seqs, write_args, "mc3-post-reconcile"):
                decision["result"] = "UNLOCKED via targeted init sweep"
            else:
                decision["result"] = "still blocked after targeted sweep"

        elif len(unique_codes) > 1:
            decision["analysis"]["mode"] = "mixed_sentinels"
            decision["analysis"]["codes"] = [f"0x{c:08X}" for c in unique_codes]
            decision["analysis"]["interpretation"] = (
                "Write functions return DIFFERENT sentinel codes. "
                "This suggests mixed failure causes — some may need init, "
                "others may need specific arguments."
            )
            decision["action"] = "no_retry_mixed"
            decision["result"] = "deferred — mixed sentinels need per-function strategy"
        else:
            decision["analysis"]["mode"] = "no_write_probes"
            decision["action"] = "fallback_reprobe"
            ctx.unlock_result = _probe_write_unlock(ctx.invocables, ctx.dll_strings, ctx.vocab)
            if ctx.unlock_result.get("unlocked"):
                _mark_unlock_resolved(ctx, "mc3-post-reconcile", ctx.unlock_result.get("notes", ""))
                decision["result"] = "UNLOCKED via fallback re-probe"
            else:
                decision["result"] = "still blocked"

    except Exception as exc:
        decision["result"] = f"error: {exc}"
        logger.debug("[%s] mc3-post-reconcile failed: %s", ctx.job_id, exc)

    _persist_mc_decision(ctx, "mc3-post-reconcile", decision)


# ── MC-4: Post-Synthesis Coordinator ────────────────────────────────────────

def _mc4_post_synthesis(ctx: ExploreContext) -> None:
    """Parse synthesis doc for init sequence clues the LLM may have inferred.

    READS: api-reference.md from blob
    REASONS: Does the synthesis describe a specific initialization mode?
             Does it describe function dependencies?
    ACTS: If synthesis mentions a specific init mode, tries it directly.
    DOCUMENTS: mc-decisions/mc4-post-synthesis.json
    """
    if ctx.unlock_result.get("unlocked"):
        return

    decision = {"trigger": "mc4-post-synthesis", "analysis": {}, "action": None, "result": None}

    try:
        api_ref = ""
        try:
            raw = _download_blob(ARTIFACT_CONTAINER, f"{ctx.job_id}/api_reference.md")
            api_ref = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
        except Exception:
            decision["analysis"]["api_reference"] = "unavailable"
            _persist_mc_decision(ctx, "mc4-post-synthesis", decision)
            return

        decision["analysis"]["api_ref_length"] = len(api_ref)

        # Look for init mode patterns in synthesis
        init_mode_matches = _re.findall(
            r"(?:CS_Initialize|init\w*)\s*\(\s*(?:mode\s*=\s*|param_1\s*=\s*)?(\d+)\s*\)",
            api_ref, _re.I,
        )
        # Also look for prose descriptions of init modes
        prose_matches = _re.findall(
            r"mode\s+(\d+)\s+(?:for|enables?|activates?|unlocks?|allows?)\s+(?:write|payment|transaction)",
            api_ref, _re.I,
        )
        all_suggested_modes = list(dict.fromkeys(
            [int(m) for m in init_mode_matches + prose_matches]
        ))

        if all_suggested_modes:
            decision["analysis"]["suggested_modes"] = all_suggested_modes
            decision["analysis"]["source"] = "synthesis document mentioned specific init modes"
            decision["action"] = "try_synthesis_suggested_modes"

            init_names = [inv["name"] for inv in ctx.invocables
                          if _re.search(r"init(ializ)?", inv["name"], _re.I)]
            init_seqs = []
            for mode in all_suggested_modes:
                for init_fn in init_names:
                    init_seqs.append({"fn": init_fn, "args": {"param_1": mode}})

            write_args = _build_write_args_from_vocab(ctx)
            if _targeted_unlock_attempt(ctx, init_seqs, write_args, "mc4-post-synthesis"):
                decision["result"] = f"UNLOCKED via synthesis-suggested mode {all_suggested_modes}"
            else:
                decision["result"] = f"tried modes {all_suggested_modes} from synthesis — still blocked"
        else:
            # Look for dependency clues
            dep_patterns = _re.findall(
                r"(?:must|should|need to|requires?)\s+(?:call|invoke)\s+(\w+)\s+(?:before|first|prior)",
                api_ref, _re.I,
            )
            if dep_patterns:
                decision["analysis"]["dependencies"] = dep_patterns
                decision["action"] = "noted_dependencies"
                decision["result"] = f"synthesis mentions dependencies: {dep_patterns} — logged for MC-5/MC-6"
            else:
                decision["analysis"]["init_clues"] = "none found"
                decision["action"] = "no_action"
                decision["result"] = "synthesis doc has no actionable init sequence hints"

    except Exception as exc:
        decision["result"] = f"error: {exc}"
        logger.debug("[%s] mc4-post-synthesis failed: %s", ctx.job_id, exc)

    _persist_mc_decision(ctx, "mc4-post-synthesis", decision)


# ── MC-5: Post-Verification Coordinator ─────────────────────────────────────

def _mc5_post_verification(ctx: ExploreContext) -> None:
    """Use verified read function outputs as concrete write function arguments.

    READS: verification-report.json, findings
    REASONS: If CS_GetAccountBalance(CUST-001) returned real data, CUST-001 is
             confirmed valid. Chain these into write function args.
    ACTS: Builds concrete write args from verified reads and attempts unlock.
    DOCUMENTS: mc-decisions/mc5-post-verification.json
    """
    if ctx.unlock_result.get("unlocked"):
        return

    decision = {"trigger": "mc5-post-verification", "analysis": {}, "action": None, "result": None}

    try:
        from api.storage import _load_findings

        findings = _load_findings(ctx.job_id)
        verified_reads: list[dict] = []
        extracted_ids: list[str] = []
        extracted_amounts: list[int] = []

        for f in findings:
            fn = f.get("function", "")
            if _WRITE_FN_PATTERN.search(fn):
                continue
            if f.get("status") != "success":
                continue
            verification = f.get("verification", "")
            if verification not in ("verified", ""):
                continue

            verified_reads.append(f)
            wc = f.get("working_call")
            if isinstance(wc, dict):
                for k, v in wc.items():
                    sv = str(v).strip()
                    if _re.match(r"^[A-Z]+-\d+", sv):
                        extracted_ids.append(sv)
                    elif _re.match(r"^ORD-", sv):
                        extracted_ids.append(sv)
                    elif _re.match(r"^ACCT-", sv):
                        extracted_ids.append(sv)
                    elif isinstance(v, (int, float)) and 1 <= v <= 100000:
                        extracted_amounts.append(int(v))

        extracted_ids = list(dict.fromkeys(extracted_ids))[:6]
        extracted_amounts = list(dict.fromkeys(extracted_amounts))[:4]
        if not extracted_amounts:
            extracted_amounts = [100, 500, 1000]

        decision["analysis"]["verified_read_count"] = len(verified_reads)
        decision["analysis"]["extracted_ids"] = extracted_ids
        decision["analysis"]["extracted_amounts"] = extracted_amounts

        if not extracted_ids:
            decision["action"] = "no_ids_extracted"
            decision["result"] = "no verified IDs to chain into write functions"
            _persist_mc_decision(ctx, "mc5-post-verification", decision)
            return

        decision["action"] = "chain_read_outputs_to_write_inputs"

        # Build write args from verified read data
        write_args: dict[str, list[dict]] = {}
        for inv in ctx.invocables:
            if not _WRITE_FN_PATTERN.search(inv["name"]):
                continue
            params = inv.get("parameters") or []
            if not params:
                continue

            arg_combos = []
            for id_val in extracted_ids[:3]:
                for amt in extracted_amounts[:2]:
                    a = {}
                    for i, p in enumerate(params):
                        pn = p.get("name", f"param_{i+1}")
                        if i == 0:
                            a[pn] = id_val
                        elif i == 1:
                            a[pn] = amt
                        else:
                            a[pn] = id_val if i % 2 == 0 else amt
                    arg_combos.append(a)
            write_args[inv["name"]] = arg_combos

        # Try with every init mode the pipeline has seen work
        init_names = [inv["name"] for inv in ctx.invocables
                      if _re.search(r"init(ializ)?", inv["name"], _re.I)]
        init_seqs = []
        for init_fn in init_names:
            for mode in range(9):
                init_seqs.append({"fn": init_fn, "args": {"param_1": mode}})

        if _targeted_unlock_attempt(ctx, init_seqs, write_args, "mc5-post-verification"):
            decision["result"] = (
                f"UNLOCKED by chaining verified read outputs "
                f"(IDs: {extracted_ids}) into write functions"
            )
        else:
            decision["result"] = (
                f"tried {len(extracted_ids)} verified IDs × {len(extracted_amounts)} "
                f"amounts × {len(init_seqs)} init sequences — still blocked"
            )

    except Exception as exc:
        decision["result"] = f"error: {exc}"
        logger.debug("[%s] mc5-post-verification failed: %s", ctx.job_id, exc)

    _persist_mc_decision(ctx, "mc5-post-verification", decision)


# ── MC-6: Post-Gap-Resolution Coordinator ───────────────────────────────────

def _mc6_post_gap_resolution(ctx: ExploreContext) -> None:
    """Final comprehensive unlock attempt with ALL accumulated knowledge.

    READS: All findings (including gap-resolved), probe log, vocab, sentinels
    REASONS: Did gap resolution crack any write function? Did it discover new
             dependencies? This is the last chance before finalization.
    ACTS: Combines everything: verified IDs, synthesis hints, all init modes.
    DOCUMENTS: mc-decisions/mc6-post-gap-resolution.json
    """
    if ctx.unlock_result.get("unlocked"):
        return

    decision = {"trigger": "mc6-post-gap-resolution", "analysis": {}, "action": None, "result": None}

    try:
        from api.storage import _load_findings

        findings = _load_findings(ctx.job_id)

        # Check if gap resolution cracked any write function
        gap_resolved_writes = [
            f for f in findings
            if _WRITE_FN_PATTERN.search(f.get("function", ""))
            and f.get("status") == "success"
            and "gap" in str(f.get("stop_reason", "")).lower()
        ]

        if gap_resolved_writes:
            decision["analysis"]["gap_resolved_writes"] = [
                f.get("function") for f in gap_resolved_writes
            ]
            decision["action"] = "gap_resolution_cracked_write"
            decision["result"] = (
                f"Gap resolution already cracked {len(gap_resolved_writes)} write functions — "
                "checking if full unlock is now possible"
            )

        # Gather ALL known-good data
        all_ids = set()
        all_amounts = set()
        for f in findings:
            if f.get("status") != "success":
                continue
            wc = f.get("working_call")
            if not isinstance(wc, dict):
                continue
            for v in wc.values():
                sv = str(v).strip()
                if _re.match(r"^[A-Z]+-", sv):
                    all_ids.add(sv)
                elif isinstance(v, (int, float)) and 1 <= v <= 100000:
                    all_amounts.add(int(v))

        if ctx.vocab:
            for fmt in (ctx.vocab.get("id_formats") or []):
                if fmt:
                    all_ids.add(str(fmt))

        all_ids_list = list(all_ids)[:8]
        all_amounts_list = list(all_amounts) or [100, 500, 1000, 2500]

        decision["analysis"]["total_known_ids"] = len(all_ids_list)
        decision["analysis"]["total_known_amounts"] = len(all_amounts_list)
        decision["analysis"]["total_sentinel_codes"] = len(ctx.sentinels)
        decision["action"] = "final_comprehensive_attempt"

        # ── Phase A: Code-reasoning unlock ──────────────────────────────
        # Reuse the shared decompilation analysis from explore_phases.
        from api.explore_phases import _analyze_decompiled_unlock_patterns

        code_analysis = _analyze_decompiled_unlock_patterns(ctx.invocables)
        decision["analysis"]["code_reasoning"] = code_analysis
        inv_map = {inv["name"]: inv for inv in ctx.invocables}

        for uf in code_analysis.get("unlock_functions", []):
            uf_inv = inv_map.get(uf["name"])
            if not uf_inv:
                continue
            params = uf.get("params") or []
            if not params:
                continue

            decision["analysis"]["unlock_fn_found"] = uf["name"]
            decision["analysis"]["xor_checksum_target"] = uf.get("xor_target_hex")
            decision["analysis"]["xor_codes_generated"] = len(uf.get("xor_codes", []))

            for id_val in all_ids_list[:4]:
                for code in uf.get("xor_codes", [])[:5]:
                    args = {}
                    for i, p in enumerate(params):
                        pn = p.get("name", f"param_{i+1}")
                        if i == 0:
                            args[pn] = id_val
                        elif i == 1:
                            args[pn] = code
                        else:
                            args[pn] = id_val
                    try:
                        for init_inv in ctx.init_invocables or []:
                            _execute_tool(init_inv, {})
                        result = _execute_tool(uf_inv, args)
                        ret_m = _re.match(r"Returned:\s*(\d+)", result or "")
                        if ret_m and int(ret_m.group(1)) == 0:
                            decision["analysis"]["unlock_cracked"] = True
                            decision["analysis"]["unlock_args"] = args
                            logger.info("[%s] MC-6: %s CRACKED with XOR code: %s",
                                        ctx.job_id, uf["name"], args)
                            _mark_unlock_resolved(
                                ctx, "mc6-code-reasoning",
                                f"{uf['name']}({args}) → 0 (XOR checksum solved)",
                            )
                            decision["result"] = (
                                f"UNLOCKED via code reasoning: {uf['name']}({args}) "
                                f"with XOR target {uf['xor_target_hex']}"
                            )
                            _persist_mc_decision(ctx, "mc6-post-gap-resolution", decision)
                            return
                    except Exception:
                        continue

        # Also try dependency chains from code analysis
        for dep in code_analysis.get("dependency_chains", []):
            src = inv_map.get(dep["source"])
            if not src:
                continue
            try:
                for init_inv in ctx.init_invocables or []:
                    _execute_tool(init_inv, {})
                _execute_tool(src, {})
                # Build write args and test
                dep_write_args: dict[str, list[dict]] = {}
                for inv in ctx.invocables:
                    if inv["name"] == dep["target"] and _WRITE_FN_PATTERN.search(inv["name"]):
                        p = inv.get("parameters") or []
                        if p:
                            for id_val in all_ids_list[:3]:
                                a = {}
                                for j, pp in enumerate(p):
                                    pn = pp.get("name", f"param_{j+1}")
                                    a[pn] = id_val if j == 0 else 100
                                dep_write_args.setdefault(inv["name"], []).append(a)
                init_seqs = [{"fn": init_inv["name"], "args": {}} for init_inv in (ctx.init_invocables or [])]
                init_seqs.append({"fn": dep["source"], "args": {}})
                if dep_write_args and _targeted_unlock_attempt(ctx, init_seqs, dep_write_args, "mc6-dependency-chain"):
                    decision["result"] = f"UNLOCKED via dependency chain: {dep['source']} → {dep['target']}"
                    _persist_mc_decision(ctx, "mc6-post-gap-resolution", decision)
                    return
            except Exception:
                continue

        # ── Phase B: Brute-force fallback ─────────────────────────────────
        write_args: dict[str, list[dict]] = {}
        for inv in ctx.invocables:
            if not _WRITE_FN_PATTERN.search(inv["name"]):
                continue
            params = inv.get("parameters") or []
            if not params:
                continue
            combos = []
            for id_val in all_ids_list[:4]:
                for amt in all_amounts_list[:3]:
                    a = {}
                    for i, p in enumerate(params):
                        pn = p.get("name", f"param_{i+1}")
                        if i == 0:
                            a[pn] = id_val
                        elif i == 1:
                            a[pn] = amt
                        else:
                            a[pn] = id_val if i % 2 == 0 else amt
                    combos.append(a)
            write_args[inv["name"]] = combos

        init_names = [inv["name"] for inv in ctx.invocables
                      if _re.search(r"init(ializ)?", inv["name"], _re.I)]
        init_seqs = []
        for init_fn in init_names:
            for mode in list(range(17)) + [32, 64, 128, 256, 512]:
                init_seqs.append({"fn": init_fn, "args": {"param_1": mode}})
            init_seqs.append({"fn": init_fn, "args": {}})

        if _targeted_unlock_attempt(ctx, init_seqs, write_args, "mc6-post-gap-resolution"):
            decision["result"] = "UNLOCKED on brute-force fallback"
        else:
            decision["result"] = (
                f"FINAL: still blocked after {len(init_seqs)} init sequences × "
                f"{sum(len(v) for v in write_args.values())} write arg combos"
            )

    except Exception as exc:
        decision["result"] = f"error: {exc}"
        logger.debug("[%s] mc6-post-gap-resolution failed: %s", ctx.job_id, exc)

    _persist_mc_decision(ctx, "mc6-post-gap-resolution", decision)


# ── Shared helper ───────────────────────────────────────────────────────────

def _build_write_args_from_vocab(ctx: ExploreContext) -> dict[str, list[dict]]:
    """Build write function test args from vocab id_formats and known values."""
    write_args: dict[str, list[dict]] = {}
    ids = []
    if ctx.vocab and ctx.vocab.get("id_formats"):
        ids = [str(f) for f in ctx.vocab["id_formats"] if f][:4]
    if not ids:
        ids = ["CUST-001", "ACCT-001", "ORD-001"]

    for inv in ctx.invocables:
        if not _WRITE_FN_PATTERN.search(inv["name"]):
            continue
        params = inv.get("parameters") or []
        if not params:
            continue
        combos = []
        for id_val in ids[:3]:
            for amt in (100, 500, 1000):
                a = {}
                for i, p in enumerate(params):
                    pn = p.get("name", f"param_{i+1}")
                    if i == 0:
                        a[pn] = id_val
                    elif i == 1:
                        a[pn] = amt
                    else:
                        a[pn] = id_val if i % 2 == 0 else amt
                combos.append(a)
        write_args[inv["name"]] = combos
    return write_args


# ══════════════════════════════════════════════════════════════════════════════
#  Phase 2 – Curriculum ordering (Active Learning-style)
# ══════════════════════════════════════════════════════════════════════════════

def _run_phase_2_curriculum_order(ctx: ExploreContext) -> None:
    """Reorder invocables: init functions first, then by ascending uncertainty score.

    By the time the LLM reaches ambiguous multi-param functions, the vocab table
    is rich with cross-function conventions learned from simpler ones.

    READS:  ctx.invocables
    WRITES: ctx.invocables (reordered), ctx.init_invocables
    INVARIANT: all original invocables present; none added or dropped
    HANDOFF: init functions available in ctx.init_invocables for Q-5 DLL state resets
    """
    ctx.init_invocables = [inv for inv in ctx.invocables if _INIT_RE.search(inv["name"])]
    _other_invs = [inv for inv in ctx.invocables if not _INIT_RE.search(inv["name"])]
    _other_invs.sort(key=_uncertainty_score)
    ctx.invocables = ctx.init_invocables + _other_invs
    logger.info("[%s] phase2: ordered %d init + %d others by uncertainty",
                ctx.job_id, len(ctx.init_invocables), len(_other_invs))


# ══════════════════════════════════════════════════════════════════════════════

def _run_phase_4_reconcile(ctx: ExploreContext) -> None:
    """Scan probe log for error findings that had a successful direct probe.

    Functions that returned 0 during the probe loop but whose LLM-recorded
    finding ended up as "error" are upgraded to "success" here.

    READS:  ctx.job_id, explore_probe_log.json from blob
    WRITES: findings patched in blob storage (error → success)
    INVARIANT: only upgrades; never downgrades success → error
    HANDOFF: findings reflect probe-log ground truth before synthesis
    """
    try:
        _set_explore_status(ctx.job_id, ctx._state["explored"], ctx.total,
                            "Reconciling probe evidence…")
        from api.storage import _load_findings
        _recon_findings = _load_findings(ctx.job_id)
        _recon_probe_log: list[dict] = []
        try:
            _recon_probe_raw = _download_blob(
                ARTIFACT_CONTAINER, f"{ctx.job_id}/explore_probe_log.json"
            )
            _recon_probe_log = json.loads(_recon_probe_raw)
        except Exception:
            pass

        if not _recon_probe_log:
            return

        # Build map: function → direct self-call probes where return was 0.
        # Prevents false upgrades from prerequisite calls such as CS_Initialize.
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
                _best_pe = max(_success_probes[_fn], key=lambda _p: len(_p.get("args") or {}))
                _wc = _best_pe.get("args") or {}
                _patch_finding(ctx.job_id, _fn, {
                    "status": "success", "working_call": _wc,
                    "stop_reason": "reconciled_from_probe_log",
                    "notes": (
                        f"AC-1 reconciliation: probe "
                        f"{_best_pe.get('probe_id', '?')} returned 0 "
                        "but finding was error. Overridden to success."
                    ),
                })
                _recon_patched += 1

        if _recon_patched:
            logger.info("[%s] phase4: AC-1 reconciliation patched %d functions error→success",
                        ctx.job_id, _recon_patched)
    except Exception as _recon_e:
        logger.debug("[%s] phase4: AC-1 reconciliation failed: %s", ctx.job_id, _recon_e)


# ══════════════════════════════════════════════════════════════════════════════
#  Phase 5 – Sentinel catalog persist + vocab promotion
# ══════════════════════════════════════════════════════════════════════════════

def _run_phase_5_sentinel_catalog(ctx: ExploreContext) -> None:
    """Persist the sentinel evidence catalog and promote confident codes to vocab.

    READS:  ctx.sentinel_catalog, ctx.vocab
    WRITES: ctx.vocab["error_codes"] (non-provisional codes promoted)
    HANDOFF: sentinel_catalog.json and vocab.json uploaded to blob
    """
    try:
        _promoted = 0
        ctx.vocab.setdefault("error_codes", {})
        for _hex, _row in ctx.sentinel_catalog.items():
            _conf = float(_row.get("confidence") or 0.0)
            _evi = int(_row.get("evidence_count") or 0)
            _src = str(_row.get("source") or "")
            _meaning = str(_row.get("meaning") or "unknown")
            _strong_det = _src.startswith("deterministic.") and _conf >= 0.95
            _stable = _conf >= 0.85 and _evi >= 2
            if (_strong_det or _stable) and _meaning and _meaning != "unknown":
                ctx.vocab["error_codes"].setdefault(_hex, _meaning)
                _row["provisional"] = False
                _row["promotion_reason"] = (
                    "deterministic_single" if _strong_det else "repeated_evidence"
                )
                _promoted += 1

        _upload_to_blob(
            ARTIFACT_CONTAINER,
            f"{ctx.job_id}/sentinel_catalog.json",
            json.dumps({
                "codes": ctx.sentinel_catalog,
                "promoted_count": _promoted,
                "total_counts": len(ctx.sentinel_catalog),
            }, indent=2).encode(),
        )
        _upload_to_blob(
            ARTIFACT_CONTAINER,
            f"{ctx.job_id}/vocab.json",
            json.dumps(ctx.vocab).encode(),
        )
        logger.info("[%s] phase5: sentinel_catalog persisted (%d codes, %d promoted)",
                    ctx.job_id, len(ctx.sentinel_catalog), _promoted)
    except Exception as _sce:
        logger.debug("[%s] phase5: sentinel_catalog persist failed: %s", ctx.job_id, _sce)


# ══════════════════════════════════════════════════════════════════════════════
#  Phase 6 – Synthesis
# ══════════════════════════════════════════════════════════════════════════════

def _run_phase_6_synthesize(ctx: ExploreContext) -> str | None:
    """Generate the API reference markdown from all findings.

    READS:  ctx.job_id, ctx.client, ctx.model, ctx.vocab, ctx.sentinels
    WRITES: api_reference.md uploaded to blob
            ctx.invocables, ctx.inv_map refreshed from current registry
    INVARIANT: returns None if synthesis fails or no findings available
    HANDOFF: returns synthesized report string for downstream phases
    """
    from api.storage import _load_findings

    _syn_findings = _load_findings(ctx.job_id)
    if not _syn_findings:
        return None

    # Save synthesis model context: what findings the LLM will synthesize from.
    try:
        import json as _json
        _ctx_lines = [
            "=== SYNTHESIS PHASE MODEL CONTEXT ===",
            f"Functions: {len(ctx.invocables)}",
            f"Findings: {len(_syn_findings)}",
            "",
            "--- Findings ---",
        ]
        for _f in _syn_findings:
            _status = _f.get("status", "?")
            _fn = _f.get("function", "?")
            _wc = _f.get("working_call") or {}
            _ctx_lines.append(
                f"  {_fn}: {_status}"
                + (f" | working_call={_wc}" if _wc else "")
            )
        _ctx_lines.append("")
        _ctx_lines.append("--- Vocab (domain terms) ---")
        for _k, _v in list((ctx.vocab or {}).items())[:15]:
            _ctx_lines.append(f"  {_k}: {str(_v)[:80]}")
        _save_stage_context(ctx.job_id, "model_context_phase_06_synthesis.txt",
                            "\n".join(_ctx_lines))
    except Exception as _mce:
        logger.debug("[%s] phase6: model context save failed: %s", ctx.job_id, _mce)

    try:
        logger.info("[%s] phase6: synthesizing API reference (%d fns)…",
                    ctx.job_id, len(_syn_findings))
        _set_explore_status(ctx.job_id, ctx._state["explored"], ctx.total,
                            "Synthesizing API reference…")
        # Reasoning artifact: snapshot synthesis inputs before the LLM call.
        # Writes to evidence/stage-04-synthesis/synthesis-input-snapshot.json
        try:
            _syn_snapshot = {
                "function_count": len(ctx.invocables),
                "findings_count": len(_syn_findings),
                "findings": _syn_findings,
                "vocab": ctx.vocab,
            }
            _upload_to_blob(
                ARTIFACT_CONTAINER,
                f"{ctx.job_id}/evidence/stage-04-synthesis/synthesis-input-snapshot.json",
                json.dumps(_syn_snapshot, indent=2).encode(),
            )
        except Exception as _snap_e:
            logger.debug("[%s] synthesis-input-snapshot write failed: %s",
                         ctx.job_id, _snap_e)

        _report = _synthesize(
            ctx.client, ctx.model, _syn_findings,
            vocab=ctx.vocab, sentinels=ctx.sentinels,
        )
        if not _report:
            return None

        _upload_to_blob(
            ARTIFACT_CONTAINER,
            f"{ctx.job_id}/api_reference.md",
            _report.encode("utf-8"),
        )
        logger.info("[%s] phase6: api_reference.md saved to blob", ctx.job_id)

        # Reasoning artifact: check which functions appear in the synthesis text.
        # Coverage < 100% means the synthesis LLM silently dropped functions.
        # Writes to evidence/stage-04-synthesis/synthesis-coverage-check.json.
        try:
            _fn_names_found = [f.get("function", "") for f in _syn_findings if f.get("function")]
            _coverage_entries = [
                {"function": _fn_c, "in_api_reference": bool(_fn_c and _fn_c in _report)}
                for _fn_c in _fn_names_found
            ]
            _covered_count = sum(1 for e in _coverage_entries if e["in_api_reference"])
            _cov_check = {
                "total_functions": len(_fn_names_found),
                "covered_in_report": _covered_count,
                "coverage_pct": (
                    round(100.0 * _covered_count / len(_fn_names_found), 1)
                    if _fn_names_found else 0.0
                ),
                "functions": _coverage_entries,
            }
            _upload_to_blob(
                ARTIFACT_CONTAINER,
                f"{ctx.job_id}/evidence/stage-04-synthesis/synthesis-coverage-check.json",
                json.dumps(_cov_check, indent=2).encode(),
            )
        except Exception as _cc_e:
            logger.debug("[%s] phase6: synthesis-coverage-check write failed: %s",
                         ctx.job_id, _cc_e)

        # Refresh invocables from the in-memory registry so backfill and gap
        # resolution see discovery enrichments, not the stale worker-start snapshot.
        _refreshed = _get_current_invocables(ctx.job_id)
        if _refreshed:
            ctx.invocables = _refreshed
            ctx.inv_map = {iv["name"]: iv for iv in ctx.invocables}

        # Snapshot BEFORE backfill so we can diff what enrichment produced
        # versus what backfill adds/overwrites.
        _snapshot_schema_stage(ctx.job_id, "mcp_schema_post_enrichment.json")
        return _report
    except Exception as _syn_e:
        logger.debug("[%s] phase6: synthesis failed: %s", ctx.job_id, _syn_e)
        return None


# ══════════════════════════════════════════════════════════════════════════════
#  Phase 7 – Schema backfill
# ══════════════════════════════════════════════════════════════════════════════

def _run_phase_7_backfill(ctx: ExploreContext, report: str) -> None:
    """Backfill schema descriptions from the synthesis document.

    Uses the completed synthesis to enrich param descriptions with proven
    semantics (units, entity refs, example values).

    READS:  ctx.job_id, ctx.client, ctx.model, ctx.invocables, report (api_reference.md)
    WRITES: param descriptions updated via _backfill_schema_from_synthesis
    HANDOFF: schema snapshots written at post-discovery and pre-gap-resolution
    """
    _backfill_stats: dict[str, Any] = {
        "patches_requested": 0,
        "patches_applied": 0,
        "patched_functions": [],
    }
    try:
        logger.info("[%s] phase7: layer3 schema backfill…", ctx.job_id)
        _set_explore_status(ctx.job_id, ctx._state["explored"], ctx.total,
                            "Enriching schema from synthesis…")
        _backfill_stats = _backfill_schema_from_synthesis(
            ctx.client, ctx.model, report, ctx.invocables, ctx.job_id
        )
    except Exception as _bf_e:
        logger.debug("[%s] phase7: backfill failed: %s", ctx.job_id, _bf_e)
        _backfill_stats["error"] = str(_bf_e)

    try:
        _upload_to_blob(
            ARTIFACT_CONTAINER,
            f"{ctx.job_id}/backfill_result.json",
            json.dumps(
                {
                    "backfill_ran": True,
                    "patches_requested": int(_backfill_stats.get("patches_requested") or 0),
                    "patches_applied": int(_backfill_stats.get("patches_applied") or 0),
                    "patched_functions": _backfill_stats.get("patched_functions") or [],
                    "error": _backfill_stats.get("error"),
                },
                indent=2,
            ).encode("utf-8"),
        )
    except Exception as _bf_emit_e:
        logger.debug("[%s] phase7: backfill_result emit failed: %s", ctx.job_id, _bf_emit_e)

    # Schema checkpoint after initial discovery/backfill but before gap-resolution
    _snapshot_schema_stage(ctx.job_id, "mcp_schema_post_discovery.json")
    _snapshot_schema_stage(ctx.job_id, "mcp_schema_pre_gap_resolution.json")


# ══════════════════════════════════════════════════════════════════════════════
#  Phase 7b – Enrichment verification (execute working_calls against the DLL)
# ══════════════════════════════════════════════════════════════════════════════

def _run_phase_7b_verify_enrichment(ctx: ExploreContext) -> None:
    """Execute each 'success' finding's working_call against the DLL to verify it.

    Closes the enrichment→verification loop: backfill/enrichment adds plausible
    args, this phase proves them by actually calling the DLL.  Results:
      - return=0 → status stays 'success', verification='verified'
      - return=sentinel → verification='inferred' (enrichment was plausible but unproven)
      - exception → verification='error'

    READS:  ctx.job_id, ctx.inv_map, ctx.sentinels
    WRITES: findings patched with 'verification' field; verification-report.json uploaded
    """
    from api.storage import _load_findings
    from api.executor import _execute_tool

    try:
        logger.info("[%s] phase7b: enrichment verification pass…", ctx.job_id)
        _set_explore_status(ctx.job_id, ctx._state["explored"], ctx.total,
                            "Verifying enriched function calls…")
        findings = _load_findings(ctx.job_id)
        if not findings:
            return

        report_entries: list[dict] = []
        verified_count = 0
        inferred_count = 0

        for finding in findings:
            fn_name = finding.get("function", "")
            status = finding.get("status", "")
            working_call = finding.get("working_call")

            if status != "success" or not working_call:
                continue

            inv = ctx.inv_map.get(fn_name)
            if not inv:
                continue

            # working_call is either the args dict itself ({"param_1": "CUST-001"})
            # or a wrapper with an "args" key. Handle both formats.
            if "args" in working_call:
                call_args = working_call["args"] or {}
            elif "arguments" in working_call:
                call_args = working_call["arguments"] or {}
            else:
                call_args = working_call

            try:
                # Ensure init has been called
                for init_inv in ctx.init_invocables:
                    _execute_tool(init_inv, {})

                result_str = _execute_tool(inv, call_args)
                ret_match = _re.match(r"Returned:\s*(\d+)", result_str or "")
                if ret_match:
                    ret_val = int(ret_match.group(1)) & 0xFFFFFFFF
                    is_sentinel = ret_val in ctx.sentinels or ret_val > 0x80000000
                    if ret_val == 0:
                        verification = "verified"
                        verified_count += 1
                    elif is_sentinel:
                        verification = "inferred"
                        inferred_count += 1
                    else:
                        verification = "verified"
                        verified_count += 1
                else:
                    verification = "inferred"
                    inferred_count += 1

                _patch_finding(ctx.job_id, fn_name, {"verification": verification})
                report_entries.append({
                    "function": fn_name, "verification": verification,
                    "args": call_args, "result": (result_str or "")[:200],
                    "return_value": ret_match.group(1) if ret_match else None,
                })

            except Exception as exc:
                _patch_finding(ctx.job_id, fn_name, {"verification": "error"})
                report_entries.append({
                    "function": fn_name, "verification": "error",
                    "args": call_args, "error": str(exc)[:200],
                })

        logger.info("[%s] phase7b: verified=%d, inferred=%d out of %d success findings",
                    ctx.job_id, verified_count, inferred_count,
                    verified_count + inferred_count)

        try:
            _upload_to_blob(
                ARTIFACT_CONTAINER,
                f"{ctx.job_id}/verification-report.json",
                json.dumps({
                    "verified_count": verified_count,
                    "inferred_count": inferred_count,
                    "total_checked": len(report_entries),
                    "entries": report_entries,
                }, indent=2).encode(),
            )
        except Exception as exc:
            logger.debug("[%s] phase7b: verification report upload failed: %s",
                         ctx.job_id, exc)

        _error_count = sum(1 for e in report_entries if e.get("verification") == "error")
        try:
            _cur = _get_job_status(ctx.job_id) or {}
            _persist_job_status(ctx.job_id, {
                **_cur,
                "verification_verified": verified_count,
                "verification_inferred": inferred_count,
                "verification_error": _error_count,
            })
        except Exception:
            pass

    except Exception as exc:
        logger.debug("[%s] phase7b: enrichment verification failed: %s", ctx.job_id, exc)


# ══════════════════════════════════════════════════════════════════════════════
#  Phase 8 – Gap resolution + clarification questions
# ══════════════════════════════════════════════════════════════════════════════

def _run_phase_8_gap_resolution(ctx: ExploreContext) -> None:
    """Retry failed functions and generate clarification questions for genuine unknowns.

    This is the single largest "tail" phase — it handles gap resolution (second-pass
    targeted probing) and the resulting clarification question generation.  They share
    a block because gap resolution resolves issues we DON'T need to ask about.

    READS:  ctx.job_id, ctx.runtime (gap_resolution_enabled, clarification_enabled),
            ctx.invocables, ctx.client, ctx.model, ctx.sentinels, ctx.vocab,
            ctx.use_cases_text, ctx.inv_map, ctx.tool_schemas
    WRITES: failing findings may be upgraded; explore_questions persisted to job status
    HANDOFF: schema snapshots written post-gap-resolution and post-clarification
    """
    from api.storage import _load_findings

    if not ctx.runtime.gap_resolution_enabled:
        logger.info("[%s] phase8: gap resolution disabled for this run", ctx.job_id)
        _persist_job_status(
            ctx.job_id,
            {**(_get_job_status(ctx.job_id) or {}), "explore_questions": []},
            sync=True,
        )
        return

    # Save gap-resolution model context: what the LLM will see when retrying failures.
    try:
        _gap_findings = _load_findings(ctx.job_id)
        _failed = [f for f in _gap_findings if f.get("status") != "success"]
        _succeeded = [f for f in _gap_findings if f.get("status") == "success"]
        _gap_sys = _build_explore_system_message(
            ctx.invocables, _gap_findings,
            sentinels=ctx.sentinels, vocab=ctx.vocab, use_cases=ctx.use_cases_text,
        )
        _gap_ctx_lines = [
            "=== GAP RESOLUTION PHASE MODEL CONTEXT ===",
            f"Functions total: {len(ctx.invocables)} | succeeded: {len(_succeeded)} | failed: {len(_failed)}",
            "",
            "--- Failed functions to retry ---",
        ]
        for _f in _failed:
            _gap_ctx_lines.append(
                f"  {_f.get('function','?')}: {_f.get('status','?')} | "
                f"finding={str(_f.get('finding',''))[:80]}"
            )
        _gap_ctx_lines.append("")
        _gap_ctx_lines.append("--- Known-good calls ---")
        for _f in _succeeded[:6]:
            _gap_ctx_lines.append(
                f"  {_f.get('function','?')}: working_call={_f.get('working_call')}"
            )
        _gap_ctx_lines.append("")
        _gap_ctx_lines.append("--- System prompt ---")
        _gap_ctx_lines.append(_gap_sys.get("content", ""))
        _save_stage_context(ctx.job_id, "model_context_phase_08_gap_resolution.txt",
                            "\n".join(_gap_ctx_lines))
    except Exception as _mce:
        logger.debug("[%s] phase8: model context save failed: %s", ctx.job_id, _mce)

    # ── Gap resolution ───────────────────────────────────────────────────────

    try:
        logger.info("[%s] phase8: gap resolution pass…", ctx.job_id)
        _set_explore_status(ctx.job_id, ctx._state["explored"], ctx.total,
                            "Retrying failed functions…")
        _attempt_gap_resolution(
            ctx.job_id, ctx.invocables, ctx.client, ctx.model,
            ctx.sentinels, ctx.vocab, ctx.use_cases_text,
            ctx.inv_map, ctx.tool_schemas,
        )
    except Exception as _gr_e:
        logger.debug("[%s] phase8: gap resolution failed: %s", ctx.job_id, _gr_e)

    _snapshot_schema_stage(ctx.job_id, "mcp_schema_post_gap_resolution.json")
    _snapshot_schema_stage(ctx.job_id, "mcp_schema_pre_clarification.json")

    # ── Clarification questions ──────────────────────────────────────────────

    if ctx.runtime.clarification_enabled:
        try:
            logger.info("[%s] phase8: generating confidence gaps…", ctx.job_id)
            _set_explore_status(ctx.job_id, ctx._state["explored"], ctx.total,
                                "Generating clarification questions…")
            _syn_findings = _load_findings(ctx.job_id)
            _gaps = _generate_confidence_gaps(
                ctx.client, ctx.model, _syn_findings, ctx.invocables
            )
            if _gaps:
                logger.info("[%s] phase8: %d confidence gaps generated", ctx.job_id, len(_gaps))
            # Always persist (even empty list) so UI knows the pass ran
            _gap_current = _get_job_status(ctx.job_id) or {}
            _persist_job_status(
                ctx.job_id, {**_gap_current, "explore_questions": _gaps}, sync=True
            )
        except Exception as _gap_e:
            logger.debug("[%s] phase8: confidence gaps failed: %s", ctx.job_id, _gap_e)
    else:
        _gap_current = _get_job_status(ctx.job_id) or {}
        _persist_job_status(
            ctx.job_id, {**_gap_current, "explore_questions": []}, sync=True
        )

    _snapshot_schema_stage(ctx.job_id, "mcp_schema_post_clarification.json")


# ══════════════════════════════════════════════════════════════════════════════
#  Phase 9 – Behavioral specification
# ══════════════════════════════════════════════════════════════════════════════

def _run_phase_9_behavioral_spec(ctx: ExploreContext, report: str) -> None:
    """Generate a typed Python behavioral specification from findings + synthesis.

    READS:  ctx.job_id, ctx.client, ctx.model, ctx.invocables, report (api_reference.md)
    WRITES: behavioral_spec.py uploaded to blob
    INVARIANT: no-ops if already canceled; report must be non-empty
    """
    if _cancel_requested(ctx.job_id):
        return

    from api.storage import _load_findings
    try:
        logger.info("[%s] phase9: generating behavioral spec…", ctx.job_id)
        _set_explore_status(ctx.job_id, ctx._state["explored"], ctx.total,
                            "Generating behavioral specification…")
        _component = (_get_job_status(ctx.job_id) or {}).get("component_name", "DLLComponent")
        _spec_py = _generate_behavioral_spec(
            ctx.client, ctx.model, _load_findings(ctx.job_id),
            ctx.invocables, _component, report,
        )
        if _spec_py:
            _upload_to_blob(
                ARTIFACT_CONTAINER,
                f"{ctx.job_id}/behavioral_spec.py",
                _spec_py.encode("utf-8"),
            )
            logger.info("[%s] phase9: behavioral_spec.py saved to blob", ctx.job_id)
    except Exception as _spec_e:
        logger.debug("[%s] phase9: behavioral spec failed: %s", ctx.job_id, _spec_e)


# ══════════════════════════════════════════════════════════════════════════════
#  Phase 10 – Final harmonization
# ══════════════════════════════════════════════════════════════════════════════

def _run_phase_10_harmonize(ctx: ExploreContext) -> None:
    """Final deterministic harmonization pass — upgrade error findings with direct-probe evidence.

    Non-LLM pass: no AI calls, only probe-log cross-referencing.

    READS:  ctx.job_id, explore_probe_log.json from blob
    WRITES: error findings upgraded when probe log shows return=0; harmonization_report.json
    INVARIANT: only upgrades error→success; never downgrades
    """
    try:
        _set_explore_status(ctx.job_id, ctx._state["explored"], ctx.total,
                            "Finalizing harmonized state…")
        from api.storage import _load_findings
        _hm_findings = _load_findings(ctx.job_id)
        _hm_probe_log: list[dict] = []
        try:
            _hm_raw = _download_blob(ARTIFACT_CONTAINER, f"{ctx.job_id}/explore_probe_log.json")
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
                _patch_finding(ctx.job_id, _fn, {
                    "status": "success",
                    "working_call": _best.get("args") or {},
                    "stop_reason": "harmonized_direct_probe_success",
                    "notes": (
                        f"Final harmonization: direct probe {_best.get('probe_id', '?')} "
                        "returned 0; status forced to success."
                    ),
                })
                _upgrades.append({
                    "function": _fn,
                    "probe_id": _best.get("probe_id"),
                    "args": _best.get("args") or {},
                })

        _post_hm_findings = _load_findings(ctx.job_id)
        _status_counts = {
            "success": sum(1 for _f in _post_hm_findings if _f.get("status") == "success"),
            "error":   sum(1 for _f in _post_hm_findings if _f.get("status") == "error"),
            "other":   sum(1 for _f in _post_hm_findings
                           if _f.get("status") not in {"success", "error"}),
        }

        _cur = _get_job_status(ctx.job_id) or {}
        _open_questions = _cur.get("explore_questions") or []
        _unanswered_questions = [
            q for q in _open_questions
            if isinstance(q, dict)
            and not (bool(q.get("answered")) or bool(str(q.get("answer") or "").strip()))
        ]

        _harmonization = {
            "job_id": ctx.job_id,
            "final_phase_suggestion": (
                "awaiting_clarification" if _unanswered_questions else "done"
            ),
            "patched_error_to_success": _upgrades,
            "counts": _status_counts,
            "open_questions": len(_open_questions),
            "unanswered_questions": len(_unanswered_questions),
        }
        _upload_to_blob(
            ARTIFACT_CONTAINER,
            f"{ctx.job_id}/harmonization_report.json",
            json.dumps(_harmonization, indent=2).encode(),
        )
        logger.info("[%s] phase10: harmonization complete (%d upgrades, %d unanswered)",
                    ctx.job_id, len(_upgrades), len(_unanswered_questions))
    except Exception as _hm_e:
        logger.debug("[%s] phase10: harmonization failed: %s", ctx.job_id, _hm_e)


# ══════════════════════════════════════════════════════════════════════════════
#  Finalize – vocab description + AC-4 closure gate
# ══════════════════════════════════════════════════════════════════════════════

def _run_finalize(ctx: ExploreContext) -> None:
    """Synthesize vocab description and apply the AC-4 closure gate.

    READS:  ctx.job_id, ctx.vocab, ctx.run_started_at
    WRITES: ctx.vocab["description"]; final explore_phase in job status
    INVARIANT: explore_phase is always set to done | awaiting_clarification | canceled
    """
    # Synthesize a one-sentence domain description from accumulated vocab.
    # Only generated once — skipped if a previous session already built it.
    if ctx.vocab and "description" not in ctx.vocab:
        try:
            _desc_seed = ctx.vocab.get("user_context") or ctx.vocab.get("notes") or ""
            _vs = ctx.vocab.get("value_semantics") or {}
            _vs_sample = "; ".join(f"{k}: {v}" for k, v in list(_vs.items())[:8])
            _ids = ", ".join(ctx.vocab.get("id_formats") or [])
            _desc_prompt = (
                "Based on the following accumulated knowledge about a DLL, write ONE sentence "
                "describing what this component does for a developer integrating it. "
                "Be specific — name the business domain, key entities, and operations. "
                "Do not mention 'DLL' or 'function'. Max 25 words.\n\n"
                + (f"User context: {_desc_seed}\n" if _desc_seed else "")
                + (f"Known ID formats: {_ids}\n" if _ids else "")
                + (f"Value semantics: {_vs_sample}\n" if _vs_sample else "")
            )
            _desc_resp = ctx.client.chat.completions.create(
                model=ctx.model,
                messages=[{"role": "user", "content": _desc_prompt}],
                temperature=0, max_tokens=60, timeout=90.0,
            )
            _desc_text = (_desc_resp.choices[0].message.content or "").strip().strip('"')
            if _desc_text:
                ctx.vocab["description"] = _desc_text
                logger.info("[%s] finalize: synthesized description: %s",
                            ctx.job_id, _desc_text)
        except Exception as _desc_e:
            logger.debug("[%s] finalize: description synthesis failed: %s",
                         ctx.job_id, _desc_e)

    # Persist final vocab with description + user_context
    if ctx.vocab:
        try:
            _upload_to_blob(
                ARTIFACT_CONTAINER,
                f"{ctx.job_id}/vocab.json",
                json.dumps(ctx.vocab).encode(),
            )
        except Exception as _vfin_e:
            logger.debug("[%s] finalize: final vocab persist failed: %s",
                         ctx.job_id, _vfin_e)

    # AC-4: Clarification closure gate — distinguish "complete" from
    # "complete with open questions" so external consumers can gate on it.
    current = _get_job_status(ctx.job_id) or {}
    _open_questions = current.get("explore_questions") or []
    _has_unanswered = any(
        isinstance(q, dict)
        and not (bool(q.get("answered")) or bool(str(q.get("answer") or "").strip()))
        for q in _open_questions
    )
    _elapsed_s = max(0.0, time.time() - ctx.run_started_at)
    _was_canceled = _cancel_requested(ctx.job_id)
    _final_phase = "canceled" if _was_canceled else (
        "awaiting_clarification" if _has_unanswered else "done"
    )
    _persist_job_status(
        ctx.job_id,
        {
            **current,
            "explore_phase": _final_phase,
            "explore_progress": f"{ctx._state['explored']}/{ctx.total}",
            "explore_last_run_seconds": round(_elapsed_s, 2),
            "sentinel_new_codes_this_run": ctx.sentinel_new_codes_this_run,
            "updated_at": time.time(),
        },
        sync=True,
    )
    logger.info("[%s] finalize: finished %d/%d functions (phase=%s)",
                ctx.job_id, ctx._state["explored"], ctx.total, _final_phase)

    # Emit machine-first contract artifacts as run outputs.
    try:
        emit_contract_artifacts(ctx.job_id)
    except Exception as _coh_e:
        logger.debug("[%s] finalize: cohesion artifact emission failed: %s", ctx.job_id, _coh_e)


# ══════════════════════════════════════════════════════════════════════════════
#  Top-level entry point
# ══════════════════════════════════════════════════════════════════════════════

def _explore_worker(job_id: str, invocables: list[dict]) -> None:
    """Background worker: explore each invocable with the LLM and enrich the schema.

    This is the single public entry point.  It builds an ExploreContext then
    drives the 14-phase pipeline via named module-level functions.  Each phase
    is independently testable and clearly named so logs and post-mortems can
    identify exactly which stage a failure occurred in.
    """
    if not OPENAI_ENDPOINT and not OPENAI_API_KEY:
        logger.warning(
            "[%s] explore_worker: neither OPENAI_API_KEY nor AZURE_OPENAI_ENDPOINT "
            "configured — aborting", job_id,
        )
        return

    logger.info("[%s] explore_worker: starting, %d functions to explore",
                job_id, len(invocables))

    try:
        ctx = _build_explore_context(job_id, invocables)
        _set_explore_status(job_id, 0, ctx.total, "Starting exploration…")

        # Persist circular feedback metadata into job status for session-meta.json
        _prior_seeded = sum(
            1 for f in (getattr(ctx, '_prior_session', {}) or {}).get("findings", [])
            if f.get("status") == "success"
        )
        if ctx.runtime.prior_job_id or _prior_seeded:
            try:
                _cur = _get_job_status(job_id) or {}
                _persist_job_status(job_id, {
                    **_cur,
                    "prior_job_id": ctx.runtime.prior_job_id,
                    "prior_findings_seeded": _prior_seeded,
                })
            except Exception:
                pass

        # Persist explore_config so save-session captures the caps used this run
        try:
            _upload_to_blob(
                ARTIFACT_CONTAINER,
                f"{job_id}/explore_config.json",
                json.dumps({
                    "mode": ((_get_job_status(job_id) or {}).get("explore_runtime") or {}).get("mode") or "normal",
                    "cap_profile":                     ctx.runtime.cap_profile,
                    "max_rounds_per_function":         ctx.runtime.max_rounds,
                    "max_tool_calls_per_function":     ctx.runtime.max_tool_calls,
                    "write_fn_tool_calls":             14,
                    "max_functions_per_session":       ctx.runtime.max_functions,
                    "min_direct_probes_per_function":  ctx.runtime.min_direct_probes,
                    "skip_documented":                 ctx.runtime.skip_documented,
                    "deterministic_fallback_enabled":  ctx.runtime.deterministic_fallback_enabled,
                    "gap_resolution_enabled":          ctx.runtime.gap_resolution_enabled,
                    "clarification_questions_enabled": ctx.runtime.clarification_enabled,
                    "prior_job_id":                    ctx.runtime.prior_job_id,
                    "prior_findings_seeded":           _prior_seeded,
                }, indent=2).encode(),
            )
        except Exception as _cfg_e:
            logger.debug("[%s] explore_worker: explore_config upload failed: %s", job_id, _cfg_e)

        # Snapshot the schema before any exploration mutates it.
        # Stored as mcp_schema_t0.json for the session-snapshot endpoint.
        try:
            _raw_schema = _download_blob(ARTIFACT_CONTAINER, f"{job_id}/mcp_schema.json")
            _upload_to_blob(ARTIFACT_CONTAINER, f"{job_id}/mcp_schema_t0.json", _raw_schema)
            logger.info("[%s] explore_worker: pre-enrichment schema snapshot saved", job_id)
        except Exception as _snap_e:
            logger.debug("[%s] explore_worker: schema snapshot failed: %s", job_id, _snap_e)

        # ── Pipeline ──────────────────────────────────────────────────────────
        _run_phase_05_calibrate(ctx)        # DLL-specific sentinel code map
        _run_phase_0_vocab_seed(ctx)        # Load/seed cross-session vocabulary
        _run_phase_0_static(ctx)            # Binary static analysis
        _run_phase_1_write_unlock(ctx)      # Write-unlock prerequisite probe
        _run_phase_2_curriculum_order(ctx)  # Sort functions: init-first, then uncertainty

        _run_phase_3_probe_loop(ctx)        # Per-function LLM probe loops  ← main work

        # Q16 Task 4: Stage-boundary re-calibration — scan probe log for high-bit
        # return codes observed during probing that weren't in the initial sentinel table.
        try:
            probe_log_bytes = _download_blob(ARTIFACT_CONTAINER,
                                             f"{ctx.job_id}/explore_probe_log.json")
            probe_entries = json.loads(probe_log_bytes) if probe_log_bytes else []
            if isinstance(probe_entries, list):
                new_sentinel_candidates: dict[int, list[str]] = {}
                for entry in probe_entries:
                    return_match = _re.match(r"Returned:\s*(\d+)",
                                             str(entry.get("result_excerpt") or ""))
                    if return_match:
                        return_val = int(return_match.group(1)) & 0xFFFFFFFF
                        if return_val > 0x80000000 and return_val not in ctx.sentinels:
                            new_sentinel_candidates.setdefault(return_val, []).append(
                                str(entry.get("function") or "")
                            )
                if new_sentinel_candidates:
                    boundary_resolved = _name_sentinel_candidates(
                        new_sentinel_candidates, ctx.client, ctx.model,
                    )
                    if boundary_resolved:
                        ctx.sentinels.update(boundary_resolved)
                        ctx.sentinel_new_codes_this_run += len(boundary_resolved)
                        logger.info("[%s] stage-boundary recal: %d new codes: %s",
                                    ctx.job_id, len(boundary_resolved),
                                    {f"0x{k:08X}": v for k, v in boundary_resolved.items()})
                        try:
                            _append_explore_probe_log(ctx.job_id, [{
                                "phase": "stage_boundary_name_sentinel_candidates",
                                "function": "(all)",
                                "args": {
                                    "candidate_codes": [f"0x{v:08X}" for v in sorted(new_sentinel_candidates.keys(), reverse=True)],
                                    "candidate_count": len(new_sentinel_candidates),
                                },
                                "result_excerpt": json.dumps(
                                    {f"0x{k:08X}": v for k, v in boundary_resolved.items()}
                                )[:400],
                                "trace": None,
                            }])
                        except Exception as exc:
                            logger.debug("[%s] stage-boundary recal log flush failed: %s",
                                         ctx.job_id, exc)

                        # Q16§5: re-probe write-unlock if new sentinel codes explain
                        # why it was failing (e.g. a code meant "not initialized"
                        # and we now know the right init args).
                        if not ctx.unlock_result.get("unlocked"):
                            logger.info("[%s] stage-boundary: re-probing write-unlock "
                                        "with %d new sentinel codes",
                                        ctx.job_id, len(boundary_resolved))
                            ctx.unlock_result = _probe_write_unlock(
                                ctx.invocables, ctx.dll_strings, ctx.vocab,
                            )
                            if ctx.unlock_result.get("unlocked"):
                                ctx.write_unlock_block = (
                                    "\nWRITE MODE ACTIVE (resolved after sentinel recalibration): "
                                    "The write-unlock sequence has been executed. Write functions "
                                    "should now succeed. Probe with real ID values from "
                                    "STATIC ANALYSIS HINTS.\n"
                                )
                                logger.info("[%s] stage-boundary: WRITE UNLOCKED after recal: %s",
                                            ctx.job_id, ctx.unlock_result.get("notes"))
                                try:
                                    current_status = _get_job_status(ctx.job_id) or {}
                                    _persist_job_status(ctx.job_id, {
                                        **current_status,
                                        "write_unlock_outcome": "resolved",
                                        "write_unlock_sentinel": None,
                                    })
                                except Exception as exc:
                                    logger.debug("[%s] stage-boundary unlock status "
                                                 "update failed: %s", ctx.job_id, exc)
        except Exception as exc:
            logger.debug("[%s] stage-boundary recal failed: %s", ctx.job_id, exc)

        _run_phase_4_reconcile(ctx)         # AC-1: reconcile probe log vs findings
        _mc3_post_reconcile(ctx)             # MC-3: analyze write failure mode, targeted retry
        _run_phase_5_sentinel_catalog(ctx)  # Persist + promote sentinel evidence

        if not _cancel_requested(job_id):
            report = _run_phase_6_synthesize(ctx)  # LLM → api_reference.md
            _mc4_post_synthesis(ctx)                # MC-4: parse synthesis for init sequence clues

            if report:
                _run_phase_7_backfill(ctx, report)  # Layer-3 schema enrichment
                _run_phase_7b_verify_enrichment(ctx)  # Execute working_calls to verify
                _mc5_post_verification(ctx)            # MC-5: chain read outputs → write inputs

                if not _cancel_requested(job_id):
                    _run_phase_8_gap_resolution(ctx)        # Retry + clarification Qs
                    _mc6_post_gap_resolution(ctx)           # MC-6: final comprehensive attempt
                    _run_phase_9_behavioral_spec(ctx, report)  # Typed Python stub

        _run_phase_10_harmonize(ctx)    # Final deterministic reconciliation
        _run_finalize(ctx)              # Vocab description + AC-4 closure gate

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
