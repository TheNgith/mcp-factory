"""api.pipeline.s00_setup.calibration – Sentinel calibration (Phase 0.5).

Probes every exported function with no args and clusters the non-zero
high-bit return values to discover DLL-specific sentinel error codes.
"""

from __future__ import annotations

import json
import logging
import os as _os
import re as _re
from collections import defaultdict

from api.executor import _execute_tool_traced
from api.sentinel_codes import classify_common_result_code
from api.pipeline.helpers import _SENTINEL_DEFAULTS

logger = logging.getLogger("mcp_factory.api")

_ENABLE_STICKY_SENTINEL_BASELINE = _os.getenv(
    "EXPLORE_ENABLE_STICKY_SENTINEL_BASELINE", "0"
).strip().lower() not in {"0", "false", "no", "off"}


def _parse_hint_error_codes(hints: str) -> dict[int, str]:
    """Extract explicit error code definitions from user hints.

    Parses patterns like:
      "Error code 0xFFFFFFFB (4294967291) = write denied"
      "0xFFFFFFFC = account not found"
    Returns {int_code: meaning}.
    """
    codes: dict[int, str] = {}
    if not hints:
        return codes
    for m in _re.finditer(
        r'(?:error\s+code\s+)?'
        r'(0x[0-9A-Fa-f]+)'
        r'(?:\s*\(\d+\))?'
        r'\s*[=:–—]\s*'
        r'(.+?)(?:\.|$)',
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
    """Ask the LLM to assign short meanings to unresolved high-bit return codes."""
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
            pass

    if _prior_sentinels:
        _success_values = {v for v, c in counts.items() if v == 0} | {0}
        for _pv, _pm in _prior_sentinels.items():
            if _pv not in candidates and _pv not in _success_values and _pv >= 0x80000000:
                candidates[_pv] = [f"(prior-session: {_pm})"]

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
