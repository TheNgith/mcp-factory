import json, sys
d = json.load(sys.stdin)
phase = d.get("explore_phase", "?")
msg = d.get("explore_message", "")[:120]
print(f"phase={phase}  msg={msg}")
