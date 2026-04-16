"""Integration test for error enrichment in the generated MCP server.

We don't install flask or the mcp SDK at test time, so we can't boot the full
generated server.  Instead we:
  1. Generate artifacts for a fixture invocable via generate_mcp_sdk_artifacts
  2. Write them to a tempdir alongside the vendored error_enrichment/sentinel_codes
  3. Import the vendored error_enrichment module standalone and confirm it
     produces the same structured payload the generated server relies on
  4. Assert the generated server.py and mcp_server.py strings contain the
     integration hooks (_format_error, findings_summary baking, error in
     /invoke response, error in /chat tool_outputs)
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

from src.generation.section4_generate_server import (
    ERROR_ENRICHMENT_PY,
    SENTINEL_CODES_PY,
    generate_mcp_sdk_artifacts,
)


FIXTURE_INVOCABLE = {
    "name": "LookupCustomer",
    "description": "Look up a customer record by ID.",
    "parameters": [
        {"name": "customer_id", "type": "string", "description": "Customer ID"},
    ],
    "execution": {
        "method": "dll_import",
        "dll": "nonexistent.dll",
        "symbol": "LookupCustomer",
        "return_type": "int",
    },
    "findings_summary": {
        "working_calls": [
            {
                "args": {"customer_id": "CUST-001"},
                "confidence": "high",
                "recorded_at": "2026-03-01T12:00:00Z",
            },
        ],
        "last_status": "success",
    },
}


@pytest.fixture
def generated(tmp_path):
    """Write the generated artifacts to a tempdir and return the path."""
    artifacts = generate_mcp_sdk_artifacts("test_component", [FIXTURE_INVOCABLE])
    (tmp_path / "mcp_server.py").write_text(artifacts["mcp_server_py"])
    (tmp_path / "mcp.json").write_text(artifacts["mcp_json"])
    (tmp_path / "error_enrichment.py").write_text(artifacts["error_enrichment_py"])
    (tmp_path / "sentinel_codes.py").write_text(artifacts["sentinel_codes_py"])
    return tmp_path, artifacts


def test_artifacts_include_vendored_enrichment(generated):
    _path, artifacts = generated
    assert artifacts["error_enrichment_py"] == ERROR_ENRICHMENT_PY
    assert artifacts["sentinel_codes_py"] == SENTINEL_CODES_PY
    assert "build_error_payload" in artifacts["error_enrichment_py"]
    assert "SENTINEL_DEFAULTS" in artifacts["sentinel_codes_py"]


def test_vendored_enrichment_rewrites_import_prefix(generated):
    _path, artifacts = generated
    # The vendored copy must not import from `api.*` — it lives at the root
    # of the generated project.
    assert "from api.sentinel_codes" not in artifacts["error_enrichment_py"]
    assert "from sentinel_codes" in artifacts["error_enrichment_py"]


def test_vendored_modules_run_standalone(generated, monkeypatch):
    """Import the generated vendored modules and exercise them end-to-end."""
    path, _ = generated
    # Ensure neither module is already loaded from a prior test.
    for mod_name in ("error_enrichment", "sentinel_codes"):
        sys.modules.pop(mod_name, None)
    monkeypatch.syspath_prepend(str(path))
    try:
        mod = importlib.import_module("error_enrichment")
        # Simulate the DLL exception path — the generated /invoke route feeds
        # exactly this shape to _format_error when ctypes raises.
        payload = mod.build_error_payload(
            "LookupCustomer",
            None,
            {"backend": "dll", "exception": "OSError: [WinError 126]"},
            "OSError: [WinError 126]",
            [{
                "status": "success",
                "working_call": {"customer_id": "CUST-001"},
                "confidence": "high",
                "recorded_at": "2026-03-01T12:00:00Z",
            }],
        )
        assert payload is not None
        assert payload["category"] == "exception"
        assert payload["known_good"][0]["args"] == {"customer_id": "CUST-001"}
        assert "OSError" in payload["human"]

        # Simulate the 0xFFFFFFFF sentinel path.
        payload2 = mod.build_error_payload(
            "LookupCustomer", 0xFFFFFFFF, None, None, None,
        )
        assert payload2 is not None
        assert payload2["category"] == "sentinel"
        assert payload2["raw_code"] == "0xFFFFFFFF"
    finally:
        sys.modules.pop("error_enrichment", None)
        sys.modules.pop("sentinel_codes", None)


def test_server_template_wires_format_error(generated):
    _path, artifacts = generated
    server_py = artifacts["mcp_server_py"]
    assert "_format_error" in server_py
    assert "build_error_payload" in server_py
    assert "findings_summary" in server_py


def test_findings_summary_baked_into_invocables(generated):
    _path, artifacts = generated
    # The generator injects the raw invocable JSON into the template verbatim.
    server_py = artifacts["mcp_server_py"]
    assert "LookupCustomer" in server_py
    assert "CUST-001" in server_py  # working_call template must travel


def test_mcp_json_schema_shape(generated):
    _path, artifacts = generated
    mcp_json = json.loads(artifacts["mcp_json"])
    # mcp.json describes the MCP server launch — structure is implementation-
    # defined but it must at least reference the generated server file.
    text = artifacts["mcp_json"]
    assert "mcp_server.py" in text or "test_component" in text
    assert isinstance(mcp_json, dict)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
