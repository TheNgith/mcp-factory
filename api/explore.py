"""api/explore.py – Autonomous reverse-engineering exploration worker.

Pipeline overview
-----------------
_explore_worker(job_id, invocables) drives 11 named phases in sequence,
threading all shared state through an ExploreContext dataclass so data-flow
between phases is explicit and each phase can be tested in isolation.

  Phase 0.5  _run_phase_05_calibrate      – calibrate DLL-specific sentinel codes
  Phase 0a   _run_phase_0_vocab_seed      – load/seed the cross-session vocab table
  Phase 0b   _run_phase_0_static          – binary static analysis (G-4/G-7/G-8/G-9)
  Phase 1    _run_phase_1_write_unlock    – probe write-unlock prerequisite sequence
  Phase 2    _run_phase_2_curriculum_order – order: init-first, then by uncertainty
  Phase 3    _run_phase_3_probe_loop      – per-function LLM probe loops (_explore_one)
  Phase 4    _run_phase_4_reconcile       – AC-1 probe-log reconciliation
  Phase 5    _run_phase_5_sentinel_catalog – persist + promote sentinel evidence
  Phase 6    _run_phase_6_synthesize      – LLM → api_reference.md
  Phase 7    _run_phase_7_backfill        – enrich schema from synthesis doc
  Phase 8    _run_phase_8_gap_resolution  – retry failed functions + clarification Qs
  Phase 9    _run_phase_9_behavioral_spec – typed Python behavioral specification
  Phase 10   _run_phase_10_harmonize      – final deterministic harmonization pass
  Finalize   _run_finalize                – vocab description + AC-4 closure gate
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
    _WRITE_FN_RE,
    _WRITE_RETRY_BUDGET_BY_CLASS,
    _build_ranked_fallback_probe_args,
    _build_tool_schemas,
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

logger = logging.getLogger("mcp_factory.api")

# Regex for identifying init/startup functions — used in Phase 2 ordering and
# in _explore_one for Q-5 DLL state resets between function probes.
_INIT_RE = _re.compile(r"(init(ializ)?|startup|start|setup|open|login|logon|connect)", _re.I)
# Version-query functions return a packed UINT (e.g. 0x20401 = 2.3.1), not 0.
# Any positive non-sentinel result is SUCCESS for these function names.
_VERSION_FN_RE = _re.compile(r"get(version|version_?string|build|revision|release)", _re.I)


# ══════════════════════════════════════════════════════════════════════════════
#  Shared helpers
# ══════════════════════════════════════════════════════════════════════════════

def _cancel_requested(job_id: str) -> bool:
    """Return True if a cancellation request is present for this job."""
    return bool((_get_job_status(job_id) or {}).get("explore_cancel_requested"))


def _build_explore_context(job_id: str, invocables: list[dict]) -> ExploreContext:
    """Build a fully-initialised ExploreContext from a job ID and invocable list."""
    from api.storage import _load_findings

    _job_runtime = (_get_job_status(job_id) or {}).get("explore_runtime") or {}
    runtime = ExploreRuntime.from_job_runtime(_job_runtime)

    total = min(len(invocables), runtime.max_functions)
    invocables = invocables[:total]

    inv_map: dict[str, dict] = {inv["name"]: inv for inv in invocables}

    # COH-1: Merge invocables into the registry so enrich_invocable /
    # _patch_invocable can resolve function names.  Use merge (not replace)
    # so that a refine/gap run with a target subset doesn't evict the full
    # set of functions already registered — run_generate must see all of
    # them to produce a complete schema.
    _merge_invocables(job_id, invocables)

    # Inject synthetic built-in tools that the LLM calls to record discoveries
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

    _run_started_at = float((_get_job_status(job_id) or {}).get("explore_started_at") or time.time())

    return ExploreContext(
        job_id=job_id,
        runtime=runtime,
        client=client,
        model=model,
        run_started_at=_run_started_at,
        invocables=invocables,
        inv_map=inv_map,
        tool_schemas=tool_schemas,
        total=total,
        sentinels=dict(_SENTINEL_DEFAULTS),
        already_explored=already_explored,
    )


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
    HANDOFF: write_unlock_block is non-empty iff unlock succeeded
    """
    try:
        logger.info("[%s] phase1: write-unlock probe…", ctx.job_id)
        _set_explore_status(ctx.job_id, 0, ctx.total, "Testing write-mode unlock…")
        ctx.unlock_result = _probe_write_unlock(ctx.invocables, ctx.dll_strings)
        if ctx.unlock_result.get("unlocked"):
            ctx.write_unlock_block = (
                "\nWRITE MODE ACTIVE: The write-unlock sequence has already been executed. "
                "Write functions (any function whose name implies state changes — Process, Update, "
                "Set, Create, Delete, Transfer, Submit, Send, Redeem, Unlock) should now succeed. "
                "Probe them with real ID values from STATIC ANALYSIS HINTS.\n"
            )
            logger.info("[%s] phase1: UNLOCKED: %s", ctx.job_id, ctx.unlock_result["notes"])
        else:
            logger.info("[%s] phase1: not unlocked: %s", ctx.job_id,
                        ctx.unlock_result.get("notes", ""))

        # Q16: emit write_unlock_outcome + write_unlock_sentinel to session-meta
        _write_fns = [
            inv for inv in ctx.invocables
            if _re.search(r"(pay|redeem|unlock|process|write|commit|transfer|debit|credit)",
                          inv["name"], _re.I)
        ]
        if not _write_fns:
            _wuo = "not_attempted"
            _wus: str | None = None
        elif ctx.unlock_result.get("unlocked"):
            _wuo = "resolved"
            _wus = None
        else:
            _wuo = "blocked"
            # Report the highest-priority write-denied sentinel code we know
            _wus = None
            for _wc in (0xFFFFFFFB, 0xFFFFFFFF, 0xFFFFFFFE, 0xFFFFFFFD, 0xFFFFFFFC):
                if _wc in ctx.sentinels:
                    _wus = f"0x{_wc:08X}"
                    break
        try:
            _wucurrent = _get_job_status(ctx.job_id) or {}
            _persist_job_status(ctx.job_id, {
                **_wucurrent,
                "write_unlock_outcome": _wuo,
                "write_unlock_sentinel": _wus,
            })
        except Exception as _wue:
            logger.debug("[%s] phase1: write-unlock status emit failed: %s", ctx.job_id, _wue)
    except Exception as _we:
        logger.debug("[%s] phase1: write-unlock probe failed: %s", ctx.job_id, _we)


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

    # Build a focused conversation for this function
    prior = _load_findings(ctx.job_id)
    sys_msg = _build_explore_system_message(
        ctx.invocables, prior, sentinels=ctx.sentinels,
        vocab=_vocab_snap, use_cases=ctx.use_cases_text,
    )
    _is_write_fn = bool(_WRITE_FN_RE.search(fn_name))
    _is_version_fn = bool(_VERSION_FN_RE.search(fn_name))
    conversation = [
        sys_msg,
        {
            "role": "user",
            "content": (
                f"Explore the function '{fn_name}'. "
                "Call it with safe probe values, observe the result, "
                "then call enrich_invocable and record_finding with what you learned. "
                "Be brief — one summary sentence after you're done."
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
        if _fn_tool_call_count >= ctx.runtime.max_tool_calls:
            logger.info("[%s] explore_worker: %s hit tool-call cap (%d), moving on",
                        ctx.job_id, fn_name, ctx.runtime.max_tool_calls)
            break

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
        except Exception as exc:
            _exc_type = type(exc).__name__
            _exc_status = getattr(exc, "status_code", None)
            _exc_body = str(getattr(exc, "body", None) or "")[:200]
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
            if _fn_tool_call_count >= ctx.runtime.max_tool_calls:
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
            if _fn_tool_call_count >= ctx.runtime.max_tool_calls:
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
        elif _fn_tool_call_count >= ctx.runtime.max_tool_calls:
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
                if _fn_tool_call_count >= ctx.runtime.max_tool_calls
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

    if ctx.runtime.concurrency > 1:
        logger.info("[%s] phase3: running %d functions with concurrency=%d",
                    ctx.job_id, len(ctx.invocables), ctx.runtime.concurrency)
        with _TPE(max_workers=ctx.runtime.concurrency) as _pool:
            list(_pool.map(lambda inv: _explore_one(inv, ctx), ctx.invocables))
    else:
        for inv in ctx.invocables:
            if _cancel_requested(ctx.job_id):
                break
            _explore_one(inv, ctx)


# ══════════════════════════════════════════════════════════════════════════════
#  Phase 4 – AC-1 probe-log reconciliation
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
                    "max_functions_per_session":       ctx.runtime.max_functions,
                    "min_direct_probes_per_function":  ctx.runtime.min_direct_probes,
                    "skip_documented":                 ctx.runtime.skip_documented,
                    "deterministic_fallback_enabled":  ctx.runtime.deterministic_fallback_enabled,
                    "gap_resolution_enabled":          ctx.runtime.gap_resolution_enabled,
                    "clarification_questions_enabled": ctx.runtime.clarification_enabled,
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
            _probe_raw_bytes = _download_blob(ARTIFACT_CONTAINER,
                                              f"{ctx.job_id}/explore_probe_log.json")
            _probe_entries = json.loads(_probe_raw_bytes) if _probe_raw_bytes else []
            if isinstance(_probe_entries, list):
                _new_cands: dict[int, list[str]] = {}
                for _pe in _probe_entries:
                    _ret_m2 = _re.match(r"Returned:\s*(\d+)",
                                        str(_pe.get("result_excerpt") or ""))
                    if _ret_m2:
                        _pv = int(_ret_m2.group(1)) & 0xFFFFFFFF
                        if _pv > 0x80000000 and _pv not in ctx.sentinels:
                            _new_cands.setdefault(_pv, []).append(
                                str(_pe.get("function") or "")
                            )
                if _new_cands:
                    _boundary_resolved = _name_sentinel_candidates(_new_cands, ctx.client, ctx.model)
                    if _boundary_resolved:
                        ctx.sentinels.update(_boundary_resolved)
                        ctx.sentinel_new_codes_this_run += len(_boundary_resolved)
                        logger.info("[%s] stage-boundary recal: %d new codes: %s",
                                    ctx.job_id, len(_boundary_resolved),
                                    {f"0x{k:08X}": v for k, v in _boundary_resolved.items()})
        except Exception as _rce:
            logger.debug("[%s] stage-boundary recal failed: %s", ctx.job_id, _rce)

        _run_phase_4_reconcile(ctx)         # AC-1: reconcile probe log vs findings
        _run_phase_5_sentinel_catalog(ctx)  # Persist + promote sentinel evidence

        if not _cancel_requested(job_id):
            report = _run_phase_6_synthesize(ctx)  # LLM → api_reference.md

            if report:
                _run_phase_7_backfill(ctx, report)  # Layer-3 schema enrichment

                if not _cancel_requested(job_id):
                    _run_phase_8_gap_resolution(ctx)        # Retry + clarification Qs
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
