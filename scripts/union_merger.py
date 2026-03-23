from __future__ import annotations

import argparse
import copy
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


FINDINGS_CANDIDATES = (
    # New orchestrator-dispatched single-leg layout
    ("evidence", "stage-07-finalize", "findings.json"),
    ("evidence", "stage-02-probe-loop", "findings.json"),
    ("artifacts", "findings.json"),
    ("findings.json",),
    # A/B parallel run layout (leg-a subfolder)
    ("leg-a", "evidence", "stage-07-finalize", "findings.json"),
    ("leg-a", "evidence", "stage-02-probe-loop", "findings.json"),
    ("leg-a", "artifacts", "findings.json"),
    ("leg-a", "findings.json"),
)

RESERVED_FIELDS = {
    "function_name",
    "function",
    "status",
    "selection_reason",
    "source_run_id",
    "supplemental_evidence",
}


def _get_function_name(finding: dict[str, Any]) -> str:
    """Return the function name, handling both 'function_name' and 'function' keys."""
    return str(finding.get("function_name") or finding.get("function") or "").strip()


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> Any:
    text = path.read_text(encoding="utf-8-sig")
    return json.loads(text)


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, dict, tuple, set)):
        return len(value) == 0
    return False


def _find_findings_path(session_dir: Path) -> Path | None:
    for relative in FINDINGS_CANDIDATES:
        candidate = session_dir.joinpath(*relative)
        if candidate.exists():
            return candidate
    return None


def _load_sessions(session_dirs: list[Path]) -> tuple[list[dict[str, Any]], str | None]:
    loaded: list[dict[str, Any]] = []
    for session_dir in session_dirs:
        findings_path = _find_findings_path(session_dir)
        if findings_path is None:
            return [], f"Missing findings.json in session: {session_dir}"

        try:
            findings_payload = _read_json(findings_path)
        except Exception as exc:
            return [], f"Unreadable findings.json in session {session_dir}: {exc}"

        if not isinstance(findings_payload, list):
            return [], f"Invalid findings payload (expected list) in session: {session_dir}"

        run_id = session_dir.name
        findings: list[dict[str, Any]] = []
        for item in findings_payload:
            if not isinstance(item, dict):
                continue
            row = copy.deepcopy(item)
            row["source_run_id"] = run_id
            findings.append(row)

        success_count = 0
        for dashboard_candidate in (
            session_dir / "human" / "dashboard-row.json",
            session_dir / "leg-a" / "human" / "dashboard-row.json",
        ):
            if dashboard_candidate.exists():
                try:
                    dashboard = _read_json(dashboard_candidate)
                    if isinstance(dashboard, dict):
                        success_count = int(dashboard.get("functions_success") or 0)
                except Exception:
                    success_count = 0
                break
        if success_count <= 0:
            success_count = sum(1 for x in findings if str(x.get("status") or "").lower() == "success")

        loaded.append(
            {
                "session_dir": str(session_dir),
                "run_id": run_id,
                "findings": findings,
                "success_count": success_count,
            }
        )
    return loaded, None


def _iter_error_codes(finding: dict[str, Any]) -> set[str]:
    codes: set[str] = set()

    top_code = finding.get("error_code")
    if top_code is not None and str(top_code).strip():
        codes.add(str(top_code).strip())

    probe_lists = [
        finding.get("probe_attempts"),
        finding.get("probe_results"),
        finding.get("attempts"),
        finding.get("results"),
    ]
    for probe_list in probe_lists:
        if not isinstance(probe_list, list):
            continue
        for attempt in probe_list:
            if not isinstance(attempt, dict):
                continue
            code = attempt.get("error_code")
            if code is not None and str(code).strip():
                codes.add(str(code).strip())
    return codes


def _confidence_score(finding: dict[str, Any]) -> float:
    """Normalize confidence to a float. Handles numeric and string ('high'/'medium'/'low') values."""
    raw = finding.get("confidence")
    if raw is None:
        return 0.0
    if isinstance(raw, (int, float)):
        return float(raw)
    mapping = {"high": 1.0, "medium": 0.5, "low": 0.0}
    return mapping.get(str(raw).lower().strip(), 0.0)


def _merge_success_group(group: list[dict[str, Any]]) -> dict[str, Any]:
    sorted_success = sorted(group, key=_confidence_score, reverse=True)
    primary = copy.deepcopy(sorted_success[0])
    supplemental: list[dict[str, Any]] = []

    for other in sorted_success[1:]:
        for key, value in other.items():
            if key in RESERVED_FIELDS:
                continue
            if key == "args_used":
                if not isinstance(value, list):
                    continue
                existing = primary.get("args_used")
                if not isinstance(existing, list):
                    existing = []
                extras = [x for x in value if x not in existing]
                if extras:
                    primary["args_used"] = existing + extras
                    supplemental.append(
                        {
                            "source_run_id": other.get("source_run_id"),
                            "field": "args_used_variants",
                            "value": extras,
                        }
                    )
                continue

            if _is_missing(primary.get(key)) and not _is_missing(value):
                primary[key] = copy.deepcopy(value)
                supplemental.append(
                    {
                        "source_run_id": other.get("source_run_id"),
                        "field": key,
                        "value": copy.deepcopy(value),
                    }
                )

    primary["selection_reason"] = "highest_confidence"
    primary["supplemental_evidence"] = supplemental
    return primary


def _merge_failure_group(group: list[dict[str, Any]]) -> dict[str, Any]:
    scored = sorted(
        group,
        key=lambda x: (
            len(_iter_error_codes(x)),
            int(x.get("probe_count") or 0),
            _confidence_score(x),
        ),
        reverse=True,
    )
    selected = copy.deepcopy(scored[0])
    selected["selection_reason"] = "richest_failure_evidence"
    selected["supplemental_evidence"] = []
    return selected


def _sessions_from_args(args: argparse.Namespace) -> tuple[list[Path], str | None]:
    if args.sessions and args.sessions_manifest:
        return [], "Use either --sessions or --sessions-manifest, not both"
    if not args.sessions and not args.sessions_manifest:
        return [], "Either --sessions or --sessions-manifest is required"

    raw_sessions: list[str] = []
    if args.sessions_manifest:
        manifest_path = Path(args.sessions_manifest)
        if not manifest_path.exists():
            return [], f"Sessions manifest not found: {manifest_path}"
        try:
            payload = _read_json(manifest_path)
        except Exception as exc:
            return [], f"Cannot read sessions manifest {manifest_path}: {exc}"
        if not isinstance(payload, list) or not all(isinstance(x, str) for x in payload):
            return [], "Sessions manifest must be a JSON array of path strings"
        raw_sessions = payload
    else:
        raw_sessions = list(args.sessions)

    session_dirs = [Path(x) for x in raw_sessions]
    if not session_dirs:
        return [], "No session directories were provided"

    for session in session_dirs:
        if not session.exists() or not session.is_dir():
            return [], f"Session directory does not exist: {session}"

    return session_dirs, None


def main() -> int:
    parser = argparse.ArgumentParser(description="Merge findings from multiple pipeline runs using UNION strategy")
    parser.add_argument("--sessions", nargs="*", default=[])
    parser.add_argument("--sessions-manifest")
    parser.add_argument("--output", required=True)
    parser.add_argument("--run-set-id", default=None)
    args = parser.parse_args()

    session_dirs, session_err = _sessions_from_args(args)
    if session_err:
        print(session_err)
        return 1

    loaded_sessions, load_err = _load_sessions(session_dirs)
    if load_err:
        print(load_err)
        return 1

    by_function: dict[str, list[dict[str, Any]]] = {}
    for session in loaded_sessions:
        for finding in session["findings"]:
            fn = _get_function_name(finding)
            if not fn:
                continue
            # normalize to function_name for consistency in output
            finding["function_name"] = fn
            by_function.setdefault(fn, []).append(finding)

    merged: list[dict[str, Any]] = []
    union_success_functions: set[str] = set()
    for function_name in sorted(by_function):
        group = by_function[function_name]
        successes = [x for x in group if str(x.get("status") or "").lower() == "success"]
        if successes:
            selected = _merge_success_group(successes)
            union_success_functions.add(function_name)
        else:
            selected = _merge_failure_group(group)
        selected["function_name"] = function_name
        selected.setdefault("source_run_id", "")
        merged.append(selected)

    best_session = max(loaded_sessions, key=lambda x: int(x.get("success_count") or 0))
    best_session_fn_success: set[str] = {
        str(x.get("function_name") or "").strip()
        for x in best_session["findings"]
        if str(x.get("status") or "").lower() == "success" and str(x.get("function_name") or "").strip()
    }

    gained_functions = sorted(union_success_functions.difference(best_session_fn_success))
    contributed_sessions = sorted(
        {
            str(x.get("source_run_id") or "")
            for x in merged
            if str(x.get("function_name") or "") in gained_functions
        }
    )

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    accumulated_path = output_dir / "accumulated-findings.json"
    summary_path = output_dir / "merge-summary.json"

    accumulated_path.write_text(json.dumps(merged, indent=2), encoding="utf-8")

    input_sessions = [str(x["run_id"]) for x in loaded_sessions]
    functions_success = len(union_success_functions)
    summary = {
        "merge_id": str(uuid.uuid4()),
        "merged_at": _now_utc(),
        "input_sessions": input_sessions,
        "run_set_id": args.run_set_id,
        "functions_total": len(merged),
        "functions_success": functions_success,
        "functions_error_only": len(merged) - functions_success,
        "sessions_contributed_new_success": contributed_sessions,
        "union_gain_vs_best_single": functions_success - int(best_session.get("success_count") or 0),
    }
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())