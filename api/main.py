"""
api/main.py – MCP Factory REST API
Exposes the discovery pipeline and MCP generation over HTTP.
Integrates with Azure Blob Storage, Azure OpenAI, and Application Insights.
"""

from __future__ import annotations

import ctypes
import json
import logging
import os
import platform
import subprocess
import sys
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Any

IS_WINDOWS = platform.system() == "Windows"

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

# ── Per-job invocable registries ───────────────────────────────────────────
# Populated by /api/execute and looked up by tool name.
# Structure: {job_id: {tool_name: invocable_dict}}
_JOB_INVOCABLE_MAPS: dict[str, dict[str, Any]] = {}
_JOB_MAP_LOCK = threading.Lock()


def _register_invocables(job_id: str, invocables: list[dict]) -> None:
    with _JOB_MAP_LOCK:
        _JOB_INVOCABLE_MAPS[job_id] = {inv["name"]: inv for inv in invocables}


def _get_invocable(job_id: str, name: str) -> dict | None:
    with _JOB_MAP_LOCK:
        return _JOB_INVOCABLE_MAPS.get(job_id, {}).get(name)


# ── ctypes type maps (Windows-only) ────────────────────────────────────────
_CTYPES_RESTYPE: dict = {}
_CTYPES_ARGTYPE: dict = {}

if IS_WINDOWS:
    _CTYPES_RESTYPE = {
        "void":           None,
        "bool":           ctypes.c_bool,
        "int":            ctypes.c_int,
        "unsigned":       ctypes.c_uint,
        "unsigned int":   ctypes.c_uint,
        "long":           ctypes.c_long,
        "unsigned long":  ctypes.c_ulong,
        "size_t":         ctypes.c_size_t,
        "float":          ctypes.c_float,
        "double":         ctypes.c_double,
        "char*":          ctypes.c_char_p,
        "const char*":    ctypes.c_char_p,
        "char *":         ctypes.c_char_p,
        "const char *":   ctypes.c_char_p,
    }
    _CTYPES_ARGTYPE = {
        "int":            ctypes.c_int,
        "unsigned":       ctypes.c_uint,
        "unsigned int":   ctypes.c_uint,
        "long":           ctypes.c_long,
        "unsigned long":  ctypes.c_ulong,
        "size_t":         ctypes.c_size_t,
        "float":          ctypes.c_float,
        "double":         ctypes.c_double,
        "bool":           ctypes.c_bool,
        "string":         ctypes.c_char_p,
        "str":            ctypes.c_char_p,
        "char*":          ctypes.c_char_p,
        "const char*":    ctypes.c_char_p,
        "char *":         ctypes.c_char_p,
        "const char *":   ctypes.c_char_p,
    }


# ── Execution helpers ──────────────────────────────────────────────────────

def _resolve_dll_path(raw: str) -> str:
    """Return an absolute path for *raw*, searching likely anchors."""
    p = Path(raw)
    if p.is_absolute() and p.exists():
        return str(p)
    project_root = Path(__file__).resolve().parent.parent
    candidate = project_root / raw
    if candidate.exists():
        return str(candidate)
    return raw  # let ctypes emit the real error


def _execute_dll(inv: dict, execution: dict, args: dict) -> str:
    if not IS_WINDOWS:
        return "DLL execution is only supported on Windows."
    dll_path  = _resolve_dll_path(execution.get("dll_path", ""))
    func_name = execution.get("function_name", "")

    ret_str = (
        inv.get("return_type")
        or (inv.get("signature") or {}).get("return_type", "unknown")
        or "unknown"
    ).strip()
    restype = _CTYPES_RESTYPE.get(ret_str.lower(), ctypes.c_size_t)

    params = list(inv.get("parameters") or [])
    if not params:
        sig_str = (inv.get("signature") or {}).get("parameters", "")
        if sig_str:
            for chunk in sig_str.split(","):
                tokens = chunk.strip().split()
                if len(tokens) >= 2:
                    raw_type = " ".join(tokens[:-1]).lower().strip("*").rstrip()
                    pname    = tokens[-1].lstrip("*")
                    params.append({"name": pname, "type": raw_type})

    try:
        lib = ctypes.CDLL(dll_path)
        fn  = getattr(lib, func_name)
        fn.restype = restype

        c_args = []
        if params and args:
            for p in params:
                pname = p.get("name", "")
                ptype = p.get("type", "string").lower().strip("*").rstrip()
                val   = args.get(pname)
                if val is None:
                    continue
                atype = _CTYPES_ARGTYPE.get(ptype, ctypes.c_char_p)
                if atype == ctypes.c_char_p:
                    c_args.append(ctypes.c_char_p(str(val).encode()))
                else:
                    c_args.append(atype(int(val)))
        elif args:
            for v in args.values():
                if isinstance(v, bool):
                    c_args.append(ctypes.c_bool(v))
                elif isinstance(v, int):
                    c_args.append(ctypes.c_size_t(v))
                elif isinstance(v, float):
                    c_args.append(ctypes.c_double(v))
                elif isinstance(v, str):
                    c_args.append(ctypes.c_char_p(v.encode()))

        result = fn(*c_args)
        if restype == ctypes.c_char_p:
            if isinstance(result, bytes):
                return f"Returned: {result.decode(errors='replace')}"
        return f"Returned: {result}"
    except Exception as exc:
        return f"DLL call error: {exc}"


def _execute_cli(execution: dict, name: str, args: dict) -> str:
    target = (
        execution.get("executable_path")
        or execution.get("target_path")
        or execution.get("dll_path", "")
    )
    if not target:
        return f"CLI error: no executable path configured for '{name}'"

    exe_stem = Path(target).stem.lower()
    if exe_stem == name.lower():
        # Launch-the-app invocable — just open it
        try:
            if IS_WINDOWS:
                subprocess.Popen(
                    [target],
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            else:
                subprocess.Popen([target])
            return f"Launched {Path(target).name}"
        except Exception as exc:
            return f"CLI error: {exc}"

    cmd = [target, name] + [str(v) for v in args.values()]
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if IS_WINDOWS else 0
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=creation_flags,
        )
        return r.stdout or r.stderr or f"exit_code={r.returncode}"
    except Exception as exc:
        return f"CLI error: {exc}"


def _execute_gui(execution: dict, name: str, args: dict) -> str:
    if not IS_WINDOWS:
        return "GUI actions are only supported on Windows."
    try:
        from pywinauto.application import Application  # type: ignore
    except ImportError:
        return "pywinauto is not installed; GUI actions unavailable."

    exe_path    = execution.get("exe_path", "")
    action_type = execution.get("action_type", "menu_click")

    # Minimal GUI dispatch — delegates to the generated server's full
    # implementation when running locally on Windows; here we handle the
    # most common actions for the cloud demo path.
    if action_type == "close_app":
        try:
            app = Application(backend="uia").connect(path=exe_path, timeout=3)
            app.kill()
            return "App closed."
        except Exception as exc:
            return f"GUI close error: {exc}"

    return (
        f"GUI action '{action_type}' requested for '{exe_path}'. "
        "Full GUI automation requires Windows with pywinauto installed."
    )


def _execute_tool(inv: dict, args: dict) -> str:
    """Dispatch a single tool call to the correct backend."""
    name      = inv.get("name", "")
    execution = inv.get("execution") or inv.get("mcp", {}).get("execution", {})
    method    = execution.get("method", "")

    if method == "dll_import":
        return _execute_dll(inv, execution, args)
    if method == "gui_action":
        return _execute_gui(execution, name, args)
    return _execute_cli(execution, name, args)


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


def _extract_invocables(data: Any) -> list:
    """Normalise a discovery JSON payload to a flat list of invocable dicts.

    The discovery pipeline emits:
        {"metadata": {...}, "invocables": [...], "summary": {...}}
    or legacy flat arrays / {name: info} objects.
    """
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        if "invocables" in data and isinstance(data["invocables"], list):
            return data["invocables"]
        # legacy: flat {name: info_dict} mapping
        return [
            {"name": k, **(v if isinstance(v, dict) else {"description": str(v)})}
            for k, v in data.items()
            if k not in ("metadata", "summary")
        ]
    return []


def _run_discovery(binary_path: Path, job_id: str, hints: str = "") -> dict:
    """Run the discovery pipeline on a local file path. Returns invocables list."""
    out_dir = Path(tempfile.mkdtemp(prefix=f"mcp_{job_id}_"))
    cmd = [
        sys.executable,
        str(SRC_DISCOVERY_DIR / "main.py"),
        "--dll", str(binary_path),
        "--out", str(out_dir),
        "--no-demangle",
    ]
    if hints:
        cmd += ["--tag", hints[:40].replace(" ", "_")]

    # PYTHONPATH must include the discovery package directory so all sibling
    # modules (classify, exports, schema, …) resolve correctly.
    discovery_env = {
        **os.environ,
        "PYTHONPATH": str(SRC_DISCOVERY_DIR),
    }

    logger.info(f"[{job_id}] Running discovery: {' '.join(cmd)}")
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=120,
        env=discovery_env,
    )
    logger.info(f"[{job_id}] Discovery stdout: {result.stdout[-1000:]}")
    if result.returncode != 0:
        logger.warning(f"[{job_id}] Discovery stderr: {result.stderr[-1000:]}")

    # ── Collect ALL *_mcp.json files produced (EXEs emit cli + gui + exports)
    mcp_files = sorted(out_dir.glob("*_mcp.json"))
    if not mcp_files:
        mcp_files = sorted(out_dir.glob("*.json"))

    if not mcp_files:
        raise RuntimeError(
            f"Discovery produced no output files.\n"
            f"returncode={result.returncode}\n"
            f"stderr: {result.stderr[-500:]}"
        )

    # ── Merge invocables from all output files and de-duplicate by name ──
    seen_names: set[str] = set()
    merged_invocables: list[dict] = []
    primary_blob = f"{job_id}/{mcp_files[0].name}"

    for mcp_file in mcp_files:
        try:
            file_data = json.loads(mcp_file.read_bytes())
        except Exception as exc:
            logger.warning(f"[{job_id}] Could not parse {mcp_file.name}: {exc}")
            continue

        invs = _extract_invocables(file_data)
        for inv in invs:
            name = inv.get("name", "")
            if name and name not in seen_names:
                seen_names.add(name)
                merged_invocables.append(inv)

        # Upload every artifact to Blob Storage
        blob_name = f"{job_id}/{mcp_file.name}"
        try:
            _upload_to_blob(ARTIFACT_CONTAINER, blob_name, mcp_file.read_bytes())
        except Exception as exc:
            logger.warning(f"[{job_id}] Blob upload failed for {mcp_file.name}: {exc}")

    logger.info(
        f"[{job_id}] Discovery complete: {len(mcp_files)} file(s), "
        f"{len(merged_invocables)} unique invocables"
    )

    return {
        "job_id": job_id,
        "artifact_blob": primary_blob,
        "invocables": merged_invocables,
    }


# ── Endpoints ──────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "version": "1.0.0"}


@app.post("/api/analyze-path")
async def analyze_path(body: dict[str, Any]):
    """
    Section 2.b: Analyze an installed directory or file already accessible on
    the server's filesystem (e.g. C:\\Program Files\\AppD\\ on a local Windows
    run, or a mounted volume path in a container).
    Body: {path: str, hints?: str}
    """
    path_str = (body.get("path") or "").strip()
    hints    = (body.get("hints") or "").strip()

    if not path_str:
        raise HTTPException(400, "path is required")

    target = Path(path_str)
    if not target.exists():
        raise HTTPException(
            400,
            f"Path not found on the server: {path_str!r}. "
            "In the cloud deployment the container does not have access to your "
            "local Windows filesystem — upload the file instead.",
        )

    job_id = str(uuid.uuid4())[:8]
    logger.info(f"[{job_id}] Analyze installed path: {target}")

    try:
        result = _run_discovery(target, job_id, hints)
    except Exception as e:
        logger.error(f"[{job_id}] Discovery failed: {e}")
        raise HTTPException(500, f"Analysis failed: {e}")

    return JSONResponse(result)


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

    # Write to temp file preserving the original filename so the discovery
    # pipeline derives a meaningful base-name (e.g. "calc" not "tmpXXX_").
    original_name = Path(file.filename).name or f"upload{suffix}"
    tmp_dir  = Path(tempfile.mkdtemp(prefix=f"upload_{job_id}_"))
    tmp_path = tmp_dir / original_name
    tmp_path.write_bytes(content)

    try:
        result = _run_discovery(tmp_path, job_id, hints)
    except Exception as e:
        logger.error(f"[{job_id}] Discovery failed: {e}")
        raise HTTPException(500, f"Analysis failed: {e}")
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
            tmp_dir.rmdir()
        except Exception:
            pass

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

        # Discovery pipeline uses `description`; older/generated schemas use
        # `doc` or `signature`.  Fall through all three, then the name.
        desc = (
            inv.get("doc")
            or inv.get("description")
            or inv.get("signature")
            or inv["name"]
        )

        tools.append({
            "type": "function",
            "function": {
                "name": inv["name"],
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
    _upload_to_blob(ARTIFACT_CONTAINER, schema_blob, json.dumps(mcp_schema, indent=2).encode())

    return JSONResponse({"job_id": job_id, "schema_blob": schema_blob, "mcp_schema": mcp_schema})


@app.post("/api/execute")
async def execute_tool(body: dict[str, Any]):
    """
    Execute a single tool call. Accepts either:
      - job_id + tool_name: looks up invocable from a previously registered job
      - invocable: full invocable dict supplied inline
    Body: {job_id?, tool_name, arguments, invocable?}
    """
    tool_name  = body.get("tool_name", "")
    arguments  = body.get("arguments", {})
    job_id     = body.get("job_id", "")
    inline_inv = body.get("invocable")

    if not tool_name:
        raise HTTPException(400, "tool_name is required")

    if inline_inv:
        inv = inline_inv
    elif job_id:
        inv = _get_invocable(job_id, tool_name)
        if inv is None:
            raise HTTPException(
                404,
                f"Tool '{tool_name}' not found for job '{job_id}'. "
                "Register invocables via /api/generate first.",
            )
    else:
        raise HTTPException(400, "Provide job_id or invocable")

    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            arguments = {}

    logger.info(f"[execute] {tool_name} args={arguments}")
    result = _execute_tool(inv, arguments)
    return JSONResponse({"tool_name": tool_name, "result": result})


@app.post("/api/chat")
async def chat(body: dict[str, Any]):
    """
    Section 5: Agentic chat interface.
    Sends messages to Azure OpenAI with MCP tool definitions attached.
    When the model emits tool_calls, actually executes them and feeds the
    results back for a second completion — up to MAX_TOOL_ROUNDS rounds.
    Body: {messages, tools, invocables?, job_id?}
      invocables: full invocable dicts (with execution metadata) needed to
                  dispatch tool calls. If omitted, execution falls back to
                  /api/execute with job_id lookup.
    """
    messages: list   = body.get("messages", [])
    tools: list      = body.get("tools", [])
    invocables: list = body.get("invocables", [])
    job_id: str      = body.get("job_id", "")

    if not messages:
        raise HTTPException(400, "No messages provided")
    if not OPENAI_ENDPOINT:
        raise HTTPException(503, "Azure OpenAI endpoint not configured")

    # Build a local invocable registry for this request
    inv_map: dict[str, dict] = {inv["name"]: inv for inv in invocables}
    if job_id and invocables:
        _register_invocables(job_id, invocables)

    MAX_TOOL_ROUNDS = 5
    conversation = list(messages)  # working copy

    try:
        client = _openai_client()
        msg = None

        for _round in range(MAX_TOOL_ROUNDS):
            kwargs: dict = {
                "model": OPENAI_DEPLOYMENT,
                "messages": conversation,
                "temperature": 0.2,
            }
            if tools:
                kwargs["tools"] = tools
                kwargs["tool_choice"] = "auto"

            response = client.chat.completions.create(**kwargs)
            msg = response.choices[0].message

            # No tool calls → final answer
            if not msg.tool_calls:
                return JSONResponse({
                    "role": msg.role,
                    "content": msg.content,
                    "tool_calls": [],
                    "rounds": _round + 1,
                })

            # Append assistant turn with tool_calls to conversation
            assistant_turn: dict[str, Any] = {
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in msg.tool_calls
                ],
            }
            conversation.append(assistant_turn)

            # Execute each tool call and append tool result messages
            for tc in msg.tool_calls:
                fn_name = tc.function.name
                try:
                    fn_args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    fn_args = {}

                inv = inv_map.get(fn_name)
                if inv is None and job_id:
                    inv = _get_invocable(job_id, fn_name)

                if inv is not None:
                    tool_result = _execute_tool(inv, fn_args)
                    logger.info(f"[chat/{_round}] Executed {fn_name}: {tool_result[:120]}")
                else:
                    tool_result = (
                        f"Tool '{fn_name}' executed (no invocable metadata "
                        f"available — pass 'invocables' in the request body "
                        f"or call /api/generate first). "
                        f"Raw arguments: {json.dumps(fn_args)}"
                    )
                    logger.warning(f"[chat/{_round}] No invocable for {fn_name}")

                conversation.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": tool_result,
                })

        # Exceeded MAX_TOOL_ROUNDS — return last assistant message
        if msg is None:
            return JSONResponse({"role": "assistant", "content": "", "tool_calls": [], "rounds": 0})
        last_tool_calls = [
            {
                "name": tc.function.name,
                "arguments": tc.function.arguments,
            }
            for tc in (msg.tool_calls or [])
        ]
        return JSONResponse({
            "role": "assistant",
            "content": msg.content or "(tool execution loop reached round limit)",
            "tool_calls": last_tool_calls,
            "rounds": MAX_TOOL_ROUNDS,
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
