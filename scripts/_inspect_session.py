import json, os, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

out = "sessions/_runs/2026-03-24-utf8-xor-test"

# Find all JSON files and look for relevant data
for root, dirs, files in os.walk(out):
    for f in files:
        fpath = os.path.join(root, f)
        
        # Write unlock probe details
        if "write_unlock_probe" in f or "write-unlock-probe" in f:
            print(f"\n{'='*60}")
            print(f"WRITE UNLOCK PROBE: {fpath}")
            print(f"{'='*60}")
            with open(fpath) as fp:
                data = json.load(fp)
            print(json.dumps(data, indent=2, default=str)[:3000])
        
        # MC decisions
        if "mc" in f.lower() and f.endswith(".json"):
            print(f"\n{'='*60}")
            print(f"MC DECISION: {fpath}")
            print(f"{'='*60}")
            with open(fpath) as fp:
                data = json.load(fp)
            print(json.dumps(data, indent=2, default=str)[:2000])
        
        # Session meta
        if f == "session-meta.json":
            print(f"\n{'='*60}")
            print(f"SESSION META: {fpath}")
            print(f"{'='*60}")
            with open(fpath) as fp:
                data = json.load(fp)
            for k in ["functions_total", "functions_success", "write_unlock_resolved_at",
                       "verification_verified", "verification_error", "verification_inferred"]:
                print(f"  {k}: {data.get(k)}")

        # Probe log - look for CS_UnlockAccount entries
        if "probe-log" in f or "probe_log" in f:
            print(f"\n{'='*60}")
            print(f"PROBE LOG CS_UnlockAccount ENTRIES:")
            print(f"{'='*60}")
            with open(fpath) as fp:
                plog = json.load(fp)
            ua_entries = [e for e in plog if e.get("function") == "CS_UnlockAccount" or e.get("tool") == "CS_UnlockAccount"]
            for e in ua_entries:
                print(f"  phase={e.get('phase')} tool={e.get('tool')} args={e.get('args')} result={str(e.get('result_excerpt',''))[:150]}")
            
            # Also show CS_RedeemLoyaltyPoints
            print(f"\nPROBE LOG CS_RedeemLoyaltyPoints ENTRIES:")
            rlp_entries = [e for e in plog if e.get("function") == "CS_RedeemLoyaltyPoints" or e.get("tool") == "CS_RedeemLoyaltyPoints"]
            for e in rlp_entries:
                print(f"  phase={e.get('phase')} tool={e.get('tool')} args={e.get('args')} result={str(e.get('result_excerpt',''))[:150]}")
            
            # Count 429s by function
            print(f"\n429 ERRORS BY FUNCTION:")
            from collections import Counter
            err_counts = Counter()
            for e in plog:
                if e.get("phase") == "llm_error" and "429" in str(e.get("result_excerpt", "")):
                    err_counts[e.get("function", "?")] += 1
            for fn, cnt in err_counts.most_common():
                print(f"  {fn}: {cnt} 429s")

print("\nDone.")
