"""
run_ab_parallel.py - Launch two identical pipeline runs concurrently (A/B),
save strict snapshots, and emit one deterministic side-by-side comparison.

This script is intentionally smaller in scope than the full model sweep plan.
It is optimized for fast deterministic iteration loops.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from api import transition_readiness as tr


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _git_short_hash(repo_root: Path) -> str:
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            check=True,
        )
        return (r.stdout or "").strip() or "nogit"
    except Exception:
        return "nogit"


def _git_commit_msg(repo_root: Path) -> str:
    try:
        r = subprocess.run(
            ["git", "log", "-1", "--format=%s"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            check=True,
        )
        return (r.stdout or "").strip()
    except Exception:
        return ""


def _read_json(path: Path) -> Any:
    text = path.read_text(encoding="utf-8-sig")
    if not text.strip():
        return None
    return json.loads(text)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _write_failure_summary(path: Path, error_text: str, run_cfg: RunConfig, commit: str, commit_msg: str) -> None:
    payload = {
        "created_at": _now_utc(),
        "type": "ab_compare_failure",
        "error": error_text,
        "run_manifest": {
            "commit": commit,
            "commit_msg": commit_msg,
            "mode": run_cfg.mode,
            "model": run_cfg.model,
            "max_rounds": run_cfg.max_rounds,
            "max_tool_calls": run_cfg.max_tool_calls,
            "gap_resolution_enabled": run_cfg.gap_resolution_enabled,
            "hint_variant": run_cfg.hint_variant,
            "hints_sha256": _sha256_text(run_cfg.hints),
            "use_cases_sha256": _sha256_text(run_cfg.use_cases),
        },
    }
    _write_json(path, payload)


def _parse_hints_file(path: Path) -> tuple[str, str]:
    text = path.read_text(encoding="utf-8")
    lower = text.lower()
    i_hints = lower.find("# hints")
    i_use = lower.find("# use_cases")
    if i_hints == -1 or i_use == -1:
        return text.strip(), ""
    hints = text[i_hints + len("# hints") : i_use].strip()
    use_cases = text[i_use + len("# use_cases") :].strip()
    return hints, use_cases


@dataclass
class RunConfig:
    api_url: str
    api_key: str
    dll_path: Path
    hints: str
    use_cases: str
    mode: str
    model: str
    max_rounds: int
    max_tool_calls: int
    gap_resolution_enabled: bool
    sessions_root: Path
    run_root: Path
    note_prefix: str
    hint_variant: str = "default"


class PipelineClient:
    def __init__(self, api_url: str, api_key: str):
        self.base = api_url.rstrip("/")
        self.headers = {"X-Pipeline-Key": api_key} if api_key else {}

    def get_job(self, job_id: str) -> dict:
        r = requests.get(f"{self.base}/api/jobs/{job_id}", headers=self.headers, timeout=90)
        r.raise_for_status()
        return r.json()

    def post_json(self, path: str, payload: dict) -> dict:
        r = requests.post(
            f"{self.base}{path}",
            headers=self.headers,
            json=payload,
            timeout=120,
        )
        r.raise_for_status()
        return r.json()

    def analyze_upload(self, dll_path: Path, hints: str, use_cases: str) -> str:
        with dll_path.open("rb") as fh:
            files = {
                "file": (dll_path.name, fh, "application/octet-stream"),
            }
            data = {
                "hints": hints,
                "use_cases": use_cases,
            }
            r = requests.post(
                f"{self.base}/api/analyze",
                headers=self.headers,
                files=files,
                data=data,
                timeout=180,
            )
        if r.status_code == 401:
            raise RuntimeError(
                "Unauthorized calling /api/analyze. Provide a valid --api-key or set MCP_FACTORY_API_KEY."
            )
        r.raise_for_status()
        payload = r.json()
        return str(payload["job_id"])


def _wait_for_analyze(client: PipelineClient, job_id: str, timeout_sec: int = 600) -> dict:
    start = time.time()
    while time.time() - start < timeout_sec:
        job = client.get_job(job_id)
        st = str(job.get("status") or "")
        if st in {"done", "error"}:
            return job
        time.sleep(8)
    raise TimeoutError(f"Analyze timeout for job {job_id}")


def _wait_for_explore(client: PipelineClient, job_id: str, timeout_sec: int = 1800) -> dict:
    start = time.time()
    while time.time() - start < timeout_sec:
        job = client.get_job(job_id)
        phase = str(job.get("explore_phase") or "")
        if phase in {"done", "awaiting_clarification", "error", "failed", "cancelled"}:
            return job
        time.sleep(12)
    raise TimeoutError(f"Explore timeout for job {job_id}")


def _run_save_session(repo_root: Path, api_url: str, api_key: str, job_id: str, note: str, out_dir: Path) -> None:
    cmd = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(repo_root / "scripts" / "save-session.ps1"),
        "-JobId",
        job_id,
        "-ApiUrl",
        api_url,
        "-Note",
        note,
        "-OutDir",
        str(out_dir),
        "-CompatibilityMode",
    ]
    if api_key:
        cmd.extend(["-ApiKey", api_key])
    r = subprocess.run(cmd, cwd=str(repo_root), capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(
            f"save-session failed for job {job_id}\nSTDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}"
        )


def _extract_result(session_dir: Path, run_cfg: RunConfig, leg: str, job_id: str) -> dict:
    collect_path = session_dir / "human" / "collect-session-result.json"
    cohesion_path = session_dir / "cohesion-report.json"
    findings_candidates = [
        session_dir / "stage-05-finalization" / "findings.json",
        session_dir / "evidence" / "stage-02-probe-loop" / "findings.json",
        session_dir / "findings.json",
    ]
    final_status_candidates = [
        session_dir / "evidence" / "stage-07-finalize" / "final-status.json",
        session_dir / "final-status.json",
    ]
    explore_cfg_candidates = [
        session_dir / "stage-00-setup" / "explore_config.json",
        session_dir / "evidence" / "stage-00-setup" / "explore-config.json",
    ]

    collect = _read_json(collect_path) if collect_path.exists() else {}
    cohesion = _read_json(cohesion_path) if cohesion_path.exists() else {}

    findings: list[dict] = []
    for fp in findings_candidates:
        if fp.exists():
            findings = _read_json(fp)
            break

    final_status: dict[str, Any] = {}
    for fp in final_status_candidates:
        if fp.exists():
            final_status = _read_json(fp)
            break

    explore_cfg: dict[str, Any] = {}
    for fp in explore_cfg_candidates:
        if fp.exists():
            explore_cfg = _read_json(fp)
            break

    success_count = 0
    error_count = 0
    unresolved_functions: list[str] = []
    if isinstance(findings, list):
        for row in findings:
            st = str((row or {}).get("status") or "")
            fn = str((row or {}).get("function") or "")
            if st == "success":
                success_count += 1
            elif st == "error":
                error_count += 1
                if fn:
                    unresolved_functions.append(fn)

    totals = (cohesion or {}).get("totals") or {}
    gates = (cohesion or {}).get("gates") or {}

    runtime = (final_status or {}).get("explore_runtime") or {}

    model_value = str(runtime.get("model") or run_cfg.model or "")

    return {
        "leg": leg,
        "job_id": job_id,
        "session_dir": str(session_dir),
        "contract": {
            "valid": bool(((collect or {}).get("contract") or {}).get("valid")),
            "hard_fail": bool(((collect or {}).get("contract") or {}).get("hard_fail")),
            "capture_quality": str((((collect or {}).get("session_save_meta") or {}).get("capture_quality") or "unknown")),
        },
        "runtime": {
            "mode": str(explore_cfg.get("mode") or runtime.get("mode") or run_cfg.mode),
            "model": model_value,
            "max_rounds": int(explore_cfg.get("max_rounds_per_function") or runtime.get("max_rounds") or run_cfg.max_rounds),
            "max_tool_calls": int(explore_cfg.get("max_tool_calls_per_function") or runtime.get("max_tool_calls") or run_cfg.max_tool_calls),
            "gap_resolution_enabled": bool(explore_cfg.get("gap_resolution_enabled") if "gap_resolution_enabled" in explore_cfg else runtime.get("gap_resolution_enabled", run_cfg.gap_resolution_enabled)),
        },
        "cohesion": {
            "stage_pass": int(totals.get("stage_pass") or 0),
            "stage_fail": int(totals.get("stage_fail") or 0),
            "transition_pass": int(totals.get("transition_pass") or 0),
            "transition_fail": int(totals.get("transition_fail") or 0),
            "transition_warn": int(totals.get("transition_warn") or 0),
            "transition_partial": int(totals.get("transition_partial") or 0),
            "transition_na": int(totals.get("transition_na") or 0),
            "failed_transitions": list((cohesion or {}).get("failed_transitions") or []),
            "failed_stages": list((cohesion or {}).get("failed_stages") or []),
            "gate_reasons": list(gates.get("reasons") or []),
        },
        "function_outcome": {
            "success": success_count,
            "error": error_count,
            "total": success_count + error_count,
            "unresolved_functions": sorted(set(unresolved_functions)),
        },
    }


def _build_comparison_payload(run_cfg: RunConfig, commit: str, commit_msg: str, a: dict, b: dict) -> dict:
    diffs: list[str] = []

    def _cmp(path: str, va: Any, vb: Any) -> None:
        if va != vb:
            diffs.append(f"{path}: A={va!r} B={vb!r}")

    _cmp("contract.valid", a["contract"]["valid"], b["contract"]["valid"])
    _cmp("contract.hard_fail", a["contract"]["hard_fail"], b["contract"]["hard_fail"])
    _cmp("runtime.mode", a["runtime"]["mode"], b["runtime"]["mode"])
    _cmp("runtime.model", a["runtime"]["model"], b["runtime"]["model"])
    _cmp("runtime.max_rounds", a["runtime"]["max_rounds"], b["runtime"]["max_rounds"])
    _cmp("runtime.max_tool_calls", a["runtime"]["max_tool_calls"], b["runtime"]["max_tool_calls"])
    _cmp("runtime.gap_resolution_enabled", a["runtime"]["gap_resolution_enabled"], b["runtime"]["gap_resolution_enabled"])

    for k in (
        "stage_pass",
        "stage_fail",
        "transition_pass",
        "transition_fail",
        "transition_warn",
        "transition_partial",
        "transition_na",
    ):
        _cmp(f"cohesion.{k}", a["cohesion"][k], b["cohesion"][k])

    _cmp("cohesion.failed_transitions", sorted(a["cohesion"]["failed_transitions"]), sorted(b["cohesion"]["failed_transitions"]))
    _cmp("function_outcome.success", a["function_outcome"]["success"], b["function_outcome"]["success"])
    _cmp("function_outcome.error", a["function_outcome"]["error"], b["function_outcome"]["error"])
    _cmp(
        "function_outcome.unresolved_functions",
        sorted(a["function_outcome"]["unresolved_functions"]),
        sorted(b["function_outcome"]["unresolved_functions"]),
    )

    deterministic = len(diffs) == 0

    return {
        "created_at": _now_utc(),
        "type": "ab_compare",
        "deterministic": deterministic,
        "differences": diffs,
        "run_manifest": {
            "commit": commit,
            "commit_msg": commit_msg,
            "mode": run_cfg.mode,
            "model": run_cfg.model,
            "max_rounds": run_cfg.max_rounds,
            "max_tool_calls": run_cfg.max_tool_calls,
            "gap_resolution_enabled": run_cfg.gap_resolution_enabled,
            "hint_variant": run_cfg.hint_variant,
            "hints_sha256": _sha256_text(run_cfg.hints),
            "use_cases_sha256": _sha256_text(run_cfg.use_cases),
            "hints_preview": run_cfg.hints[:240],
            "use_cases_preview": run_cfg.use_cases[:240],
        },
        "A": a,
        "B": b,
    }


def _write_markdown_summary(path: Path, payload: dict) -> None:
    a = payload["A"]
    b = payload["B"]
    lines = [
        "# A/B Parallel Comparison",
        "",
        f"- Created: {payload['created_at']}",
        f"- Deterministic: {payload['deterministic']}",
        f"- Commit: {payload['run_manifest']['commit']} {payload['run_manifest']['commit_msg']}",
        "",
        "## Inputs",
        "",
        f"- Mode: {payload['run_manifest']['mode']}",
        f"- Model: {payload['run_manifest']['model']}",
        f"- Max rounds: {payload['run_manifest']['max_rounds']}",
        f"- Max tool calls: {payload['run_manifest']['max_tool_calls']}",
        f"- Gap resolution enabled: {payload['run_manifest']['gap_resolution_enabled']}",
        "",
        "## Side-by-Side",
        "",
        "| Field | A | B |",
        "|---|---:|---:|",
        f"| contract_valid | {a['contract']['valid']} | {b['contract']['valid']} |",
        f"| hard_fail | {a['contract']['hard_fail']} | {b['contract']['hard_fail']} |",
        f"| capture_quality | {a['contract']['capture_quality']} | {b['contract']['capture_quality']} |",
        f"| stage_pass | {a['cohesion']['stage_pass']} | {b['cohesion']['stage_pass']} |",
        f"| stage_fail | {a['cohesion']['stage_fail']} | {b['cohesion']['stage_fail']} |",
        f"| transition_pass | {a['cohesion']['transition_pass']} | {b['cohesion']['transition_pass']} |",
        f"| transition_fail | {a['cohesion']['transition_fail']} | {b['cohesion']['transition_fail']} |",
        f"| transition_warn | {a['cohesion']['transition_warn']} | {b['cohesion']['transition_warn']} |",
        f"| transition_partial | {a['cohesion']['transition_partial']} | {b['cohesion']['transition_partial']} |",
        f"| success_functions | {a['function_outcome']['success']} | {b['function_outcome']['success']} |",
        f"| error_functions | {a['function_outcome']['error']} | {b['function_outcome']['error']} |",
        "",
        "## Differences",
        "",
    ]
    if payload["differences"]:
        lines.extend(f"- {d}" for d in payload["differences"])
    else:
        lines.append("- none")

    path.write_text("\n".join(lines), encoding="utf-8")


def _append_index(index_path: Path, payload: dict) -> None:
    rows: list[dict] = []
    if index_path.exists():
        loaded = _read_json(index_path)
        if loaded is None:
            rows = []
        elif isinstance(loaded, list):
            rows = loaded
        else:
            # Keep automation resilient: malformed index should not kill a run.
            rows = []

    a = payload["A"]
    b = payload["B"]
    run_manifest = payload["run_manifest"]

    rows.append(
        {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "type": "ab_compare",
            "deterministic": payload["deterministic"],
            "commit": run_manifest["commit"],
            "commit_msg": run_manifest["commit_msg"],
            "mode": run_manifest["mode"],
            "model": run_manifest["model"],
            "max_rounds": run_manifest["max_rounds"],
            "max_tool_calls": run_manifest["max_tool_calls"],
            "gap_resolution_enabled": run_manifest["gap_resolution_enabled"],
            "hints_sha256": run_manifest["hints_sha256"],
            "use_cases_sha256": run_manifest["use_cases_sha256"],
            "job_a": a["job_id"],
            "job_b": b["job_id"],
            "folder_a": a["session_dir"],
            "folder_b": b["session_dir"],
            "success_a": a["function_outcome"]["success"],
            "success_b": b["function_outcome"]["success"],
            "transition_warn_a": a["cohesion"]["transition_warn"],
            "transition_warn_b": b["cohesion"]["transition_warn"],
            "transition_partial_a": a["cohesion"]["transition_partial"],
            "transition_partial_b": b["cohesion"]["transition_partial"],
            "differences": payload["differences"],
            "saved_at": _now_utc(),
        }
    )

    _write_json(index_path, rows)


def _run_one_leg(repo_root: Path, run_cfg: RunConfig, leg: str, log_lock: threading.Lock) -> dict:
    client = PipelineClient(run_cfg.api_url, run_cfg.api_key)

    def _log(msg: str) -> None:
        with log_lock:
            print(f"[{leg}] {msg}", flush=True)

    _log("Uploading and analyzing")
    job_id = client.analyze_upload(run_cfg.dll_path, run_cfg.hints, run_cfg.use_cases)
    _log(f"job_id={job_id}")

    analyze = _wait_for_analyze(client, job_id)
    if str(analyze.get("status")) == "error":
        raise RuntimeError(f"Analyze failed for {leg}: {analyze.get('error')}")

    state = client.get_job(job_id)
    invocables = (state.get("result") or {}).get("invocables") or []
    if not invocables:
        raise RuntimeError(f"No invocables found for {leg} job {job_id}")

    _log("Registering invocables via /api/generate")
    client.post_json(
        "/api/generate",
        {
            "job_id": job_id,
            "component_name": "contoso-cs",
            "selected": invocables,
        },
    )

    explore_payload = {
        "explore_settings": {
            "mode": run_cfg.mode,
            "model": run_cfg.model,
            "max_rounds": run_cfg.max_rounds,
            "max_tool_calls": run_cfg.max_tool_calls,
            "gap_resolution_enabled": run_cfg.gap_resolution_enabled,
        }
    }
    _log("Starting explore")
    client.post_json(f"/api/jobs/{job_id}/explore", explore_payload)

    done = _wait_for_explore(client, job_id)
    _log(f"Explore finished: phase={done.get('explore_phase')} progress={done.get('explore_progress')}")

    leg_dir = run_cfg.run_root / f"leg-{leg.lower()}"
    note = f"{run_cfg.note_prefix}-{leg.lower()}"
    _log(f"Saving strict session to {leg_dir}")
    _run_save_session(repo_root, run_cfg.api_url, run_cfg.api_key, job_id, note, leg_dir)

    return _extract_result(leg_dir, run_cfg, leg, job_id)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run A/B pipeline jobs in parallel and compare outputs")
    parser.add_argument("--api-url", required=True, help="Pipeline API base URL")
    parser.add_argument("--api-key", default="", help="Pipeline API key (or use MCP_FACTORY_API_KEY env)")
    parser.add_argument("--dll", default="tests/fixtures/contoso_legacy/contoso_cs.dll")
    parser.add_argument("--hints-file", default="sessions/contoso_cs/contoso_cs.txt")
    parser.add_argument("--mode", default="dev")
    parser.add_argument("--model", default="gpt-4o")
    parser.add_argument("--max-rounds", type=int, default=2)
    parser.add_argument("--max-tool-calls", type=int, default=5)
    parser.add_argument(
        "--gap-resolution-enabled",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Enable/disable phase-8 gap resolution",
    )
    parser.add_argument("--note-prefix", default="ab-parallel")
    parser.add_argument("--sessions-root", default="sessions")
    parser.add_argument("--append-index", action="store_true", help="Append compact A/B row to sessions/index.json")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    api_key = args.api_key or os.getenv("MCP_FACTORY_API_KEY", "")

    dll_path = (repo_root / args.dll).resolve()
    hints_file = (repo_root / args.hints_file).resolve()
    if not dll_path.exists():
        raise FileNotFoundError(f"DLL not found: {dll_path}")
    if not hints_file.exists():
        raise FileNotFoundError(f"Hints file not found: {hints_file}")

    hints, use_cases = _parse_hints_file(hints_file)

    commit = _git_short_hash(repo_root)
    commit_msg = _git_commit_msg(repo_root)

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    sessions_root = (repo_root / args.sessions_root).resolve()
    run_root = sessions_root / "_runs" / f"{datetime.now():%Y-%m-%d}-{commit}-{args.note_prefix}-{ts}"
    run_root.mkdir(parents=True, exist_ok=True)

    hint_variant = hints_file.stem if args.hints_file != "sessions/contoso_cs/contoso_cs.txt" else "default"

    run_cfg = RunConfig(
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
        hint_variant=hint_variant,
    )

    print("Launching parallel A/B runs...", flush=True)
    log_lock = threading.Lock()
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            f_a = pool.submit(_run_one_leg, repo_root, run_cfg, "A", log_lock)
            f_b = pool.submit(_run_one_leg, repo_root, run_cfg, "B", log_lock)
            a = f_a.result()
            b = f_b.result()
    except Exception as ex:
        failure_json = run_root / "ab-failure.json"
        _write_failure_summary(failure_json, str(ex), run_cfg, commit, commit_msg)
        print(f"A/B run failed: {ex}", flush=True)
        print(f"Failure JSON: {failure_json}", flush=True)
        return 1

    payload = _build_comparison_payload(run_cfg, commit, commit_msg, a, b)

    a_readiness = tr.evaluate_session(Path(a["session_dir"]))
    b_readiness = tr.evaluate_session(Path(b["session_dir"]))
    readiness = tr.build_ab_readiness(
        leg_a=a_readiness,
        leg_b=b_readiness,
        deterministic=payload["deterministic"],
        require_determinism=True,
    )
    payload["transition_readiness"] = {
        "pass": readiness["pass"],
        "reasons": readiness["reasons"],
    }

    compare_json = run_root / "ab-compare.json"
    compare_md = run_root / "ab-compare.md"
    _write_json(compare_json, payload)
    _write_markdown_summary(compare_md, payload)

    readiness_json = run_root / "transition-readiness.json"
    readiness_md = run_root / "transition-readiness.md"
    tr.write_readiness_json(readiness_json, readiness)
    tr.write_readiness_markdown(readiness_md, readiness)

    # Phase-gate: evaluate discovery satisfaction for each leg independently.
    # This tells the autopilot whether discovery produced enough signal to
    # advance to the gap-resolution phase (rather than retrying discovery).
    a_dir = Path(a["session_dir"]) if a.get("session_dir") else None
    b_dir = Path(b["session_dir"]) if b.get("session_dir") else None
    for leg_dir in filter(None, [a_dir, b_dir]):
        if leg_dir.exists():
            disc_sat = tr.evaluate_discovery_satisfaction(leg_dir)
            tr.write_readiness_json(leg_dir / "discovery-satisfaction.json", disc_sat)
    a_sat = tr.evaluate_discovery_satisfaction(a_dir) if a_dir and a_dir.exists() else {"pass": False, "reasons": ["no_leg_dir"]}
    b_sat = tr.evaluate_discovery_satisfaction(b_dir) if b_dir and b_dir.exists() else {"pass": False, "reasons": ["no_leg_dir"]}
    agg_sat = {
        "created_at": tr._now_utc(),
        "type": "discovery_satisfaction_ab",
        "pass": bool(a_sat.get("pass") and b_sat.get("pass")),
        "leg_a": a_sat,
        "leg_b": b_sat,
    }
    tr.write_readiness_json(run_root / "discovery-satisfaction.json", agg_sat)
    print(f"Discovery satisfaction: {'PASS' if agg_sat['pass'] else 'FAIL'} | "
          f"A findings={a_sat.get('finding_count')} gaps={a_sat.get('gap_count')} | "
          f"B findings={b_sat.get('finding_count')} gaps={b_sat.get('gap_count')}", flush=True)

    if args.append_index:
        _append_index(sessions_root / "index.json", payload)

    print("\n=== A/B Summary ===", flush=True)
    print(f"Deterministic: {payload['deterministic']}", flush=True)
    print(f"A job: {a['job_id']}  success={a['function_outcome']['success']}  warn={a['cohesion']['transition_warn']}", flush=True)
    print(f"B job: {b['job_id']}  success={b['function_outcome']['success']}  warn={b['cohesion']['transition_warn']}", flush=True)
    print(f"Comparison JSON: {compare_json}", flush=True)
    print(f"Comparison MD:   {compare_md}", flush=True)
    print(tr.compact_summary_line(readiness), flush=True)
    print(f"Readiness JSON:  {readiness_json}", flush=True)
    print(f"Readiness MD:    {readiness_md}", flush=True)
    if payload["differences"]:
        print("Differences:", flush=True)
        for d in payload["differences"]:
            print(f"  - {d}", flush=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
