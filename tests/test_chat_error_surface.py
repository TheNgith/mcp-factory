"""Tests that the chat surface exposes structured error payloads.

Covers:
- The synthetic _EXPLAIN_FAILURE_TOOL has the shape the OpenAI tools API expects.
- api/executor.py:_execute_tool dispatches explain_failure against the caller's
  _tool_log and returns the recorded payload as JSON.
- _execute_tool_traced passes through a None error for synthetic/success calls.
"""

from __future__ import annotations

import json

import pytest

# api.chat / api.executor transitively import the Azure SDK; skip when the
# runtime (e.g. local dev without Azure libs) can't satisfy it.  CI containers
# and the main deployment both have these installed.
pytest.importorskip("azure.identity")
pytest.importorskip("azure.storage.blob")

from api.chat import _EXPLAIN_FAILURE_TOOL  # noqa: E402
from api.executor import _execute_tool, _execute_tool_traced  # noqa: E402


def test_explain_failure_tool_schema():
    fn = _EXPLAIN_FAILURE_TOOL["function"]
    assert fn["name"] == "explain_failure"
    assert "function_name" in fn["parameters"]["required"]
    assert fn["parameters"]["type"] == "object"
    assert "function_name" in fn["parameters"]["properties"]


def test_explain_failure_returns_recent_error_payload():
    tool_log = [
        {"name": "OtherFn", "error": None},
        {
            "name": "LookupCustomer",
            "error": {
                "category": "sentinel",
                "classified_name": "not found / invalid input",
                "raw_code": "0xFFFFFFFF",
                "what_tried": [],
                "known_good": [{"args": {"customer_id": "CUST-001"}, "confidence": "high"}],
                "suggestion": "Try CUST-001.",
                "human": "LookupCustomer returned 0xFFFFFFFF.",
                "severity": "recoverable",
            },
        },
    ]
    inv = {
        "name": "explain_failure",
        "execution": {"method": "explain_failure"},
        "_tool_log": tool_log,
    }
    result = _execute_tool(inv, {"function_name": "LookupCustomer"})
    parsed = json.loads(result)
    assert parsed["classified_name"] == "not found / invalid input"
    assert parsed["known_good"][0]["args"]["customer_id"] == "CUST-001"


def test_explain_failure_no_recent_error():
    inv = {
        "name": "explain_failure",
        "execution": {"method": "explain_failure"},
        "_tool_log": [],
    }
    result = _execute_tool(inv, {"function_name": "Missing"})
    assert "no recent error" in result.lower()


def test_traced_synthetic_returns_no_error():
    """Synthetic tools (explain_failure etc.) must set error=None."""
    inv = {
        "name": "explain_failure",
        "execution": {"method": "explain_failure"},
        "_tool_log": [],
    }
    traced = _execute_tool_traced(inv, {"function_name": "Missing"})
    assert traced["error"] is None
    assert traced["trace"] is None
    assert isinstance(traced["result_str"], str)
