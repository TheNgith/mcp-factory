# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

MCP Factory is a pipeline that automatically generates [Model Context Protocol](https://modelcontextprotocol.io/) servers from undocumented Windows binaries (DLLs, COM objects, CLI utilities). It discovers callable entry points via static analysis, probes them with an LLM-driven exploration engine to learn semantics, and generates MCP-compliant servers that AI agents can consume.

USF CSE Senior Design Capstone — Microsoft Sponsored. Python 3.10+ (uses union types, `match` statements, `dict[str, Any]` generics).

## Build & Run Commands

```bash
# Install dependencies (two requirement files)
pip install -r requirements.txt
pip install -r api/requirements.txt

# Run the API server locally
uvicorn api.main:app --reload --port 8000

# Run tests (excludes MCP stdio test which needs Windows)
pytest tests/ -v --tb=short --ignore=tests/test_mcp_stdio.py

# Run a single test file
pytest tests/test_contract_artifacts.py -v --tb=short

# Run a single test
pytest tests/test_fixtures.py::TestFixtures::test_zstd_exports_exist -v

# Local discovery demo (29 source types)
python scripts/demo_all_capabilities.py

# Single-command local pipeline
python mcp_factory.py --target <path-to-binary> --description "text editor"

# .NET Aspire local orchestration (requires .NET 8 SDK + Docker)
cd aspire/AppHost && dotnet run
```

## Architecture

### Two-Container Deployment
- **Pipeline** (`Dockerfile`, port 8000): `uvicorn api.main:app` — discovery, explore, generation, chat
- **UI** (`Dockerfile.ui`, port 8080): `uvicorn ui.main:app` — web wizard, proxies `/api/*` to pipeline

### Core Pipeline Flow
Upload binary → Static Analysis → LLM Explore Engine → Schema Synthesis → MCP Server Generation → Interactive Chat

### Pipeline Stages (api/pipeline/)
The explore pipeline runs stages S-00 through S-07, each in its own subpackage (`s00_setup/` through `s07_finalize/`). The orchestrator (`orchestrator.py`) drives all stages sequentially.

| Stage | Purpose | Key module |
|-------|---------|------------|
| S-00 | Sentinel calibration, vocab seed, static analysis | `s00_setup/` |
| S-01 | Write-unlock probe | `s01_unlock/` |
| S-02 | Curriculum ordering + per-function LLM probe loop | `s02_probe/` |
| S-03 | Reconcile probe log, sentinel catalog | `s03_reconcile/` |
| S-04 | API reference synthesis | `s04_synthesis/` |
| S-05 | Schema backfill + verification | `s05_enrichment/` |
| S-06 | Gap resolution | `s06_gaps/` |
| S-07 | Behavioral spec, harmonize, finalize | `s07_finalize/` |

Four **Micro Coordinators** (MC-3, MC-4, MC-5, MC-6) run between stages to reason about accumulated results and adjust strategy. They save decisions to `{job_id}/mc-decisions/`.

### Key Shared Infrastructure (api/)
- `main.py` — FastAPI routes: `/api/analyze`, `/api/generate`, `/api/chat`, `/api/jobs`
- `config.py` — All environment-driven config (must not import from other api modules)
- `storage.py` — Azure Blob Storage + in-memory caches (`_JOB_STATUS`, `_JOB_INVOCABLE_MAPS`)
- `executor.py` — DLL/CLI/GUI/bridge dispatch for tool execution
- `chat.py` — Agentic SSE chat loop with tool calling
- `cohesion.py` — T-01..T-21 data-flow transitions, session-meta contract
- `routes_session.py` — Session snapshot ZIP + report endpoints

### Pipeline Shared Modules (api/pipeline/)
- `types.py` — `ExploreContext` (mutable state) + `ExploreRuntime` (immutable config) dataclasses
- `helpers.py` — Constants, regex patterns, cap profiles, utility functions
- `checkpoint.py` — Checkpoint save/load for resumable runs
- `vocab.py` — Vocabulary table management
- `prompts.py` — LLM prompt builders

### Legacy Shims
`api/explore*.py` files are thin re-exports from `api/pipeline/*`. Always import from `api.pipeline.*` directly.

### Local Discovery Engine (src/discovery/)
22+ static analyzers producing a uniform JSON schema across all source types (PE, .NET, COM, CLI, GUI, SQL, scripting, RPC, legacy protocols).

## Canonical Names

Use these consistently — never abbreviate:
- `invocables` — list of invocable dicts; `inv_map` — dict {name: invocable}
- `_JOB_INVOCABLE_MAPS` — module-level registry in storage.py
- `vocab` / `ctx.vocab` — cross-function semantic accumulator
- `findings` — list of per-function probe outcome dicts
- `ctx` — always an `ExploreContext` instance in the pipeline
- `ctx.sentinels` — runtime sentinel table (int keys)
- `current_status` or `job_status` — never `_cur`, `_wucurrent`, `_gap_current`
- Exception variable: always `exc`, never `_wue`, `_rle`, `_fb_e`

## Code Style

- `snake_case` for all variables and functions
- Never use cryptic abbreviations (`_wuo`, `_hc`, `_pv`)
- Never alias imports to shorter names
- Comments explain WHY, not WHAT
- Ruff linter: line-length 120, target Python 3.8
- Commit messages: [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, `docs:`, `refactor:`, etc.)

## Environment Configuration

Copy `.env.example` to `.env`. Key variables:
- `OPENAI_API_KEY` — direct OpenAI (takes priority over Azure)
- `OPENAI_EXPLORE_MODEL` — model for explore loop (default: `gpt-4o-mini`)
- `OPENAI_MODEL` / `OPENAI_CHAT_MODEL` — model for chat
- `AZURE_STORAGE_ACCOUNT` — required for blob storage
- `GUI_BRIDGE_URL` / `GUI_BRIDGE_SECRET` — Windows DLL bridge
- `EXPLORE_CAP_PROFILE` — `dev` (fast), `stabilize` (balanced), `deploy` (deepest)
- `MCP_CONTAINER=1` — set in Docker to skip Windows-only analyzers

## Checkpoint System

Pass `checkpoint_id` in `explore_settings` to resume from a prior run. `focus_functions` limits probing to specific functions. `skip_to_stage` resumes from a stage boundary. Functions with `status="success"` and `working_call` are automatically skipped.

## Circular Feedback

Pass `prior_job_id` in `explore_settings` to seed from a previous session (loads findings, sentinel table, vocab, api-reference).

## Cloud Pipeline End-to-End Flow

### Job Lifecycle

1. **Upload** — `POST /api/analyze` receives the binary, uploads it to Azure Blob Storage (`uploads/{job_id}/input.dll`), writes initial job status (`queued`), enqueues to Azure Storage Queue, returns `202` with `job_id`. Falls back to a direct background thread if queue is unavailable.
2. **Discovery** — `worker.py` queue worker picks up the message, downloads the binary from blob, runs `_run_discovery` which: checks SHA-256 cache → runs `src/discovery/main.py` subprocess (22+ static analyzers) → calls GUI bridge for Windows-only analysis → merges/deduplicates → caches result → sets job status to `done`.
3. **Explore** — `POST /api/jobs/{job_id}/explore` spawns `_explore_worker` (orchestrator.py) in a background thread. Runs stages S-00 through S-07 with micro coordinators. UI polls `GET /api/jobs/{job_id}` for `explore_phase`/`explore_progress`/`explore_message`.
4. **Generate** — `POST /api/generate` builds OpenAI function-calling schema + `mcp_server.py` from enriched invocables. Applies findings (renames `param_1` → `customer_id` from working calls). Uploads artifacts to blob, registers invocable map in memory.
5. **Chat** — `POST /api/chat` returns SSE stream. LLM (GPT-4o) gets full tool schema, issues `call_function` tool calls, each executed via `executor.py` (dispatches to bridge/ctypes/subprocess/pywinauto). Streams events: `tool_call` → `tool_result` → `token` → `done`. Loops up to 10 rounds. Injects synthetic `record_finding` and `enrich_invocable` tools.
6. **Session Snapshot** — `routes_session.py` provides ZIP download of all artifacts (findings, vocab, transcripts, executor traces, checkpoints, MC decisions).

### Additional Endpoints

- `POST /api/jobs/{job_id}/refine` — targeted re-exploration with user corrections and specific function list
- `POST /api/jobs/{job_id}/explore-cancel` — cooperative cancellation (worker checks flag between steps)
- `POST /api/jobs/{job_id}/answer-gaps` — accepts user answers to clarification questions, merges into vocab/hints, triggers gap mini-sessions
- `POST /api/check-cache` — pre-flight SHA-256 cache check before uploading
- `POST /api/analyze-path` — analyze a file already on the server filesystem (path-traversal protected by `_SAFE_PATH_PREFIXES`)

### Explore Modes

Configured via `explore_settings.mode` in the explore request body:
- **dev** — `cap_profile=dev`, 2 rounds, 5 tool calls, gap resolution disabled
- **normal** (default) — `cap_profile=deploy`, 5 rounds, 8 tool calls, gap resolution enabled
- **extended** — `cap_profile=deploy`, 7 rounds, 14 tool calls, gap resolution enabled

## Windows GUI Bridge

### What It Is

`scripts/gui_bridge.py` — a FastAPI server (port 8090) running on a Windows VM that handles Windows-only analysis and execution. The Linux pipeline container cannot run GUI apps, COM, or pywinauto, so it delegates to the bridge over HTTP.

### Two Endpoints

- `POST /analyze` — discovery: launches the app, walks UIA tree (pywinauto), scans COM/TLB, runs CLI help, Ghidra decompilation, registry scan. Returns `{"invocables": [...]}`. Streams NDJSON with heartbeats every 15s to prevent idle TCP drops.
- `POST /execute` — tool execution: connects to running app via pywinauto, performs action (click button, type text, menu select), returns result string. Pipeline calls this for every tool call during explore and chat.

### Authentication

Every request must include `X-Bridge-Key: <BRIDGE_SECRET>`. Constant-time comparison via `secrets.compare_digest`.

### GUI App Discovery Flow

1. Pipeline base64-encodes binary, sends to bridge
2. Bridge decodes to `C:\mcp-factory\uploads\<name>` — but prefers system path for UWP/MSIX apps (e.g., uses `C:\Windows\System32\calc.exe` instead of the upload copy, because UWP stubs need proper package activation)
3. Bridge launches the app, uses pywinauto UIA backend to walk the control tree with a 60s timeout
4. Each UI element (button, menu, text field) becomes an invocable with `method: "gui_action"`
5. Also runs COM/TLB, CLI, Ghidra (if 0 invocables from other analyzers), registry, .NET, RPC analyzers
6. Deduplicates by name, returns result. Caches indefinitely by resolved path.

### Bridge Must Run as Interactive User

pywinauto needs Session 1 (interactive desktop) to access UIA trees. SYSTEM/Session 0 cannot see GUI windows. The bridge runs as a Scheduled Task under `azureuser` with `AtLogOn` trigger. Auto-logon is configured via `AutoAdminLogon` registry key so the VM signs in without RDP on every reboot.

### Execution Dispatch (executor.py)

`_execute_tool` checks bridge first — if `GUI_BRIDGE_URL` is set, ALL execution (DLL, GUI, CLI) routes through the bridge. Only falls back to local execution when bridge is absent (local Windows dev). Bridge reachability is cached with a 2-minute TTL (15s on failure). If a DLL call returns `0xFFFFFFFF` (sentinel), `_probe_bridge` automatically runs a probe matrix trying different pointer/scalar encodings.

## Windows VM Infrastructure

### Deployment

Azure VM (Standard_D2s_v3, Windows Server 2022) defined in `infra/runner-vm.bicep`. Opt-in via `deployWindowsRunner=true` in `infra/main.bicep`. First-boot `CustomScriptExtension` runs `scripts/install-github-runner.ps1` which installs Python 3.10, all packages (pywinauto, comtypes, pywin32, fastapi, uvicorn, httpx), registers the bridge as a Scheduled Task, configures auto-logon, and sets up the GitHub Actions runner.

### Network Topology

Shared VNet `mcpfactory-vnet` (`10.0.0.0/16`) defined in `infra/vnet.bicep`:
- `aca-infra` subnet (`10.0.0.0/23`) — ACA environment, delegated to `Microsoft.App/environments`
- `vm` subnet (`10.0.2.0/24`) — Windows VM, static private IP `10.0.2.4`

ACA has VNet integration so outbound traffic flows through the VNet, giving private connectivity to the VM. The pipeline container reaches the bridge at `http://10.0.2.4:8090` (never over public internet).

### NSG Rules (VM Subnet)

| Priority | Rule | Effect |
|----------|------|--------|
| 1000 | AllowRDP | Port 3389 open from anywhere (admin access) |
| 1100 | AllowGUIBridgeFromACA | Port 8090 allowed only from `10.0.0.0/23` (ACA subnet) |
| 1200 | DenyGUIBridgeFromInternet | Port 8090 blocked from all other sources |

### Credential Flow

`GUI_BRIDGE_URL` and `GUI_BRIDGE_SECRET` are stored in Azure Key Vault, injected into the pipeline container via ACA `secretRef`. Wiring script: `scripts/wire-bridge-to-aca.ps1` (opens NSG, sets env vars on ACA, verifies health).

### Auto-Update

A Scheduled Task (`_bridge_autopull.ps1`) runs every 5 minutes: `git fetch && git reset --hard origin/main`. If commit hash changed, kills bridge process and restarts with latest code. Changes to `gui_bridge.py` are live on the VM within 5 minutes of pushing to `main`.

### User Visibility

Users never interact with the VM. The browser only talks to the UI container, which proxies to the pipeline container, which talks to the VM over the private VNet. Users see tool call names, arguments, and text results in the chat — not the actual GUI windows on the VM. Admins debug via RDP (port 3389).
