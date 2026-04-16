"""api/error_enrichment.py – Build structured error payloads for failed tool calls.

Pure module, no I/O.  Consumed by api/chat.py and the generated standalone
mcp_server.py (vendored by src/generation/section4_generate_server.py).  The
payload is attached to SSE tool_result events and to the generated server's
/invoke + /chat responses so the LLM and the UI both receive the same
classified failure context.
"""

from __future__ import annotations

from typing import Any

from api.sentinel_codes import (
    COMMON_HRESULTS,
    COMMON_NTSTATUS,
    COMMON_WIN32_ERRORS,
    SENTINEL_DEFAULTS,
    classify_common_result_code,
)


# Categories that can be surfaced.  Kept as module constants so callers can
# reference them by name without magic strings.
CATEGORY_SENTINEL = "sentinel"
CATEGORY_HRESULT = "hresult"
CATEGORY_WIN32 = "win32"
CATEGORY_NTSTATUS = "ntstatus"
CATEGORY_BRIDGE_UNREACHABLE = "bridge_unreachable"
CATEGORY_TIMEOUT = "timeout"
CATEGORY_NO_EXECUTABLE = "no_executable"
CATEGORY_UNKNOWN_TOOL = "unknown_tool"
CATEGORY_SCHEMA_MISMATCH = "schema_mismatch"
CATEGORY_EXCEPTION = "exception"


def _classify_code(code: int) -> tuple[str, str | None]:
    """Return (category, classified_name) for a numeric return code."""
    code32 = int(code) & 0xFFFFFFFF
    if code32 in SENTINEL_DEFAULTS:
        return CATEGORY_SENTINEL, SENTINEL_DEFAULTS[code32]
    if code32 in COMMON_HRESULTS:
        return CATEGORY_HRESULT, COMMON_HRESULTS[code32]
    if code32 in COMMON_NTSTATUS:
        return CATEGORY_NTSTATUS, COMMON_NTSTATUS[code32]
    if code32 <= 0xFFFF and code32 in COMMON_WIN32_ERRORS:
        return CATEGORY_WIN32, COMMON_WIN32_ERRORS[code32]
    # Fall back to the shared classifier for family-level labels.
    label = classify_common_result_code(code32)
    if label is None:
        return CATEGORY_SENTINEL, None
    if label.startswith("HRESULT"):
        return CATEGORY_HRESULT, label
    if label.startswith("NTSTATUS"):
        return CATEGORY_NTSTATUS, label
    if label.startswith("Win32"):
        return CATEGORY_WIN32, label
    return CATEGORY_SENTINEL, label


def _coerce_code(raw_result: Any) -> int | None:
    """Best-effort extract of a numeric return code from executor output.

    Accepts a raw int, a plain numeric string, or the executor's human form
    'Returned: 4294967295, sentinel: ...' (via a regex scan for the first
    decimal or hex token after 'Returned:').
    """
    if raw_result is None:
        return None
    if isinstance(raw_result, bool):
        return None
    if isinstance(raw_result, int):
        return raw_result
    if not isinstance(raw_result, str):
        return None
    import re as _re
    m = _re.search(r"[Rr]eturned:\s*(-?\d+|0x[0-9a-fA-F]+)", raw_result)
    token = m.group(1) if m else raw_result.strip().split(",")[0].strip()
    try:
        if token.lower().startswith("0x"):
            return int(token, 16)
        return int(token)
    except ValueError:
        return None


def _category_for_trace(trace: dict | None, exception: str | None) -> str | None:
    """Derive a non-numeric category from the trace dict or exception."""
    if not trace and not exception:
        return None
    if trace:
        backend = trace.get("backend")
        if backend == "bridge" and trace.get("exception"):
            return CATEGORY_BRIDGE_UNREACHABLE
        if backend == "timeout":
            return CATEGORY_TIMEOUT
        if backend == "cli" and trace.get("exception") == "no executable path":
            return CATEGORY_NO_EXECUTABLE
        if trace.get("timeout_hit"):
            return CATEGORY_TIMEOUT
        if trace.get("exception"):
            return CATEGORY_EXCEPTION
    if exception:
        return CATEGORY_EXCEPTION
    return None


def _build_what_tried(trace: dict | None) -> list[dict]:
    """Pull probe-matrix attempts out of the trace.  Empty list when absent."""
    if not trace:
        return []
    tried_raw = trace.get("probe_tried") or trace.get("tried") or []
    out: list[dict] = []
    for entry in tried_raw:
        if isinstance(entry, dict):
            out.append({
                "attempt": entry.get("encoding") or entry.get("attempt") or entry.get("label") or "",
                "result": str(entry.get("raw_result") or entry.get("result") or "")[:200],
            })
        else:
            out.append({"attempt": str(entry), "result": ""})
    return out


def _build_known_good(findings_for_fn: list[dict] | None) -> list[dict]:
    """Extract working_call templates from findings, newest first."""
    if not findings_for_fn:
        return []
    out: list[dict] = []
    for f in findings_for_fn:
        wc = f.get("working_call")
        if not isinstance(wc, dict) or not wc:
            continue
        out.append({
            "args": wc,
            "confidence": f.get("confidence") or "medium",
            "recorded_at": f.get("recorded_at"),
        })
    # Newest first (recorded_at is ISO-8601, lexicographically sortable).
    out.sort(key=lambda e: e.get("recorded_at") or "", reverse=True)
    # Deduplicate adjacent identical working_calls.
    deduped: list[dict] = []
    seen: set[str] = set()
    for e in out:
        key = str(sorted(e["args"].items()))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(e)
    return deduped[:3]


_SEVERITY_RECOVERABLE = {
    CATEGORY_SENTINEL,
    CATEGORY_BRIDGE_UNREACHABLE,
    CATEGORY_TIMEOUT,
    CATEGORY_SCHEMA_MISMATCH,
}


def _suggestion_for(category: str, classified_name: str | None,
                    known_good: list[dict]) -> str:
    """One-line actionable recommendation.  Keep terse — UI renders on one line."""
    if known_good:
        example = known_good[0]["args"]
        sample = ", ".join(f"{k}={v!r}" for k, v in list(example.items())[:3])
        return (
            f"This function has previously succeeded with: {sample}. "
            "Try matching that call shape."
        )
    if category == CATEGORY_SENTINEL:
        return (
            "The function returned a generic failure sentinel. Try alternate "
            "argument encodings (string vs integer, different buffer sizes) "
            "or call any setup/context function first."
        )
    if category == CATEGORY_HRESULT and classified_name:
        if "INVALIDARG" in classified_name:
            return "Arguments appear malformed. Check parameter types and order."
        if "ACCESSDENIED" in classified_name or "DENIED" in classified_name:
            return "Access denied. A prerequisite auth/unlock call may be required."
        if "POINTER" in classified_name:
            return "Null pointer passed. Supply a non-null value for pointer params."
        return f"HRESULT {classified_name}. Consult Windows documentation."
    if category == CATEGORY_WIN32 and classified_name:
        if "NOT_FOUND" in classified_name:
            return "Resource not found. Confirm the identifier exists."
        if "ACCESS_DENIED" in classified_name:
            return "Access denied. Check permissions or run an unlock call first."
        if "INVALID_PARAMETER" in classified_name:
            return "Parameter invalid. Re-check type, encoding, and range."
        return f"Win32 error {classified_name}."
    if category == CATEGORY_NTSTATUS and classified_name:
        return f"Kernel-level failure ({classified_name}); arguments likely corrupt."
    if category == CATEGORY_BRIDGE_UNREACHABLE:
        return "The Windows VM bridge is temporarily unreachable. Retry in a few seconds."
    if category == CATEGORY_TIMEOUT:
        return "The call exceeded its time budget. The target may be hung or blocking on a dialog."
    if category == CATEGORY_NO_EXECUTABLE:
        return "No executable path is configured for this tool. Regenerate the server."
    if category == CATEGORY_UNKNOWN_TOOL:
        return "The requested tool is not registered. Check the function name against the schema."
    if category == CATEGORY_SCHEMA_MISMATCH:
        return "Arguments do not match the declared schema. Inspect the tool's parameter list."
    if category == CATEGORY_EXCEPTION:
        return "An unexpected exception occurred inside the handler. Check the trace."
    return "Inspect the trace and retry with adjusted arguments."


def _build_human(function_name: str, category: str, classified_name: str | None,
                 raw_code_hex: str | None, exception: str | None,
                 suggestion: str) -> str:
    """Single-paragraph string safe to render directly to the user or LLM."""
    head: str
    if raw_code_hex and classified_name:
        head = f"{function_name} returned {raw_code_hex} ({classified_name})."
    elif raw_code_hex:
        head = f"{function_name} returned {raw_code_hex} — unclassified failure."
    elif exception:
        head = f"{function_name} raised {exception}."
    else:
        head = f"{function_name} failed ({category})."
    return f"{head} {suggestion}"


def build_error_payload(
    function_name: str,
    raw_result: Any,
    trace: dict | None,
    exception: str | None,
    findings_for_fn: list[dict] | None,
    extra_sentinels: dict | None = None,
) -> dict | None:
    """Construct a structured error payload, or None if the call succeeded.

    Success is inferred when there is no exception, no trace-level failure
    marker, and the numeric return code (if any) is neither a known sentinel
    nor a classified Windows error.
    """
    code = _coerce_code(raw_result)
    trace_category = _category_for_trace(trace, exception)

    classified_name: str | None = None
    category: str | None = None
    raw_code_hex: str | None = None

    if code is not None:
        merged = dict(SENTINEL_DEFAULTS)
        if extra_sentinels:
            for k, v in extra_sentinels.items():
                try:
                    key = int(k, 16) if isinstance(k, str) else int(k)
                    merged.setdefault(key, str(v))
                except (ValueError, TypeError):
                    continue
        code32 = code & 0xFFFFFFFF
        if code32 in merged or classify_common_result_code(code32) is not None:
            category, classified_name = _classify_code(code32)
            if classified_name is None and code32 in merged:
                classified_name = merged[code32]
            raw_code_hex = f"0x{code32:08X}"

    if category is None and trace_category is not None:
        category = trace_category

    if category is None:
        return None

    known_good = _build_known_good(findings_for_fn)
    what_tried = _build_what_tried(trace)
    suggestion = _suggestion_for(category, classified_name, known_good)
    severity = "recoverable" if category in _SEVERITY_RECOVERABLE else "blocking"
    human = _build_human(
        function_name, category, classified_name, raw_code_hex, exception, suggestion,
    )

    return {
        "category": category,
        "severity": severity,
        "classified_name": classified_name,
        "raw_code": raw_code_hex,
        "what_tried": what_tried,
        "known_good": known_good,
        "suggestion": suggestion,
        "human": human,
    }
