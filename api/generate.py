"""api/generate.py – MCP tool-schema generation, P1 SDK artifact emit, P5 AI Search indexing.

run_generate(body) implements everything inside the /api/generate handler
beyond routing: tool-schema construction, blob persistence, MCP SDK artifact
generation, and Azure AI Search embedding.

Returns a plain dict suitable for wrapping in JSONResponse by the caller.
Raises fastapi.HTTPException on validation errors.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any

from fastapi import HTTPException

from api.config import OPENAI_ENDPOINT, ARTIFACT_CONTAINER
from api.storage import _upload_to_blob, _register_invocables
from api.telemetry import _openai_client

logger = logging.getLogger("mcp_factory.api")

# Positional param ordinals that map to common DLL calling conventions.
# Used by _apply_findings_param_names to derive semantic names from working
# call keys.  The mapping is: if a working_call has key "customer_id" at
# position 0 and the invocable has "param_1" at position 0, rename param_1
# → customer_id.
_GENERIC_PARAM_RE = re.compile(r'^param_\d+$')


def _apply_findings_param_names(
    invocables: list[dict],
    findings_by_fn: dict[str, list],
) -> None:
    """RC-3: Deterministic param naming from findings working_call keys.

    When a finding has a working_call like {"param_1": "CUST-001", "param_2": 0},
    the keys are generic.  But when it has {"customer_id": "CUST-001"}, those
    keys came from a previous enrichment that was later clobbered.

    This function also inspects the working_call VALUES to infer names:
    - String values matching ID patterns → "<entity>_id"
    - Integer values → keep param_N unless description says otherwise

    Runs once in run_generate, so every schema rebuild benefits.
    """
    for inv in invocables:
        params = inv.get("parameters") or []
        if not params or not isinstance(params, list):
            continue
        fn_name = inv.get("name", "")
        fn_findings = findings_by_fn.get(fn_name, [])
        if not fn_findings:
            continue

        # Find the best working_call (prefer success findings with most keys)
        best_wc: dict | None = None
        for f in reversed(fn_findings):
            wc = f.get("working_call")
            if wc and isinstance(wc, dict) and f.get("status") == "success":
                if best_wc is None or len(wc) > len(best_wc):
                    best_wc = wc
        if not best_wc:
            continue

        # Strategy 1: If working_call already has semantic keys (from a
        # previous enrichment), apply them to matching positional params.
        # Strategy 2: If working_call keys are generic (param_N), infer
        # names from the VALUES (ID patterns, numeric ranges, etc.)
        wc_keys = list(best_wc.keys())
        for i, p in enumerate(params):
            if not isinstance(p, dict):
                continue
            pname = p.get("name", "")
            if not _GENERIC_PARAM_RE.match(pname):
                continue  # already has a semantic name

            # Check if working_call has a non-generic key at this position
            if i < len(wc_keys):
                wc_key = wc_keys[i]
                if not _GENERIC_PARAM_RE.match(wc_key):
                    p["name"] = wc_key
                    continue

            # Infer from value: string matching ID patterns → entity_id
            wc_val = best_wc.get(pname)
            if wc_val is None and i < len(wc_keys):
                wc_val = best_wc.get(wc_keys[i])
            if isinstance(wc_val, str) and re.match(r'[A-Z]{2,6}-[\w-]+', wc_val):
                # Extract entity prefix: "CUST-001" → "customer_id",
                # "ORD-123" → "order_id"
                _PREFIX_MAP = {
                    "CUST": "customer_id",
                    "ORD": "order_id",
                    "ACCT": "account_id",
                    "PAY": "payment_id",
                    "INV": "invoice_id",
                    "TXN": "transaction_id",
                }
                prefix = wc_val.split("-")[0]
                inferred = _PREFIX_MAP.get(prefix, f"{prefix.lower()}_id")
                # Avoid duplicate names within the same function
                existing_names = {pp.get("name") for pp in params if isinstance(pp, dict)}
                if inferred not in existing_names:
                    p["name"] = inferred


def run_generate(body: dict[str, Any]) -> dict[str, Any]:
    """Build an MCP tool schema, persist artifacts, and index in AI Search.

    Returns the final response dict (caller wraps in JSONResponse).
    """
    job_id = body.get("job_id", str(uuid.uuid4())[:8])
    selected: list = body.get("selected", [])

    if not selected:
        raise HTTPException(400, "No invocables selected")

    # Load findings so we can produce human-readable param descriptions
    # (same logic as the report generator — ensures schema descriptions match report)
    try:
        from api.storage import _load_findings
        from api.main import _infer_param_desc
        _findings_list = _load_findings(job_id)
        _findings_by_fn: dict[str, list] = {}
        for _f in _findings_list:
            _findings_by_fn.setdefault(_f.get("function", ""), []).append(_f)
    except Exception:
        _findings_by_fn = {}
        _infer_param_desc = None  # type: ignore[assignment]

    # Build OpenAI function-calling tool schema from selected invocables
    tools = []
    for inv in selected:
        props: dict = {}
        required: list = []
        # parameters may be None (bridge raw field) or a string (legacy) —
        # normalise to a list so iteration is always safe.
        raw_params = inv.get("parameters") or []
        if isinstance(raw_params, str):
            raw_params = []
        for p in raw_params:
            # Normalize: plain strings (legacy TLB output) become minimal dicts.
            if isinstance(p, str):
                p = {"name": p, "type": "string", "description": f"Parameter {p}"}
            if not isinstance(p, dict):
                continue
            pname = p.get("name", "arg")
            # Prefer the pre-computed json_type (set by ghidra_analyzer) so that
            # numeric params get "integer"/"number" rather than always "string".
            json_type = p.get("json_type") or "string"
            pdesc = p.get("description") or p.get("type", "string")
            # Replace useless Ghidra boilerplate with human-readable descriptions
            # using findings context — keeps schema descriptions in sync with report
            if _infer_param_desc and (
                not pdesc or pdesc.startswith("Parameter recovered by Ghidra")
            ):
                fn_findings = _findings_by_fn.get(inv.get("name", ""), [])
                ptype = p.get("type", "")
                pdesc = _infer_param_desc(pname, ptype, fn_findings)
                # RC-1: Write enriched description back to the invocable so
                # it persists across stages.  Without this, any later
                # run_generate call from a different stage can lose the
                # enriched description.
                p["description"] = pdesc
            props[pname] = {
                "type":        json_type,
                "description": pdesc,
            }
            # Only "in" direction params go in required; "out" buffers are
            # allocated by the executor, not passed by the caller.
            if p.get("direction", "in") != "out":
                required.append(pname)

        # Discovery pipeline uses `description`; older/generated schemas use
        # `doc` or `signature`.  Fall through all three, then the name.
        desc = (
            inv.get("doc")
            or inv.get("description")
            or inv.get("signature")
            or inv["name"]
        )

        # OpenAI requires function names matching ^[a-zA-Z0-9_\.-]+$ and ≤ 64 chars.
        # Replace any disallowed character then truncate.
        safe_name = re.sub(r"[^a-zA-Z0-9_.\-]", "_", inv["name"])[:64]

        tools.append({
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

    mcp_schema = {
        "job_id": job_id,
        "mcp_version": "1.0",
        "component": body.get("component_name", "mcp-component"),
        "tools": tools,
    }

    # RC-1 / RC-3: Apply findings-based param naming to invocables before
    # registration.  This uses working_call keys from findings to
    # deterministically derive semantic param names — not dependent on
    # LLM variability like D-5's keyword regex.
    _apply_findings_param_names(selected, _findings_by_fn)

    # Register invocables for later execution via /api/chat or /api/execute
    _register_invocables(job_id, selected)

    # Save schema to Blob
    schema_blob = f"{job_id}/mcp_schema.json"
    try:
        _upload_to_blob(ARTIFACT_CONTAINER, schema_blob, json.dumps(mcp_schema, indent=2).encode())
    except Exception as blob_exc:
        logger.warning("[%s] Schema blob upload failed (non-fatal): %s", job_id, blob_exc)

    # ── P1: Generate true MCP SDK server artifacts ─────────────────────────
    mcp_artifacts: dict = {}
    try:
        from section4_generate_server import generate_mcp_sdk_artifacts  # type: ignore
        component_name = mcp_schema["component"]
        mcp_artifacts = generate_mcp_sdk_artifacts(component_name, selected)
        for fname, content in [
            ("mcp_server.py",       mcp_artifacts.get("mcp_server_py", "")),
            ("mcp.json",            mcp_artifacts.get("mcp_json", "")),
            ("mcp_requirements.txt", mcp_artifacts.get("mcp_requirements_txt", "")),
        ]:
            if content:
                _upload_to_blob(
                    ARTIFACT_CONTAINER,
                    f"{job_id}/{fname}",
                    content.encode(),
                )
        logger.info("[%s] MCP SDK artifacts uploaded (mcp_server.py, mcp.json)", job_id)
    except Exception as mcp_exc:
        logger.warning("[%s] MCP SDK artifact generation failed: %s", job_id, mcp_exc)

    # ── P5: Embed & index invocables in Azure AI Search ────────────────────
    if OPENAI_ENDPOINT:
        try:
            from search import embed_and_index  # type: ignore
            _oai = _openai_client()
            embed_and_index(job_id, selected, _oai, functions=tools)
            logger.info("[%s] AI Search indexing triggered for %d tools", job_id, len(tools))
        except Exception as search_exc:
            logger.warning("[%s] AI Search indexing failed (non-fatal): %s", job_id, search_exc)

    logger.info(
        "[%s] Generated MCP schema with %d tools",
        job_id, len(tools),
        extra={"custom_dimensions": {
            "event": "generate_complete",
            "job_id": job_id,
            "tool_count": len(tools),
            "component": mcp_schema.get("component", ""),
        }},
    )
    return {
        "job_id": job_id,
        "schema_blob": schema_blob,
        "mcp_schema": mcp_schema,
        "mcp_server_blob": f"{job_id}/mcp_server.py" if mcp_artifacts else None,
        "mcp_json_blob": f"{job_id}/mcp.json" if mcp_artifacts else None,
    }
