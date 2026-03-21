import json, os, sys

base = sys.argv[1] if len(sys.argv) > 1 else 'sessions/2026-03-21-66a7135-L2-fix2'

# 1. Schema evolution
schema_dir = os.path.join(base, 'schema')
print("=== SCHEMA CHECKPOINTS ===")
if os.path.exists(schema_dir):
    checkpoints = sorted(os.listdir(schema_dir))
    sizes = {}
    for cp in checkpoints:
        fp = os.path.join(schema_dir, cp)
        sz = os.path.getsize(fp)
        sizes[cp] = sz
        print(f"  {cp}: {sz} bytes")
    print()
    # Deltas
    print("=== SCHEMA DELTAS ===")
    prev_name, prev_sz = None, None
    for cp in checkpoints:
        sz = sizes[cp]
        if prev_sz is not None:
            delta = sz - prev_sz
            verdict = "CHANGED" if delta != 0 else "frozen"
            print(f"  {prev_name} -> {cp}: {'+' if delta >= 0 else ''}{delta} bytes ({verdict})")
        prev_name, prev_sz = cp, sz
else:
    print("  No schema/ dir found")

# 2. Vocab
print()
print("=== VOCAB.JSON ===")
vocab_fp = os.path.join(base, 'artifacts', 'vocab.json')
if os.path.exists(vocab_fp):
    with open(vocab_fp) as f:
        vocab = json.load(f)
    # Top-level keys
    keys = list(vocab.keys())
    print(f"  Top-level keys: {keys}")
    for k in keys:
        v = vocab[k]
        if isinstance(v, dict):
            print(f"  [{k}] {len(v)} entries: {list(v.keys())[:8]}{'...' if len(v) > 8 else ''}")
        elif isinstance(v, list):
            print(f"  [{k}] {len(v)} items")
        else:
            print(f"  [{k}] = {str(v)[:80]}")
    print()
    # Drill into key sections
    if 'id_formats' in vocab:
        print("  id_formats:", json.dumps(vocab['id_formats'], indent=2)[:400])
    if 'error_codes' in vocab:
        print("  error_codes:", json.dumps(vocab['error_codes'], indent=2)[:400])
    if 'functions' in vocab:
        fns = vocab['functions']
        print(f"\n  functions ({len(fns)} total):")
        for fn_name, fn_data in list(fns.items())[:5]:
            params = fn_data.get('parameters', {})
            print(f"    {fn_name}: {len(params)} params, keys={list(fn_data.keys())}")
            for p, pd in list(params.items())[:3]:
                print(f"      {p}: {str(pd)[:100]}")
else:
    print("  No vocab.json found")

# 3. How context is built — check the schema context file if present
print()
print("=== BEHAVIORAL SPEC ===")
bspec = os.path.join(base, 'behavioral_spec.py')
if os.path.exists(bspec):
    with open(bspec) as f:
        content = f.read()
    print(content[:1000])
else:
    print("  No behavioral_spec.py")

# 4. Gap resolution effectiveness — findings before vs after
print()
print("=== GAP RESOLUTION EFFECTIVENESS ===")
gap_fp = os.path.join(base, 'gap_resolution_log.json')
if os.path.exists(gap_fp):
    with open(gap_fp) as f:
        grl = json.load(f)
    flipped = [e for e in grl if e.get('status') == 'success']
    still_err = [e for e in grl if e.get('status') == 'error']
    print(f"  Flipped to success: {len(flipped)}")
    for e in flipped:
        print(f"    + {e['function']}: wc={e.get('working_call')}")
    print(f"  Still error: {len(still_err)}")
    for e in still_err:
        print(f"    - {e['function']}: attempts={e.get('attempts', 0)}")
else:
    print("  No gap_resolution_log.json")
