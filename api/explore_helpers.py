from __future__ import annotations

import logging
import os as _os
import re as _re
import time
from typing import Any

from api.config import ARTIFACT_CONTAINER
from api.sentinel_codes import classify_common_result_code
from api.storage import _download_blob, _get_job_status, _persist_job_status, _upload_to_blob

logger = logging.getLogger("mcp_factory.api")

_WRITE_FN_RE = _re.compile(r"(pay|refund|redeem|unlock|process|write|commit|transfer|debit|credit)", _re.I)

_WRITE_POLICY_RULES: dict[str, dict] = {
    "CS_ProcessPayment": {
        "required_any": ["customer_id", "order_id", "account_id", "param_1"],
        "amount_keys": ["amount_cents", "amount", "param_2", "param_3"],
    },
    "CS_ProcessRefund": {
        "required_any": ["customer_id", "order_id", "account_id", "param_1"],
        "amount_keys": ["amount_cents", "amount", "param_2", "param_3"],
    },
    "CS_RedeemLoyaltyPoints": {
        "required_any": ["customer_id", "account_id", "param_1"],
        "amount_keys": ["points", "amount", "param_2"],
    },
    "CS_UnlockAccount": {
        "required_any": ["customer_id", "account_id", "param_1"],
        "amount_keys": [],
    },
}

_WRITE_RETRY_BUDGET_BY_CLASS: dict[str, int] = {
    "write_denied": 2,
    "not_initialized": 2,
    "account_locked": 1,
    "invalid_input": 2,
    "unknown": 1,
}

# Dev-speed switch: disable expensive gap-resolution/mini-session loops while
# preserving the code path for higher-quality validation runs.
# Values treated as false: 0, false, no, off
_GAP_RESOLUTION_ENABLED = _os.getenv("EXPLORE_ENABLE_GAP_RESOLUTION", "1").strip().lower() not in {
    "0", "false", "no", "off"
}


def _parse_id_format_pattern(pattern: str) -> str:
    out = []
    for ch in str(pattern):
        if ch == "N":
            out.append(r"\d")
        elif ch == "A":
            out.append(r"[A-Z]")
        elif ch == "X":
            out.append(r"[A-Z0-9]")
        elif ch == "*":
            out.append(r"[A-Z0-9_-]+")
        elif ch in "-_":
            out.append(_re.escape(ch))
        else:
            out.append(_re.escape(ch))
    return "^" + "".join(out) + "$"


def _sample_id_from_vocab(id_formats: list[str], kind: str, attempt: int = 0) -> str:
    kind = (kind or "").lower()
    # Q-3: rotate IDs across attempts so probes aren't locked to one account
    _cust_ids = ["CUST-001", "CUST-002", "CUST-003"]
    _order_ids = ["ORD-20260315-0117", "ORD-20260315-0118", "ORD-20260315-0119"]
    for fmt in id_formats:
        s = str(fmt or "").upper()
        if kind == "order" and "ORD" in s:
            return _order_ids[attempt % len(_order_ids)]
        if kind in {"customer", "account"} and ("CUST" in s or "ACCT" in s or "ACCOUNT" in s):
            return _cust_ids[attempt % len(_cust_ids)]
    if kind == "order":
        return _order_ids[attempt % len(_order_ids)]
    return _cust_ids[attempt % len(_cust_ids)]


def _default_scalar_value(param_name: str, json_type: str, description: str, vocab: dict, attempt: int = 0) -> Any:
    name = (param_name or "").lower()
    desc = (description or "").lower()
    jtype = (json_type or "").lower()
    id_formats = [str(x) for x in (vocab.get("id_formats") or []) if x]

    if _re.search(r"order", name) or _re.search(r"order", desc):
        return _sample_id_from_vocab(id_formats, "order", attempt)
    if _re.search(r"customer|account", name) or _re.search(r"customer|account", desc):
        return _sample_id_from_vocab(id_formats, "customer", attempt)
    if _re.search(r"amount|cents|refund|payment|debit|credit", name) or _re.search(r"amount|cents", desc):
        return 100 if attempt > 0 else 1000
    if _re.search(r"points", name) or _re.search(r"points", desc):
        return 100
    if _re.search(r"size|count|length|len", name):
        return 64
    if _re.search(r"mode|flag|enable", name):
        return 1

    if jtype in {"integer", "number"}:
        return 1
    if jtype == "boolean":
        return True
    return "TEST"


def _ranked_param_candidates(param_name: str, json_type: str, description: str, vocab: dict) -> list[dict]:
    """Return ranked candidate values for one parameter.

    Candidates are generic and vocabulary-driven. They are ordered by score so
    attempt=0 picks the strongest candidate, attempt=1 picks the next, etc.
    """
    name = (param_name or "").lower()
    desc = (description or "").lower()
    jtype = (json_type or "").lower()
    id_formats = [str(x) for x in (vocab.get("id_formats") or []) if x]

    ranked: list[dict] = []

    def _add(value: Any, score: float, source: str, reason: str) -> None:
        ranked.append({
            "value": value,
            "score": float(score),
            "source": source,
            "reason": reason,
        })

    if _re.search(r"order", name) or _re.search(r"order", desc):
        _add(_sample_id_from_vocab(id_formats, "order", attempt=0), 1.0, "vocab.id_formats", "order-like parameter")
        _add(_sample_id_from_vocab(id_formats, "order", attempt=1), 0.92, "vocab.id_formats", "order-like retry variant")
        _add(_sample_id_from_vocab(id_formats, "order", attempt=2), 0.85, "vocab.id_formats", "order-like second retry variant")
    elif _re.search(r"customer|account", name) or _re.search(r"customer|account", desc):
        _add(_sample_id_from_vocab(id_formats, "customer", attempt=0), 1.0, "vocab.id_formats", "customer/account-like parameter")
        _add(_sample_id_from_vocab(id_formats, "customer", attempt=1), 0.92, "vocab.id_formats", "customer/account retry variant")
        _add(_sample_id_from_vocab(id_formats, "customer", attempt=2), 0.85, "vocab.id_formats", "customer/account second retry variant")
    elif _re.search(r"amount|cents|refund|payment|debit|credit", name) or _re.search(r"amount|cents", desc):
        _add(2500, 1.0, "heuristic.amount", "common cents baseline")
        _add(1000, 0.9, "heuristic.amount", "smaller cents baseline")
        _add(100, 0.8, "heuristic.amount", "low cents probe")
        _add(1, 0.6, "heuristic.amount", "minimum positive cents")
    elif _re.search(r"points", name) or _re.search(r"points", desc):
        _add(100, 1.0, "heuristic.points", "common points baseline")
        _add(250, 0.85, "heuristic.points", "higher points probe")
        _add(10, 0.75, "heuristic.points", "low points probe")
        _add(1, 0.65, "heuristic.points", "minimum positive points")
    elif _re.search(r"size|count|length|len", name):
        _add(64, 1.0, "heuristic.buffer", "safe buffer/count baseline")
        _add(128, 0.85, "heuristic.buffer", "medium buffer/count retry")
        _add(256, 0.75, "heuristic.buffer", "larger buffer/count retry")
    elif _re.search(r"mode|flag|enable", name):
        _add(1, 1.0, "heuristic.flag", "enabled mode baseline")
        _add(0, 0.9, "heuristic.flag", "disabled mode retry")
        _add(2, 0.65, "heuristic.flag", "alternate mode probe")

    # T-05: for unmatched string parameters, use static binary-string IDs as
    # higher-quality candidates before falling back to generic placeholders.
    # This ensures IDs found in the binary (e.g. "CUST-001") are exercised.
    _binary_string_ids = [str(x) for x in (vocab.get("binary_string_ids") or []) if x]
    if _binary_string_ids and jtype not in {"integer", "number", "boolean"} and not ranked:
        for _bsi, _bsid in enumerate(_binary_string_ids[:3]):
            _add(_bsid, 0.72 - _bsi * 0.05, "static_analysis.binary_strings", f"static ID from binary: {_bsid}")

    if jtype in {"integer", "number"}:
        _add(1, 0.7, "default.numeric", "generic positive numeric")
        _add(0, 0.6, "default.numeric", "generic zero numeric")
        _add(2, 0.5, "default.numeric", "generic alternate numeric")
    elif jtype == "boolean":
        _add(True, 0.7, "default.boolean", "generic true baseline")
        _add(False, 0.6, "default.boolean", "generic false retry")
    else:
        _add("TEST", 0.7, "default.string", "generic string baseline")
        _add("A", 0.55, "default.string", "short string retry")
        _add("0", 0.5, "default.string", "numeric-string retry")

    deduped: list[dict] = []
    seen: set[str] = set()
    for c in sorted(ranked, key=lambda x: x["score"], reverse=True):
        key = repr(c["value"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(c)
    return deduped


def _build_ranked_fallback_probe_args(inv: dict, vocab: dict, attempt: int = 0) -> tuple[dict, dict]:
    """Build deterministic fallback args plus per-parameter selection metadata."""
    args: dict = {}
    selection: dict = {}
    for p in (inv.get("parameters") or []):
        if isinstance(p, str):
            p = {"name": p, "json_type": "string"}
        pname = str(p.get("name") or "")
        direction = str(p.get("direction") or "in").lower()
        if direction == "out":
            continue
        jtype = str(p.get("json_type") or "string")
        pdesc = str(p.get("description") or p.get("type") or "")
        candidates = _ranked_param_candidates(pname, jtype, pdesc, vocab)
        if not candidates:
            val = _default_scalar_value(pname, jtype, pdesc, vocab, attempt=attempt)
            args[pname] = val
            selection[pname] = {
                "source": "default.scalar",
                "reason": "no ranked candidates available",
                "score": 0.0,
                "rank": 1,
                "candidate_count": 1,
            }
            continue
        idx = min(max(int(attempt), 0), len(candidates) - 1)
        chosen = candidates[idx]
        args[pname] = chosen["value"]
        selection[pname] = {
            "source": chosen["source"],
            "reason": chosen["reason"],
            "score": chosen["score"],
            "rank": idx + 1,
            "candidate_count": len(candidates),
        }
    return args, selection


def _build_fallback_probe_args(inv: dict, vocab: dict, attempt: int = 0) -> dict:
    """Build deterministic fallback args for one probe attempt.

    This is intentionally generic: it uses parameter names/types plus vocabulary
    seeds rather than component-specific constants.
    """
    args, _ = _build_ranked_fallback_probe_args(inv, vocab, attempt=attempt)
    return args


def _classify_result_text(result_text: str) -> dict:
    m = _re.search(r"Returned:\s*(-?\d+)", result_text or "")
    if not m:
        return {
            "has_return": False,
            "format_guess": "no_return_detected",
            "confidence": 0.0,
            "source": "none",
        }
    signed = int(m.group(1))
    unsigned = signed & 0xFFFFFFFF
    meaning = classify_common_result_code(unsigned)

    if signed == 0:
        fmt = "bool_style_success"
        conf = 1.0
        src = "deterministic.zero"
    elif meaning:
        if meaning.startswith("HRESULT") or meaning.startswith("HRESULT_FROM_WIN32"):
            fmt = "hresult_like"
            conf = 0.98
            src = "deterministic.common_hresult"
        elif meaning.startswith("NTSTATUS"):
            fmt = "ntstatus_like"
            conf = 0.98
            src = "deterministic.common_ntstatus"
        elif meaning.startswith("Win32"):
            fmt = "win32_error"
            conf = 0.95
            src = "deterministic.common_win32"
        elif "-like" in meaning:
            fmt = "high_bit_failure_family"
            conf = 0.75
            src = "heuristic.high_bit_family"
        else:
            fmt = "custom_signed_negative"
            conf = 0.9
            src = "deterministic.default_sentinel"
    elif signed < 0:
        fmt = "custom_signed_negative"
        conf = 0.7
        src = "heuristic.signed_negative"
    elif unsigned >= 0x80000000:
        fmt = "high_bit_unknown"
        conf = 0.45
        src = "heuristic.high_bit"
    elif signed > 0:
        fmt = "custom_positive_status_or_data"
        conf = 0.65
        src = "heuristic.custom_positive"
    else:
        fmt = "domain_or_non_sentinel"
        conf = 0.3
        src = "heuristic.low_value"

    return {
        "has_return": True,
        "signed": signed,
        "unsigned": unsigned,
        "hex": f"0x{unsigned:08X}",
        "format_guess": fmt,
        "confidence": conf,
        "source": src,
        "meaning": meaning,
    }


def _sentinel_class_from_classification(classification: dict) -> str:
    meaning = (classification.get("meaning") or "").lower()
    if "write" in meaning and "denied" in meaning:
        return "write_denied"
    if "not initialized" in meaning:
        return "not_initialized"
    if "locked" in meaning:
        return "account_locked"
    if "invalid" in meaning or "not found" in meaning or "null" in meaning:
        return "invalid_input"
    return "unknown"


def _write_policy_precheck(
    fn_name: str,
    args: dict,
    vocab: dict,
    unlock_result: dict | None,
) -> tuple[bool, str | None, str | None]:
    if not _WRITE_FN_RE.search(fn_name):
        return True, None, None

    if unlock_result is not None and not unlock_result.get("unlocked"):
        return False, "dependency_missing", "write path requires initialization/unlock sequence"

    rule = _WRITE_POLICY_RULES.get(fn_name, {})
    required_any = rule.get("required_any", [])
    if required_any and not any(k in args and str(args.get(k, "")).strip() for k in required_any):
        return False, "schema_missing", "missing required ID/account argument for write function"

    amount_keys = set(rule.get("amount_keys", []))
    if not amount_keys:
        amount_keys = {k for k in args if _re.search(r"amount|points|cents|value", k, _re.I)}
    for k in amount_keys:
        if k not in args:
            continue
        try:
            v = int(args[k])
        except (ValueError, TypeError):
            return False, "schema_missing", f"{k} must be numeric"
        if v <= 0 or v > 1_000_000_000:
            return False, "policy_exhausted", f"{k}={v} outside bounded write-policy range"

    id_patterns = [str(x) for x in (vocab.get("id_formats") or []) if x]
    id_like_args = {
        k: str(v) for k, v in args.items()
        if v is not None and _re.search(r"id|account|order|customer", k, _re.I)
    }
    if id_patterns and id_like_args:
        compiled = []
        for p in id_patterns:
            try:
                compiled.append(_re.compile(_parse_id_format_pattern(p), _re.I))
            except Exception:
                continue
        if compiled:
            for k, v in id_like_args.items():
                if not any(rx.match(v) for rx in compiled):
                    return False, "policy_exhausted", f"{k}='{v}' failed ID format normalization"

    return True, None, None


def _build_tool_schemas(invocables: list[dict]) -> list[dict]:
    """Build OpenAI tool call schemas from a list of invocable dicts."""
    from api.chat import _RECORD_FINDING_TOOL, _ENRICH_INVOCABLE_TOOL  # type: ignore

    tool_schemas: list[dict] = []
    for inv in invocables:
        props: dict = {}
        required: list = []
        for p in (inv.get("parameters") or []):
            if isinstance(p, str):
                p = {"name": p, "type": "string"}
            pname = p.get("name", "arg")
            json_type = p.get("json_type") or "string"
            props[pname] = {
                "type": json_type,
                "description": p.get("description") or p.get("type", "string"),
            }
            if p.get("direction", "in") != "out":
                required.append(pname)
        safe_name = _re.sub(r"[^a-zA-Z0-9_.\-]", "_", inv["name"])[:64]
        desc = inv.get("doc") or inv.get("description") or inv.get("signature") or inv["name"]
        tool_schemas.append({
            "type": "function",
            "function": {
                "name": safe_name,
                "description": desc,
                "parameters": {
                    "type": "object",
                    "properties": props,
                    "required": required,
                },
            },
        })
    tool_schemas.append(_RECORD_FINDING_TOOL)
    tool_schemas.append(_ENRICH_INVOCABLE_TOOL)
    return tool_schemas


def _strip_output_buffer_params(tc_args: dict, p_lookup: dict) -> dict:
    """Return *tc_args* with genuine output-buffer parameters removed.

    FIX-1 invariant: only strip params whose Ghidra type is a pointer to a
    numeric/undefined base type (e.g. ``int*``, ``undefined4*``).  Do NOT
    strip params whose base type is ``byte`` or a text-like type — Ghidra
    tags ``byte*`` as ``direction=out`` because it is a pointer, but such
    params are commonly string *inputs* (customer_id, order_id, etc.).

    READS:  tc_args   — {param_name: value} dict from tool call
            p_lookup  — {param_name: invocable_param_dict}
    WRITES: returns a new dict (never mutates tc_args or p_lookup)
    INVARIANT: every non-output-buffer key from tc_args is present in result
    """
    _out_bases: frozenset[str] = frozenset({
        "undefined", "undefined2", "undefined4", "undefined8",
        "uint", "uint32_t", "int", "int32_t", "dword",
        "ulong", "uint4", "uint8", "long", "ulong32",
    })
    result: dict = {}
    for k, v in tc_args.items():
        p = p_lookup.get(k, {})
        pt = p.get("type", "").lower().replace("const ", "").strip().rstrip(" *")
        is_out_buffer = "*" in p.get("type", "") and pt in _out_bases
        if not is_out_buffer:
            result[k] = v
    return result


def _snapshot_schema_stage(job_id: str, stage_blob_name: str) -> None:
    """Copy current mcp_schema.json to a stage-specific blob for diffing."""
    try:
        _raw = _download_blob(ARTIFACT_CONTAINER, f"{job_id}/mcp_schema.json")
        _upload_to_blob(ARTIFACT_CONTAINER, f"{job_id}/{stage_blob_name}", _raw)
    except Exception as _se:
        logger.debug("[%s] schema snapshot failed (%s): %s", job_id, stage_blob_name, _se)


def _save_stage_context(job_id: str, blob_name: str, content: str) -> None:
    """Persist a plain-text model-context snapshot for one pipeline phase.

    Captures the system prompt (or a human-readable summary of it) at the
    start of a phase so that save-session can expose what the LLM saw at
    each stage.  Failures are silently swallowed — this is diagnostic only.
    """
    try:
        _upload_to_blob(ARTIFACT_CONTAINER, f"{job_id}/{blob_name}",
                        content.encode("utf-8"))
    except Exception as _ce:
        logger.debug("[%s] stage context save failed (%s): %s", job_id, blob_name, _ce)


def _set_explore_status(job_id: str, explored: int, total: int, message: str) -> None:
    current = _get_job_status(job_id) or {}
    _persist_job_status(
        job_id,
        {
            **current,
            "explore_phase": "exploring",
            "explore_progress": f"{explored}/{total}",
            "explore_message": message,
            "updated_at": time.time(),
        },
    )