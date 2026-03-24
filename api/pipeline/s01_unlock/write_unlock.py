"""api.pipeline.s01_unlock.write_unlock – Write-unlock probe (Phase 1).

Attempts to flip the DLL from read-only to write-ready by discovering
the correct initialization sequence and unlock codes.

Strategy:
  0. Code reasoning: analyze decompiled C for XOR checksums, flag checks
  1. No-param init -> test write fn with real args
  2. Mode-based init (0..512) -> test write fn with real args
  3. Credential sweep from binary strings -> test write fn with real args
"""

from __future__ import annotations

import logging
import re as _re

from api.executor import _execute_tool

logger = logging.getLogger("mcp_factory.api")


def _analyze_decompiled_unlock_patterns(invocables: list[dict]) -> dict:
    """Analyze decompiled code for unlock patterns (XOR checksums, flag checks, etc.).

    Returns a structured analysis that both Phase 1 and MC-6 can use to generate
    targeted inputs instead of brute-forcing.
    """
    analysis: dict = {
        "unlock_functions": [],
        "dependency_chains": [],
        "flag_checks": [],
    }

    for inv in invocables:
        doc = inv.get("doc_comment") or inv.get("doc") or ""
        if not doc:
            continue
        name = inv["name"]

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

        flag_match = _re.search(r"\(.*?&\s*(0x[0-9a-fA-F]+|[0-9]+)\)\s*[!=]=\s*0", doc)
        if flag_match and not is_unlock:
            flag_val = flag_match.group(1)
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

        for cmp_match in _re.finditer(r"return\s+0x([0-9a-fA-F]{8})", doc):
            val = int(cmp_match.group(1), 16)
            if val >= 0x80000000:
                pass  # handled by sentinel calibration

    return analysis


def _generate_xor_codes(target: int, max_results: int = 10) -> list[str]:
    """Generate Python strings whose UTF-8 encoded bytes XOR to *target*.

    The DLL receives a C string via ctypes. The executor path is:
      Python str -> str.encode("utf-8") -> ctypes c_char_p -> DLL byte*
    The DLL then XORs each byte of that C string (via lstrlenA).

    For targets < 128: ASCII-only codes work (same in UTF-8 and latin-1).
    For targets >= 128 (e.g. 0xA5): we use 3-byte UTF-8 characters whose
    encoded bytes contribute high-bit XOR values that ASCII cannot reach.
    """
    if target >= 256 or target == 0:
        return []

    codes: list[str] = []

    if target < 128:
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
                    full_utf8 = code.encode("utf-8")
                    if 0 not in full_utf8:
                        _found.add(code)
            if len(_found) >= max_results:
                break

    codes = list(_found)

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


def _build_write_test_args(
    inv_map: dict[str, dict],
    write_fn_names: list[str],
    dll_strings: dict,
    vocab: dict | None,
) -> dict[str, list[dict]]:
    """Build plausible argument sets for write-function unlock testing."""
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


def _probe_write_unlock(invocables: list[dict], dll_strings: dict,
                        vocab: dict | None = None) -> dict:
    """Phase 1: try to flip the DLL from read-only to write-ready."""
    _WRITE_SENTINELS = {0xFFFFFFFB, 0xFFFFFFFE, 0xFFFFFFFF}
    inv_map = {inv["name"]: inv for inv in invocables}
    init_names = [n for n in inv_map if _re.search(r"init(ializ)?", n, _re.I)]
    write_fn_names = [
        n for n in inv_map
        if _re.search(r"(pay|redeem|unlock|process|write|commit|transfer|debit|credit)", n, _re.I)
    ]
    tried = []
    code_analysis: dict = {}

    test_arg_sets = _build_write_test_args(inv_map, write_fn_names, dll_strings, vocab)

    def _test_write_after_init(init_seq: list[dict]) -> dict | None:
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

    # Strategy 0: Code-reasoning from decompiled source
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
                        hit = _test_write_after_init([
                            {"fn": init_n, "args": {}} for init_n in init_names
                        ] + [{"fn": uf["name"], "args": args}])
                        if hit:
                            hit["notes"] = (
                                f"unlocked via CODE REASONING — {uf['name']}({args}) "
                                f"returned 0 (XOR target: {uf['xor_target_hex']})"
                            )
                            return hit

    for dep in code_analysis.get("dependency_chains", []):
        src = inv_map.get(dep["source"])
        if not src:
            continue
        for init_n in init_names:
            _execute_tool(inv_map[init_n], {})
        src_params = src.get("parameters") or []
        if src_params:
            for id_val in (test_arg_sets.get(dep["source"]) or [{}])[:3]:
                result = _execute_tool(src, id_val)
                tried.append(f"[dependency] {dep['source']}({id_val}) -> {result}")
        else:
            result = _execute_tool(src, {})
            tried.append(f"[dependency] {dep['source']}() -> {result}")
        hit = _test_write_after_init([
            {"fn": init_n, "args": {}} for init_n in init_names
        ] + [{"fn": dep["source"], "args": {}}])
        if hit:
            hit["notes"] = f"unlocked via DEPENDENCY CHAIN — {dep['source']} → {dep['target']}"
            return hit

    # Brute-force fallback: no-param init
    no_param_inits = [n for n in init_names if not inv_map[n].get("parameters")]
    if no_param_inits:
        for n in no_param_inits:
            result = _execute_tool(inv_map[n], {})
            tried.append(f"{n}() -> {result}")
        if write_fn_names:
            hit = _test_write_after_init([{"fn": n, "args": {}} for n in no_param_inits])
            if hit:
                return hit

    # Mode-based init
    for mode in (0, 1, 2, 4, 8, 16, 256, 512):
        for n in init_names:
            if inv_map[n].get("parameters"):
                result = _execute_tool(inv_map[n], {"param_1": mode})
                tried.append(f"{n}(mode={mode}) -> {result}")
                if write_fn_names:
                    hit = _test_write_after_init([{"fn": n, "args": {"param_1": mode}}])
                    if hit:
                        return hit

    # Credential sweep
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
