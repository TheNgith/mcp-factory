import json, sys

base = sys.argv[1] if len(sys.argv) > 1 else 'sessions/2026-03-21-66a7135-L2-fix2'

with open(base + '/artifacts/invocables_map.json') as f:
    imap_raw = json.load(f)

# invocables_map can be dict {name: inv} or list [{name, ...}]
if isinstance(imap_raw, dict):
    imap_items = list(imap_raw.items())
else:
    imap_items = [(x.get('name', ''), x) for x in imap_raw]

targets = ('CS_GetAccountBalance', 'CS_GetLoyaltyPoints', 'CS_GetVersion', 'CS_UnlockAccount')
for name, inv in imap_items:
    if name in targets:
        params = inv.get('parameters', [])
        print(name + ':')
        for p in params:
            pn = p.get('name', '?')
            pt = p.get('type', '')
            pd = p.get('direction', 'in')
            desc = p.get('description', '')[:60]
            print('  ' + pn + ': type=' + pt + ', dir=' + pd + ', desc=' + desc)
        print()
