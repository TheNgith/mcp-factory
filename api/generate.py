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


def run_generate(body: dict[str, Any]) -> dict[str, Any]:
    """Build an MCP tool schema, persist artifacts, and index in AI Search.

    Returns the final response dict (caller wraps in JSONResponse).
    """
    job_id = body.get("job_id", str(uuid.uuid4())[:8])
    selected: list = body.get("selected", [])

    if not selected:
        raise HTTPException(400, "No invocables selected")

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
            props[pname] = {
                "type":        json_type,
                "description": p.get("description") or p.get("type", "string"),
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
