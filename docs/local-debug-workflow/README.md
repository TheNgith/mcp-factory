# Local Debug Workflow — Ghidra JSON → LLM Self-Documentation

This guide covers how to take a raw Ghidra-analyzed DLL and run the full
chat + invocation loop locally with no Azure, no containers, no bridge server.

---

## What This Workflow Does

```
DLL + Ghidra JSON
      │
      ▼
scripts/run_local.py
  ├── Converts Ghidra OpenAI-format JSON → internal invocables (with dll_path)
  ├── Builds ctypes execution config for each function
  ├── Runs agentic chat loop (OpenAI API → tool call → ctypes → result)
  └── --discover: autonomous probe of every function → self-documents findings
```

---

## Prerequisites

1. **Python 3.11+** with the project venv active:
   ```powershell
   cd C:\Users\evanw\Downloads\capstone_project\mcp-factory
   .venv\Scripts\Activate.ps1
   ```

2. **`.env` file** in the project root (already gitignored):
   ```env
   OPENAI_API_KEY=sk-proj-...
   OPENAI_DEPLOYMENT=gpt-4o-mini
   ```

3. **The DLL** you want to probe, accessible on your local disk.

4. **The Ghidra JSON** — either:
   - Output directly from Ghidra (top-level `"tools"` key, OpenAI format)
   - Or the `invocables_map.json` downloaded from Azure Blob for an existing pipeline job

---

## Where to Get the Ghidra JSON

### Option A — Pipeline job already ran (e.g. job `d19f9c49`)
Download from Azure Blob:
```powershell
az storage blob download `
  --account-name mcpfactorystore `
  --container-name jobs `
  --name d19f9c49/invocables_map.json `
  --file artifacts/contoso_cs_invocables.json `
  --auth-mode login
```

### Option B — Fresh DLL, run Ghidra locally
```powershell
# Requires Ghidra installed and GHIDRA_HOME set
python src/discovery/ghidra_analyzer.py --dll "C:\path\to\your.dll" --out artifacts\
```
The output file will be named `<dll>_exports_mcp.json` and has the
`"invocables"` top-level key.

### Option C — Web UI (quickest for new DLLs)
1. Start the API + UI locally (see below)
2. Upload the DLL through `http://localhost:8080`
3. Wait for the Ghidra step to finish
4. Download via: `GET http://localhost:8000/api/jobs/{job_id}/invocables`

---

## Running the Debug Script

### Single prompt (chat mode)
```powershell
python scripts/run_local.py `
  --dll  "C:\Users\evanw\Downloads\mcp-test-binaries\contoso_cs.dll" `
  --json "C:\Users\evanw\Downloads\ghidra.json" `
  --prompt "initialize the library and get the version number"
```

### Interactive prompt (omit `--prompt`)
```powershell
python scripts/run_local.py `
  --dll  "C:\Users\evanw\Downloads\mcp-test-binaries\contoso_cs.dll" `
  --json "C:\Users\evanw\Downloads\ghidra.json"
```

### Autonomous discovery (probes every function, self-documents)
```powershell
python scripts/run_local.py `
  --dll  "C:\Users\evanw\Downloads\mcp-test-binaries\contoso_cs.dll" `
  --json "C:\Users\evanw\Downloads\ghidra.json" `
  --discover
```

### Override model for one run
```powershell
python scripts/run_local.py --dll ... --json ... --prompt "..." --model gpt-4o
```

---

## JSON Format Support

`run_local.py` auto-detects the JSON format:

| Format | Top-level key | Source |
|--------|--------------|--------|
| Ghidra OpenAI tool format | `"tools"` | Ghidra analyzer / web UI export |
| Pipeline invocables map | `"invocables"` | Azure Blob `jobs/{job_id}/invocables_map.json` |

Both formats work — just pass the file path to `--json`.

---

## What the Script Does Internally

1. **Loads JSON** → normalises to invocables with `execution.method = "dll_import"`
2. **Parses C types** from Ghidra description strings like `"(type: uint)"`
3. **Parses return type** from the C prototype string in the description
4. **Injects `dll_path`** pointing to your local DLL
5. **Calls `_execute_dll_bridge`** from `gui_bridge.py` directly via ctypes — no HTTP server needed
6. Runs an OpenAI tool-call loop (up to 8 rounds) feeding results back to the model

---

## Running the Full Web Stack Locally

If you need the full UI + API (to test the Discover button, report download, etc.):

**Terminal 1 — API:**
```powershell
$env:OPENAI_API_KEY     = "sk-..."
$env:OPENAI_CHAT_MODEL  = "gpt-4o-mini"
$env:GUI_BRIDGE_URL     = "http://localhost:8090"
$env:GUI_BRIDGE_SECRET  = "local-debug"
uvicorn api.main:app --port 8000 --reload
```

**Terminal 2 — UI:**
```powershell
uvicorn ui.main:app --port 8080 --reload
```

**Terminal 3 — Bridge (for GUI/DLL tool calls from the API):**
```powershell
$env:BRIDGE_SECRET = "local-debug"
python scripts/gui_bridge.py
```

Open `http://localhost:8080` and upload the DLL.

> **Note:** Azure Blob Storage is still required for job state when running
> the full web stack. For pure local ctypes testing, `run_local.py`
> needs no Azure at all.

---

## Known Issues & Fixes

### `dll_path` mismatch
If using `invocables_map.json` from a pipeline run, the `dll_path` inside
will point to the container path (e.g. `/tmp/...`). Pass `--dll` to override
it — `run_local.py` always injects your local path.

### Output buffer params (`byte *`, `undefined4 *`)
Functions with pointer output params (e.g. `CS_LookupCustomer`) always return
access violation because the bridge passes string literals into pointer
registers instead of pre-allocated ctypes buffers. This is a known limitation
in `gui_bridge.py -> _execute_dll_bridge`. The fix is to detect `* ` pointer
types and pre-allocate a `ctypes.create_string_buffer(256)` instead.

### Access violation on every call
Usually means the init function wasn't called first. The script auto-detects
init functions (`*Initialize*`, `*Init*`, `*Open*`, etc.) and hintts the model
to call them first.

---

## Cost Reference (gpt-4o-mini)

| Operation | Approx cost |
|-----------|-------------|
| Single chat message | ~$0.002 |
| Full 13-function discover run | ~$0.03 |
| Full 13-function discover run (gpt-4o) | ~$0.50 |

Set `OPENAI_DEPLOYMENT=gpt-4o-mini` in `.env` for development. Switch to
`gpt-4o` only when you need higher-quality reasoning:
```powershell
python scripts/run_local.py ... --model gpt-4o
```

---

## Key Files

| File | Purpose |
|------|---------|
| `scripts/run_local.py` | **Start here** — local debug runner |
| `scripts/gui_bridge.py` | ctypes DLL executor (`_execute_dll_bridge`) |
| `scripts/debug_chat.py` | Older debug runner (invocables_map format only) |
| `api/chat.py` | Full agentic loop used by the web stack |
| `api/explore.py` | Persistent discover worker (used by web UI Discover button) |
| `api/executor.py` | Tool dispatcher — probe fix, enrich handler |
| `.env` | Local secrets (gitignored) |
| `.env.example` | Template for env vars |

---

## Contoso CS DLL Reference (job `d19f9c49`)

13 exported functions:

| Function | Status | Notes |
|----------|--------|-------|
| `CS_Initialize` | ✅ Works | Returns 0, no params, call first |
| `CS_GetVersion` | ✅ Works | Returns 131841 (= v2.1.1) |
| `entry` | ✅ Works | Returns 0, not callable by users |
| `CS_LookupCustomer` | ⚠️ Access violation | Output buffer param — bridge fix needed |
| `CS_GetAccountBalance` | ⚠️ Access violation | Output buffer param |
| `CS_CalculateInterest` | ⚠️ Returns 0 | param_4 is output pointer, result lost |
| `CS_GetDiagnostics` | ⚠️ Access violation | Output buffer param |
| `CS_GetLoyaltyPoints` | ⚠️ Access violation | Output buffer param |
| `CS_GetOrderStatus` | ⚠️ Access violation | Output buffer param |
| `CS_ProcessPayment` | ⚠️ Access violation | Output buffer param |
| `CS_ProcessRefund` | ⚠️ Access violation | Output buffer param |
| `CS_RedeemLoyaltyPoints` | ⚠️ Access violation | Output buffer param |
| `CS_UnlockAccount` | ⚠️ Access violation | Output buffer param |

**Root cause of access violations:** The bridge doesn't pre-allocate output
buffers. Fix target: `gui_bridge.py -> _execute_dll_bridge`, detect `*` in
C type → allocate `ctypes.create_string_buffer(256)` → pass pointer → return
buffer contents alongside the return value.
