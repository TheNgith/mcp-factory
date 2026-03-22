"""
run_batch_parallel.py - Launch a batch of identical pipeline runs concurrently,
save strict snapshots per leg, and emit one consolidated batch summary.

This extends the A/B runner into N-way parallel execution for faster iteration.
"""

from __future__ import annotations

import argparse
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import run_ab_parallel as ab


@dataclass
class BatchLegResult:
    leg: str
    ok: bool
    result: dict[str, Any] | None = None
    error: str | None = None


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _make_leg_name(i: int) -> str:
    return f"R{i:02d}"


def _compare_two(base: dict[str, Any], other: dict[str, Any]) -> list[str]:
    diffs: list[str] = []

    def _cmp(path: str, a: Any, b: Any) -> None:
        if a != b:
            diffs.append(f"{path}: base={a!r} other={b!r}")

    _cmp("contract.valid", base["contract"]["valid"], other["contract"]["valid"])
    _cmp("contract.hard_fail", base["contract"]["hard_fail"], other["contract"]["hard_fail"])

    for k in (
        "mode",
        "model",
        "max_rounds",
        "max_tool_calls",
        "gap_resolution_enabled",
    ):
        _cmp(f"runtime.{k}", base["runtime"][k], other["runtime"][k])

    for k in (
        "stage_pass",
        "stage_fail",
        "transition_pass",
        "transition_fail",
        "transition_warn",
        "transition_partial",
        "transition_na",
    ):
        _cmp(f"cohesion.{k}", base["cohesion"][k], other["cohesion"][k])

    _cmp(
        "cohesion.failed_transitions",
        sorted(base["cohesion"]["failed_transitions"]),
        sorted(other["cohesion"]["failed_transitions"]),
    )
    _cmp(
        "function_outcome.unresolved_functions",
        sorted(base["function_outcome"]["unresolved_functions"]),
        sorted(other["function_outcome"]["unresolved_functions"]),
    )
    _cmp("function_outcome.success", base["function_outcome"]["success"], other["function_outcome"]["success"])
    _cmp("function_outcome.error", base["function_outcome"]["error"], other["function_outcome"]["error"])

    return diffs


def _build_batch_payload(
    run_cfg: ab.RunConfig,
    commit: str,
    commit_msg: str,
    run_root: Path,
    leg_results: list[BatchLegResult],
) -> dict[str, Any]:
    ok_runs = [r for r in leg_results if r.ok and r.result]
    failed_runs = [r for r in leg_results if not r.ok]

    baseline = ok_runs[0].result if ok_runs else None
    deterministic_vs_baseline: dict[str, bool] = {}
    per_leg_diffs: dict[str, list[str]] = {}

    if baseline:
        for r in ok_runs:
            assert r.result is not None
            if r.leg == ok_runs[0].leg:
                deterministic_vs_baseline[r.leg] = True
                per_leg_diffs[r.leg] = []
                continue
            diffs = _compare_two(baseline, r.result)
            deterministic_vs_baseline[r.leg] = len(diffs) == 0
            per_leg_diffs[r.leg] = diffs

    return {
        "created_at": ab._now_utc(),
        "type": "batch_parallel",
        "run_manifest": {
            "commit": commit,
            "commit_msg": commit_msg,
            "mode": run_cfg.mode,
            "model": run_cfg.model,
            "max_rounds": run_cfg.max_rounds,
            "max_tool_calls": run_cfg.max_tool_calls,
            "gap_resolution_enabled": run_cfg.gap_resolution_enabled,
            "hints_sha256": ab._sha256_text(run_cfg.hints),
            "use_cases_sha256": ab._sha256_text(run_cfg.use_cases),
        },
        "batch": {
            "run_root": str(run_root),
            "requested_legs": len(leg_results),
            "completed_ok": len(ok_runs),
            "completed_failed": len(failed_runs),
            "deterministic_count_vs_baseline": sum(1 for v in deterministic_vs_baseline.values() if v),
            "baseline_leg": ok_runs[0].leg if ok_runs else None,
        },
        "legs": [
            {
                "leg": r.leg,
                "ok": r.ok,
                "error": r.error,
                "result": r.result,
                "deterministic_vs_baseline": deterministic_vs_baseline.get(r.leg),
                "differences_vs_baseline": per_leg_diffs.get(r.leg, []),
            }
            for r in leg_results
        ],
    }


def _write_markdown(path: Path, payload: dict[str, Any]) -> None:
    b = payload["batch"]
    m = payload["run_manifest"]
    lines = [
        "# Batch Parallel Summary",
        "",
        f"- Created: {payload['created_at']}",
        f"- Commit: {m['commit']} {m['commit_msg']}",
        f"- Mode: {m['mode']} | Model: {m['model']}",
        f"- Max rounds: {m['max_rounds']} | Max tool calls: {m['max_tool_calls']}",
        f"- Gap resolution enabled: {m['gap_resolution_enabled']}",
        "",
        "## Batch Stats",
        "",
        f"- Requested legs: {b['requested_legs']}",
        f"- Completed OK: {b['completed_ok']}",
        f"- Completed Failed: {b['completed_failed']}",
        f"- Baseline leg: {b['baseline_leg']}",
        f"- Deterministic vs baseline: {b['deterministic_count_vs_baseline']}/{b['completed_ok']}",
        "",
        "## Legs",
        "",
    ]

    for leg in payload["legs"]:
        lines.append(f"### {leg['leg']}")
        lines.append(f"- ok: {leg['ok']}")
        if leg["ok"] and leg["result"]:
            result = leg["result"]
            lines.append(f"- job_id: {result['job_id']}")
            lines.append(f"- session_dir: {result['session_dir']}")
            lines.append(f"- success_functions: {result['function_outcome']['success']}")
            lines.append(f"- error_functions: {result['function_outcome']['error']}")
            lines.append(f"- transition_warn: {result['cohesion']['transition_warn']}")
            lines.append(f"- transition_partial: {result['cohesion']['transition_partial']}")
            lines.append(f"- deterministic_vs_baseline: {leg['deterministic_vs_baseline']}")
            if leg["differences_vs_baseline"]:
                lines.append("- differences_vs_baseline:")
                for d in leg["differences_vs_baseline"]:
                    lines.append(f"  - {d}")
        else:
            lines.append(f"- error: {leg['error']}")
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


def _append_index(index_path: Path, payload: dict[str, Any]) -> None:
    rows: list[dict[str, Any]] = []
    if index_path.exists():
        loaded = json.loads(index_path.read_text(encoding="utf-8"))
        if isinstance(loaded, list):
            rows = loaded

    b = payload["batch"]
    m = payload["run_manifest"]
    rows.append(
        {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "type": "batch_parallel",
            "commit": m["commit"],
            "commit_msg": m["commit_msg"],
            "mode": m["mode"],
            "model": m["model"],
            "max_rounds": m["max_rounds"],
            "max_tool_calls": m["max_tool_calls"],
            "gap_resolution_enabled": m["gap_resolution_enabled"],
            "hints_sha256": m["hints_sha256"],
            "use_cases_sha256": m["use_cases_sha256"],
            "requested_legs": b["requested_legs"],
            "completed_ok": b["completed_ok"],
            "completed_failed": b["completed_failed"],
            "deterministic_count_vs_baseline": b["deterministic_count_vs_baseline"],
            "baseline_leg": b["baseline_leg"],
            "saved_at": ab._now_utc(),
        }
    )

    _write_json(index_path, rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run N pipeline jobs in parallel and record consolidated outputs")
    parser.add_argument("--api-url", required=True, help="Pipeline API base URL")
    parser.add_argument("--api-key", default="", help="Pipeline API key (or use MCP_FACTORY_API_KEY env)")
    parser.add_argument("--dll", default="tests/fixtures/contoso_legacy/contoso_cs.dll")
    parser.add_argument("--hints-file", default="sessions/contoso_cs/contoso_cs.txt")
    parser.add_argument("--mode", default="dev")
    parser.add_argument("--model", default="gpt-4o")
    parser.add_argument("--max-rounds", type=int, default=2)
    parser.add_argument("--max-tool-calls", type=int, default=5)
    parser.add_argument("--runs", type=int, default=4, help="How many total legs to run")
    parser.add_argument("--max-parallel", type=int, default=4, help="Max concurrent legs")
    parser.add_argument(
        "--gap-resolution-enabled",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Enable/disable phase-8 gap resolution",
    )
    parser.add_argument("--note-prefix", default="batch-parallel")
    parser.add_argument("--sessions-root", default="sessions")
    parser.add_argument("--append-index", action="store_true", help="Append compact batch row to sessions/index.json")
    args = parser.parse_args()

    if args.runs < 2:
        raise ValueError("--runs must be >= 2")
    if args.max_parallel < 1:
        raise ValueError("--max-parallel must be >= 1")

    repo_root = Path(__file__).resolve().parent.parent
    api_key = args.api_key or os.getenv("MCP_FACTORY_API_KEY", "")

    dll_path = (repo_root / args.dll).resolve()
    hints_file = (repo_root / args.hints_file).resolve()
    if not dll_path.exists():
        raise FileNotFoundError(f"DLL not found: {dll_path}")
    if not hints_file.exists():
        raise FileNotFoundError(f"Hints file not found: {hints_file}")

    hints, use_cases = ab._parse_hints_file(hints_file)
    commit = ab._git_short_hash(repo_root)
    commit_msg = ab._git_commit_msg(repo_root)

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    sessions_root = (repo_root / args.sessions_root).resolve()
    run_root = sessions_root / "_runs" / f"{datetime.now():%Y-%m-%d}-{commit}-{args.note_prefix}-{ts}"
    run_root.mkdir(parents=True, exist_ok=True)

    run_cfg = ab.RunConfig(
        api_url=args.api_url,
        api_key=api_key,
        dll_path=dll_path,
        hints=hints,
        use_cases=use_cases,
        mode=args.mode,
        model=args.model,
        max_rounds=args.max_rounds,
        max_tool_calls=args.max_tool_calls,
        gap_resolution_enabled=bool(args.gap_resolution_enabled),
        sessions_root=sessions_root,
        run_root=run_root,
        note_prefix=args.note_prefix,
    )

    print(f"Launching batch runs: runs={args.runs} max_parallel={args.max_parallel}", flush=True)
    lock = threading.Lock()

    futures = {}
    results: list[BatchLegResult] = []
    with ThreadPoolExecutor(max_workers=args.max_parallel) as pool:
        for i in range(1, args.runs + 1):
            leg = _make_leg_name(i)
            fut = pool.submit(ab._run_one_leg, repo_root, run_cfg, leg, lock)
            futures[fut] = leg

        for fut in as_completed(futures):
            leg = futures[fut]
            try:
                result = fut.result()
                results.append(BatchLegResult(leg=leg, ok=True, result=result))
            except Exception as ex:
                results.append(BatchLegResult(leg=leg, ok=False, error=str(ex)))

    results.sort(key=lambda r: r.leg)
    payload = _build_batch_payload(run_cfg, commit, commit_msg, run_root, results)

    out_json = run_root / "batch-summary.json"
    out_md = run_root / "batch-summary.md"
    _write_json(out_json, payload)
    _write_markdown(out_md, payload)

    if args.append_index:
        _append_index(sessions_root / "index.json", payload)

    print("\n=== Batch Summary ===", flush=True)
    print(f"OK={payload['batch']['completed_ok']} failed={payload['batch']['completed_failed']}", flush=True)
    print(
        f"Deterministic vs baseline: {payload['batch']['deterministic_count_vs_baseline']}/"
        f"{payload['batch']['completed_ok']}",
        flush=True,
    )
    print(f"Batch JSON: {out_json}", flush=True)
    print(f"Batch MD:   {out_md}", flush=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
