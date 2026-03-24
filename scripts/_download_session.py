import json, os, io, zipfile, requests

API = "https://mcp-factory-pipeline.icycoast-8ddfa278.eastus.azurecontainerapps.io"
KEY = "BLO0DLLEAgW0XohMe2eN2Pip4PaCUTaE1QL6cVZXk4k"
HDR = {"X-Pipeline-Key": KEY}
JOB = "c2ef612d"

print("Downloading session snapshot...")
r = requests.get(f"{API}/api/jobs/{JOB}/session-snapshot", headers=HDR)
r.raise_for_status()
out = "sessions/_runs/2026-03-24-direction-fix-test"
os.makedirs(out, exist_ok=True)
zf = zipfile.ZipFile(io.BytesIO(r.content))
zf.extractall(out)
print(f"Extracted {len(zf.namelist())} files to {out}")

# Analyze findings
findings_path = None
for root, dirs, files in os.walk(out):
    for f in files:
        if f == "findings.json":
            findings_path = os.path.join(root, f)
            break
    if findings_path:
        break

if findings_path:
    with open(findings_path) as fp:
        findings = json.load(fp)
    total = len(findings)
    success = sum(1 for f in findings if f.get("status") == "success")
    print(f"\n{'='*60}")
    print(f"RESULTS: {success}/{total} functions successful")
    print(f"{'='*60}")
    for f in sorted(findings, key=lambda x: x.get("function", "")):
        fn = f.get("function", "?")
        st = f.get("status", "?")
        wc = f.get("working_call")
        notes = (f.get("notes") or f.get("finding") or "")[:100]
        marker = "OK" if st == "success" else "FAIL"
        print(f"  [{marker:4}] {fn}")
        if wc and st == "success":
            print(f"         working_call: {wc}")
        elif st != "success":
            print(f"         notes: {notes}")

    write_fns = ["CS_ProcessPayment", "CS_ProcessRefund", "CS_RedeemLoyaltyPoints", "CS_UnlockAccount"]
    print(f"\n{'='*60}")
    print("WRITE FUNCTION DETAIL")
    print(f"{'='*60}")
    for wf in write_fns:
        match = [f for f in findings if f.get("function") == wf]
        if match:
            f = match[0]
            print(f"  {wf}:")
            print(f"    status: {f.get('status')}")
            print(f"    working_call: {f.get('working_call')}")
            print(f"    finding: {(f.get('finding') or '')[:200]}")
            print(f"    notes: {(f.get('notes') or '')[:200]}")
        else:
            print(f"  {wf}: NOT FOUND")

# Write unlock probe
print(f"\n{'='*60}")
print("WRITE UNLOCK PROBE")
print(f"{'='*60}")
wup_path = None
for root, dirs, files in os.walk(out):
    for f in files:
        if "write_unlock_probe" in f or "write-unlock-probe" in f:
            wup_path = os.path.join(root, f)
            break
    if wup_path:
        break
if wup_path:
    with open(wup_path) as fp:
        wup = json.load(fp)
    print(f"  unlocked: {wup.get('unlocked')}")
    print(f"  write_fn_tested: {wup.get('write_fn_tested')}")
    print(f"  notes: {(wup.get('notes') or '')[:200]}")
    cra = wup.get("code_reasoning_analysis") or {}
    ufs = cra.get("unlock_functions") or []
    print(f"  code_reasoning: {len(ufs)} unlock functions")
    for uf in ufs:
        print(f"    {uf.get('name')}: xor={uf.get('xor_target_hex')}, codes={len(uf.get('xor_codes', []))}")
    deps = cra.get("dependency_chains") or []
    print(f"  dependency_chains: {len(deps)}")

# Probe log 429s
print(f"\n{'='*60}")
print("RATE LIMIT ERRORS")
print(f"{'='*60}")
probe_path = None
for root, dirs, files in os.walk(out):
    for f in files:
        if "probe-log" in f or "probe_log" in f:
            probe_path = os.path.join(root, f)
            break
    if probe_path:
        break
if probe_path:
    with open(probe_path) as fp:
        plog = json.load(fp)
    total_entries = len(plog)
    rate_errors = [e for e in plog if "429" in (e.get("result_excerpt") or "") or "rate" in (e.get("result_excerpt") or "").lower()]
    llm_errors = [e for e in plog if e.get("phase") == "llm_error"]
    print(f"  total probe log entries: {total_entries}")
    print(f"  rate limit (429) errors: {len(rate_errors)}")
    print(f"  total LLM errors: {len(llm_errors)}")
    for re_ in llm_errors[:5]:
        print(f"    {re_.get('function')}: {(re_.get('result_excerpt') or '')[:100]}")

# Session meta
print(f"\n{'='*60}")
print("SESSION META")
print(f"{'='*60}")
meta_path = None
for root, dirs, files in os.walk(out):
    for f in files:
        if f == "session-meta.json":
            meta_path = os.path.join(root, f)
            break
    if meta_path:
        break
if meta_path:
    with open(meta_path) as fp:
        meta = json.load(fp)
    print(f"  functions_total: {meta.get('functions_total')}")
    print(f"  functions_success: {meta.get('functions_success')}")
    print(f"  write_unlock_resolved_at: {meta.get('write_unlock_resolved_at')}")
    print(f"  verification_verified: {meta.get('verification_verified')}")
    print(f"  verification_error: {meta.get('verification_error')}")

print("\nDone.")
