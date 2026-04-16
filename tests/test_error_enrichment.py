"""Unit tests for api/error_enrichment.py::build_error_payload.

Covers each category and the success path.  Keeps inputs deterministic —
no I/O, no LLM, no fixture files.
"""

from __future__ import annotations

import pytest

from api.error_enrichment import (
    CATEGORY_BRIDGE_UNREACHABLE,
    CATEGORY_EXCEPTION,
    CATEGORY_HRESULT,
    CATEGORY_NO_EXECUTABLE,
    CATEGORY_NTSTATUS,
    CATEGORY_SENTINEL,
    CATEGORY_TIMEOUT,
    CATEGORY_WIN32,
    build_error_payload,
)


def test_success_returns_none():
    assert build_error_payload("Ok", 0, None, None, None) is None
    assert build_error_payload("Ok", "Returned: 0, no error", None, None, None) is None


def test_sentinel_not_found():
    p = build_error_payload(
        "LookupCustomer",
        0xFFFFFFFF,
        {"probe_tried": []},
        None,
        None,
    )
    assert p is not None
    assert p["category"] == CATEGORY_SENTINEL
    assert p["classified_name"] == "not found / invalid input"
    assert p["raw_code"] == "0xFFFFFFFF"
    assert p["severity"] == "recoverable"
    assert "LookupCustomer" in p["human"]


def test_sentinel_from_returned_string():
    """Executor gives 'Returned: N, sentinel: ...' strings."""
    p = build_error_payload(
        "SomeFunc",
        "Returned: 4294967295, sentinel: not found/invalid",
        None,
        None,
        None,
    )
    assert p is not None
    assert p["category"] == CATEGORY_SENTINEL
    assert p["raw_code"] == "0xFFFFFFFF"


def test_hresult_invalidarg():
    p = build_error_payload(
        "CreateWidget",
        0x80070057,
        None,
        None,
        None,
    )
    assert p is not None
    assert p["category"] == CATEGORY_HRESULT
    assert p["classified_name"] and "INVALIDARG" in p["classified_name"]
    assert "malformed" in p["suggestion"].lower() or "invalid" in p["suggestion"].lower()


def test_win32_file_not_found():
    p = build_error_payload(
        "OpenThing",
        2,
        None,
        None,
        None,
    )
    assert p is not None
    assert p["category"] == CATEGORY_WIN32
    assert p["classified_name"] == "ERROR_FILE_NOT_FOUND"


def test_ntstatus_access_violation():
    p = build_error_payload(
        "UnsafeCall",
        0xC0000005,
        None,
        None,
        None,
    )
    assert p is not None
    assert p["category"] == CATEGORY_NTSTATUS
    assert p["classified_name"] == "STATUS_ACCESS_VIOLATION"
    assert p["severity"] == "blocking"


def test_bridge_unreachable():
    p = build_error_payload(
        "RemoteCall",
        None,
        {"backend": "bridge", "exception": "ConnectionRefused"},
        None,
        None,
    )
    assert p is not None
    assert p["category"] == CATEGORY_BRIDGE_UNREACHABLE
    assert p["severity"] == "recoverable"


def test_timeout_from_flag():
    p = build_error_payload(
        "SlowCall",
        None,
        {"backend": "dll", "timeout_hit": True},
        None,
        None,
    )
    assert p is not None
    assert p["category"] == CATEGORY_TIMEOUT


def test_no_executable():
    p = build_error_payload(
        "CliThing",
        None,
        {"backend": "cli", "exception": "no executable path"},
        None,
        None,
    )
    assert p is not None
    assert p["category"] == CATEGORY_NO_EXECUTABLE


def test_plain_exception_path():
    p = build_error_payload(
        "CrashFn",
        None,
        None,
        "OSError: [WinError 126]",
        None,
    )
    assert p is not None
    assert p["category"] == CATEGORY_EXCEPTION
    assert "OSError" in p["human"]


def test_known_good_from_findings():
    findings = [
        {
            "status": "success",
            "working_call": {"customer_id": "CUST-001"},
            "confidence": "high",
            "recorded_at": "2026-01-01T00:00:00Z",
        },
        {
            "status": "success",
            "working_call": {"customer_id": "CUST-042"},
            "confidence": "medium",
            "recorded_at": "2026-02-15T12:00:00Z",
        },
    ]
    p = build_error_payload(
        "LookupCustomer",
        0xFFFFFFFF,
        None,
        None,
        findings,
    )
    assert p is not None
    assert len(p["known_good"]) == 2
    # Newest first.
    assert p["known_good"][0]["args"]["customer_id"] == "CUST-042"
    assert "CUST-042" in p["suggestion"]


def test_what_tried_from_probe_matrix():
    trace = {
        "probe_tried": [
            {"encoding": "utf16_ptr", "raw_result": "0xFFFFFFFF"},
            {"encoding": "utf8_ptr",  "raw_result": "0xFFFFFFFF"},
        ]
    }
    p = build_error_payload("Fn", 0xFFFFFFFF, trace, None, None)
    assert p is not None
    assert len(p["what_tried"]) == 2
    assert p["what_tried"][0]["attempt"] == "utf16_ptr"


def test_extra_sentinels_override():
    # DLL-specific sentinel not in SENTINEL_DEFAULTS.
    p = build_error_payload(
        "Fn",
        0x12345,
        None,
        None,
        None,
        extra_sentinels={"0x12345": "custom_payment_rejected"},
    )
    assert p is not None
    assert p["classified_name"] == "custom_payment_rejected"


def test_unknown_non_error_code_returns_none():
    """Small non-zero return that isn't any known code should NOT be flagged."""
    p = build_error_payload("Fn", 42, None, None, None)
    assert p is None


def test_known_good_dedup():
    findings = [
        {"status": "success", "working_call": {"id": "A"}, "recorded_at": "2026-01-01"},
        {"status": "success", "working_call": {"id": "A"}, "recorded_at": "2026-01-02"},
        {"status": "success", "working_call": {"id": "B"}, "recorded_at": "2026-01-03"},
    ]
    p = build_error_payload("Fn", 0xFFFFFFFF, None, None, findings)
    assert p is not None
    ids = [kg["args"]["id"] for kg in p["known_good"]]
    assert ids.count("A") == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
