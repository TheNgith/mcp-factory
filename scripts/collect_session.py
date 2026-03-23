from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REQUIRED_CONTRACT_FILES = (
    "session-meta.json",
    "stage-index.json",
    "transition-index.json",
    "cohesion-report.json",
)
SUPPORTED_VERSION = {"1.0"}


@dataclass
class ContractResult:
    valid: bool
    missing: list[str]
    parse_errors: list[str]
    hard_fail: bool
    parsed: dict[str, Any]


@dataclass
class CollectResult:
    contract: ContractResult
    dashboard_row: dict[str, Any]
    session_save_meta: dict[str, Any]


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _validate_contract(session_dir: Path, mode: str) -> ContractResult:
    missing: list[str] = []
    parse_errors: list[str] = []
    parsed: dict[str, Any] = {}

    for name in REQUIRED_CONTRACT_FILES:
        path = session_dir / name
        if not path.exists():
            missing.append(name)
            continue
        try:
            parsed[name] = _read_json(path)
        except Exception:
            parse_errors.append(f"{name}: invalid JSON")

    # Fail-closed on unsupported contract version for known versioned files.
    if "stage-index.json" in parsed:
        version = str((parsed["stage-index.json"] or {}).get("version") or "")
        if version and version not in SUPPORTED_VERSION:
            parse_errors.append(f"stage-index.json: unsupported version '{version}'")
    if "transition-index.json" in parsed:
        version = str((parsed["transition-index.json"] or {}).get("version") or "")
        if version and version not in SUPPORTED_VERSION:
            parse_errors.append(f"transition-index.json: unsupported version '{version}'")
    if "cohesion-report.json" in parsed:
        version = str((parsed["cohesion-report.json"] or {}).get("version") or "")
        if version and version not in SUPPORTED_VERSION:
            parse_errors.append(f"cohesion-report.json: unsupported version '{version}'")

    hard_fail = False
    if "cohesion-report.json" in parsed:
        try:
            hard_fail = bool(parsed["cohesion-report.json"]["gates"]["hard_fail"])
        except Exception:
            hard_fail = False

    valid = not missing and not parse_errors
    if mode == "compatibility":
        # Compatibility mode keeps validity semantics but allows caller to continue.
        return ContractResult(valid=valid, missing=missing, parse_errors=parse_errors, hard_fail=hard_fail, parsed=parsed)

    return ContractResult(valid=valid, missing=missing, parse_errors=parse_errors, hard_fail=hard_fail, parsed=parsed)


def _write_summary(human_dir: Path, contract: ContractResult, mode: str) -> None:
    lines = [
        "# Contract Compliance Summary",
        "",
        f"Mode: {mode}",
        f"Valid: {contract.valid}",
        f"Hard Fail: {contract.hard_fail}",
        "",
        "## Missing Required Files",
    ]
    if contract.missing:
        lines.extend(f"- {x}" for x in contract.missing)
    else:
        lines.append("(none)")

    lines.extend(["", "## Parse Errors"])
    if contract.parse_errors:
        lines.extend(f"- {x}" for x in contract.parse_errors)
    else:
        lines.append("(none)")

    (human_dir / "summary.md").write_text("\n".join(lines), encoding="utf-8")


def _build_dashboard_row(
    contract: ContractResult,
    date: str,
    folder: str,
    job_id: str,
    component: str,
    commit: str,
    note: str,
    saved_at: str,
) -> dict[str, Any]:
    cohesion = contract.parsed.get("cohesion-report.json") if contract.parsed else None
    session_meta = contract.parsed.get("session-meta.json") if contract.parsed else None

    transition_fail_count = None
    stage_fail_count = None
    pipeline_verdict = "UNKNOWN"
    failed_transitions: list[str] = []
    failed_stages: list[str] = []

    if isinstance(cohesion, dict):
        totals = cohesion.get("totals") or {}
        transition_fail_count = totals.get("transition_fail")
        stage_fail_count = totals.get("stage_fail")
        failed_transitions = list(cohesion.get("failed_transitions") or [])
        failed_stages = list(cohesion.get("failed_stages") or [])

        summary = cohesion.get("summary") or {}
        if summary.get("pipeline_verdict"):
            pipeline_verdict = str(summary.get("pipeline_verdict"))
        elif bool((cohesion.get("gates") or {}).get("hard_fail")):
            pipeline_verdict = "DEGRADED"

    session_meta_fields = {
        "prompt_profile_id": None,
        "layer": None,
        "ablation_variable": None,
        "ablation_value": None,
        "run_set_id": None,
        "coordinator_cycle": None,
        "playbook_step": None,
        # Q16: sentinel + write-unlock emission fields
        "write_unlock_outcome": None,
        "write_unlock_sentinel": None,
        "write_unlock_resolved_at": None,
        "sentinel_new_codes_this_run": 0,
        # Function outcome counts (used by merger, orchestrator, coordinator)
        "functions_total": 0,
        "functions_success": 0,
        "functions_error": 0,
        # Circular feedback fields
        "prior_job_id": None,
        "prior_findings_seeded": 0,
        # Phase 7b verification counts
        "verification_verified": 0,
        "verification_inferred": 0,
        "verification_error": 0,
    }
    if isinstance(session_meta, dict):
        for key in session_meta_fields:
            val = session_meta.get(key)
            session_meta_fields[key] = val if val is not None else session_meta_fields[key]

    return {
        "date": date,
        "folder": folder,
        "job_id": job_id,
        "component": component,
        "commit": commit,
        "note": note,
        "saved_at": saved_at,
        "contract_valid": bool(contract.valid),
        "hard_fail": bool(contract.hard_fail),
        "pipeline_verdict": pipeline_verdict,
        "transition_fail_count": transition_fail_count,
        "stage_fail_count": stage_fail_count,
        "failed_transitions": failed_transitions,
        "failed_stages": failed_stages,
        **session_meta_fields,
    }


def _build_session_save_meta(
    contract: ContractResult,
    mode: str,
    capture_quality: str,
    job_id: str,
    folder: str,
    commit: str,
    commit_msg: str,
    note: str,
    saved_at: str,
) -> dict[str, Any]:
    return {
        "job_id": job_id,
        "folder": folder,
        "commit": commit,
        "commit_msg": commit_msg,
        "note": note,
        "saved_at": saved_at,
        "compatibility_mode": mode == "compatibility",
        "contract_valid": bool(contract.valid),
        "missing_contract_files": list(contract.missing),
        "parse_errors": list(contract.parse_errors),
        "hard_fail": bool(contract.hard_fail),
        "capture_quality": capture_quality,
    }


def _json_default(value: Any) -> Any:
    return value.__dict__ if hasattr(value, "__dict__") else str(value)


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect and validate session contract artifacts")
    parser.add_argument("--session-dir", required=True)
    parser.add_argument("--mode", choices=("strict", "compatibility"), default="strict")
    parser.add_argument("--enforce-hard-fail", action="store_true")
    parser.add_argument("--job-id", default="")
    parser.add_argument("--folder", default="")
    parser.add_argument("--component", default="unknown")
    parser.add_argument("--commit", default="unknown")
    parser.add_argument("--commit-msg", default="")
    parser.add_argument("--note", default="")
    parser.add_argument("--date", default="")
    parser.add_argument("--saved-at", default="")
    args = parser.parse_args()

    session_dir = Path(args.session_dir)
    human_dir = session_dir / "human"
    human_dir.mkdir(parents=True, exist_ok=True)

    contract = _validate_contract(session_dir, args.mode)
    _write_summary(human_dir, contract, args.mode)

    capture_quality = "complete" if contract.valid else "degraded"
    dashboard_row = _build_dashboard_row(
        contract=contract,
        date=args.date,
        folder=args.folder,
        job_id=args.job_id,
        component=args.component,
        commit=args.commit,
        note=args.note,
        saved_at=args.saved_at,
    )
    session_save_meta = _build_session_save_meta(
        contract=contract,
        mode=args.mode,
        capture_quality=capture_quality,
        job_id=args.job_id,
        folder=args.folder,
        commit=args.commit,
        commit_msg=args.commit_msg,
        note=args.note,
        saved_at=args.saved_at,
    )

    (human_dir / "dashboard-row.json").write_text(
        json.dumps(dashboard_row, indent=2),
        encoding="utf-8",
    )
    (human_dir / "session-save-meta.json").write_text(
        json.dumps(session_save_meta, indent=2),
        encoding="utf-8",
    )

    result = CollectResult(contract=contract, dashboard_row=dashboard_row, session_save_meta=session_save_meta)
    (human_dir / "collect-session-result.json").write_text(
        json.dumps(result, indent=2, default=_json_default),
        encoding="utf-8",
    )

    if not contract.valid and args.mode == "strict":
        return 1
    if args.enforce_hard_fail and contract.valid and contract.hard_fail:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
