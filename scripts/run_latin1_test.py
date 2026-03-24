"""Quick test: upload contoso_cs.dll, run explore, download session."""
import json, time, sys, requests, zipfile, io

API = "https://mcp-factory-pipeline.icycoast-8ddfa278.eastus.azurecontainerapps.io"
KEY = "BLO0DLLEAgW0XohMe2eN2Pip4PaCUTaE1QL6cVZXk4k"
HDR = {"X-Pipeline-Key": KEY}
JOB = "c2ef612d"

# 1) Get invocables from the completed analysis
print("[1] Fetching invocables from completed analysis...")
r = requests.get(f"{API}/api/jobs/{JOB}", headers=HDR)
r.raise_for_status()
job = r.json()
invocables = job["result"]["invocables"]
print(f"    Got {len(invocables)} invocables")

# 2) Start explore
print("[2] Starting explore phase...")
explore_body = {
    "invocables": invocables,
    "hints": "Customer IDs follow the format CUST-NNN. Sentinel error codes: 0xFFFFFFFF=not found, 0xFFFFFFFE=null arg, 0xFFFFFFFD=not initialized, 0xFFFFFFFC=account locked, 0xFFFFFFFB=write denied. CS_UnlockAccount requires XOR checksum 0xA5 on param_2.",
    "runtime": {
        "max_rounds": 6,
        "max_tool_calls": 20,
        "max_functions": 13,
        "context_density": "full",
        "instruction_fragment": "",
        "concurrency": 1,
    }
}
r = requests.post(f"{API}/api/jobs/{JOB}/explore", headers=HDR, json=explore_body)
r.raise_for_status()
print(f"    Explore started: {r.json()}")

# 3) Poll until done
print("[3] Polling for completion...")
for i in range(180):
    time.sleep(15)
    r = requests.get(f"{API}/api/jobs/{JOB}", headers=HDR)
    r.raise_for_status()
    st = r.json()
    phase = st.get("explore_phase", "?")
    msg = st.get("explore_message", "")[:80]
    print(f"    [{i*15}s] phase={phase}  msg={msg}")
    if phase in ("done", "awaiting_clarification"):
        break
else:
    print("    TIMEOUT after 45 min")
    sys.exit(1)

# 4) Download session snapshot
print("[4] Downloading session snapshot...")
r = requests.get(f"{API}/api/jobs/{JOB}/session-snapshot", headers=HDR)
r.raise_for_status()
out = f"C:/Users/evanw/Downloads/capstone_project/mcp-factory/sessions/_runs/2026-03-24-direction-fix-test"
import os, shutil
os.makedirs(out, exist_ok=True)
zf = zipfile.ZipFile(io.BytesIO(r.content))
zf.extractall(out)
print(f"    Extracted to {out}")

# 5) Quick analysis
findings_path = None
for root, dirs, files in os.walk(out):
    for f in files:
        if f == "findings.json":
            findings_path = os.path.join(root, f)
            break

if findings_path:
    with open(findings_path) as fp:
        findings = json.load(fp)
    total = len(findings)
    success = sum(1 for f in findings if f.get("status") == "success")
    print(f"\n=== RESULTS: {success}/{total} functions successful ===")
    for f in findings:
        fn = f.get("function", "?")
        st = f.get("status", "?")
        wc = f.get("working_call")
        print(f"  {fn}: {st}" + (f" (working_call: {wc})" if wc and st == "success" else ""))
    
    # Check for write functions specifically
    write_fns = ["CS_ProcessPayment", "CS_ProcessRefund", "CS_RedeemLoyaltyPoints", "CS_UnlockAccount"]
    print(f"\n=== WRITE FUNCTION STATUS ===")
    for wf in write_fns:
        match = [f for f in findings if f.get("function") == wf]
        if match:
            f = match[0]
            print(f"  {wf}: {f.get('status')} | notes: {(f.get('notes') or f.get('finding') or '')[:100]}")
        else:
            print(f"  {wf}: NOT FOUND IN FINDINGS")
else:
    print("    findings.json not found in session")

# 6) Check write_unlock_probe.json
wup_path = None
for root, dirs, files in os.walk(out):
    for f in files:
        if "write_unlock_probe" in f or "write-unlock-probe" in f:
            wup_path = os.path.join(root, f)
            break
if wup_path:
    with open(wup_path) as fp:
        wup = json.load(fp)
    print(f"\n=== WRITE UNLOCK PROBE ===")
    print(f"  unlocked: {wup.get('unlocked')}")
    print(f"  sequence: {wup.get('sequence')}")
    print(f"  write_fn_tested: {wup.get('write_fn_tested')}")
    cra = wup.get("code_reasoning_analysis") or {}
    if cra:
        ufs = cra.get("unlock_functions") or []
        print(f"  code_reasoning: {len(ufs)} unlock functions found")
        for uf in ufs:
            print(f"    {uf.get('name')}: xor_target={uf.get('xor_target_hex')}, codes={len(uf.get('xor_codes', []))}")

# 7) Check probe log for 429 errors
probe_path = None
for root, dirs, files in os.walk(out):
    for f in files:
        if "probe-log" in f or "probe_log" in f:
            probe_path = os.path.join(root, f)
            break
if probe_path:
    with open(probe_path) as fp:
        plog = json.load(fp)
    rate_errors = [e for e in plog if "429" in (e.get("result_excerpt") or "") or "rate" in (e.get("result_excerpt") or "").lower()]
    print(f"\n=== RATE LIMIT ERRORS: {len(rate_errors)} ===")
    for re in rate_errors[:5]:
        print(f"  {re.get('function')}: {(re.get('result_excerpt') or '')[:80]}")

print("\nDone.")
