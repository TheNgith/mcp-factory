from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import run_ab_parallel as ab


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


@dataclass
class RunLeg:
    leg_id: str
    layer: int
    profile_id: str


@dataclass
class RunLegResult:
    leg_id: str
    ok: bool
    layer: int
    profile_id: str
    session_dir: str | None = None
    job_id: str | None = None
    functions_success: int | None = None
    hard_fail: bool | None = None
    error: str | None = None


class PipelineClient:
    def __init__(self, api_url: str, api_key: str):
        self.base = api_url.rstrip("/")
        self.headers = {"X-Pipeline-Key": api_key} if api_key else {}

    def get_job(self, job_id: str) -> dict[str, Any]:
        r = requests.get(f"{self.base}/api/jobs/{job_id}", headers=self.headers, timeout=90)
        r.raise_for_status()
        return r.json()

    def post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        r = requests.post(f"{self.base}{path}", headers=self.headers, json=payload, timeout=120)
        r.raise_for_status()
        return r.json()

    def analyze_upload(
        self,
        dll_path: Path,
        hints: str,
        use_cases: str,
        tags: dict[str, Any],
    ) -> str:
        with dll_path.open("rb") as fh:
            files = {"file": (dll_path.name, fh, "application/octet-stream")}
            data: dict[str, Any] = {
                "hints": hints,
                "use_cases": use_cases,
            }
            for key, value in tags.items():
                if value is None:
                    continue
                data[key] = str(value)
            r = requests.post(
                f"{self.base}/api/analyze",
                headers=self.headers,
                files=files,
                data=data,
                timeout=180,
            )
        if r.status_code == 401:
            raise RuntimeError("Unauthorized calling /api/analyze. Provide --api-key or MCP_FACTORY_API_KEY.")
        r.raise_for_status()
        payload = r.json()
        return str(payload["job_id"])


def _wait_for_analyze(client: PipelineClient, job_id: str, timeout_sec: int) -> dict[str, Any]:
    start = time.time()
    while time.time() - start < timeout_sec:
        job = client.get_job(job_id)
        status = str(job.get("status") or "")
        if status in {"done", "error"}:
            return job
        time.sleep(8)
    raise TimeoutError(f"Analyze timeout for {job_id}")


def _wait_for_explore(client: PipelineClient, job_id: str, timeout_sec: int) -> dict[str, Any]:
    start = time.time()
    while time.time() - start < timeout_sec:
        job = client.get_job(job_id)
        phase = str(job.get("explore_phase") or "")
        if phase in {"done", "awaiting_clarification", "error", "failed", "cancelled"}:
            return job
        time.sleep(12)
    raise TimeoutError(f"Explore timeout for {job_id}")


def _build_legs(n_control: int, m_ablation: int, ablation_profiles: list[str]) -> list[RunLeg]:
    if m_ablation > len(ablation_profiles):
        raise ValueError("run_set_def.ablation_profiles must contain at least m_ablation entries")

    legs: list[RunLeg] = []
    for i in range(1, n_control + 1):
        legs.append(RunLeg(leg_id=f"C{i:02d}", layer=1, profile_id="baseline"))
    for i in range(1, m_ablation + 1):
        legs.append(RunLeg(leg_id=f"A{i:02d}", layer=2, profile_id=ablation_profiles[i - 1]))
    return legs


def _load_run_set_def(path: Path) -> dict[str, Any]:
    payload = _read_json(path)
    if not isinstance(payload, dict):
        raise ValueError("run-set definition must be a JSON object")
    return payload


def _load_profiles(path: Path) -> dict[str, dict[str, Any]]:
    payload = _read_json(path)
    if not isinstance(payload, dict):
        raise ValueError("prompt profiles file must be a JSON object")
    out: dict[str, dict[str, Any]] = {}
    for key, value in payload.items():
        if isinstance(key, str) and isinstance(value, dict):
            out[key] = value
    return out


def _read_hints_use_cases(repo_root: Path, base_cfg: dict[str, Any]) -> tuple[str, str]:
    hints_file = str(base_cfg.get("hints_file") or "").strip()
    if hints_file:
        hf = (repo_root / hints_file).resolve()
        if not hf.exists():
            raise FileNotFoundError(f"Hints file not found: {hf}")
        return ab._parse_hints_file(hf)

    return str(base_cfg.get("hints") or ""), str(base_cfg.get("use_cases") or "")


def _select_python(repo_root: Path) -> str:
    venv_python = repo_root / ".venv" / "Scripts" / "python.exe"
    if venv_python.exists():
        return str(venv_python)
    return sys.executable


def _run_leg(
    repo_root: Path,
    client: PipelineClient,
    leg: RunLeg,
    profile: dict[str, Any],
    run_set: dict[str, Any],
    hints: str,
    use_cases: str,
    dll_path: Path,
    output_dir: Path,
    log_lock: threading.Lock,
) -> RunLegResult:
    base_cfg = run_set.get("base_config") or {}

    def _log(text: str) -> None:
        with log_lock:
            print(f"[{leg.leg_id}] {text}", flush=True)

    profile_overrides = profile.get("overrides") if isinstance(profile, dict) else {}
    if not isinstance(profile_overrides, dict):
        profile_overrides = {}

    tags = {
        "prompt_profile_id": leg.profile_id,
        "layer": leg.layer,
        "ablation_variable": profile.get("ablation_variable"),
        "ablation_value": profile.get("ablation_value"),
        "run_set_id": run_set.get("run_set_id"),
        "coordinator_cycle": run_set.get("coordinator_cycle"),
        "playbook_step": run_set.get("playbook_step"),
    }

    _log("Uploading and analyzing")
    job_id = client.analyze_upload(dll_path, hints, use_cases, tags)
    _log(f"job_id={job_id}")

    analyze_done = _wait_for_analyze(client, job_id, int(run_set.get("analyze_timeout_sec") or 900))
    if str(analyze_done.get("status") or "") == "error":
        return RunLegResult(
            leg_id=leg.leg_id,
            ok=False,
            layer=leg.layer,
            profile_id=leg.profile_id,
            job_id=job_id,
            error=f"Analyze failed: {analyze_done.get('error')}",
        )

    job_state = client.get_job(job_id)
    invocables = (job_state.get("result") or {}).get("invocables") or []
    if not invocables:
        return RunLegResult(
            leg_id=leg.leg_id,
            ok=False,
            layer=leg.layer,
            profile_id=leg.profile_id,
            job_id=job_id,
            error="No invocables returned from analyze",
        )

    component = str(run_set.get("component") or "unknown")
    _log("Registering invocables")
    client.post_json(
        "/api/generate",
        {
            "job_id": job_id,
            "component_name": component,
            "selected": invocables,
        },
    )

    explore_settings = {
        "mode": str(base_cfg.get("mode") or "dev"),
        "model": str(base_cfg.get("model") or "gpt-4o"),
        "max_rounds": int(base_cfg.get("max_rounds") or 2),
        "max_tool_calls": int(base_cfg.get("max_tool_calls") or 5),
        "gap_resolution_enabled": bool(base_cfg.get("gap_resolution_enabled", True)),
    }
    if "max_tool_calls" in profile_overrides:
        try:
            explore_settings["max_tool_calls"] = int(profile_overrides["max_tool_calls"])
        except Exception:
            pass

    explore_payload = {
        "explore_settings": explore_settings,
        "prompt_profile_id": leg.profile_id,
        "ablation_variable": tags["ablation_variable"],
        "ablation_value": tags["ablation_value"],
        "run_set_id": tags["run_set_id"],
        "coordinator_cycle": tags["coordinator_cycle"],
        "playbook_step": tags["playbook_step"],
    }

    _log("Starting explore")
    client.post_json(f"/api/jobs/{job_id}/explore", explore_payload)
    done = _wait_for_explore(client, job_id, int(run_set.get("explore_timeout_sec") or 2400))
    _log(f"Explore finished: phase={done.get('explore_phase')}")

    leg_dir = output_dir / "sessions" / f"leg-{leg.leg_id.lower()}"
    note = f"runset-{run_set['run_set_id']}-{leg.leg_id.lower()}"
    _log(f"Saving session to {leg_dir}")
    ab._run_save_session(repo_root, str(run_set.get("api_url") or ""), str(run_set.get("api_key") or ""), job_id, note, leg_dir)

    dashboard_path = leg_dir / "human" / "dashboard-row.json"
    dashboard = _read_json(dashboard_path)

    # Patch ablation tags into dashboard-row.json — server-side fields may not
    # propagate if the deployed container is older than the Q15 code.
    if isinstance(dashboard, dict):
        dashboard["prompt_profile_id"] = leg.profile_id
        dashboard["layer"] = leg.layer
        dashboard["ablation_variable"] = tags.get("ablation_variable")
        dashboard["ablation_value"] = tags.get("ablation_value")
        dashboard["run_set_id"] = run_set.get("run_set_id")
        dashboard["coordinator_cycle"] = run_set.get("coordinator_cycle")
        dashboard["playbook_step"] = run_set.get("playbook_step")
        _write_json(dashboard_path, dashboard)

    functions_success = int((dashboard or {}).get("functions_success") or 0)
    if functions_success <= 0:
        findings_candidates = [
            leg_dir / "stage-05-finalization" / "findings.json",
            leg_dir / "evidence" / "stage-02-probe-loop" / "findings.json",
            leg_dir / "evidence" / "stage-07-finalize" / "findings.json",
            leg_dir / "findings.json",
        ]
        findings: list[dict[str, Any]] = []
        for fp in findings_candidates:
            if not fp.exists():
                continue
            payload = _read_json(fp)
            if isinstance(payload, list):
                findings = [x for x in payload if isinstance(x, dict)]
                break
        if findings:
            functions_success = sum(1 for x in findings if str(x.get("status") or "").lower() == "success")

    hard_fail = bool((dashboard or {}).get("hard_fail") or False)

    return RunLegResult(
        leg_id=leg.leg_id,
        ok=True,
        layer=leg.layer,
        profile_id=leg.profile_id,
        job_id=job_id,
        session_dir=str(leg_dir),
        functions_success=functions_success,
        hard_fail=hard_fail,
    )


def _invoke_union_merger(repo_root: Path, session_dirs: list[str], out_dir: Path, run_set_id: str) -> dict[str, Any]:
    # Verify all directories exist before launching the subprocess — gives a
    # clear error if a save-session race condition dropped a directory.
    missing = [p for p in session_dirs if not Path(p).is_dir()]
    if missing:
        raise RuntimeError(f"union_merger pre-check: directories missing: {missing}")

    # Write paths to a manifest JSON file instead of passing on the command line
    # to avoid Windows path quoting / arg-length issues.
    manifest_path = out_dir / "merged-schema" / "sessions-manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(session_dirs), encoding="utf-8")

    python_exe = _select_python(repo_root)
    cmd = [
        python_exe,
        str(repo_root / "scripts" / "union_merger.py"),
        "--sessions-manifest",
        str(manifest_path),
        "--output",
        str(out_dir / "merged-schema"),
        "--run-set-id",
        run_set_id,
    ]
    r = subprocess.run(cmd, cwd=str(repo_root), capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"union_merger failed\nSTDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}")

    return _read_json(out_dir / "merged-schema" / "merge-summary.json")


def _validate_run_set_def(run_set: dict[str, Any]) -> None:
    missing = []
    for key in ("component", "api_url", "base_config", "output_dir"):
        if key not in run_set:
            missing.append(key)
    if missing:
        raise ValueError(f"run_set_def missing required keys: {', '.join(missing)}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Dispatch run-set controls + ablations and merge via UNION strategy")
    parser.add_argument("--run-set-def", required=True)
    parser.add_argument("--api-key", default="")
    parser.add_argument("--profiles", default="scripts/prompt_profiles.json")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    run_set_path = (repo_root / args.run_set_def).resolve()
    profiles_path = (repo_root / args.profiles).resolve()

    run_set = _load_run_set_def(run_set_path)
    _validate_run_set_def(run_set)
    run_set_id = str(run_set.get("run_set_id") or f"runset-{datetime.now():%Y-%m-%d}-{uuid.uuid4().hex[:6]}")
    run_set["run_set_id"] = run_set_id

    api_key = args.api_key or str(run_set.get("api_key") or os.getenv("MCP_FACTORY_API_KEY") or "")
    run_set["api_key"] = api_key

    base_cfg = run_set.get("base_config") or {}
    dll_rel = str(base_cfg.get("dll") or "tests/fixtures/contoso_legacy/contoso_cs.dll")
    dll_path = (repo_root / dll_rel).resolve()
    if not dll_path.exists():
        raise FileNotFoundError(f"DLL not found: {dll_path}")

    hints, use_cases = _read_hints_use_cases(repo_root, base_cfg)

    profiles = _load_profiles(profiles_path)
    if "baseline" not in profiles:
        raise ValueError("profiles file must include baseline profile")

    n_control = int(run_set.get("n_control") or 3)
    m_ablation = int(run_set.get("m_ablation") or 3)
    ablation_profiles = list(run_set.get("ablation_profiles") or [])
    if not all(isinstance(x, str) for x in ablation_profiles):
        raise ValueError("ablation_profiles must be a list of profile IDs")

    legs = _build_legs(n_control, m_ablation, [str(x) for x in ablation_profiles])
    for leg in legs:
        if leg.profile_id not in profiles:
            raise ValueError(f"Unknown profile '{leg.profile_id}'")

    output_dir = (repo_root / str(run_set.get("output_dir"))).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    max_parallel = int(run_set.get("max_parallel") or len(legs) or 1)
    max_parallel = max(1, min(max_parallel, len(legs)))

    client = PipelineClient(str(run_set.get("api_url")), api_key)
    lock = threading.Lock()

    print(
        f"Launching run-set {run_set_id}: controls={n_control} ablations={m_ablation} parallel={max_parallel}",
        flush=True,
    )

    results: list[RunLegResult] = []
    futures = {}
    with ThreadPoolExecutor(max_workers=max_parallel) as pool:
        for leg in legs:
            futures[
                pool.submit(
                    _run_leg,
                    repo_root,
                    client,
                    leg,
                    profiles[leg.profile_id],
                    run_set,
                    hints,
                    use_cases,
                    dll_path,
                    output_dir,
                    lock,
                )
            ] = leg.leg_id

        for fut in as_completed(futures):
            try:
                results.append(fut.result())
            except Exception as exc:
                leg_id = futures[fut]
                leg = next((x for x in legs if x.leg_id == leg_id), None)
                results.append(
                    RunLegResult(
                        leg_id=leg_id,
                        ok=False,
                        layer=leg.layer if leg else 0,
                        profile_id=leg.profile_id if leg else "unknown",
                        error=str(exc),
                    )
                )

    results.sort(key=lambda r: r.leg_id)
    failed = [x for x in results if not x.ok]
    succeeded = [x for x in results if x.ok and x.session_dir]

    summary: dict[str, Any] = {
        "run_set_id": run_set_id,
        "completed_at": _now_utc(),
        "n_control": n_control,
        "m_ablation": m_ablation,
        "sessions": [
            {
                "folder": x.session_dir,
                "layer": x.layer,
                "profile": x.profile_id,
                "functions_success": x.functions_success,
                "hard_fail": x.hard_fail,
                "ok": x.ok,
                "job_id": x.job_id,
                "error": x.error,
            }
            for x in results
        ],
    }

    control_success = [int(x.functions_success or 0) for x in succeeded if x.layer == 1]
    ablation_success = [int(x.functions_success or 0) for x in succeeded if x.layer == 2]
    summary["control_median_success"] = int(statistics.median(control_success)) if control_success else 0
    summary["best_ablation_success"] = max(ablation_success) if ablation_success else 0

    if failed:
        summary["union_success"] = 0
        summary["union_gain_vs_best_single"] = 0
        summary["merge_id"] = None
        _write_json(output_dir / "run-set-summary.json", summary)
        for item in failed:
            print(f"FAILED {item.leg_id}: {item.error}", flush=True)
        return 1

    merge_summary = _invoke_union_merger(
        repo_root,
        [str(x.session_dir) for x in succeeded if x.session_dir],
        output_dir,
        run_set_id,
    )

    summary["union_success"] = int(merge_summary.get("functions_success") or 0)
    summary["union_gain_vs_best_single"] = int(merge_summary.get("union_gain_vs_best_single") or 0)
    summary["merge_id"] = merge_summary.get("merge_id")

    _write_json(output_dir / "run-set-summary.json", summary)

    print(f"Run-set summary: {output_dir / 'run-set-summary.json'}", flush=True)
    print(f"Merged schema: {output_dir / 'merged-schema' / 'accumulated-findings.json'}", flush=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
