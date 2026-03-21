"""
Model comparison runner — kicks off contoso_cs.dll through the full pipeline
for each Azure OpenAI model deployment, then downloads session snapshots.

Usage:  python scripts/run_model_comparison.py
Output: sessions/model-cmp-<model>/  for each model
"""
import json, time, sys, os, zipfile, io
import requests

BASE = "https://mcp-factory-pipeline.icycoast-8ddfa278.eastus.azurecontainerapps.io"
API_KEY = "BLO0DLLEAgW0XohMe2eN2Pip4PaCUTaE1QL6cVZXk4k"
DLL = "tests/fixtures/contoso_legacy/contoso_cs.dll"
HINTS_FILE = "sessions/contoso_cs/contoso_cs.txt"

MODELS = ["gpt-4o", "gpt-4-1", "gpt-4-1-mini", "o4-mini"]
HEADERS = {"X-Pipeline-Key": API_KEY}

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

def api_get(path):
    r = requests.get(f"{BASE}{path}", headers=HEADERS, timeout=60)
    r.raise_for_status()
    return r.json()

def api_post_json(path, body):
    r = requests.post(f"{BASE}{path}", headers=HEADERS, json=body, timeout=120)
    r.raise_for_status()
    return r.json()

def upload_and_analyze(dll_path, hints):
    with open(dll_path, "rb") as f:
        files = {"file": ("contoso_cs.dll", f, "application/octet-stream")}
        data = {"hints": hints}
        r = requests.post(f"{BASE}/api/analyze", headers=HEADERS, files=files, data=data, timeout=120)
    r.raise_for_status()
    return r.json()["job_id"]

def wait_analyze(job_id, timeout=300):
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = api_get(f"/api/jobs/{job_id}")
        if st["status"] == "done":
            return st
        if st["status"] == "error":
            log(f"  ANALYZE ERROR: {st.get('error')}")
            return st
        time.sleep(8)
    log(f"  TIMEOUT waiting for analyze")
    return None

def wait_explore(job_id, timeout=1200):
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = api_get(f"/api/jobs/{job_id}")
        ep = st.get("explore_phase", "")
        eprog = st.get("explore_progress", "")
        log(f"  [{job_id}] explore_phase={ep} progress={eprog}")
        if ep in ("awaiting_clarification", "done", "error", "cancelled"):
            return st
        time.sleep(20)
    log(f"  TIMEOUT waiting for explore on {job_id}")
    return api_get(f"/api/jobs/{job_id}")

def download_snapshot(job_id, dest_dir):
    r = requests.get(f"{BASE}/api/jobs/{job_id}/session-snapshot", headers=HEADERS, timeout=120)
    r.raise_for_status()
    os.makedirs(dest_dir, exist_ok=True)
    z = zipfile.ZipFile(io.BytesIO(r.content))
    z.extractall(dest_dir)
    return len(z.namelist())

def score_run(dest_dir):
    """Quick scoring from findings.json."""
    fp = os.path.join(dest_dir, "artifacts", "findings.json")
    if not os.path.exists(fp):
        return {"success": 0, "error": 0, "total": 0}
    with open(fp, encoding="utf-8") as f:
        findings = json.load(f)
    success = sum(1 for fn in findings if fn.get("status") == "success")
    error = sum(1 for fn in findings if fn.get("status") == "error")
    wc_params = 0
    for fn in findings:
        wc = fn.get("working_call", {})
        if isinstance(wc, dict):
            wc_params += len(wc)
    return {"success": success, "error": error, "total": len(findings), "wc_params": wc_params}


def main():
    with open(HINTS_FILE, "r", encoding="utf-8") as f:
        hints = f.read()

    results = {}

    # Phase 1: Upload + analyze all 4 (analyze is model-independent but we need separate jobs)
    log("=== PHASE 1: Upload + Analyze ===")
    job_ids = {}
    for model in MODELS:
        jid = upload_and_analyze(DLL, hints)
        job_ids[model] = jid
        log(f"  {model} -> job_id={jid}")

    # Wait for all to finish analyzing
    log("Waiting for analysis to complete...")
    for model in MODELS:
        st = wait_analyze(job_ids[model])
        if st and st["status"] == "done":
            log(f"  {model} ({job_ids[model]}) analyze DONE")
        else:
            log(f"  {model} ({job_ids[model]}) analyze FAILED - skipping")
            results[model] = {"error": "analyze_failed"}

    # Phase 2: Generate for each
    log("=== PHASE 2: Generate ===")
    for model in MODELS:
        if model in results:
            continue
        jid = job_ids[model]
        # Get the invocables from the job result
        st = api_get(f"/api/jobs/{jid}")
        invocables = st.get("result", {}).get("invocables", [])
        if not invocables:
            log(f"  {model}: No invocables found!")
            results[model] = {"error": "no_invocables"}
            continue
        gen = api_post_json("/api/generate", {"job_id": jid, "selected": invocables})
        tool_count = len(gen.get("tools", []))
        log(f"  {model} ({jid}): generated {tool_count} tools")

    # Phase 3: Start explore for all (they run in parallel on the server)
    log("=== PHASE 3: Start Explore (all models) ===")
    for model in MODELS:
        if model in results:
            continue
        jid = job_ids[model]
        explore_body = {"explore_settings": {"mode": "normal", "model": model}}
        resp = api_post_json(f"/api/jobs/{jid}/explore", explore_body)
        log(f"  {model} ({jid}): explore started, status={resp.get('status')}")

    # Phase 4: Poll all explores until done
    log("=== PHASE 4: Waiting for all explores ===")
    pending = {m: job_ids[m] for m in MODELS if m not in results}
    while pending:
        time.sleep(30)
        done_models = []
        for model, jid in pending.items():
            st = api_get(f"/api/jobs/{jid}")
            ep = st.get("explore_phase", "")
            eprog = st.get("explore_progress", "")
            log(f"  {model} ({jid}): phase={ep} progress={eprog}")
            if ep in ("awaiting_clarification", "done", "error", "cancelled"):
                results[model] = {"job_id": jid, "explore_phase": ep, "explore_progress": eprog}
                done_models.append(model)
        for m in done_models:
            del pending[m]
        if pending:
            log(f"  Still waiting on: {list(pending.keys())}")

    # Phase 5: Download snapshots + score
    log("=== PHASE 5: Download snapshots + Score ===")
    scores = {}
    for model in MODELS:
        r = results.get(model, {})
        jid = r.get("job_id") or job_ids.get(model)
        if not jid or "error" in r:
            log(f"  {model}: SKIPPED (error)")
            continue
        dest = f"sessions/model-cmp-{model}"
        try:
            n = download_snapshot(jid, dest)
            log(f"  {model} ({jid}): downloaded {n} files -> {dest}")
            sc = score_run(dest)
            scores[model] = sc
            log(f"  {model}: {sc['success']}/{sc['total']} success, wc_params={sc['wc_params']}")
        except Exception as e:
            log(f"  {model}: download failed: {e}")

    # Final summary
    log("")
    log("=" * 60)
    log("MODEL COMPARISON RESULTS")
    log("=" * 60)
    log(f"{'Model':<16} {'Job ID':<10} {'Phase':<25} {'Success':<10} {'WC Params'}")
    log("-" * 75)
    for model in MODELS:
        r = results.get(model, {})
        jid = r.get("job_id", job_ids.get(model, "?"))
        phase = r.get("explore_phase", r.get("error", "?"))
        sc = scores.get(model, {})
        succ = f"{sc['success']}/{sc['total']}" if sc else "N/A"
        wcp = sc.get("wc_params", "N/A")
        log(f"  {model:<16} {jid:<10} {phase:<25} {succ:<10} {wcp}")

    # Save summary JSON
    summary = {"models": {m: {**results.get(m, {}), **scores.get(m, {})} for m in MODELS}}
    with open("sessions/model-comparison-summary.json", "w") as f:
        json.dump(summary, f, indent=2)
    log(f"\nSummary saved to sessions/model-comparison-summary.json")


if __name__ == "__main__":
    main()
