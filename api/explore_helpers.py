from __future__ import annotations

import logging
import os as _os
import re as _re
import time

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


def _snapshot_schema_stage(job_id: str, stage_blob_name: str) -> None:
    """Copy current mcp_schema.json to a stage-specific blob for diffing."""
    try:
        _raw = _download_blob(ARTIFACT_CONTAINER, f"{job_id}/mcp_schema.json")
        _upload_to_blob(ARTIFACT_CONTAINER, f"{job_id}/{stage_blob_name}", _raw)
    except Exception as _se:
        logger.debug("[%s] schema snapshot failed (%s): %s", job_id, stage_blob_name, _se)


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