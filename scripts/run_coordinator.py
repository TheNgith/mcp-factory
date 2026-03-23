"""scripts/run_coordinator.py — Autonomous coordinator agent (Q17).

Drives the full Q15/Q16 improvement loop:
  Cycle:
    Step A — confirm trunk stability (N=3 baseline runs, check median + quality)
    Step B — variable sweep (N=3 controls + M=3 ablations for one variable family)
    Step C — sentinel check (auto, after every Step B)
    Step D — write-unlock re-probe (only if Step C found newly resolved sentinel)
  Reports a cycle-level Markdown report after each cycle.
  Writes a final report on any stopping condition.

Prerequisites: Q14 (union_merger.py), Q15 (run_set_orchestrator.py),
               Q16 (adaptive sentinel emission fields).
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_VARIABLE_FAMILIES = ["prompt_framing", "vocab_ordering", "context_density", "tool_budget"]

_DEFAULT_STATE: dict[str, Any] = {
    "component": "contoso_cs",
    "current_cycle": 0,
    "playbook_step": "A",
    "current_baseline_profile": "baseline",
    "baseline_functions_success": 4,
    "baseline_run_set_id": None,
    "cycles_completed": [],
    "playbook_variable_queue": list(_VARIABLE_FAMILIES),
    "stopping_reason": None,
    "max_cycles": 10,
    "output_dir": "sessions/_runs",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _select_python(repo_root: Path) -> str:
    venv_py = repo_root / ".venv" / "Scripts" / "python.exe"
    if venv_py.exists():
        return str(venv_py)
    return sys.executable


def _load_profiles(repo_root: Path) -> dict[str, dict[str, Any]]:
    path = repo_root / "scripts" / "prompt_profiles.json"
    if not path.exists():
        raise FileNotFoundError(f"prompt_profiles.json not found: {path}")
    raw = _read_json(path)
    if not isinstance(raw, dict):
        raise ValueError("prompt_profiles.json must be a JSON object")
    return {k: v for k, v in raw.items() if isinstance(k, str) and isinstance(v, dict)}


def _profiles_for_variable(
    profiles: dict[str, dict[str, Any]], variable: str
) -> list[str]:
    """Return all profile IDs whose ablation_variable matches the given family."""
    return [
        pid
        for pid, p in profiles.items()
        if p.get("ablation_variable") == variable and pid != "baseline"
    ]


def _load_or_init_state(state_file: Path, args: argparse.Namespace) -> dict[str, Any]:
    if state_file.exists():
        return _read_json(state_file)
    state = dict(_DEFAULT_STATE)
    state["component"] = args.component
    state["max_cycles"] = args.max_cycles
    state["output_dir"] = args.output_dir
    return state


def _save_state(state_file: Path, state: dict[str, Any]) -> None:
    _write_json(state_file, state)


# ── Run-set dispatch ──────────────────────────────────────────────────────────

def _build_run_set_def(
    state: dict[str, Any],
    cycle: int,
    variable: str | None,
    ablation_profile_ids: list[str],
    api_url: str,
    api_key: str,
    run_set_id: str,
    base_config: dict[str, Any],
) -> dict[str, Any]:
    n_control = 3
    m_ablation = len(ablation_profile_ids)
    component = state["component"]
    output_dir = state["output_dir"]
    return {
        "run_set_id": run_set_id,
        "component": component,
        "api_url": api_url,
        "api_key": api_key,
        "n_control": n_control,
        "m_ablation": m_ablation,
        "ablation_profiles": ablation_profile_ids,
        "coordinator_cycle": cycle,
        "playbook_step": "A" if variable is None else "B",
        "output_dir": str(Path(output_dir) / run_set_id),
        "base_config": base_config,
        "analyze_timeout_sec": 900,
        "explore_timeout_sec": 2400,
        "max_parallel": max(1, n_control + m_ablation),
    }


def _dispatch_run_set(
    repo_root: Path,
    run_set_def: dict[str, Any],
    api_key: str,
) -> tuple[int, Path]:
    """Write the run-set def to a temp file and call run_set_orchestrator.py.

    Returns (exit_code, run_set_output_dir).
    """
    tmp_file = repo_root / "sessions" / "_runs" / f"_coord-def-{run_set_def['run_set_id']}.json"
    _write_json(tmp_file, run_set_def)

    python_exe = _select_python(repo_root)
    cmd = [
        python_exe,
        str(repo_root / "scripts" / "run_set_orchestrator.py"),
        "--run-set-def",
        str(tmp_file),
        "--api-key",
        api_key,
    ]
    result = subprocess.run(cmd, cwd=str(repo_root), capture_output=False, text=True)
    try:
        tmp_file.unlink(missing_ok=True)
    except Exception:
        pass
    out_dir = Path(run_set_def["output_dir"])
    return result.returncode, out_dir


# ── Result reading ────────────────────────────────────────────────────────────

def _read_summary(out_dir: Path) -> dict[str, Any]:
    summary_path = out_dir / "run-set-summary.json"
    if summary_path.exists():
        return _read_json(summary_path)
    return {}


def _read_dashboard_rows(out_dir: Path) -> list[dict[str, Any]]:
    """Read all human/dashboard-row.json files under out_dir/sessions/."""
    rows = []
    sessions_root = out_dir / "sessions"
    if not sessions_root.is_dir():
        return rows
    for leg_dir in sorted(sessions_root.iterdir()):
        row_path = leg_dir / "human" / "dashboard-row.json"
        if row_path.exists():
            try:
                rows.append(_read_json(row_path))
            except Exception:
                pass
    return rows


def _functions_success(row: dict[str, Any], summary_session: dict[str, Any]) -> int:
    """Return functions_success, preferring summary_session then dashboard row."""
    sc = int(summary_session.get("functions_success") or 0)
    if sc > 0:
        return sc
    return int(row.get("functions_success") or 0)


# ── Promotion evaluation ──────────────────────────────────────────────────────

def _gate_counts(row: dict[str, Any]) -> tuple[int, int]:
    """Return (fail_count, stage_fail_count) from a dashboard row."""
    return (
        int(row.get("transition_fail_count") or 0),
        int(row.get("stage_fail_count") or 0),
    )


def _is_promotion_candidate(
    m_row: dict[str, Any],
    m_fs: int,
    control_median: float,
    control_rows: list[dict[str, Any]],
) -> tuple[bool, str]:
    """Return (is_candidate, reason_string).

    Candidate if:
    a) m_fs >= control_median
    b) No gate regression vs. control median gate counts (avg)
    """
    if m_fs < control_median:
        return False, f"functions_success {m_fs} < control median {control_median:.1f}"

    m_fails, m_stage_fails = _gate_counts(m_row)
    ctrl_fails = [r.get("transition_fail_count") or 0 for r in control_rows]
    ctrl_stage_fails = [r.get("stage_fail_count") or 0 for r in control_rows]
    ctrl_fail_med = statistics.median(ctrl_fails) if ctrl_fails else 0
    ctrl_stage_fail_med = statistics.median(ctrl_stage_fails) if ctrl_stage_fails else 0

    if m_fails > ctrl_fail_med:
        return False, f"gate regression: {m_fails} fail > control median {ctrl_fail_med}"
    if m_stage_fails > ctrl_stage_fail_med:
        return False, f"stage fail regression: {m_stage_fails} > control median {ctrl_stage_fail_med}"

    improvement = m_fs > control_median or m_fails < ctrl_fail_med or m_stage_fails < ctrl_stage_fail_med
    if not improvement:
        return False, "no gate improvement vs control"

    return True, (
        f"functions_success={m_fs} >= median={control_median:.1f}, "
        f"fails={m_fails} (ctrl={ctrl_fail_med:.0f})"
    )


# ── Cycle steps ───────────────────────────────────────────────────────────────

def _step_a(
    state: dict[str, Any],
    cycle: int,
    summary: dict[str, Any],
    dashboard_rows: list[dict[str, Any]],
    run_set_id: str,
) -> tuple[dict[str, Any], str | None]:
    """Evaluate Step A — robustness confirmation.

    Returns (cycle_entry, stopping_reason | None).
    """
    sessions = summary.get("sessions") or []
    ctrl_sessions = [s for s in sessions if int(s.get("layer") or 0) == 1]
    ctrl_rows = [r for r in dashboard_rows if int(r.get("layer") or 0) == 1]

    ctrl_fs = [_functions_success(r, s)
               for r, s in zip(ctrl_rows, ctrl_sessions)] if ctrl_rows else []
    if not ctrl_fs and ctrl_sessions:
        ctrl_fs = [int(s.get("functions_success") or 0) for s in ctrl_sessions]

    median_fs = statistics.median(ctrl_fs) if ctrl_fs else 0
    capture_ok = all(
        str(r.get("capture_quality") or "complete") == "complete" for r in ctrl_rows
    )

    cycle_entry: dict[str, Any] = {
        "cycle": cycle,
        "step": "A",
        "run_set_id": run_set_id,
        "completed_at": _now_iso(),
        "control_median_fs": median_fs,
        "capture_quality_ok": capture_ok,
        "promoted": False,
    }

    stopping_reason: str | None = None

    if cycle == 1:
        # First run: establish the baseline
        state["baseline_functions_success"] = int(median_fs)
        state["baseline_run_set_id"] = run_set_id
        cycle_entry["note"] = "baseline established"
    else:
        baseline_fs = int(state.get("baseline_functions_success") or 0)
        if not capture_ok:
            stopping_reason = "capture_unreliable"
            cycle_entry["note"] = "capture_quality check failed"
        elif median_fs < baseline_fs:
            stopping_reason = "baseline_regression"
            cycle_entry["note"] = f"regression: median {median_fs} < baseline {baseline_fs}"
        else:
            cycle_entry["note"] = "trunk stable"

    return cycle_entry, stopping_reason


def _step_b(
    state: dict[str, Any],
    cycle: int,
    variable: str,
    summary: dict[str, Any],
    dashboard_rows: list[dict[str, Any]],
    run_set_id: str,
    profiles: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], str | None]:
    """Evaluate Step B — variable sweep promotion decision.

    Returns (cycle_entry, stopping_reason | None).
    """
    sessions = summary.get("sessions") or []
    ctrl_sessions = [s for s in sessions if int(s.get("layer") or 0) == 1]
    ablation_sessions = [s for s in sessions if int(s.get("layer") or 0) == 2]

    ctrl_rows = [r for r in dashboard_rows if int(r.get("layer") or 0) == 1]
    ablation_rows = [r for r in dashboard_rows if int(r.get("layer") or 0) == 2]

    ctrl_fs = [_functions_success(r, s)
               for r, s in zip(ctrl_rows, ctrl_sessions)] if ctrl_rows else []
    if not ctrl_fs and ctrl_sessions:
        ctrl_fs = [int(s.get("functions_success") or 0) for s in ctrl_sessions]

    ctrl_median = statistics.median(ctrl_fs) if ctrl_fs else 0

    # Pair each ablation row with its summary session
    ablation_evals: list[dict[str, Any]] = []
    for i, (m_row, m_sess) in enumerate(zip(ablation_rows, ablation_sessions)):
        m_fs = _functions_success(m_row, m_sess)
        profile_id = str(m_sess.get("profile") or m_row.get("prompt_profile_id") or "unknown")
        candidate, reason = _is_promotion_candidate(m_row, m_fs, ctrl_median, ctrl_rows)
        ablation_evals.append({
            "profile_id": profile_id,
            "functions_success": m_fs,
            "candidate": candidate,
            "reason": reason,
            "write_unlock_outcome": m_row.get("write_unlock_outcome"),
            "sentinel_new_codes": int(m_row.get("sentinel_new_codes_this_run") or 0),
        })

    # Promotion rule: majority of ablation runs with same profile are candidates
    # (with only 1 run per profile, candidate on that run = promoted)
    promoted_profile: str | None = None
    promoted_fs = int(ctrl_median)
    for ev in ablation_evals:
        if ev["candidate"]:
            promoted_profile = ev["profile_id"]
            promoted_fs = ev["functions_success"]
            break

    cycle_entry: dict[str, Any] = {
        "cycle": cycle,
        "step": "B",
        "variable_tested": variable,
        "run_set_id": run_set_id,
        "completed_at": _now_iso(),
        "control_median_fs": ctrl_median,
        "ablation_evals": ablation_evals,
        "promoted": promoted_profile is not None,
        "promoted_profile": promoted_profile,
        "promoted_fs": promoted_fs if promoted_profile else None,
    }

    stopping_reason: str | None = None

    if promoted_profile:
        state["current_baseline_profile"] = promoted_profile
        state["baseline_functions_success"] = promoted_fs
        cycle_entry["note"] = f"promoted: {promoted_profile}"
    else:
        cycle_entry["note"] = "no promotion"
        # Check plateau: if this is the 3rd consecutive no-improvement cycle
        recent = [c for c in state.get("cycles_completed", [])[-3:] if isinstance(c, dict)]
        if len(recent) >= 2 and not any(c.get("promoted") for c in recent):
            stopping_reason = "plateau_reached"

    # Step C: Sentinel check
    new_codes_total = sum(ev.get("sentinel_new_codes", 0) for ev in ablation_evals)
    write_unlock_resolved = any(
        str(ev.get("write_unlock_outcome") or "") == "resolved" for ev in ablation_evals
    )
    cycle_entry["sentinel_new_codes_this_cycle"] = new_codes_total
    cycle_entry["write_unlock_newly_resolved"] = write_unlock_resolved

    return cycle_entry, stopping_reason


# ── Report generation ─────────────────────────────────────────────────────────

def _format_cycle_report(
    cycle: int,
    state: dict[str, Any],
    cycle_entry: dict[str, Any],
    profiles: dict[str, dict[str, Any]],
) -> str:
    step = str(cycle_entry.get("step") or "A")
    variable = str(cycle_entry.get("variable_tested") or "—")
    run_set_id = str(cycle_entry.get("run_set_id") or "—")
    ctrl_median = cycle_entry.get("control_median_fs", 0)
    promoted = bool(cycle_entry.get("promoted"))
    promoted_profile = str(cycle_entry.get("promoted_profile") or "—")
    promoted_fs = cycle_entry.get("promoted_fs")
    sentinel_new = int(cycle_entry.get("sentinel_new_codes_this_cycle", 0))
    write_resolved = bool(cycle_entry.get("write_unlock_newly_resolved"))

    ablation_evals = cycle_entry.get("ablation_evals") or []
    abl_lines = []
    for ev in ablation_evals:
        tag = " → CANDIDATE" if ev.get("candidate") else ""
        abl_lines.append(
            f"  - {ev.get('profile_id', '?')}: "
            f"functions_success={ev.get('functions_success', 0)}, "
            f"unlock={ev.get('write_unlock_outcome') or 'n/a'}{tag}"
        )

    queue = list(state.get("playbook_variable_queue") or [])
    next_var = queue[0] if queue else "(exhausted)"

    current_fs = int(state.get("baseline_functions_success") or 0)
    component = str(state.get("component") or "unknown")

    lines = [
        f"# Coordinator Cycle {cycle} Report",
        f"Generated: {_now_iso()}",
        f"Component: {component}",
        f"Playbook step: {step}",
        f"Variable tested: {variable}",
        "",
        "## Run-Set",
        f"Run set ID: {run_set_id}",
        f"Control runs (N=3): median functions_success={ctrl_median:.0f}",
    ]
    if ablation_evals:
        lines += ["Ablation runs:"] + (abl_lines if abl_lines else ["  (none)"])
    lines += [""]

    if promoted:
        lines += [
            "## Promotion Decision",
            f"Promoted: YES — {promoted_profile}",
            f"New baseline profile: {promoted_profile}",
            f"New baseline functions_success: {promoted_fs}",
            "",
        ]
    else:
        lines += [
            "## Promotion Decision",
            "Promoted: NO — no candidate met promotion criteria",
            "",
        ]

    write_status = "resolved" if write_resolved else str(
        next((ev.get("write_unlock_outcome") or "blocked") for ev in ablation_evals)
        if ablation_evals else "n/a"
    )
    lines += [
        "## Sentinel Status",
        f"New sentinel codes this cycle: {sentinel_new}",
        f"Write-unlock status: {write_status}",
        "",
        "## Current Coverage",
        f"Functions succeeded: {current_fs}/? (known baseline)",
        "",
        "## Next Step",
    ]
    if queue:
        lines.append(
            f"Cycle {cycle + 1}: test variable family = {next_var}"
        )
    else:
        lines.append("Queue exhausted — coordinator will halt after final report.")

    stopping_reason = state.get("stopping_reason")
    if stopping_reason:
        lines += ["", "## Stopping Condition", f"Reason: {stopping_reason}"]
        lines += _recommended_action(stopping_reason)
    else:
        lines += ["", "## Recommended Human Action", "None required — coordinator will proceed automatically."]

    return "\n".join(lines) + "\n"


def _recommended_action(reason: str) -> list[str]:
    suggestions = {
        "playbook_exhausted": [
            "",
            "## Recommended Human Action",
            "All variable families have been tested. Review the final report and decide",
            "whether to run another coordinator pass with new ablation profiles.",
        ],
        "baseline_regression": [
            "",
            "## Recommended Human Action",
            "CRITICAL: The baseline regressed. Investigate recent code changes.",
            "Do not proceed with ablation testing until trunk stability is restored.",
        ],
        "layer1_unstable": [
            "",
            "## Recommended Human Action",
            "Trunk is producing inconsistent results. Check DLL access, executor errors, and",
            "whether the capture_quality field is being set correctly.",
        ],
        "plateau_reached": [
            "",
            "## Recommended Human Action",
            "Three consecutive cycles with no improvement. Consider modifying the prompt profiles",
            "or giving Codex a new targeted improvement task.",
        ],
        "max_cycles_reached": [
            "",
            "## Recommended Human Action",
            "Max cycles reached. Review all cycle reports and decide on next steps.",
            "Run with --max-cycles N to continue.",
        ],
        "capture_unreliable": [
            "",
            "## Recommended Human Action",
            "Session capture quality is unreliable. Check the API container, blob connectivity,",
            "and whether the session-snapshot endpoint returns complete data.",
        ],
        "run_set_failed": [
            "",
            "## Recommended Human Action",
            "A run-set failed. Check the orchestrator logs and API status.",
            "Fix the underlying issue before resuming.",
        ],
    }
    return suggestions.get(reason, ["", "## Recommended Human Action", f"Stopping reason: {reason}. Review logs."])


def _format_final_report(state: dict[str, Any], reason: str) -> str:
    cycles = state.get("cycles_completed") or []
    lines = [
        "# Coordinator Final Report",
        f"Generated: {_now_iso()}",
        f"Component: {state.get('component', 'unknown')}",
        f"Stopping reason: {reason}",
        f"Cycles completed: {len(cycles)}",
        "",
        "## Cycle Summary",
    ]
    for c in cycles:
        if not isinstance(c, dict):
            continue
        prom = "PROMOTED" if c.get("promoted") else "no promotion"
        var = c.get("variable_tested") or "(step A)"
        lines.append(
            f"  Cycle {c.get('cycle', '?')}: step={c.get('step', '?')}, "
            f"variable={var}, median_fs={c.get('control_median_fs', '?')}, {prom}"
        )

    baseline_profile = state.get("current_baseline_profile", "baseline")
    baseline_fs = state.get("baseline_functions_success", 0)
    lines += [
        "",
        "## Final State",
        f"Best profile: {baseline_profile}",
        f"Best functions_success: {baseline_fs}",
        "",
    ]
    lines += _recommended_action(reason)
    return "\n".join(lines) + "\n"


# ── Stopping condition check ──────────────────────────────────────────────────

def _check_stopping(
    state: dict[str, Any],
    cycle_entry: dict[str, Any],
    cycle: int,
) -> str | None:
    # Explicit stopping reason set by step evaluation
    if state.get("stopping_reason"):
        return state["stopping_reason"]

    max_cycles = int(state.get("max_cycles") or 10)
    if cycle >= max_cycles:
        return "max_cycles_reached"

    if not state.get("playbook_variable_queue"):
        return "playbook_exhausted"

    return None


# ── Main loop ─────────────────────────────────────────────────────────────────

def _base_config(args: argparse.Namespace) -> dict[str, Any]:
    cfg: dict[str, Any] = {
        "mode": "dev",
        "model": "gpt-4o",
        "max_rounds": 2,
        "max_tool_calls": 5,
        "gap_resolution_enabled": True,
    }
    if args.dll:
        cfg["dll"] = args.dll
    if args.hints_file:
        cfg["hints_file"] = args.hints_file
    return cfg


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Autonomous coordinator agent that drives the Q15/Q16 improvement loop"
    )
    parser.add_argument("--component", default="contoso_cs")
    parser.add_argument("--api-url", required=True)
    parser.add_argument("--api-key", default="")
    parser.add_argument("--output-dir", default="sessions/_runs")
    parser.add_argument("--state-file", default="sessions/coordinator-state.json")
    parser.add_argument("--max-cycles", type=int, default=10)
    parser.add_argument("--resume", action="store_true",
                        help="Resume from existing state file")
    parser.add_argument("--dll", default="",
                        help="Relative path to DLL (default: tests/fixtures/contoso_legacy/contoso_cs.dll)")
    parser.add_argument("--hints-file", default="",
                        help="Relative path to hints file (optional)")
    args = parser.parse_args()

    repo_root = _REPO_ROOT
    api_key = args.api_key or str(os.getenv("MCP_FACTORY_API_KEY") or "")

    state_file = (repo_root / args.state_file).resolve()
    sessions_dir = (repo_root / "sessions").resolve()
    sessions_dir.mkdir(parents=True, exist_ok=True)

    # Load or initialise state
    if state_file.exists() and not args.resume:
        existing = _read_json(state_file)
        if existing.get("stopping_reason") is None and int(existing.get("current_cycle") or 0) > 0:
            print(
                f"ERROR: State file exists with active run at cycle "
                f"{existing['current_cycle']}. Use --resume to continue or delete it.",
                file=sys.stderr,
            )
            return 1

    args.output_dir = str((repo_root / args.output_dir).resolve())
    state = _load_or_init_state(state_file, args)

    # Sync args into state for resume
    if args.api_url:
        state["api_url"] = args.api_url
    if api_key:
        state["api_key"] = api_key
    state["max_cycles"] = args.max_cycles
    state["output_dir"] = args.output_dir

    profiles = _load_profiles(repo_root)
    base_cfg = _base_config(args)

    print(f"Coordinator starting: component={state['component']}, "
          f"cycle={state['current_cycle']}, max_cycles={state['max_cycles']}",
          flush=True)

    while True:
        cycle = int(state["current_cycle"]) + 1
        state["current_cycle"] = cycle

        playbook_step = str(state.get("playbook_step") or "A")
        variable: str | None = None
        ablation_profile_ids: list[str] = []

        if playbook_step == "B":
            vq = list(state.get("playbook_variable_queue") or [])
            if not vq:
                state["stopping_reason"] = "playbook_exhausted"
                _save_state(state_file, state)
                break
            variable = str(vq[0])
            ablation_profile_ids = _profiles_for_variable(profiles, variable)
            if not ablation_profile_ids:
                print(f"[cycle {cycle}] No profiles for variable '{variable}', skipping.")
                state["playbook_variable_queue"] = vq[1:]
                _save_state(state_file, state)
                continue
        else:
            # Step A: baseline confirmation — control-only run-set
            ablation_profile_ids = []

        run_set_id = f"coord-{state['component']}-c{cycle:03d}-{uuid.uuid4().hex[:6]}"
        run_set_def = _build_run_set_def(
            state=state,
            cycle=cycle,
            variable=variable,
            ablation_profile_ids=ablation_profile_ids,
            api_url=args.api_url,
            api_key=api_key,
            run_set_id=run_set_id,
            base_config=base_cfg,
        )

        print(f"[cycle {cycle}] step={playbook_step}, variable={variable}, "
              f"run_set_id={run_set_id}", flush=True)

        exit_code, out_dir = _dispatch_run_set(repo_root, run_set_def, api_key)

        if exit_code != 0:
            state["stopping_reason"] = "run_set_failed"
            cycle_entry: dict[str, Any] = {
                "cycle": cycle,
                "step": playbook_step,
                "run_set_id": run_set_id,
                "completed_at": _now_iso(),
                "note": f"orchestrator exit code {exit_code}",
                "promoted": False,
            }
            state.setdefault("cycles_completed", []).append(cycle_entry)
            _save_state(state_file, state)
            report_text = _format_cycle_report(cycle, state, cycle_entry, profiles)
            report_path = sessions_dir / f"coordinator-cycle-{cycle}-report.md"
            report_path.write_text(report_text, encoding="utf-8")
            print(f"[cycle {cycle}] FAILED — {report_path}", flush=True)
            break

        summary = _read_summary(out_dir)
        dashboard_rows = _read_dashboard_rows(out_dir)

        if playbook_step == "A":
            cycle_entry, step_stopping = _step_a(state, cycle, summary, dashboard_rows, run_set_id)
        else:
            cycle_entry, step_stopping = _step_b(
                state, cycle, variable, summary, dashboard_rows, run_set_id, profiles
            )

        if step_stopping:
            state["stopping_reason"] = step_stopping

        state.setdefault("cycles_completed", []).append(cycle_entry)
        _save_state(state_file, state)

        # Write cycle report
        report_text = _format_cycle_report(cycle, state, cycle_entry, profiles)
        report_path = sessions_dir / f"coordinator-cycle-{cycle}-report.md"
        report_path.write_text(report_text, encoding="utf-8")
        print(f"[cycle {cycle}] report: {report_path}", flush=True)

        # Advance playbook
        if playbook_step == "A":
            state["playbook_step"] = "B"
        elif playbook_step == "B":
            vq = list(state.get("playbook_variable_queue") or [])
            if variable and variable in vq:
                vq.remove(variable)
            state["playbook_variable_queue"] = vq
            if not vq:
                state["stopping_reason"] = "playbook_exhausted"

        # Check stopping after playbook advance
        ultimate_stop = _check_stopping(state, cycle_entry, cycle)
        if ultimate_stop:
            state["stopping_reason"] = ultimate_stop

        _save_state(state_file, state)

        if state.get("stopping_reason"):
            break

    # Write final report
    final_reason = str(state.get("stopping_reason") or "unknown")
    final_report_path = sessions_dir / "coordinator-final-report.md"
    final_report_path.write_text(
        _format_final_report(state, final_reason),
        encoding="utf-8",
    )
    print(f"Final report: {final_report_path} (reason={final_reason})", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
