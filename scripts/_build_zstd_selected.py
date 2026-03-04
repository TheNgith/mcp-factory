"""One-shot: normalise zstd_zstd_exports_mcp.json → selected-invocables.json."""
import json
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent.parent
src  = ROOT / "artifacts" / "zstd_zstd_exports_mcp.json"
out  = ROOT / "artifacts" / "selected-invocables.json"

data = json.loads(src.read_text(encoding="utf-8"))
meta = data["metadata"]


def normalize(inv: dict) -> dict:
    """Flatten rich MCP schema → flat format that _execute_dll expects."""
    r = dict(inv)
    # hoist mcp.execution → top-level execution
    if "execution" not in r and "mcp" in r:
        r["execution"] = (r["mcp"] or {}).get("execution", {})
    # hoist signature.return_type → top-level return_type
    sig = r.get("signature") or {}
    if sig.get("return_type") and (not r.get("return_type") or r.get("return_type") == "unknown"):
        r["return_type"] = sig["return_type"]
    # parse signature.parameters string → [{name, type, required, description}]
    if not r.get("parameters") and sig.get("parameters"):
        parsed = []
        for chunk in sig["parameters"].split(","):
            tokens = chunk.strip().split()
            if len(tokens) >= 2:
                raw_type = " ".join(tokens[:-1]).strip()
                pname    = tokens[-1].lstrip("*")
                parsed.append({"name": pname, "type": raw_type, "required": True, "description": raw_type})
        if parsed:
            r["parameters"] = parsed
    return r


# All exported functions
selected = [normalize(inv) for inv in data["invocables"]]

for s in selected:
    ex  = s.get("execution", {})
    ret = s.get("return_type", "?")
    ps  = [p["name"] for p in s.get("parameters", [])]
    print(f"  {s['name']:30s}  return={ret:15s}  params={ps}")
    print(f"    dll_path={ex.get('dll_path', '?')}")

output = {
    "generated_at":       datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    "component_name":     "zstd",
    "metadata":           meta,
    "selected_invocables": selected,
}
out.write_text(json.dumps(output, indent=2), encoding="utf-8")
print(f"\nWrote {out}")
