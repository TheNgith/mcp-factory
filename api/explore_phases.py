"""api/explore_phases.py – Phase-level probing utilities.

Contains the sentinel-calibration pass (Phase 0.5), the write-unlock probe
(Phase 1), and the _infer_param_desc helper used by the report endpoint.
"""

from __future__ import annotations

import json
import logging
import os as _os
import re as _re
from collections import defaultdict

from api.executor import _execute_tool, _execute_tool_traced
from api.sentinel_codes import SENTINEL_DEFAULTS, classify_common_result_code

logger = logging.getLogger("mcp_factory.api")

# Tunable via env for development speed vs. quality tradeoff:
#   EXPLORE_CAP_PROFILE=dev|stabilize|deploy (defaults to deploy)
#   EXPLORE_MAX_ROUNDS=1   → explicit override
#   EXPLORE_MAX_ROUNDS=5   → explicit override
#   EXPLORE_MAX_TOOL_CALLS → hard cap on DLL probe calls per function (prevents one function
#                             from starving the others; every function is guaranteed exploration)
#   EXPLORE_MAX_FUNCTIONS=10 → cap number of functions probed
#   EXPLORE_ENABLE_GAP_RESOLUTION=0|1 → controls expensive post-discovery gap retries
#                                       and answer-gaps mini-sessions (wired in explore.py)
_CAP_PROFILES = {
    "dev": {"rounds": 3, "tool_calls": 5},
    "stabilize": {"rounds": 4, "tool_calls": 8},
    "deploy": {"rounds": 5, "tool_calls": 10},
}
_CAP_PROFILE = _os.getenv("EXPLORE_CAP_PROFILE", "deploy").strip().lower()
if _CAP_PROFILE not in _CAP_PROFILES:
    _CAP_PROFILE = "deploy"

_MAX_EXPLORE_ROUNDS_PER_FUNCTION = int(
    _os.getenv("EXPLORE_MAX_ROUNDS", str(_CAP_PROFILES[_CAP_PROFILE]["rounds"]))
)
_MAX_TOOL_CALLS_PER_FUNCTION = int(
    _os.getenv("EXPLORE_MAX_TOOL_CALLS", str(_CAP_PROFILES[_CAP_PROFILE]["tool_calls"]))
)
_MAX_FUNCTIONS_PER_SESSION = int(_os.getenv("EXPLORE_MAX_FUNCTIONS", "50"))
_ENABLE_STICKY_SENTINEL_BASELINE = _os.getenv(
    "EXPLORE_ENABLE_STICKY_SENTINEL_BASELINE", "0"
).strip().lower() not in {"0", "false", "no", "off"}

_SENTINEL_DEFAULTS = SENTINEL_DEFAULTS


def _parse_hint_error_codes(hints: str) -> dict[int, str]:
    """Q-1: Extract explicit error code definitions from user hints.

    Parses patterns like:
      "Error code 0xFFFFFFFB (4294967291) = write denied"
      "0xFFFFFFFC = account not found"
    Returns {int_code: meaning}.
    """
    codes: dict[int, str] = {}
    if not hints:
        return codes
    for m in _re.finditer(
        r'(?:error\s+code\s+)?'           # optional prefix
        r'(0x[0-9A-Fa-f]+)'               # hex code
        r'(?:\s*\(\d+\))?'                # optional decimal
        r'\s*[=:–—]\s*'                   # separator
        r'(.+?)(?:\.|$)',                  # meaning (up to period or EOL)
        hints, _re.IGNORECASE | _re.MULTILINE,
    ):
        try:
            code = int(m.group(1), 16)
            meaning = m.group(2).strip()
            if meaning and code > 0:
                codes[code] = meaning
        except (ValueError, TypeError):
            pass
    return codes


def _calibrate_sentinels(
    invocables: list[dict], client, model: str, job_id: str = ""
) -> dict[int, str]:
    """Phase 0.5: probe every exported function with no args and cluster the
    non-zero high-bit return values to discover this DLL's sentinel error codes.
    Falls back to _SENTINEL_DEFAULTS if nothing useful is found."""
    counts: dict[int, int] = defaultdict(int)
    val_fns: dict[int, list[str]] = defaultdict(list)
    _calibrate_entries: list[dict] = []

    for inv in invocables:
        try:
            _ct = _execute_tool_traced(inv, {})
            result = _ct["result_str"]
            _calibrate_entries.append({
                "phase": "calibrate_sentinels",
                "function": inv["name"],
                "args": {},
                "result_excerpt": str(result)[:200],
                "trace": _ct.get("trace"),
            })
            m = _re.match(r"Returned:\s*(\d+)", result or "")
            if not m:
                continue
            val = int(m.group(1))
            if val == 0:
                continue
            counts[val] += 1
            val_fns[val].append(inv["name"])
        except Exception:
            pass

    if job_id and _calibrate_entries:
        try:
            from api.storage import _append_explore_probe_log
            _append_explore_probe_log(job_id, _calibrate_entries)
        except Exception as _fle:
            logger.debug("[%s] calibrate_sentinels probe flush failed: %s", job_id, _fle)

    candidates = {}
    for v, fns in val_fns.items():
        if v < 0x80000000:
            continue
        _m = classify_common_result_code(v)
        _strong_det = bool(_m and "-like" not in _m)
        if counts[v] >= 2 or _strong_det:
            candidates[v] = fns

    # Sticky sentinel baseline (deferred by default): can be enabled for
    # development diagnostics, but stays disabled until component-scoped
    # storage behavior is fully validated.
    _prior_sentinels: dict[int, str] = {}
    if _ENABLE_STICKY_SENTINEL_BASELINE and job_id:
        try:
            from api.storage import _download_blob as _dl_blob
            from api.config import ARTIFACT_CONTAINER as _AC
            _prior_raw = json.loads(_dl_blob(_AC, f"{job_id}/sentinel_calibration.json"))
            for _hk, _mv in _prior_raw.items():
                try:
                    _prior_sentinels[int(_hk, 16)] = str(_mv)
                except (ValueError, TypeError):
                    pass
        except Exception:
            pass  # no prior calibration — normal on first run

    if _prior_sentinels:
        # Values that returned 0 (success) in this run contradict sentinel role.
        _success_values = {v for v, c in counts.items() if v == 0} | {0}
        for _pv, _pm in _prior_sentinels.items():
            if _pv not in candidates and _pv not in _success_values and _pv >= 0x80000000:
                candidates[_pv] = [f"(prior-session: {_pm})"]

    if not candidates:
        return _prior_sentinels or _SENTINEL_DEFAULTS

    resolved: dict[int, str] = {}
    unresolved: dict[int, list[str]] = {}
    for _v, _fns in candidates.items():
        _meaning = classify_common_result_code(_v)
        if _meaning:
            resolved[_v] = _meaning
        else:
            unresolved[_v] = _fns

    if not unresolved:
        return resolved or _SENTINEL_DEFAULTS

    cand_lines = "\n".join(
        f"  0x{v:08X} (decimal {v}) — returned by: {', '.join(fns[:6])}"
        for v, fns in sorted(unresolved.items(), reverse=True)
    )
    prompt = (
        "Assign a SHORT plain-English meaning (3-8 words) to each of these "
        "32-bit return codes from an undocumented Windows DLL.\n"
        f"{cand_lines}\n"
        "Output ONLY a JSON object: {\"0xFFFFFF..\": \"meaning\", ...}"
    )
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
        )
        raw = (resp.choices[0].message.content or "{}").strip()
        if raw.startswith("```"):
            raw = "\n".join(raw.splitlines()[1:]).rstrip("`").strip()
        named: dict[str, str] = json.loads(raw)
        result_map = {}
        for k, meaning in named.items():
            try:
                result_map[int(k, 16)] = str(meaning)
            except (ValueError, TypeError):
                pass
        if result_map:
            merged = dict(resolved)
            merged.update(result_map)
            return merged
    except Exception as exc:
        logger.debug("[explore] sentinel calibration LLM call failed: %s", exc)
    return resolved or _SENTINEL_DEFAULTS


def _probe_write_unlock(invocables: list[dict], dll_strings: dict) -> dict:
    """Phase 1: try to flip the DLL from read-only to write-ready.
    Tries Init with mode integers, then any Begin/Enable/Auth-style functions,
    then a credential sweep using strings extracted from the binary.
    Returns unlock result dict."""
    _WRITE_SENTINELS = {0xFFFFFFFB}
    inv_map = {inv["name"]: inv for inv in invocables}
    _init_names = [n for n in inv_map if _re.search(r"init(ializ)?", n, _re.I)]
    _write_fn_names = [
        n for n in inv_map
        if _re.search(r"(pay|redeem|unlock|process|write|commit|transfer|debit|credit)", n, _re.I)
    ]
    tried = []

    # Detect no-param init variants first
    no_param_inits = [n for n in _init_names if not inv_map[n].get("parameters")]
    if no_param_inits:
        for n in no_param_inits:
            r = _execute_tool(inv_map[n], {})
            tried.append(f"{n}() -> {r}")

    # Try mode-based init
    for mode in (0, 1, 2, 4, 8, 16, 256, 512):
        for n in _init_names:
            if inv_map[n].get("parameters"):
                _r = _execute_tool(inv_map[n], {"param_1": mode})
                tried.append(f"{n}(mode={mode}) -> {_r}")
                # Test against first write fn
                if _write_fn_names:
                    _wfn = _write_fn_names[0]
                    _wr  = _execute_tool(inv_map[_wfn], {})
                    _ret_m = _re.match(r"Returned:\s*(\d+)", _wr or "")
                    _ret   = int(_ret_m.group(1)) & 0xFFFFFFFF if _ret_m else 0xFFFFFFFF
                    if _ret not in _WRITE_SENTINELS and _ret == 0:
                        return {"unlocked": True, "sequence": [{"fn": n, "args": {"param_1": mode}}],
                                "notes": f"unlocked with {n}(mode={mode})"}

    # Credential sweep using strings extracted from the binary
    _all_strings = dll_strings.get("ids", []) + dll_strings.get("misc", [])
    _cred_tokens = list(dict.fromkeys([  # preserve order, deduplicate
        s for s in _all_strings if 3 < len(s) < 40
    ]))[:28]
    _canary = _write_fn_names[0] if _write_fn_names else None
    for n in _init_names:
        if not inv_map[n].get("parameters"):
            continue
        for tok in _cred_tokens:
            _r = _execute_tool(inv_map[n], {"param_1": tok})
            tried.append(f"{n}(cred={tok!r}) -> {_r}")
            if _canary:
                _wr  = _execute_tool(inv_map[_canary], {})
                _ret_m = _re.match(r"Returned:\s*(\d+)", _wr or "")
                _ret   = int(_ret_m.group(1)) & 0xFFFFFFFF if _ret_m else 0xFFFFFFFF
                if _ret not in _WRITE_SENTINELS and _ret == 0:
                    return {"unlocked": True,
                            "sequence": [{"fn": n, "args": {"param_1": tok}}],
                            "notes": f"unlocked with {n}(cred={tok!r})"}

    return {"unlocked": False, "sequence": [], "write_fn_tested": _canary,
            "notes": f"write-unlock failed after {len(tried)} attempts"}


def _infer_param_desc(pname: str, ptype: str, fn_findings: list) -> str:
    """Produce a human-readable parameter description from type info and findings.
    Called when the stored description is just Ghidra boilerplate."""
    t = (ptype or "").lower().replace("const ", "").strip()
    base = t.rstrip(" *").strip()
    is_ptr = "*" in t

    # Collect all finding/notes text for this function
    all_text = " ".join(
        (f.get("finding", "") + " " + f.get("notes", ""))
        for f in fn_findings
    )

    # Output integer pointer (uint *, ulong *, etc.)
    if is_ptr and base in {"uint", "ulong", "ushort", "int", "uint32_t", "dword"}:
        m = _re.search(rf"{_re.escape(pname)}\s*[=:]\s*(\S+)", all_text)
        val = f" (observed: {m.group(1)})" if m else ""
        return f"Output — receives integer result{val}"

    # Output buffer (undefined*, undefined4*, undefined8*)
    if is_ptr and base in {"undefined", "undefined2", "undefined4", "undefined8", "void"}:
        if "pipe-delimited" in all_text or "|" in all_text:
            return "Output buffer — receives pipe-delimited key=value result string"
        if "balance" in all_text and pname in ("param_2", "param_4"):
            return "Output buffer — receives balance or result data"
        return "Output buffer — receives result data (omit from call; auto-allocated)"

    # Input string (byte *) — extract ID patterns observed in findings generically
    if t == "byte *":
        id_patterns = list(dict.fromkeys(_re.findall(r'[A-Z]{2,6}-[\w-]+', all_text)))
        if id_patterns:
            return "Input string — e.g. " + " or ".join(f"'{p}'" for p in id_patterns[:3])
        return "Input string parameter"

    # Windows DLL entry point params
    if base == "hinstance__":
        return "DLL instance handle (Windows DllMain param)"
    if t == "void *":
        return "Reserved pointer (Windows DllMain param)"

    # Plain integers
    if base in {"uint", "ulong", "ushort", "int", "uint32_t", "dword", "ulong32"}:
        m = _re.search(rf"{_re.escape(pname)}\s*[=:]\s*(\S+)", all_text)
        val = f" (e.g. {m.group(1)})" if m else ""
        return f"Integer input parameter{val}"

    return f"Parameter of type {ptype}"
