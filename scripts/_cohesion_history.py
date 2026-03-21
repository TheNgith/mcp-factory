import json, os

sessions_dir = 'sessions'
runs = []
for d in sorted(os.listdir(sessions_dir)):
    fp = os.path.join(sessions_dir, d, 'artifacts', 'findings.json')
    sc_path = os.path.join(sessions_dir, d, 'sentinel_calibration.json')
    if not os.path.exists(fp):
        continue
    with open(fp) as f:
        findings = json.load(f)
    success = sum(1 for fn in findings if fn.get('status') == 'success')
    total = len(findings)
    sentinels = 0
    if os.path.exists(sc_path):
        with open(sc_path) as f:
            sentinels = len(json.load(f))
    # Extract commit from session name
    commit = 'unknown'
    for part in d.split('-'):
        if len(part) == 7 and all(c in '0123456789abcdef' for c in part):
            commit = part
            break
    runs.append({'session': d, 'commit': commit, 'success': success, 'total': total, 'sentinels': sentinels})

print(f"{'Session':<55} {'Commit':<10} {'Result':<8} Sentinels")
print('-' * 90)
for r in runs:
    result = f"{r['success']}/{r['total']}"
    print(f"{r['session']:<55} {r['commit']:<10} {result:<8} {r['sentinels']}")
