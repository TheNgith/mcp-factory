from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TARGET_TRANSITIONS: tuple[str, ...] = ("T-04", "T-05", "T-14", "T-15")
PASS_STATUS = "pass"


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_json_if_exists(path: Path) -> Any | None:
    if not path.exists():
        return None
    try:
        return _read_json(path)
    except Exception:
        return None


def _load_transition_index(session_dir: Path, collect_result: dict[str, Any]) -> dict[str, Any]:
    # Prefer canonical top-level artifact first.
    top_level = _read_json_if_exists(session_dir / "transition-index.json")
    if isinstance(top_level, dict) and isinstance(top_level.get("transitions"), list):
        return top_level

    # Compatibility fallback: transition-index embedded in collect-session parsed payload.
    parsed = ((collect_result or {}).get("contract") or {}).get("parsed") or {}
    parsed_transition = parsed.get("transition-index.json")
    if isinstance(parsed_transition, dict) and isinstance(parsed_transition.get("transitions"), list):
        return parsed_transition

    # Legacy save-session snapshots may keep it under evidence/stage-07-finalize.
    evidence_transition = _read_json_if_exists(session_dir / "evidence" / "stage-07-finalize" / "transition-index.json")
    if isinstance(evidence_transition, dict) and isinstance(evidence_transition.get("transitions"), list):
        return evidence_transition

    return {}


def evaluate_session(
    session_dir: Path,
    transition_ids: tuple[str, ...] = TARGET_TRANSITIONS,
    expected_status: str = PASS_STATUS,
    require_contract_valid: bool = True,
    require_capture_quality_complete: bool = True,
) -> dict[str, Any]:
    collect_result = _read_json_if_exists(session_dir / "human" / "collect-session-result.json") or {}
    transition_index = _load_transition_index(session_dir, collect_result)

    transitions = (transition_index or {}).get("transitions") or []
    by_id: dict[str, dict[str, Any]] = {
        str(row.get("id") or ""): row for row in transitions if isinstance(row, dict)
    }

    contract = (collect_result or {}).get("contract") or {}
    session_meta = (collect_result or {}).get("session_save_meta") or {}

    contract_valid = bool(contract.get("valid"))
    capture_quality = str(session_meta.get("capture_quality") or "unknown")

    missing_transition_ids: list[str] = []
    bad_status_ids: list[str] = []
    transition_results: dict[str, dict[str, str]] = {}

    for tid in transition_ids:
        row = by_id.get(tid)
        if not row:
            missing_transition_ids.append(tid)
            transition_results[tid] = {"status": "missing", "reason": "transition not found in transition-index"}
            continue

        status = str(row.get("status") or "unknown")
        reason = str(row.get("reason") or "")
        transition_results[tid] = {"status": status, "reason": reason}
        # "not_applicable" means the transition doesn't apply to this run type
        # (e.g. T-14/T-15 for explore-only runs). Treat it as a neutral pass.
        if status != expected_status and status != "not_applicable":
            bad_status_ids.append(tid)

    reasons: list[str] = []
    if require_contract_valid and not contract_valid:
        reasons.append("contract.invalid")
    if require_capture_quality_complete and capture_quality != "complete":
        reasons.append(f"capture_quality.{capture_quality}")
    if missing_transition_ids:
        reasons.append(f"missing_transitions:{','.join(missing_transition_ids)}")
    if bad_status_ids:
        details = ",".join(f"{tid}={transition_results[tid]['status']}" for tid in bad_status_ids)
        reasons.append(f"non_pass_transitions:{details}")

    return {
        "session_dir": str(session_dir),
        "contract_valid": contract_valid,
        "capture_quality": capture_quality,
        "required_transition_ids": list(transition_ids),
        "expected_status": expected_status,
        "transitions": transition_results,
        "missing_transition_ids": missing_transition_ids,
        "bad_status_ids": bad_status_ids,
        "pass": len(reasons) == 0,
        "reasons": reasons,
    }


def build_ab_readiness(
    leg_a: dict[str, Any],
    leg_b: dict[str, Any],
    deterministic: bool,
    require_determinism: bool = True,
) -> dict[str, Any]:
    reasons: list[str] = []
    if not leg_a.get("pass"):
        reasons.append("leg_a_failed")
    if not leg_b.get("pass"):
        reasons.append("leg_b_failed")
    if require_determinism and not deterministic:
        reasons.append("ab_not_deterministic")

    return {
        "created_at": _now_utc(),
        "type": "transition_readiness_ab",
        "targets": list(TARGET_TRANSITIONS),
        "require_determinism": require_determinism,
        "deterministic": deterministic,
        "leg_a": leg_a,
        "leg_b": leg_b,
        "pass": len(reasons) == 0,
        "reasons": reasons,
    }


def build_batch_readiness(
    per_leg: dict[str, dict[str, Any]],
    failed_legs: list[str] | None = None,
) -> dict[str, Any]:
    failed_legs = failed_legs or []
    reasons: list[str] = []
    failed_readiness_legs = sorted([leg for leg, row in per_leg.items() if not row.get("pass")])

    if failed_legs:
        reasons.append(f"runner_failures:{','.join(sorted(failed_legs))}")
    if failed_readiness_legs:
        reasons.append(f"readiness_failures:{','.join(failed_readiness_legs)}")

    return {
        "created_at": _now_utc(),
        "type": "transition_readiness_batch",
        "targets": list(TARGET_TRANSITIONS),
        "legs": per_leg,
        "runner_failed_legs": sorted(failed_legs),
        "readiness_failed_legs": failed_readiness_legs,
        "pass": len(reasons) == 0,
        "reasons": reasons,
    }


def write_readiness_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def write_readiness_markdown(path: Path, payload: dict[str, Any]) -> None:
    lines = [
        "# Transition Readiness",
        "",
        f"- Created: {payload.get('created_at', '')}",
        f"- Type: {payload.get('type', 'unknown')}",
        f"- Pass: {payload.get('pass')}",
    ]

    if payload.get("targets"):
        lines.append(f"- Targets: {', '.join(payload['targets'])}")

    if payload.get("reasons"):
        lines.append(f"- Reasons: {', '.join(payload['reasons'])}")

    if payload.get("type") == "transition_readiness_ab":
        lines.extend([
            "",
            "## A/B",
            "",
            f"- Deterministic: {payload.get('deterministic')}",
            f"- Leg A pass: {bool((payload.get('leg_a') or {}).get('pass'))}",
            f"- Leg B pass: {bool((payload.get('leg_b') or {}).get('pass'))}",
        ])
    elif payload.get("type") == "transition_readiness_batch":
        lines.extend([
            "",
            "## Batch",
            "",
            f"- Runner failed legs: {', '.join(payload.get('runner_failed_legs') or []) or '(none)'}",
            f"- Readiness failed legs: {', '.join(payload.get('readiness_failed_legs') or []) or '(none)'}",
        ])

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def compact_summary_line(payload: dict[str, Any]) -> str:
    verdict = "PASS" if payload.get("pass") else "FAIL"
    reasons = payload.get("reasons") or []
    reason_text = "none" if not reasons else "; ".join(reasons)
    return f"Transition readiness: {verdict} | reasons={reason_text}"


# ---------------------------------------------------------------------------
# Phase-gated discovery satisfaction
# ---------------------------------------------------------------------------
# The pipeline has multiple phases: discover → gap-resolution → report.
# Rather than treating the whole run as a single pass/fail, we can gate
# progression: only advance to gap-resolution if discovery is "satisfied",
# and only advance to report if gap-resolution is "satisfied".
#
# Discovery satisfaction criteria:
#   - finding_count >= min_findings (default 5): we learned something
#   - resolved_fraction >= min_resolved_fraction (default 0.5): >50% of
#     explored functions produced a finding (not just errors/unresolved)
#   - gap_count <= max_gaps (default None = no cap): known-unknowns are bounded
#
# These thresholds are intentionally conservative defaults. Tune up as the
# pipeline matures.

_DISCOVERY_MIN_FINDINGS = 5
_DISCOVERY_MIN_RESOLVED_FRACTION = 0.5
_DISCOVERY_MAX_GAPS: int | None = None  # None = no cap


def evaluate_discovery_satisfaction(
    session_dir: Path,
    min_findings: int = _DISCOVERY_MIN_FINDINGS,
    min_resolved_fraction: float = _DISCOVERY_MIN_RESOLVED_FRACTION,
    max_gaps: int | None = _DISCOVERY_MAX_GAPS,
) -> dict[str, Any]:
    """Evaluate whether the discovery phase produced enough signal to advance.

    Reads session-meta.json (or collect-session-result.json for legacy runs).
    Returns a dict with keys: pass, finding_count, gap_count, resolved_fraction,
    total_functions, reasons.
    """
    session_meta: dict[str, Any] = {}

    meta_path = session_dir / "session-meta.json"
    if meta_path.exists():
        try:
            session_meta = _read_json(meta_path)
        except Exception:
            pass

    if not session_meta:
        collect_result = _read_json_if_exists(session_dir / "human" / "collect-session-result.json") or {}
        session_meta = (collect_result.get("session_save_meta") or {})

    finding_count = int(session_meta.get("finding_count") or 0)
    gap_count = int(session_meta.get("gap_count") or 0)

    # total_functions: try explore_phase fields or ab-compare if available
    total_functions: int | None = None
    ab_compare_path = session_dir / "ab-compare.json"
    if ab_compare_path.exists():
        try:
            ab = _read_json(ab_compare_path)
            # Each leg reports success/warn/error counts
            for leg_key in ("leg_a", "leg_b"):
                leg = (ab or {}).get(leg_key) or {}
                s = int(leg.get("success") or 0)
                w = int(leg.get("warn") or 0)
                e = int(leg.get("error") or 0)
                t = s + w + e
                if t > 0:
                    total_functions = t
                    break
        except Exception:
            pass

    resolved_fraction: float | None = None
    if total_functions and total_functions > 0:
        resolved_fraction = finding_count / total_functions

    reasons: list[str] = []
    if finding_count < min_findings:
        reasons.append(f"finding_count.{finding_count}<{min_findings}")
    if resolved_fraction is not None and resolved_fraction < min_resolved_fraction:
        pct = round(resolved_fraction * 100)
        min_pct = round(min_resolved_fraction * 100)
        reasons.append(f"resolved_fraction.{pct}%<{min_pct}%")
    if max_gaps is not None and gap_count > max_gaps:
        reasons.append(f"gap_count.{gap_count}>{max_gaps}")

    return {
        "session_dir": str(session_dir),
        "finding_count": finding_count,
        "gap_count": gap_count,
        "total_functions": total_functions,
        "resolved_fraction": round(resolved_fraction, 3) if resolved_fraction is not None else None,
        "thresholds": {
            "min_findings": min_findings,
            "min_resolved_fraction": min_resolved_fraction,
            "max_gaps": max_gaps,
        },
        "pass": len(reasons) == 0,
        "reasons": reasons,
    }