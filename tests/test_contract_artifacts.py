import json


def _blob_bytes(obj):
    if isinstance(obj, bytes):
        return obj
    if isinstance(obj, str):
        return obj.encode("utf-8")
    return json.dumps(obj).encode("utf-8")


def _build_base_blobs() -> dict[str, bytes]:
    return {
        "job1/vocab.json": _blob_bytes(
            {
                "id_formats": ["CUST-001", "ORD-20260315-0117"],
                "value_semantics": {"amount_cents": "payment amount in cents"},
                "error_codes": {"0xfffffffb": "write denied"},
            }
        ),
        "job1/static_analysis.json": _blob_bytes(
            {
                "static_hints_block_length": 128,
                "binary_strings": {"ids_found": ["CUST-001", "ORD-20260315-0117"]},
            }
        ),
        "job1/probe_user_message_sample.txt": _blob_bytes("Probe user message with static hints."),
        "job1/sentinel_calibration.json": _blob_bytes({"0xFFFFFFFB": "write denied"}),
        "job1/explore_probe_log.json": _blob_bytes(
            [
                {"phase": "explore", "function": "CS_ProcessPayment", "tool": "CS_ProcessPayment", "args": {"customer_id": "CUST-001"}},
                {"phase": "explore", "function": "CS_ProcessPayment", "tool": "enrich_invocable", "args": {}},
                {"phase": "deterministic_fallback", "function": "CS_ProcessPayment", "tool": "CS_ProcessPayment", "args": {"customer_id": "CUST-001"}},
            ]
        ),
        "job1/invocables_map.json": _blob_bytes(
            {
                "CS_ProcessPayment": {
                    "name": "CS_ProcessPayment",
                    "description": "Processes payment for a customer",
                    "parameters": [{"name": "customer_id"}, {"name": "amount_cents"}],
                },
                "CS_GetVersion": {
                    "name": "CS_GetVersion",
                    "description": "Returns packed version",
                    "parameters": [],
                },
            }
        ),
        "job1/api_reference.md": _blob_bytes(
            "CS_ProcessPayment supports CUST-001 and amount_cents.\n"
            "CS_GetVersion returns packed version."
        ),
        "job1/model_context_phase_01_probe_loop.txt": _blob_bytes("Contains CUST-001"),
        "job1/model_context_phase_06_synthesis.txt": _blob_bytes("Synthesis input context"),
        "job1/backfill_result.json": _blob_bytes({"backfill_ran": True, "patches_applied": 2}),
        "job1/mcp_schema_t0.json": _blob_bytes({"version": 1, "a": 1}),
        "job1/mcp_schema.json": _blob_bytes({"version": 1, "a": 2}),
        "job1/mcp_schema_post_discovery.json": _blob_bytes({"version": 1, "a": 2}),
        "job1/findings.json": _blob_bytes(
            [
                {"function": "CS_ProcessPayment", "status": "success"},
                {"function": "CS_GetVersion", "status": "success"},
            ]
        ),
        "job1/harmonization_report.json": _blob_bytes({"ok": True}),
        "job1/sentinel_catalog.json": _blob_bytes({"codes": {}}),
        "job1/behavioral_spec.py": _blob_bytes("# behavioral spec"),
        "job1/status.json": _blob_bytes({"explore_phase": "done"}),
        "job1/explore_config.json": _blob_bytes({"cap_profile": "default"}),
    }


def test_emit_contract_artifacts_writes_required_files(monkeypatch):
    from api import cohesion

    blobs = _build_base_blobs()
    uploaded: dict[str, bytes] = {}

    def fake_download(_container, blob_name):
        if blob_name in uploaded:
            return uploaded[blob_name]
        if blob_name in blobs:
            return blobs[blob_name]
        raise FileNotFoundError(blob_name)

    def fake_upload(_container, blob_name, data):
        uploaded[blob_name] = data
        return blob_name

    monkeypatch.setattr(cohesion, "_download_blob", fake_download)
    monkeypatch.setattr(cohesion, "_upload_to_blob", fake_upload)
    monkeypatch.setattr(
        cohesion,
        "_get_job_status",
        lambda _job_id: {
            "component_name": "contoso_cs",
            "explore_phase": "done",
            "explore_started_at": 100,
            "updated_at": 200,
            "hints": "Use CUST-001 and code 0xFFFFFFFB",
        },
    )
    monkeypatch.setattr(
        cohesion,
        "_load_findings",
        lambda _job_id: [
            {"function": "CS_ProcessPayment", "status": "success"},
            {"function": "CS_GetVersion", "status": "success"},
        ],
    )
    monkeypatch.setattr(cohesion.subprocess, "check_output", lambda *a, **k: b"abc123\n")

    result = cohesion.emit_contract_artifacts("job1")

    assert "job1/session-meta.json" in uploaded
    assert "job1/stage-index.json" in uploaded
    assert "job1/transition-index.json" in uploaded
    assert "job1/cohesion-report.json" in uploaded

    transition_index = json.loads(uploaded["job1/transition-index.json"].decode("utf-8"))
    stage_index = json.loads(uploaded["job1/stage-index.json"].decode("utf-8"))
    assert transition_index["version"] == "1.0"
    assert len(transition_index["transitions"]) == 18
    assert all("severity" in t and "status" in t for t in transition_index["transitions"])

    t17 = next(t for t in transition_index["transitions"] if t["id"] == "T-17")
    t18 = next(t for t in transition_index["transitions"] if t["id"] == "T-18")
    assert t17["name"] == "sentinel_calibration_outcome"
    assert t18["name"] == "write_unlock_probe_outcome"
    assert t17["status"] in {"pass", "warn", "fail"}
    assert t18["status"] in {"pass", "warn", "fail", "partial", "not_applicable"}
    assert stage_index["version"] == "1.0"
    for stage in stage_index["stages"]:
        for artifact in stage.get("artifacts") or []:
            assert str(artifact).startswith("evidence/")
            assert "stage-" in str(artifact)

    t12 = next(t for t in transition_index["transitions"] if t["id"] == "T-12")
    t13 = next(t for t in transition_index["transitions"] if t["id"] == "T-13")
    assert t12["status"] == "not_applicable"
    assert t13["status"] == "not_applicable"

    report = result["cohesion_report"]
    assert isinstance(report["gates"]["hard_fail"], bool)
    assert report["totals"]["transition_pass"] >= 1


def test_hard_fail_when_high_severity_transition_fails(monkeypatch):
    from api import cohesion

    blobs = _build_base_blobs()
    # Force T-16 fail by making schema unchanged and dropping schema_evolution evidence.
    blobs["job1/mcp_schema.json"] = blobs["job1/mcp_schema_t0.json"]
    uploaded: dict[str, bytes] = {}

    def fake_download(_container, blob_name):
        if blob_name in uploaded:
            return uploaded[blob_name]
        if blob_name in blobs:
            return blobs[blob_name]
        raise FileNotFoundError(blob_name)

    monkeypatch.setattr(cohesion, "_download_blob", fake_download)
    monkeypatch.setattr(cohesion, "_upload_to_blob", lambda _c, n, d: uploaded.setdefault(n, d) or n)
    monkeypatch.setattr(
        cohesion,
        "_get_job_status",
        lambda _job_id: {
            "component_name": "contoso_cs",
            "explore_phase": "done",
            "explore_started_at": 100,
            "updated_at": 200,
            "hints": "Use CUST-001 and code 0xFFFFFFFB",
        },
    )
    monkeypatch.setattr(cohesion, "_load_findings", lambda _job_id: [{"function": "CS_ProcessPayment", "status": "success"}])
    monkeypatch.setattr(cohesion.subprocess, "check_output", lambda *a, **k: b"abc123\n")

    result = cohesion.emit_contract_artifacts("job1")
    report = result["cohesion_report"]

    assert report["gates"]["hard_fail"] is True
    assert "T-16" in report["failed_transitions"]
