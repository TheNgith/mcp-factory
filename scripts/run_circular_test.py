"""scripts/run_circular_test.py — Run 3 iterations of the circular feedback pipeline.

Iteration 1: Cold start (no prior context)
Iteration 2: Warm start (seeded with iteration 1's findings, sentinels, vocab)
Iteration 3: Hot start (seeded with iteration 2's output)

Each iteration uploads the DLL, runs explore, and saves the session.
The prior_job_id parameter chains them together.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import requests

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _poll_job(api_url: str, job_id: str, headers: dict, timeout_sec: int = 2400) -> dict:
    """Poll until explore_phase is done/error/awaiting_clarification."""
    start = time.time()
    last_msg = ""
    while time.time() - start < timeout_sec:
        try:
            resp = requests.get(f"{api_url}/api/jobs/{job_id}", headers=headers, timeout=30)
            status = resp.json()
            phase = str(status.get("explore_phase") or "")
            msg = str(status.get("explore_message") or status.get("explore_progress") or "")
            if msg != last_msg:
                elapsed = int(time.time() - start)
                print(f"  [{elapsed}s] phase={phase} | {msg}", flush=True)
                last_msg = msg
            if phase in ("done", "error", "awaiting_clarification", "canceled"):
                return status
        except Exception as exc:
            print(f"  [poll error: {exc}]", flush=True)
        time.sleep(15)
    print(f"  TIMEOUT after {timeout_sec}s", flush=True)
    return {}


def _run_iteration(
    api_url: str,
    headers: dict,
    dll_path: str,
    hints: str,
    iteration: int,
    prior_job_id: str = "",
    explore_mode: str = "normal",
) -> str:
    """Run one pipeline iteration. Returns the job_id."""
    print(f"\n{'='*60}", flush=True)
    label = {1: "COLD START", 2: "WARM START", 3: "HOT START"}.get(iteration, f"Iteration {iteration}")
    print(f"ITERATION {iteration}: {label}", flush=True)
    if prior_job_id:
        print(f"  prior_job_id = {prior_job_id}", flush=True)
    print(f"{'='*60}", flush=True)

    # 1. Upload DLL
    print("  Uploading DLL...", flush=True)
    with open(dll_path, "rb") as f:
        resp = requests.post(
            f"{api_url}/api/analyze",
            files={"file": (Path(dll_path).name, f, "application/octet-stream")},
            headers=headers,
            timeout=120,
        )
    resp.raise_for_status()
    job_id = resp.json()["job_id"]
    print(f"  job_id = {job_id}", flush=True)

    # 2. Wait for analysis to complete
    time.sleep(5)

    # 3. Start explore with prior_job_id if set
    explore_body = {
        "explore_settings": {
            "mode": explore_mode,
            "prior_job_id": prior_job_id,
        },
    }
    if hints:
        explore_body["hints"] = hints

    print(f"  Starting explore (mode={explore_mode})...", flush=True)
    resp = requests.post(
        f"{api_url}/api/jobs/{job_id}/explore",
        json=explore_body,
        headers=headers,
        timeout=30,
    )
    resp.raise_for_status()
    print(f"  Explore started: {resp.json()}", flush=True)

    # 4. Poll until done
    final_status = _poll_job(api_url, job_id, headers)
    functions_success = final_status.get("functions_success", "?")
    functions_total = final_status.get("functions_total", "?")
    write_unlock = final_status.get("write_unlock_outcome", "?")
    print(f"\n  RESULT: {functions_success}/{functions_total} functions | write_unlock={write_unlock}", flush=True)

    return job_id


def main() -> int:
    parser = argparse.ArgumentParser(description="Run 3-iteration circular feedback test")
    parser.add_argument("--api-url", default="https://mcp-factory-pipeline.icycoast-8ddfa278.eastus.azurecontainerapps.io")
    parser.add_argument("--api-key", default="")
    parser.add_argument("--dll", default=r"C:\Users\evanw\Downloads\mcp-test-binaries\contoso_cs.dll")
    parser.add_argument("--iterations", type=int, default=3)
    parser.add_argument("--mode", default="normal")
    args = parser.parse_args()

    api_key = args.api_key or os.getenv("MCP_FACTORY_API_KEY", "")
    if not api_key:
        print("ERROR: --api-key or MCP_FACTORY_API_KEY required", file=sys.stderr)
        return 1

    headers = {"X-Pipeline-Key": api_key}

    hints = (
        "Contoso Customer Service DLL. Functions: CS_Initialize (call first), "
        "CS_GetVersion, CS_GetCustomerName, CS_GetAccountBalance, CS_GetLoyaltyPoints, "
        "CS_ProcessPayment, CS_ProcessRefund, CS_RedeemLoyaltyPoints, CS_UnlockAccount, "
        "CS_ValidateAccount, CS_CheckAccountStatus, CS_GetOrderStatus, CS_LookupCustomer, "
        "CS_GetTransactionHistory, CS_CalculateInterest, CS_GetDiagnostics.\n"
        "Error codes: 0xFFFFFFFB = write denied, 0xFFFFFFFE = not found, "
        "0xFFFFFFFF = general error.\n"
        "ID formats: CUST-001, ORD-20040301-0042, ACCT-001.\n"
        "Amounts are in cents (e.g. 25000 = $250.00)."
    )

    print(f"Circular Feedback Test: {args.iterations} iterations", flush=True)
    print(f"API: {args.api_url}", flush=True)
    print(f"DLL: {args.dll}", flush=True)

    prior_job_id = ""
    results = []

    for i in range(1, args.iterations + 1):
        job_id = _run_iteration(
            api_url=args.api_url,
            headers=headers,
            dll_path=args.dll,
            hints=hints,
            iteration=i,
            prior_job_id=prior_job_id,
            explore_mode=args.mode,
        )
        results.append({"iteration": i, "job_id": job_id, "prior_job_id": prior_job_id})
        prior_job_id = job_id

    print(f"\n{'='*60}", flush=True)
    print("CIRCULAR FEEDBACK TEST COMPLETE", flush=True)
    print(f"{'='*60}", flush=True)
    for r in results:
        label = {1: "cold", 2: "warm", 3: "hot"}.get(r["iteration"], str(r["iteration"]))
        print(f"  Iteration {r['iteration']} ({label}): job_id={r['job_id']}", flush=True)

    # Save results
    results_path = _REPO_ROOT / "sessions" / "circular-test-results.json"
    results_path.parent.mkdir(parents=True, exist_ok=True)
    results_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nResults saved to {results_path}", flush=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
