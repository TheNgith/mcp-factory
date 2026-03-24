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
    "deploy": {"rounds": 5, "tool_calls": 8},
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


def _name_sentinel_candidates(
    candidates: dict[int, list[str]], client, model: str
) -> dict[int, str]:
    """Q16: Ask the LLM to assign short meanings to unresolved high-bit return codes.

    candidates: {int_code: [function_names_that_returned_it]}
    Returns {int_code: meaning_string} for codes the LLM could name.
    Extracted from _calibrate_sentinels so stage-boundary re-calibration can
    reuse the same naming logic without a full empty-arg sweep.
    """
    if not candidates:
        return {}
    cand_lines = "\n".join(
        f"  0x{v:08X} (decimal {v}) — returned by: {', '.join(fns[:6])}"
        for v, fns in sorted(candidates.items(), reverse=True)
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
        result_map: dict[int, str] = {}
        for k, meaning in named.items():
            try:
                result_map[int(k, 16)] = str(meaning)
            except (ValueError, TypeError):
                pass
        return result_map
    except Exception as exc:
        logger.debug("[explore] sentinel candidate naming LLM call failed: %s", exc)
        return {}


def _calibrate_sentinels(
    invocables: list[dict], client, model: str, job_id: str = "", hints: str = ""
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
            from api.storage import _download_blob
            from api.config import ARTIFACT_CONTAINER
            _prior_raw = json.loads(_download_blob(ARTIFACT_CONTAINER, f"{job_id}/sentinel_calibration.json"))
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

    # Q-1 fix: pre-seed from hint-derived error codes so codes already known
    # from user hints don't need LLM naming and are present even if the empty-
    # arg sweep never triggered them.
    if hints:
        hint_codes = _parse_hint_error_codes(hints)
        for hint_code, hint_meaning in hint_codes.items():
            if hint_code not in candidates:
                candidates[hint_code] = [f"(hint: {hint_meaning})"]

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

    named_by_llm = _name_sentinel_candidates(unresolved, client, model)
    # Q16/T-17: log the LLM naming decision to probe-log for audit transparency
    if job_id:
        try:
            from api.storage import _append_explore_probe_log
            _append_explore_probe_log(job_id, [{
                "phase": "name_sentinel_candidates",
                "function": "(all)",
                "args": {
                    "candidate_codes": [f"0x{v:08X}" for v in sorted(unresolved.keys(), reverse=True)],
                    "candidate_count": len(unresolved),
                },
                "result_excerpt": json.dumps(
                    {f"0x{k:08X}": v for k, v in named_by_llm.items()}
                )[:400] if named_by_llm else "(no codes named)",
                "trace": None,
            }])
        except Exception as _nse:
            logger.debug("[%s] name_sentinel_candidates log flush failed: %s", job_id, _nse)
    if named_by_llm:
        merged = dict(resolved)
        merged.update(named_by_llm)
        return merged
    return resolved or _SENTINEL_DEFAULTS


def _analyze_decompiled_unlock_patterns(invocables: list[dict]) -> dict:
    """Analyze decompiled code for unlock patterns (XOR checksums, flag checks, etc.).

    Returns a structured analysis that both Phase 1 and MC-6 can use to generate
    targeted inputs instead of brute-forcing.

    Result: {
        "unlock_functions": [{name, xor_target, xor_codes, comparison_values}],
        "dependency_chains": [{source, target, mechanism}],
        "flag_checks": [{function, flag_hex, setter_function}],
    }
    """
    analysis: dict = {
        "unlock_functions": [],
        "dependency_chains": [],
        "flag_checks": [],
    }

    inv_map = {inv["name"]: inv for inv in invocables}

    # Identify unlock/auth functions and analyze their decompiled code
    for inv in invocables:
        doc = inv.get("doc_comment") or inv.get("doc") or ""
        if not doc:
            continue
        name = inv["name"]

        # Pattern 1: XOR checksum — "== 0xNN)" where NN is a small hex value
        is_unlock = bool(_re.search(r"unlock|auth|login|enable|activate", name, _re.I))
        xor_match = _re.search(r"==\s*0x([0-9a-fA-F]{1,4})\s*\)", doc)

        if is_unlock and xor_match:
            xor_target = int(xor_match.group(1), 16)
            xor_codes = _generate_xor_codes(xor_target)
            analysis["unlock_functions"].append({
                "name": name,
                "xor_target": xor_target,
                "xor_target_hex": f"0x{xor_target:02X}",
                "xor_codes": xor_codes,
                "params": inv.get("parameters") or [],
            })

        # Pattern 2: Flag/bit checks — "(DAT_xxx & N) != 0" or "*(ptr + offset) & mask"
        flag_match = _re.search(r"\(.*?&\s*(0x[0-9a-fA-F]+|[0-9]+)\)\s*[!=]=\s*0", doc)
        if flag_match and not is_unlock:
            flag_val = flag_match.group(1)
            # Look for which function sets this flag
            for other_inv in invocables:
                other_doc = other_inv.get("doc_comment") or other_inv.get("doc") or ""
                other_name = other_inv["name"]
                if other_name == name or not other_doc:
                    continue
                if flag_val in other_doc and _re.search(
                    r"unlock|init|enable|activate|set|clear", other_name, _re.I
                ):
                    analysis["dependency_chains"].append({
                        "source": other_name,
                        "target": name,
                        "mechanism": f"sets flag {flag_val}",
                    })
                    analysis["flag_checks"].append({
                        "function": name,
                        "flag_hex": flag_val,
                        "setter_function": other_name,
                    })

        # Pattern 3: Comparison against specific constants — "== 0xNNNNNNNN"
        # (sentinel return codes that indicate specific error conditions)
        for cmp_match in _re.finditer(r"return\s+0x([0-9a-fA-F]{8})", doc):
            val = int(cmp_match.group(1), 16)
            if val >= 0x80000000:
                pass  # sentinel codes are handled by calibration

    return analysis


def _generate_xor_codes(target: int, max_results: int = 10) -> list[str]:
    """Generate Python strings whose UTF-8 encoded bytes XOR to *target*.

    The DLL receives a C string via ctypes. The executor path is:
      Python str → str.encode("utf-8") → ctypes c_char_p → DLL byte*
    The DLL then XORs each byte of that C string (via lstrlenA).

    For targets < 128: ASCII-only codes work (same in UTF-8 and latin-1).
    For targets >= 128 (e.g. 0xA5): we use 3-byte UTF-8 characters whose
    encoded bytes contribute high-bit XOR values that ASCII cannot reach.
    """
    if target >= 256 or target == 0:
        return []

    codes: list[str] = []

    if target < 128:
        # Pure ASCII: 3 known bytes + 1 computed byte
        prefixes = [b"AAA", b"abc", b"123", b"XYZ", b"key",
                    b"COD", b"PAS", b"UNL", b"adm", b"XXX"]
        for prefix in prefixes:
            xor_acc = 0
            for b in prefix:
                xor_acc ^= b
            needed = xor_acc ^ target
            if needed == 0 or needed > 127:
                continue
            candidate = prefix + bytes([needed])
            if 0 not in candidate:
                codes.append(candidate.decode("ascii"))
            if len(codes) >= max_results:
                break
        return list(dict.fromkeys(codes))

    # Target >= 128: use 3-byte UTF-8 characters (U+0800..U+FFFF).
    # A 3-byte UTF-8 char encodes as [0xE0|h, 0x80|m, 0x80|l]
    # whose byte XOR is always 0xC0 | (bits from h,m,l) with bit7=1, bit6=1.
    # Adding an ASCII prefix byte can flip bit 6 to reach targets where bit6=0.
    #
    # We solve: prefix_xor ^ utf8_byte1 ^ utf8_byte2 ^ utf8_byte3 = target
    # by brute-forcing over valid codepoints.

    _found: set[str] = set()

    for prefix_byte in [0x41, 0x42, 0x43, 0x61, 0x62, 0x63, 0x31, 0x32, 0x33, 0x58]:
        if len(_found) >= max_results:
            break
        for cp in range(0x0800, 0x10000):
            if len(_found) >= max_results:
                break
            try:
                s = chr(cp)
                utf8 = s.encode("utf-8")
            except (ValueError, UnicodeEncodeError):
                continue
            if len(utf8) != 3:
                continue
            xor_val = prefix_byte
            for b in utf8:
                xor_val ^= b
            if xor_val == target:
                code = chr(prefix_byte) + s
                if code not in _found:
                    # Verify: no null bytes in UTF-8 encoding
                    full_utf8 = code.encode("utf-8")
                    if 0 not in full_utf8:
                        _found.add(code)
            if len(_found) >= max_results:
                break

    codes = list(_found)

    # Fallback: also try 2 ASCII + 1 three-byte UTF-8
    if len(codes) < max_results:
        for p1 in [0x41, 0x61, 0x31]:
            if len(codes) >= max_results:
                break
            for p2 in [0x42, 0x62, 0x32]:
                if len(codes) >= max_results:
                    break
                prefix_xor = p1 ^ p2
                for cp in range(0x0800, 0x10000):
                    if len(codes) >= max_results:
                        break
                    try:
                        s = chr(cp)
                        utf8 = s.encode("utf-8")
                    except (ValueError, UnicodeEncodeError):
                        continue
                    if len(utf8) != 3:
                        continue
                    xor_val = prefix_xor
                    for b in utf8:
                        xor_val ^= b
                    if xor_val == target:
                        code = chr(p1) + chr(p2) + s
                        full_utf8 = code.encode("utf-8")
                        if 0 not in full_utf8 and code not in codes:
                            codes.append(code)

    return codes[:max_results]


def _probe_write_unlock(invocables: list[dict], dll_strings: dict,
                        vocab: dict | None = None) -> dict:
    """Phase 1: try to flip the DLL from read-only to write-ready.

    Strategy:
      1. No-param init → test write fn with real args
      2. Mode-based init (0..512) → test write fn with real args
      3. Credential sweep from binary strings → test write fn with real args

    The key improvement: test write functions with plausible args derived from
    vocab id_formats and dll_strings — not empty dicts. A write fn returning
    a sentinel with {} doesn't mean init failed; it means the args are wrong.
    """
    _WRITE_SENTINELS = {0xFFFFFFFB, 0xFFFFFFFE, 0xFFFFFFFF}
    inv_map = {inv["name"]: inv for inv in invocables}
    init_names = [n for n in inv_map if _re.search(r"init(ializ)?", n, _re.I)]
    write_fn_names = [
        n for n in inv_map
        if _re.search(r"(pay|redeem|unlock|process|write|commit|transfer|debit|credit)", n, _re.I)
    ]
    tried = []
    code_analysis: dict = {}

    # Build plausible write-function test args from vocab and binary strings
    test_arg_sets = _build_write_test_args(inv_map, write_fn_names, dll_strings, vocab)

    def _test_write_after_init(init_seq: list[dict]) -> dict | None:
        """After an init sequence, test each write fn with real args."""
        for wfn in write_fn_names:
            arg_candidates = test_arg_sets.get(wfn, [{}])
            for test_args in arg_candidates[:3]:
                result = _execute_tool(inv_map[wfn], test_args)
                tried.append(f"{wfn}({test_args}) -> {result}")
                ret_match = _re.match(r"Returned:\s*(\d+)", result or "")
                ret_val = int(ret_match.group(1)) & 0xFFFFFFFF if ret_match else 0xFFFFFFFF
                if ret_val not in _WRITE_SENTINELS and ret_val == 0:
                    return {
                        "unlocked": True, "sequence": init_seq,
                        "write_fn_tested": wfn, "write_fn_args": test_args,
                        "notes": f"unlocked — {wfn}({test_args}) returned 0",
                        "code_reasoning_analysis": code_analysis,
                    }
        return None

    # ── Strategy 0: Code-reasoning from decompiled source ─────────────────
    # Analyze decompiled code BEFORE brute-force to find targeted inputs.
    code_analysis = _analyze_decompiled_unlock_patterns(invocables)

    if code_analysis.get("unlock_functions"):
        for uf in code_analysis["unlock_functions"]:
            uf_inv = inv_map.get(uf["name"])
            if not uf_inv:
                continue
            params = uf.get("params") or []
            if not params:
                continue

            id_samples = []
            if vocab and vocab.get("id_formats"):
                id_samples = [str(f) for f in vocab["id_formats"] if f][:4]
            for s in dll_strings.get("ids", []):
                if s and s not in id_samples:
                    id_samples.append(s)
            if not id_samples:
                id_samples = ["CUST-001", "ORD-001", "ACCT-001"]

            # Init first, then try unlock with XOR codes, then test writes
            for init_n in init_names:
                _execute_tool(inv_map[init_n], {})

            for xor_code in uf.get("xor_codes", []):
                for id_val in id_samples[:3]:
                    args = {}
                    for i, p in enumerate(params):
                        pn = p.get("name", f"param_{i+1}")
                        if i == 0:
                            args[pn] = id_val
                        elif i == 1:
                            args[pn] = xor_code
                        else:
                            args[pn] = id_val
                    result = _execute_tool(uf_inv, args)
                    tried.append(f"[code-reasoning] {uf['name']}({args}) -> {result}")
                    ret_match = _re.match(r"Returned:\s*(\d+)", result or "")
                    ret_val = int(ret_match.group(1)) & 0xFFFFFFFF if ret_match else 0xFFFFFFFF
                    if ret_val == 0:
                        # Unlock succeeded — now test write functions
                        hit = _test_write_after_init([
                            {"fn": init_n, "args": {}} for init_n in init_names
                        ] + [{"fn": uf["name"], "args": args}])
                        if hit:
                            hit["notes"] = (
                                f"unlocked via CODE REASONING — {uf['name']}({args}) "
                                f"returned 0 (XOR target: {uf['xor_target_hex']})"
                            )
                            return hit

    # Also try dependency chains: if function A depends on B, call B first
    for dep in code_analysis.get("dependency_chains", []):
        src = inv_map.get(dep["source"])
        if not src:
            continue
        for init_n in init_names:
            _execute_tool(inv_map[init_n], {})
        # Call the setter function with various args
        src_params = src.get("parameters") or []
        if src_params:
            for id_val in (test_arg_sets.get(dep["source"]) or [{}])[:3]:
                result = _execute_tool(src, id_val)
                tried.append(f"[dependency] {dep['source']}({id_val}) -> {result}")
        else:
            result = _execute_tool(src, {})
            tried.append(f"[dependency] {dep['source']}() -> {result}")
        # Now test if the dependent write function works
        hit = _test_write_after_init([
            {"fn": init_n, "args": {}} for init_n in init_names
        ] + [{"fn": dep["source"], "args": {}}])
        if hit:
            hit["notes"] = f"unlocked via DEPENDENCY CHAIN — {dep['source']} → {dep['target']}"
            return hit

    # 1. No-param init variants (brute-force fallback)
    no_param_inits = [n for n in init_names if not inv_map[n].get("parameters")]
    if no_param_inits:
        for n in no_param_inits:
            result = _execute_tool(inv_map[n], {})
            tried.append(f"{n}() -> {result}")
        if write_fn_names:
            hit = _test_write_after_init([{"fn": n, "args": {}} for n in no_param_inits])
            if hit:
                return hit

    # 2. Mode-based init
    for mode in (0, 1, 2, 4, 8, 16, 256, 512):
        for n in init_names:
            if inv_map[n].get("parameters"):
                result = _execute_tool(inv_map[n], {"param_1": mode})
                tried.append(f"{n}(mode={mode}) -> {result}")
                if write_fn_names:
                    hit = _test_write_after_init([{"fn": n, "args": {"param_1": mode}}])
                    if hit:
                        return hit

    # 3. Credential sweep using strings extracted from the binary
    all_strings = dll_strings.get("ids", []) + dll_strings.get("misc", [])
    cred_tokens = list(dict.fromkeys(
        s for s in all_strings if 3 < len(s) < 40
    ))[:28]
    for n in init_names:
        if not inv_map[n].get("parameters"):
            continue
        for tok in cred_tokens:
            result = _execute_tool(inv_map[n], {"param_1": tok})
            tried.append(f"{n}(cred={tok!r}) -> {result}")
            if write_fn_names:
                hit = _test_write_after_init([{"fn": n, "args": {"param_1": tok}}])
                if hit:
                    return hit

    canary = write_fn_names[0] if write_fn_names else None
    return {"unlocked": False, "sequence": [], "write_fn_tested": canary,
            "notes": f"write-unlock failed after {len(tried)} attempts",
            "code_reasoning_analysis": code_analysis}


def _build_write_test_args(
    inv_map: dict[str, dict],
    write_fn_names: list[str],
    dll_strings: dict,
    vocab: dict | None,
) -> dict[str, list[dict]]:
    """Build plausible argument sets for write-function unlock testing.

    Uses vocab id_formats and binary strings to generate realistic args
    (e.g. CUST-001 for customer_id, 100 for amount) instead of empty dicts.
    Returns {fn_name: [arg_dict, ...]} with up to 3 candidates per function.
    """
    id_samples = []
    if vocab and vocab.get("id_formats"):
        for fmt in vocab["id_formats"]:
            s = str(fmt).strip()
            if s:
                id_samples.append(s)
    for s in dll_strings.get("ids", []):
        if s and s not in id_samples:
            id_samples.append(s)
    if not id_samples:
        id_samples = ["CUST-001", "ORD-001", "ACCT-001"]

    result: dict[str, list[dict]] = {}
    for fn_name in write_fn_names:
        inv = inv_map.get(fn_name)
        if not inv:
            continue
        params = inv.get("parameters") or []
        if not params:
            result[fn_name] = [{}]
            continue
        candidates: list[dict] = []
        for id_val in id_samples[:3]:
            args: dict = {}
            for p in params:
                if isinstance(p, str):
                    p = {"name": p, "type": "string"}
                pname = p.get("name", "arg")
                ptype = (p.get("type") or "").lower()
                if _re.search(r"id|account|customer|order", pname, _re.I):
                    args[pname] = id_val
                elif _re.search(r"amount|cents|points|value|price", pname, _re.I):
                    args[pname] = 100
                elif "int" in ptype or "dword" in ptype or "long" in ptype:
                    args[pname] = 1
                elif "char" in ptype or "str" in ptype:
                    args[pname] = id_val
                else:
                    args[pname] = 1
            candidates.append(args)
        result[fn_name] = candidates or [{}]
    return result


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
