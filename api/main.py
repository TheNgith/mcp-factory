"""
api/main.py – MCP Factory REST API
Exposes the discovery pipeline and MCP generation over HTTP.
Integrates with Azure Blob Storage, Azure OpenAI, and Application Insights.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

# ── Azure SDK imports ──────────────────────────────────────────────────────
from azure.identity import ManagedIdentityCredential, DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, generate_blob_sas, BlobSasPermissions
from openai import AzureOpenAI

# ── App Insights telemetry ─────────────────────────────────────────────────
APPINSIGHTS_CONN = os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING", "")
if APPINSIGHTS_CONN:
    try:
        from opencensus.ext.azure.log_exporter import AzureLogHandler
        _ai_handler = AzureLogHandler(connection_string=APPINSIGHTS_CONN)
        logging.getLogger().addHandler(_ai_handler)
    except Exception:
        pass  # telemetry is best-effort

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("mcp_factory.api")

# ── Config from environment ────────────────────────────────────────────────
STORAGE_ACCOUNT   = os.getenv("AZURE_STORAGE_ACCOUNT", "mcpfactorystore")
OPENAI_ENDPOINT   = os.getenv("AZURE_OPENAI_ENDPOINT", "")
OPENAI_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")
MANAGED_CLIENT_ID = os.getenv("AZURE_CLIENT_ID", "")   # Managed Identity clientId

# ── Azure credential (Managed Identity in ACA, DefaultAzureCredential locally) ──
def _get_credential():
    if MANAGED_CLIENT_ID:
        return ManagedIdentityCredential(client_id=MANAGED_CLIENT_ID)
    return DefaultAzureCredential()

def _blob_client() -> BlobServiceClient:
    credential = _get_credential()
    return BlobServiceClient(
        account_url=f"https://{STORAGE_ACCOUNT}.blob.core.windows.net",
        credential=credential,
    )

def _openai_client() -> AzureOpenAI:
    credential = _get_credential()
    # Get token for Azure OpenAI
    token = credential.get_token("https://cognitiveservices.azure.com/.default")
    return AzureOpenAI(
        azure_endpoint=OPENAI_ENDPOINT,
        api_version="2024-10-21",
        azure_ad_token=token.token,
    )

# ── FastAPI app ────────────────────────────────────────────────────────────
app = FastAPI(title="MCP Factory API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOAD_CONTAINER   = "uploads"
ARTIFACT_CONTAINER = "artifacts"
SRC_DISCOVERY_DIR  = Path(__file__).parent.parent / "src" / "discovery"


# ── helpers ────────────────────────────────────────────────────────────────

def _upload_to_blob(container: str, blob_name: str, data: bytes) -> str:
    client = _blob_client()
    cc = client.get_container_client(container)
    cc.upload_blob(blob_name, data, overwrite=True)
    logger.info(f"Uploaded blob {container}/{blob_name}")
    return blob_name


def _download_blob(container: str, blob_name: str) -> bytes:
    client = _blob_client()
    cc = client.get_container_client(container)
    return cc.download_blob(blob_name).readall()


def _run_discovery(binary_path: Path, job_id: str, hints: str = "") -> dict:
    """Run the discovery pipeline on a local file path. Returns invocables dict."""
    out_dir = Path(tempfile.mkdtemp(prefix=f"mcp_{job_id}_"))
    cmd = [
        sys.executable,
        str(SRC_DISCOVERY_DIR / "main.py"),
        str(binary_path),
        "--output-dir", str(out_dir),
        "--format", "json",
        "--no-color",
    ]
    if hints:
        cmd += ["--description", hints]

    logger.info(f"[{job_id}] Running discovery: {' '.join(cmd)}")
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=120,
        env={**os.environ, "PYTHONPATH": str(SRC_DISCOVERY_DIR)},
    )
    logger.info(f"[{job_id}] Discovery stdout: {result.stdout[-500:]}")
    if result.returncode != 0:
        logger.warning(f"[{job_id}] Discovery stderr: {result.stderr[-500:]}")

    # Find the generated *_mcp.json file
    mcp_files = list(out_dir.glob("*_mcp.json"))
    if not mcp_files:
        # Try artifacts dir as fallback
        mcp_files = list(out_dir.glob("*.json"))

    if not mcp_files:
        raise RuntimeError(f"Discovery produced no output. stderr: {result.stderr[-300:]}")

    with open(mcp_files[0]) as f:
        data = json.load(f)

    # Upload artifact to Blob
    artifact_blob = f"{job_id}/{mcp_files[0].name}"
    _upload_to_blob(ARTIFACT_CONTAINER, artifact_blob, mcp_files[0].read_bytes())

    return {"job_id": job_id, "artifact_blob": artifact_blob, "invocables": data}


# ── Endpoints ──────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "version": "1.0.0"}


@app.post("/api/analyze")
async def analyze(
    file: UploadFile = File(...),
    hints: str = Form(default=""),
):
    """
    Section 2-3: Accept a binary upload, run discovery, return invocables list.
    """
    job_id = str(uuid.uuid4())[:8]
    suffix = Path(file.filename).suffix or ".bin"
    blob_name = f"{job_id}/input{suffix}"

    content = await file.read()
    if len(content) == 0:
        raise HTTPException(400, "Empty file uploaded")

    logger.info(f"[{job_id}] Received {file.filename} ({len(content)} bytes)")

    # Save to Blob Storage
    _upload_to_blob(UPLOAD_CONTAINER, blob_name, content)

    # Write to temp file for the pipeline
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)

    try:
        result = _run_discovery(tmp_path, job_id, hints)
    except Exception as e:
        logger.error(f"[{job_id}] Discovery failed: {e}")
        raise HTTPException(500, f"Analysis failed: {e}")
    finally:
        tmp_path.unlink(missing_ok=True)

    return JSONResponse(result)


@app.post("/api/generate")
async def generate(body: dict[str, Any]):
    """
    Section 4: Accept selected invocables, generate MCP server JSON definition.
    Returns the MCP tool schema ready for an LLM to consume.
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
        for p in inv.get("parameters", []):
            pname = p.get("name", "arg")
            props[pname] = {
                "type": "string",
                "description": p.get("type", "string"),
            }
            required.append(pname)

        tools.append({
            "type": "function",
            "function": {
                "name": inv["name"],
                "description": inv.get("doc", inv.get("signature", inv["name"])),
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

    # Save schema to Blob
    schema_blob = f"{job_id}/mcp_schema.json"
    _upload_to_blob(ARTIFACT_CONTAINER, schema_blob, json.dumps(mcp_schema, indent=2).encode())

    return JSONResponse({"job_id": job_id, "schema_blob": schema_blob, "mcp_schema": mcp_schema})


@app.post("/api/chat")
async def chat(body: dict[str, Any]):
    """
    Section 5: Chat interface. Sends messages to Azure OpenAI with the MCP
    tool definitions attached. Returns the assistant response.
    """
    messages: list = body.get("messages", [])
    tools: list = body.get("tools", [])

    if not messages:
        raise HTTPException(400, "No messages provided")
    if not OPENAI_ENDPOINT:
        raise HTTPException(503, "Azure OpenAI endpoint not configured")

    try:
        client = _openai_client()
        kwargs: dict = {
            "model": OPENAI_DEPLOYMENT,
            "messages": messages,
            "temperature": 0.2,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        response = client.chat.completions.create(**kwargs)
        msg = response.choices[0].message

        return JSONResponse({
            "role": msg.role,
            "content": msg.content,
            "tool_calls": [
                {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                }
                for tc in (msg.tool_calls or [])
            ],
        })
    except Exception as e:
        logger.error(f"Chat error: {e}")
        raise HTTPException(500, f"Chat failed: {e}")


@app.get("/api/download/{job_id}/{filename}")
def download(job_id: str, filename: str):
    """Section 5: Download a generated artifact from Blob Storage."""
    blob_name = f"{job_id}/{filename}"
    try:
        data = _download_blob(ARTIFACT_CONTAINER, blob_name)
    except Exception as e:
        raise HTTPException(404, f"Artifact not found: {e}")

    return StreamingResponse(
        iter([data]),
        media_type="application/json",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
