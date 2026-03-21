import json, os

models = ['gpt-4o', 'gpt-4-1', 'gpt-4-1-mini', 'o4-mini']
all_fns = set()
data = {}

for model in models:
    fp = f'sessions/model-cmp-{model}/artifacts/findings.json'
    if not os.path.exists(fp):
        print(f'{model}: NO FINDINGS')
        continue
    with open(fp) as f:
        findings = json.load(f)
    data[model] = {}
    for fn in findings:
        name = fn.get('function','')
        all_fns.add(name)
        data[model][name] = {
            'status': fn.get('status','?'),
            'wc': fn.get('working_call', {}),
            'wc_count': len(fn.get('working_call', {}) or {}) if isinstance(fn.get('working_call'), dict) else 0
        }

fns = sorted(all_fns)
header = f"{'Function':30s}"
for m in models:
    header += f' {m:16s}'
print(header)
print('-' * (30 + 17 * len(models)))

totals = {m: 0 for m in models}
wc_totals = {m: 0 for m in models}
for fn in fns:
    row = f'{fn:30s}'
    for m in models:
        if m not in data:
            row += f' {"N/A":16s}'
            continue
        d = data[m].get(fn, {})
        status = d.get('status', '?')
        wc_count = d.get('wc_count', 0)
        marker = 'OK' if status == 'success' else 'FAIL'
        if status == 'success':
            totals[m] += 1
            wc_totals[m] += wc_count
        row += f' {marker + "(" + str(wc_count) + "p)":16s}'
    print(row)

print('-' * (30 + 17 * len(models)))
row = f"{'TOTAL':30s}"
for m in models:
    row += f' {str(totals.get(m,0)) + "/13":16s}'
print(row)
row = f"{'WC params total':30s}"
for m in models:
    row += f' {str(wc_totals.get(m,0)):16s}'
print(row)

# Timing from explore layer (13/13) — extracted from log
print("\n--- Explore layer speed (from log) ---")
print("o4-mini:       ~30s (but 3/13 — useless)")
print("gpt-4-1-mini:  ~2.5 min to 13/13")
print("gpt-4-1:       ~3 min to 13/13")
print("gpt-4o:        ~4 min to 13/13")
