from __future__ import annotations

import hashlib
import json
import re
import subprocess
import time
from typing import Any

from api.config import ARTIFACT_CONTAINER
from api.storage import _download_blob, _get_job_status, _load_findings, _upload_to_blob


_TRANSITION_SEVERITY: dict[str, str] = {
    "T-01": "medium",
    "T-02": "high",
    "T-03": "medium",
    "T-04": "medium",
    "T-05": "low",
    "T-06": "high",
    "T-07": "medium",
    "T-08": "high",
    "T-09": "medium",
    "T-10": "medium",
    "T-11": "high",
    "T-12": "medium",
    "T-13": "high",
    "T-14": "high",
    "T-15": "medium",
    "T-16": "high",
    # Q16: sentinel calibration + write-unlock probe outcome transitions
    "T-17": "low",
    "T-18": "low",
}


def _try_blob(job_id: str, blob_name: str) -> bytes | None:
    try:
        return _download_blob(ARTIFACT_CONTAINER, f"{job_id}/{blob_name}")
    except Exception:
        return None


def _try_json(job_id: str, blob_name: str) -> Any | None:
    raw = _try_blob(job_id, blob_name)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def _as_text(data: bytes | None) -> str:
    if not data:
        return ""
    return data.decode("utf-8", errors="replace")


def _hash(data: bytes | None) -> str:
    return hashlib.sha256(data or b"").hexdigest()


def _latest_status_by_function(findings: list[dict]) -> dict[str, dict]:
    latest: dict[str, dict] = {}
    for f in findings:
        fn = str(f.get("function") or "").strip()
        if fn:
            latest[fn] = f
    return latest


def _id_patterns_from_hints(hints: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r"[A-Z]{2,6}-[\w-]+", hints or "")))


def _first_system_block(transcript: str) -> str:
    if not transcript:
        return ""
    return transcript[:4000]


def _extract_turn0_system_context(transcript: str) -> str:
    """Best-effort extraction of turn-0 system context from chat transcript text.

    The chat transcript format has evolved over time, so we support multiple
    lightweight patterns and fall back to the first transcript block.
    """
    if not transcript:
        return ""

    # Common tagged formats from transcript dumps.
    tagged_patterns = [
        r"(?is)^\s*\[system\]\s*(.+?)(?:\n\s*\[[a-z_]+\]|\Z)",
        r"(?is)^\s*role\s*:\s*system\s*\n(.+?)(?:\n\s*role\s*:|\Z)",
        r"(?is)^\s*SYSTEM\s*:\s*(.+?)(?:\n\s*(?:USER|ASSISTANT|TOOL)\s*:|\Z)",
    ]
    for pat in tagged_patterns:
        m = re.search(pat, transcript)
        if m:
            return str(m.group(1) or "").strip()

    return _first_system_block(transcript).strip()


def _ev(stage_slug: str, filename: str) -> str:
    return f"evidence/{stage_slug}/{filename}"


def _has_any_blob(job_id: str, candidates: list[str]) -> bool:
    return any(_try_blob(job_id, c) for c in candidates)


def _canonical_or_legacy(canonical: str, legacy: str, exists: bool) -> str:
    return canonical if exists else legacy


def _build_stage_index(job_id: str, run_started: int, run_finished: int, gap_triggered: bool) -> dict:
    stage_defs: list[tuple[str, str, list[tuple[str, list[str]]], dict[str, Any], bool]] = [
        (
            "S-00",
            "setup",
            [
                (_ev("stage-00-setup", "schema-t0.json"), ["mcp_schema_t0.json", "stage-00-setup/schema.json"]),
                (_ev("stage-00-setup", "invocables-map.json"), ["invocables_map.json", "stage-05-finalization/invocables_map.json"]),
            ],
            {
                "schema_generated": bool(_try_blob(job_id, "mcp_schema_t0.json")),
                "invocables_map_generated": bool(_try_blob(job_id, "invocables_map.json")),
            },
            False,
        ),
        (
            "S-01",
            "pre-probe",
            [
                (_ev("stage-01-pre-probe", "vocab.json"), ["vocab.json", "stage-05-finalization/vocab.json"]),
                (_ev("stage-01-pre-probe", "static-analysis.json"), ["static_analysis.json", "stage-00-setup/static_analysis.json"]),
                (_ev("stage-01-pre-probe", "sentinel-calibration.json"), ["sentinel_calibration.json", "stage-00-setup/sentinel_calibration.json"]),
            ],
            {
                "vocab_seeded": bool(_try_blob(job_id, "vocab.json")),
                "static_analysis_ran": bool(_try_blob(job_id, "static_analysis.json")),
                "sentinel_calibration_ran": bool(_try_blob(job_id, "sentinel_calibration.json")),
            },
            False,
        ),
        (
            "S-02",
            "probe-loop",
            [
                (_ev("stage-02-probe-loop", "probe-log.json"), ["explore_probe_log.json", "stage-01-probe-loop/explore_probe_log.json"]),
                (_ev("stage-02-probe-loop", "findings.json"), ["findings.json", "stage-02-synthesis/findings.json"]),
            ],
            {
                "probe_loop_ran": bool(_try_blob(job_id, "explore_probe_log.json")),
                "findings_written": bool(_try_blob(job_id, "findings.json")),
            },
            False,
        ),
        (
            "S-03",
            "post-probe",
            [
                (_ev("stage-03-post-probe", "sentinel-catalog.json"), ["sentinel_catalog.json", "stage-05-finalization/sentinel_catalog.json"]),
                (_ev("stage-03-post-probe", "harmonization-report.json"), ["harmonization_report.json", "stage-05-finalization/harmonization_report.json"]),
            ],
            {
                "sentinel_catalog_written": bool(_try_blob(job_id, "sentinel_catalog.json")),
                "harmonization_written": bool(_try_blob(job_id, "harmonization_report.json")),
            },
            False,
        ),
        (
            "S-04",
            "synthesis",
            [
                (_ev("stage-04-synthesis", "api-reference.md"), ["api_reference.md", "stage-02-synthesis/api_reference.md"]),
                (_ev("stage-04-synthesis", "stage-context.txt"), ["model_context_phase_06_synthesis.txt", "stage-02-synthesis/model_context.txt"]),
            ],
            {
                "synthesis_ran": bool(_try_blob(job_id, "api_reference.md")),
            },
            False,
        ),
        (
            "S-05",
            "backfill",
            [
                (_ev("stage-05-backfill", "backfill-report.json"), ["backfill_result.json"]),
                (_ev("stage-05-backfill", "schema-post-backfill.json"), ["mcp_schema_post_discovery.json", "stage-02-synthesis/schema-after.json"]),
            ],
            {
                "backfill_ran": bool(_try_blob(job_id, "backfill_result.json")),
            },
            False,
        ),
        (
            "S-06",
            "gap-resolution",
            [
                (_ev("stage-06-gap-resolution", "gap-resolution-log.json"), ["gap_resolution_log.json", "stage-03-gap-resolution/gap_resolution_log.json"]),
            ],
            {
                "gap_resolution_ran": bool(_try_blob(job_id, "gap_resolution_log.json")),
                "answer_gaps_triggered": gap_triggered,
            },
            not gap_triggered,
        ),
        (
            "S-07",
            "finalize",
            [
                (_ev("stage-07-finalize", "behavioral-spec.py"), ["behavioral_spec.py", "stage-05-finalization/behavioral_spec.py"]),
                (_ev("stage-07-finalize", "final-vocab.json"), ["vocab.json", "stage-05-finalization/vocab.json"]),
                (_ev("stage-07-finalize", "final-status.json"), ["status.json"]),
            ],
            {
                "final_vocab_written": bool(_try_blob(job_id, "vocab.json")),
                "status_written": bool(_try_blob(job_id, "status.json")),
            },
            False,
        ),
    ]

    out_stages: list[dict] = []
    for sid, name, required, checks, allow_skip in stage_defs:
        missing = [canonical for canonical, candidates in required if not _has_any_blob(job_id, candidates)]
        artifacts = [canonical for canonical, _candidates in required]
        if allow_skip and missing:
            status = "skipped"
            errors: list[str] = []
        else:
            status = "completed" if not missing else "failed"
            errors = [f"missing artifact: {m}" for m in missing]
        out_stages.append(
            {
                "id": sid,
                "name": name,
                "status": status,
                "started_at": run_started,
                "finished_at": run_finished,
                "artifacts": artifacts,
                "checks": checks,
                "errors": errors,
            }
        )

    return {"version": "1.0", "stages": out_stages}


def emit_contract_artifacts(job_id: str) -> dict[str, dict]:
    status = _get_job_status(job_id) or {}
    now = int(time.time())
    run_started = int(float(status.get("explore_started_at") or status.get("created_at") or now))
    run_finished = int(float(status.get("updated_at") or now))

    final_phase = str(status.get("explore_phase") or "error")
    if final_phase not in {"done", "awaiting_clarification", "canceled", "error"}:
        final_phase = "error"

    hints = str(status.get("hints") or "")
    findings = _load_findings(job_id)
    latest_findings = _latest_status_by_function(findings)
    vocab = _try_json(job_id, "vocab.json") or {}
    static_analysis = _try_json(job_id, "static_analysis.json") or {}
    probe_log = _try_json(job_id, "explore_probe_log.json") or []
    sentinels = _try_json(job_id, "sentinel_calibration.json") or {}
    inv_map = _try_json(job_id, "invocables_map.json") or {}
    backfill_result = _try_json(job_id, "backfill_result.json") or {}
    schema_t0 = _try_blob(job_id, "mcp_schema_t0.json")
    schema_now = _try_blob(job_id, "mcp_schema.json")
    schema_pre_mini = _try_blob(job_id, "mcp_schema_pre_mini_session.json")
    schema_post_mini = _try_blob(job_id, "mcp_schema_post_mini_session.json")
    api_reference = _as_text(_try_blob(job_id, "api_reference.md"))
    probe_context = _as_text(_try_blob(job_id, "model_context_phase_01_probe_loop.txt"))
    chat_transcript = _as_text(_try_blob(job_id, "chat_transcript.txt"))
    chat_system_turn0_blob = _as_text(_try_blob(job_id, "chat_system_context_turn0.txt"))
    mini_transcript = _as_text(_try_blob(job_id, "mini_session_transcript.txt"))
    schema_evolution = _try_json(job_id, "schema_evolution.json") or []

    chat_system_turn0 = chat_system_turn0_blob.strip() if chat_system_turn0_blob else ""
    if not chat_system_turn0 and chat_transcript:
        chat_system_turn0 = _extract_turn0_system_context(chat_transcript)
    if chat_system_turn0 and not chat_system_turn0_blob:
        _upload_to_blob(
            ARTIFACT_CONTAINER,
            f"{job_id}/chat_system_context_turn0.txt",
            chat_system_turn0.encode("utf-8"),
        )

    gap_answers = (vocab.get("gap_answers") or {}) if isinstance(vocab, dict) else {}
    answered_gap_functions = sorted(
        [k for k, v in gap_answers.items() if k != "general" and str(v or "").strip()]
    )
    gap_triggered = bool(answered_gap_functions) or ("DOMAIN ANSWER" in hints) or bool(mini_transcript)

    id_formats = vocab.get("id_formats") or []
    if not id_formats:
        id_formats = _id_patterns_from_hints(hints)
    if isinstance(id_formats, str):
        id_formats = [id_formats]

    finding_functions = sorted(k for k in latest_findings.keys())
    finding_success_functions = sorted(
        fn for fn, row in latest_findings.items() if str(row.get("status")) == "success"
    )

    transitions: list[dict] = []

    def add_transition(
        tid: str,
        name: str,
        status_value: str,
        reason: str,
        producer_stage: str,
        consumer_stage: str,
        producer: list[str],
        consumer: list[str],
        effect: list[str],
    ) -> None:
        transitions.append(
            {
                "id": tid,
                "name": name,
                "status": status_value,
                "tier": "NOW",
                "severity": _TRANSITION_SEVERITY[tid],
                "producer_stage": producer_stage,
                "consumer_stage": consumer_stage,
                "producer_evidence": producer,
                "consumer_evidence": consumer,
                "effect_evidence": effect,
                "reason": reason,
            }
        )

    hint_ids = _id_patterns_from_hints(hints)
    stale_hint = bool(hint_ids) and not any(str(h) in [str(v) for v in id_formats] for h in hint_ids)
    e_hints = _canonical_or_legacy(
        _ev("stage-01-pre-probe", "hints.txt"),
        "hints.txt",
        bool(_try_blob(job_id, "hints.txt")),
    )
    e_vocab = _ev("stage-01-pre-probe", "vocab.json")
    e_probe_ctx = _ev("stage-02-probe-loop", "stage-context.txt")
    e_probe_log = _ev("stage-02-probe-loop", "probe-log.json")
    e_static = _ev("stage-01-pre-probe", "static-analysis.json")
    e_probe_user = _ev("stage-02-probe-loop", "probe-user-message-sample.txt")
    e_findings = _ev("stage-02-probe-loop", "findings.json")
    e_synth_ctx = _ev("stage-04-synthesis", "stage-context.txt")
    e_api_ref = _ev("stage-04-synthesis", "api-reference.md")
    e_backfill = _ev("stage-05-backfill", "backfill-report.json")
    e_schema_post_backfill = _ev("stage-05-backfill", "schema-post-backfill.json")
    e_inv_map = _ev("stage-00-setup", "invocables-map.json")
    e_mini_tx = _ev("stage-06-gap-resolution", "mini-session-transcript.txt")
    e_gap_log = _ev("stage-06-gap-resolution", "gap-resolution-log.json")
    e_schema_pre_mini = _ev("stage-06-gap-resolution", "schema-pre-mini-session.json")
    e_schema_post_mini = _ev("stage-06-gap-resolution", "schema-post-mini-session.json")
    e_chat_tx = "diagnostics/transcript.txt"
    e_chat_system = "diagnostics/chat-system-context-turn0.txt"
    e_exec_trace = "diagnostics/executor-trace.json"
    e_schema_t0 = _ev("stage-00-setup", "schema-t0.json")
    e_schema_evolution = _ev("stage-07-finalize", "schema-evolution.json")
    e_schema_final = _ev("stage-07-finalize", "schema-final.json")
    e_sentinel_calibration = _ev("stage-01-pre-probe", "sentinel-calibration.json")
    e_write_unlock = _ev("stage-01-pre-probe", "write-unlock-probe.json")

    if id_formats and not stale_hint:
        add_transition(
            "T-01", "hints_to_vocab", "pass", "id_formats present and aligned with current hints",
            "S-01", "S-02", [e_hints], [e_vocab], [e_probe_ctx],
        )
    elif id_formats and stale_hint:
        add_transition(
            "T-01", "hints_to_vocab", "warn", "id_formats present but may be stale vs current hints",
            "S-01", "S-02", [e_hints], [e_vocab], [e_probe_ctx],
        )
    else:
        add_transition(
            "T-01", "hints_to_vocab", "fail", "id_formats missing",
            "S-01", "S-02", [e_hints], [e_vocab], [e_probe_ctx],
        )

    hint_hexes = {h.lower() for h in re.findall(r"0x[0-9a-fA-F]{8}", hints or "")}
    sentinel_keys = {str(k).lower() for k in (sentinels.keys() if isinstance(sentinels, dict) else [])}
    missing_hexes = sorted(h for h in hint_hexes if h not in sentinel_keys)
    add_transition(
        "T-02",
        "hint_error_codes_to_sentinels",
        "pass" if not missing_hexes else "fail",
        "all hint error codes merged into sentinel calibration"
        if not missing_hexes
        else f"missing hint codes in sentinel map: {', '.join(missing_hexes)}",
        "S-01",
        "S-01",
        [e_hints],
        [e_sentinel_calibration],
        [e_vocab],
    )

    id_hits = [x for x in id_formats if str(x) and str(x) in probe_context]
    if not id_formats:
        t03_status = "partial"
        t03_reason = "id_formats unavailable"
    elif id_hits:
        t03_status = "pass"
        t03_reason = "id formats present in probe context"
    else:
        t03_status = "warn"
        t03_reason = "could not confirm id formats in probe context"
    add_transition(
        "T-03", "vocab_to_probe_prompt", t03_status, t03_reason,
        "S-01", "S-02", [e_vocab], [e_probe_ctx], [e_probe_log],
    )

    sh_len = int((static_analysis.get("static_hints_block_length") or 0)) if isinstance(static_analysis, dict) else 0
    if sh_len <= 0:
        t04_status = "fail"
        t04_reason = "static_hints_block_length is zero"
    else:
        user_prompt_sample = _as_text(_try_blob(job_id, "probe_user_message_sample.txt"))
        if user_prompt_sample and user_prompt_sample.strip():
            t04_status = "pass"
            t04_reason = "static hints block and probe user prompt sample present"
        else:
            t04_status = "warn"
            t04_reason = "static hints block present but probe user prompt sample not yet instrumented"
    add_transition(
        "T-04", "static_analysis_to_probe_prompt", t04_status, t04_reason,
        "S-01", "S-02", [e_static], [e_probe_user], [e_probe_log],
    )

    static_ids = set()
    if isinstance(static_analysis, dict):
        static_ids = set(((static_analysis.get("binary_strings") or {}).get("ids_found") or []))
    fallback_args: list[str] = []
    for row in probe_log if isinstance(probe_log, list) else []:
        if row.get("phase") == "deterministic_fallback" and isinstance(row.get("args"), dict):
            fallback_args.extend(str(v) for v in row.get("args", {}).values())
    t05_hit = bool(static_ids) and any(v in static_ids for v in fallback_args)
    t05_status = "pass" if t05_hit else ("warn" if static_ids else "partial")
    t05_reason = "fallback args include static IDs" if t05_hit else "could not prove static IDs propagated into fallback args"
    add_transition(
        "T-05", "static_ids_to_fallback_args", t05_status, t05_reason,
        "S-01", "S-02", [e_static], [e_probe_log], [e_findings],
    )

    missing_in_synth = [fn for fn in finding_success_functions if fn not in api_reference]
    add_transition(
        "T-06",
        "probe_findings_to_synthesis",
        "pass" if not missing_in_synth else "fail",
        "all successful finding functions reflected in synthesis"
        if not missing_in_synth
        else f"missing in api_reference.md: {', '.join(missing_in_synth[:8])}",
        "S-02",
        "S-04",
        [e_findings],
        [e_api_ref],
        [e_synth_ctx],
    )

    if backfill_result:
        add_transition(
            "T-07",
            "synthesis_to_backfill_patches",
            "pass",
            f"backfill_result.json emitted; patches_applied={int(backfill_result.get('patches_applied') or 0)}",
            "S-04",
            "S-05",
            [e_api_ref],
            [e_backfill],
            [e_schema_post_backfill],
        )
    else:
        add_transition(
            "T-07",
            "synthesis_to_backfill_patches",
            "partial",
            "backfill_result.json missing",
            "S-04",
            "S-05",
            [e_api_ref],
            [e_backfill],
            [e_schema_post_backfill],
        )

    enrich_functions = {
        str(r.get("function"))
        for r in (probe_log if isinstance(probe_log, list) else [])
        if str(r.get("tool") or "") == "enrich_invocable" and str(r.get("function") or "")
    }
    t08_missing = []
    for fn in sorted(enrich_functions):
        inv = inv_map.get(fn) if isinstance(inv_map, dict) else None
        params = (inv or {}).get("parameters") or []
        if not any(isinstance(p, dict) and not re.match(r"^param_\d+$", str(p.get("name") or "")) for p in params):
            t08_missing.append(fn)
    add_transition(
        "T-08",
        "enrich_call_to_schema",
        "pass" if not t08_missing else "fail",
        "enriched functions have semantic parameter names"
        if not t08_missing
        else f"semantic names missing after enrich for: {', '.join(t08_missing[:8])}",
        "S-02",
        "S-05",
        [e_probe_log],
        [e_inv_map],
        [e_schema_post_backfill],
    )

    auto_enrich_functions = {
        str(r.get("function"))
        for r in (probe_log if isinstance(probe_log, list) else [])
        if str(r.get("phase") or "") == "d12_autoenrich" and str(r.get("function") or "")
    }
    if auto_enrich_functions:
        weak_desc = []
        for fn in sorted(auto_enrich_functions):
            inv = inv_map.get(fn) if isinstance(inv_map, dict) else None
            desc = str((inv or {}).get("description") or (inv or {}).get("doc") or "")
            if not desc or "ghidra" in desc.lower():
                weak_desc.append(fn)
        t09_status = "pass" if not weak_desc else "warn"
        t09_reason = "auto-enriched descriptions applied" if not weak_desc else "some auto-enriched functions still have generic descriptions"
    else:
        t09_status = "not_applicable"
        t09_reason = "no d12_autoenrich events in this run"
    add_transition(
        "T-09", "auto_enrich_to_schema", t09_status, t09_reason,
        "S-02", "S-05", [e_probe_log], [e_inv_map], [e_schema_post_backfill],
    )

    all_param_names: list[str] = []
    if isinstance(inv_map, dict):
        for inv in inv_map.values():
            for p in (inv.get("parameters") or []):
                if isinstance(p, dict):
                    all_param_names.append(str(p.get("name") or ""))
    all_generic = bool(all_param_names) and all(re.match(r"^param_\d+$", n or "") for n in all_param_names)
    t10_status = "fail" if all_generic else "pass"
    t10_reason = "parameter names show semantic vocab transfer" if not all_generic else "all parameter names remain generic param_N"
    add_transition(
        "T-10", "vocab_to_param_names", t10_status, t10_reason,
        "S-01", "S-05", [e_vocab], [e_inv_map], [e_schema_post_backfill],
    )

    semantic_tokens: list[str] = []
    if isinstance(vocab, dict):
        semantic_tokens.extend(str(x) for x in (vocab.get("id_formats") or [])[:3])
        semantic_tokens.extend(
            str(k) for k in list((vocab.get("value_semantics") or {}).keys())[:3]
        )
    has_semantic_signal = any(tok and tok in api_reference for tok in semantic_tokens)
    if missing_in_synth:
        t11_status = "fail"
        t11_reason = "synthesis misses functions from findings"
    elif semantic_tokens and not has_semantic_signal:
        t11_status = "warn"
        t11_reason = "findings reached synthesis but vocab semantic signals are weak"
    else:
        t11_status = "pass"
        t11_reason = "synthesis reflects findings and vocabulary semantics"
    add_transition(
        "T-11", "enriched_knowledge_to_synthesis_input", t11_status, t11_reason,
        "S-02", "S-04", [e_findings, e_vocab], [e_synth_ctx], [e_api_ref],
    )

    if not gap_triggered:
        t12_status = "not_applicable"
        t12_reason = "answer-gaps flow not triggered"
    elif answered_gap_functions and mini_transcript:
        missing_fn = [fn for fn in answered_gap_functions if fn not in mini_transcript]
        t12_status = "pass" if not missing_fn else "fail"
        t12_reason = "all answered functions appear in mini-session transcript" if not missing_fn else f"missing in mini-session transcript: {', '.join(missing_fn)}"
    else:
        t12_status = "partial"
        t12_reason = "answer-gaps triggered but transcript evidence incomplete"
    add_transition(
        "T-12", "gap_answers_to_mini_session_context", t12_status, t12_reason,
        "S-06", "S-06", [e_vocab], [e_mini_tx], [e_gap_log],
    )

    if not gap_triggered:
        t13_status = "not_applicable"
        t13_reason = "answer-gaps flow not triggered"
    elif schema_pre_mini and schema_post_mini:
        changed = _hash(schema_pre_mini) != _hash(schema_post_mini)
        t13_status = "pass" if changed else "fail"
        t13_reason = "schema changed across mini-session" if changed else "schema unchanged across mini-session"
    else:
        t13_status = "partial"
        t13_reason = "mini-session schema snapshots incomplete"
    add_transition(
        "T-13", "mini_session_to_schema_evolution", t13_status, t13_reason,
        "S-06", "S-06", [e_mini_tx], [e_schema_pre_mini], [e_schema_post_mini],
    )

    if not chat_system_turn0 and not chat_transcript:
        t14_status = "not_applicable"
        t14_reason = "chat context artifacts not present in this run (explore-only session)"
    else:
        _ctx = chat_system_turn0 or _first_system_block(chat_transcript)
        fn_hits = [fn for fn in finding_functions[:20] if fn in _ctx]
        t14_status = "pass" if fn_hits else "warn"
        t14_reason = "finding functions are present in chat context" if fn_hits else "could not confirm findings injection in chat context"
    add_transition(
        "T-14", "findings_to_chat_prompt", t14_status, t14_reason,
        "S-02", "S-07", [e_findings], [e_chat_system], [e_exec_trace],
    )

    chat_probe = (chat_system_turn0 or chat_transcript).lower()
    vocab_markers = ["id formats", "error codes", "value semantics"]
    if not chat_system_turn0 and not chat_transcript:
        t15_status = "not_applicable"
        t15_reason = "chat context artifacts not present in this run (explore-only session)"
    elif any(m in chat_probe for m in vocab_markers):
        t15_status = "pass"
        t15_reason = "vocab sections detected in chat context"
    else:
        t15_status = "warn"
        t15_reason = "could not confirm vocab block markers in chat context"
    add_transition(
        "T-15", "vocab_to_chat_prompt", t15_status, t15_reason,
        "S-01", "S-07", [e_vocab], [e_chat_system], [e_exec_trace],
    )

    if schema_evolution and isinstance(schema_evolution, list):
        changed_entries = [e for e in schema_evolution if bool((e or {}).get("changed"))]
        t16_changed = bool(changed_entries)
        t16_reason = "schema_evolution.json includes changed=true checkpoints" if t16_changed else "schema_evolution.json has no changed checkpoints"
    else:
        t16_changed = bool(schema_t0 and schema_now and _hash(schema_t0) != _hash(schema_now))
        t16_reason = "schema changed between t0 and final snapshot" if t16_changed else "schema unchanged between t0 and final snapshot"
    add_transition(
        "T-16", "schema_evolution_present", "pass" if t16_changed else "fail", t16_reason,
        "S-00", "S-07", [e_schema_t0], [e_schema_evolution], [e_schema_final],
    )

    # T-17: sentinel_calibration_outcome — did Phase 0.5 calibration name DLL-specific codes?
    sentinel_default_hexes = {"0xffffffff", "0xfffffffe", "0xfffffffd", "0xfffffffc", "0xfffffffb"}
    if not isinstance(sentinels, dict) or not sentinels:
        t17_status = "fail"
        t17_reason = "sentinel_calibration.json missing or empty"
    else:
        non_default_codes = [k for k in sentinels if str(k).lower() not in sentinel_default_hexes]
        sentinel_new_count = int(status.get("sentinel_new_codes_this_run") or 0)
        if non_default_codes or sentinel_new_count > 0:
            t17_status = "pass"
            t17_reason = (
                f"calibration produced {len(non_default_codes)} DLL-specific code(s)"
                + (f"; {sentinel_new_count} new code(s) named this run" if sentinel_new_count else "")
            )
        else:
            t17_status = "warn"
            t17_reason = "sentinel calibration present but returned only default fallback codes"
    add_transition(
        "T-17", "sentinel_calibration_outcome", t17_status, t17_reason,
        "S-01", "S-02", [e_sentinel_calibration], [e_probe_ctx], [e_probe_log],
    )

    # T-18: write_unlock_probe_outcome — did the write-mode unlock probe succeed?
    write_unlock_outcome = str(status.get("write_unlock_outcome") or "")
    if write_unlock_outcome == "resolved":
        t18_status = "pass"
        t18_reason = "write-unlock probe succeeded; write-mode functions are accessible"
    elif write_unlock_outcome == "blocked":
        sentinel_label = str(status.get("write_unlock_sentinel") or "unknown")
        t18_status = "warn"
        t18_reason = f"write-unlock probe could not unlock write mode; blocking sentinel={sentinel_label}"
    elif write_unlock_outcome == "not_attempted":
        t18_status = "not_applicable"
        t18_reason = "no write-style functions detected in this DLL; write-unlock probe skipped"
    else:
        t18_status = "partial"
        t18_reason = "write_unlock_outcome not recorded (probe may not have run or predates Q16)"
    add_transition(
        "T-18", "write_unlock_probe_outcome", t18_status, t18_reason,
        "S-01", "S-02", [e_write_unlock], [e_probe_log], [e_probe_log],
    )

    transition_index = {"version": "1.0", "transitions": transitions}

    stage_index = _build_stage_index(job_id, run_started, run_finished, gap_triggered)

    stage_pass = sum(1 for s in stage_index["stages"] if s["status"] == "completed")
    stage_fail = sum(1 for s in stage_index["stages"] if s["status"] == "failed")
    t_counts = {
        "transition_pass": 0,
        "transition_fail": 0,
        "transition_warn": 0,
        "transition_partial": 0,
        "transition_na": 0,
    }
    for t in transitions:
        st = t["status"]
        if st == "pass":
            t_counts["transition_pass"] += 1
        elif st == "fail":
            t_counts["transition_fail"] += 1
        elif st == "warn":
            t_counts["transition_warn"] += 1
        elif st == "partial":
            t_counts["transition_partial"] += 1
        elif st == "not_applicable":
            t_counts["transition_na"] += 1

    high_fail = [t["id"] for t in transitions if t["status"] == "fail" and t["severity"] == "high"]
    failed_transitions = [t["id"] for t in transitions if t["status"] == "fail"]
    failed_stages = [s["id"] for s in stage_index["stages"] if s["status"] == "failed"]

    cohesion_report = {
        "version": "1.0",
        "run": {
            "job_id": job_id,
            "component": status.get("component_name", "unknown"),
            "final_phase": final_phase,
        },
        "totals": {
            "stage_pass": stage_pass,
            "stage_fail": stage_fail,
            **t_counts,
        },
        "gates": {
            "hard_fail": bool(high_fail),
            "reasons": [f"high-severity transition failed: {tid}" for tid in high_fail],
        },
        "failed_transitions": failed_transitions,
        "failed_stages": failed_stages,
    }

    profile = "default"
    explore_cfg = _try_json(job_id, "explore_config.json") or {}
    if isinstance(explore_cfg, dict):
        profile = str(explore_cfg.get("cap_profile") or explore_cfg.get("mode") or "default")

    code_commit = "unknown"
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
            timeout=2.0,
        )
        code_commit = out.decode("utf-8", errors="replace").strip() or "unknown"
    except Exception:
        pass

    functions_success = sum(1 for f in latest_findings.values() if f.get("status") == "success")
    functions_error = sum(1 for f in latest_findings.values() if f.get("status") != "success")

    session_meta = {
        "job_id": job_id,
        "component": status.get("component_name", "unknown"),
        "run_started_at": run_started,
        "run_finished_at": run_finished,
        "code_commit": code_commit,
        "profile": profile,
        "final_phase": final_phase,
        "functions_total": len(latest_findings),
        "functions_success": functions_success,
        "functions_error": functions_error,
        # Q15: ablation tagging fields (pass-through from job submission)
        "prompt_profile_id": status.get("prompt_profile_id"),
        "layer": status.get("layer"),
        "ablation_variable": status.get("ablation_variable"),
        "ablation_value": status.get("ablation_value"),
        "run_set_id": status.get("run_set_id"),
        "coordinator_cycle": status.get("coordinator_cycle"),
        "playbook_step": status.get("playbook_step"),
        # Q16: sentinel + write-unlock emission fields
        "write_unlock_outcome": status.get("write_unlock_outcome"),
        "write_unlock_sentinel": status.get("write_unlock_sentinel"),
        "sentinel_new_codes_this_run": status.get("sentinel_new_codes_this_run", 0),
    }

    _upload_to_blob(
        ARTIFACT_CONTAINER,
        f"{job_id}/session-meta.json",
        json.dumps(session_meta, indent=2).encode("utf-8"),
    )
    _upload_to_blob(
        ARTIFACT_CONTAINER,
        f"{job_id}/stage-index.json",
        json.dumps(stage_index, indent=2).encode("utf-8"),
    )
    _upload_to_blob(
        ARTIFACT_CONTAINER,
        f"{job_id}/transition-index.json",
        json.dumps(transition_index, indent=2).encode("utf-8"),
    )
    _upload_to_blob(
        ARTIFACT_CONTAINER,
        f"{job_id}/cohesion-report.json",
        json.dumps(cohesion_report, indent=2).encode("utf-8"),
    )

    return {
        "session_meta": session_meta,
        "stage_index": stage_index,
        "transition_index": transition_index,
        "cohesion_report": cohesion_report,
    }
