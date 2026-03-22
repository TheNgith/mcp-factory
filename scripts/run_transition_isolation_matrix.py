"""
run_transition_isolation_matrix.py - Execute a sequence of isolated A/B runs,
one variable family at a time, and write a consolidated matrix report.

This script orchestrates existing run_ab_parallel.py runs to keep the
MVP transition automation loop moving while preserving causal clarity.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Case:
    name: str
    family: str
    mode: str
    model: str
    max_rounds: int
    max_tool_calls: int
    gap_resolution_enabled: bool
    hints_file: str = ""


def _default_cases(base_model: str) -> list[Case]:
    return [
        Case(
            name="control-baseline",
            family="control",
            mode="dev",
            model=base_model,
            max_rounds=2,
            max_tool_calls=5,
            gap_resolution_enabled=True,
        ),
        Case(
            name="gap-resolution-off",
            family="gap-policy",
            mode="dev",
            model=base_model,
            max_rounds=2,
            max_tool_calls=5,
            gap_resolution_enabled=False,
        ),
        Case(
            name="probe-depth-rounds-3",
            family="probe-depth",
            mode="dev",
            model=base_model,
            max_rounds=3,
            max_tool_calls=5,
            gap_resolution_enabled=True,
        ),
        Case(
            name="tool-budget-8",
            family="probe-depth",
            mode="dev",
            model=base_model,
            max_rounds=2,
            max_tool_calls=8,
            gap_resolution_enabled=True,
        ),
        Case(
            name="context-verbose",
            family="context-variation",
            mode="dev",
            model=base_model,
            max_rounds=2,
            max_tool_calls=5,
            gap_resolution_enabled=True,
            hints_file="sessions/contoso_cs/contoso_cs_verbose.txt",
        ),
        Case(
            name="context-minimal",
            family="context-variation",
            mode="dev",
            model=base_model,
            max_rounds=2,
            max_tool_calls=5,
            gap_resolution_enabled=True,
            hints_file="sessions/contoso_cs/contoso_cs_minimal.txt",
        ),
        Case(
            name="context-no-init",
            family="context-variation",
            mode="dev",
            model=base_model,
            max_rounds=2,
            max_tool_calls=5,
            gap_resolution_enabled=True,
            hints_file="sessions/contoso_cs/contoso_cs_no_init.txt",
        ),
    ]


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _write_md(path: Path, payload: dict[str, Any]) -> None:
    lines = [
        "# Transition Isolation Matrix",
        "",
        f"- Created: {payload['created_at']}",
        f"- API URL: {payload['api_url']}",
        f"- Cases requested: {payload['cases_requested']}",
        f"- Cases completed: {payload['cases_completed']}",
        "",
        "## Case Results",
        "",
        "| case | family | ok | readiness_pass | reasons | run_root |",
        "|---|---|---|---|---|---|",
    ]

    for case in payload["cases"]:
        reason_list = case.get("readiness_reasons") or case.get("error") or []
        reasons = "; ".join(reason_list)
        lines.append(
            f"| {case['name']} | {case['family']} | {case['ok']} | {case.get('readiness_pass')} | {reasons or 'none'} | {case.get('run_root', '')} |"
        )

    path.write_text("\n".join(lines), encoding="utf-8")


def _resolve_latest_run_root(before: set[str], sessions_runs_root: Path, case_name: str) -> str | None:
    after = {
        str(p.resolve())
        for p in sessions_runs_root.iterdir()
        if p.is_dir()
    }
    created = sorted(after - before)
    if not created:
        return None

    tag = f"-isolation-{case_name}-"
    tagged = [p for p in created if tag in Path(p).name]
    if tagged:
        return tagged[-1]
    return created[-1]


def _run_case(repo_root: Path, sessions_runs_root: Path, args: argparse.Namespace, case: Case) -> dict[str, Any]:
    before = {
        str(p.resolve())
        for p in sessions_runs_root.iterdir()
        if p.is_dir()
    }

    cmd = [
        str(repo_root / ".venv" / "Scripts" / "python.exe"),
        str(repo_root / "scripts" / "run_ab_parallel.py"),
        "--api-url",
        args.api_url,
        "--api-key",
        args.api_key,
        "--mode",
        case.mode,
        "--model",
        case.model,
        "--max-rounds",
        str(case.max_rounds),
        "--max-tool-calls",
        str(case.max_tool_calls),
        "--note-prefix",
        f"isolation-{case.name}",
        "--append-index",
    ]
    if case.gap_resolution_enabled:
        cmd.append("--gap-resolution-enabled")
    else:
        cmd.append("--no-gap-resolution-enabled")

    if case.hints_file:
        cmd.extend(["--hints-file", case.hints_file])

    proc = subprocess.run(cmd, cwd=str(repo_root), capture_output=True, text=True)
    run_root = _resolve_latest_run_root(before, sessions_runs_root, case.name)

    out: dict[str, Any] = {
        "name": case.name,
        "family": case.family,
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "run_root": run_root,
        "stdout_tail": (proc.stdout or "")[-4000:],
        "stderr_tail": (proc.stderr or "")[-2000:],
    }

    stderr_lower = (proc.stderr or "").lower()
    stdout_lower = (proc.stdout or "").lower()

    if run_root:
        readiness_path = Path(run_root) / "transition-readiness.json"
        failure_path = Path(run_root) / "ab-failure.json"
        if readiness_path.exists():
            readiness = _read_json(readiness_path)
            out["readiness_pass"] = bool(readiness.get("pass"))
            out["readiness_reasons"] = list(readiness.get("reasons") or [])
        elif failure_path.exists():
            failure = _read_json(failure_path)
            err = str(failure.get("error") or "ab_runner_failed")
            out["failure_error"] = err
            out["readiness_pass"] = None
            if "unauthorized" in err.lower():
                out["readiness_reasons"] = ["auth_unauthorized"]
            else:
                out["readiness_reasons"] = ["ab_runner_failed"]
        else:
            out["readiness_pass"] = None
            out["readiness_reasons"] = ["missing_transition_readiness_artifact"]

    if not out["ok"] and "readiness_reasons" not in out:
        if "unauthorized" in stderr_lower or "unauthorized" in stdout_lower:
            out["error"] = ["auth_unauthorized"]
        elif "timeout" in stderr_lower or "timeout" in stdout_lower:
            out["error"] = ["runner_timeout"]
        else:
            out["error"] = ["ab_runner_failed"]

    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Run isolated A/B cases and write transition readiness matrix")
    parser.add_argument("--api-url", required=True)
    parser.add_argument("--api-key", default="")
    parser.add_argument("--model", default="gpt-4o-mini")
    parser.add_argument("--output-root", default="sessions/_runs")
    parser.add_argument("--continue-on-error", action="store_true", default=True)
    parser.add_argument("--max-cases", type=int, default=0, help="Optional cap for number of cases")
    parser.add_argument("--parallel", action="store_true", default=True, help="Run cases in parallel (default: on)")
    parser.add_argument("--no-parallel", dest="parallel", action="store_false", help="Run cases sequentially")
    parser.add_argument("--max-workers", type=int, default=4, help="Max parallel A/B pairs (default: 4)")
    args = parser.parse_args()

    repo_root = _REPO_ROOT
    sessions_runs_root = (repo_root / args.output_root).resolve()
    sessions_runs_root.mkdir(parents=True, exist_ok=True)

    api_key = args.api_key or os.getenv("MCP_FACTORY_API_KEY", "")
    cases = _default_cases(base_model=args.model)
    if args.max_cases > 0:
        cases = cases[: args.max_cases]

    effective_args = argparse.Namespace(**{**vars(args), "api_key": api_key})
    results: list[dict[str, Any]] = []

    if args.parallel and len(cases) > 1:
        workers = min(args.max_workers, len(cases))
        print(f"Running {len(cases)} cases in parallel (max_workers={workers})", flush=True)
        future_to_case: dict = {}
        with ThreadPoolExecutor(max_workers=workers) as executor:
            for case in cases:
                future = executor.submit(_run_case, repo_root, sessions_runs_root, effective_args, case)
                future_to_case[future] = case
            for future in as_completed(future_to_case):
                result = future.result()
                results.append(result)
                print(f"  [{result['name']}] ok={result['ok']} readiness_pass={result.get('readiness_pass')}", flush=True)
    else:
        for case in cases:
            result = _run_case(repo_root, sessions_runs_root, effective_args, case)
            results.append(result)
            print(f"  [{result['name']}] ok={result['ok']} readiness_pass={result.get('readiness_pass')}", flush=True)
            if not result["ok"] and not args.continue_on_error:
                break

    matrix = {
        "created_at": _now_utc(),
        "type": "transition_isolation_matrix",
        "api_url": args.api_url,
        "cases_requested": len(cases),
        "cases_completed": len(results),
        "cases": results,
    }

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = sessions_runs_root / f"{datetime.now():%Y-%m-%d}-isolation-matrix-{ts}"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_json = out_dir / "transition-isolation-matrix.json"
    out_md = out_dir / "transition-isolation-matrix.md"
    _write_json(out_json, matrix)
    _write_md(out_md, matrix)

    print("=== Transition Isolation Matrix ===", flush=True)
    print(f"Cases completed: {matrix['cases_completed']}/{matrix['cases_requested']}", flush=True)
    for row in results:
        print(
            f"- {row['name']}: ok={row['ok']} readiness={row.get('readiness_pass')} run_root={row.get('run_root')}",
            flush=True,
        )
    print(f"Matrix JSON: {out_json}", flush=True)
    print(f"Matrix MD:   {out_md}", flush=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
