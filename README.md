# MCP Factory

> Automated generation of [Model Context Protocol](https://modelcontextprotocol.io/) servers from undocumented Windows binaries

**Project:** USF CSE Senior Design Capstone — Microsoft Sponsored  
**Team:** Evan King (lead), Layalie AbuOleim, Caden Spokas, Thinh Nguyen

---

## Why This Exists

Enterprises run thousands of internal Windows tools — DLLs, COM objects, CLI utilities, legacy services — that **have no API documentation and no modern integration points**. When organizations adopt AI agents (Copilot, custom GPTs, internal chatbots), those agents can't call these tools because there's nothing machine-readable describing what the tools do or how to invoke them.

**MCP Factory solves this.** Point it at any Windows binary and it automatically:

1. **Discovers** every callable entry point (exports, COM interfaces, CLI commands, GUI controls)
2. **Probes** each function with an LLM-driven exploration engine to learn parameter semantics, error codes, and valid call patterns — even when source code is unavailable
3. **Generates** a standards-compliant [MCP](https://modelcontextprotocol.io/) server that any AI agent can consume immediately

The result: an AI agent goes from "I have no idea what `contoso_cs.dll` does" to being able to call `CS_Initialize`, `CS_GetAccountBalance`, `CS_ProcessPayment` with correct parameters and meaningful descriptions — automatically, without human reverse-engineering.

### Business Scenario

A bank deploys a customer-service AI agent. The agent needs to look up account balances, process payments, and check loyalty points — but the only implementation is a legacy Win32 DLL with no documentation. MCP Factory analyzes that DLL, discovers 13 exported functions, probes each one to learn what it does, and generates an MCP server. The AI agent connects and starts serving customers.

---

## How It Works

```
                    ┌─────────────────────────────────────────────┐
                    │              MCP Factory Pipeline            │
                    │                                             │
  Upload binary ──► │  1. Static Analysis (PE headers, exports,   │
  (DLL/EXE/COM/     │     Ghidra decompilation, IAT, strings)     │
   script/dir)      │                                             │
                    │  2. LLM Explore Engine (GPT-4o probes each  │
                    │     function: tries inputs, reads outputs,  │
                    │     learns semantics, records findings)      │
                    │                                             │
                    │  3. Schema Synthesis (enriches param names,  │
                    │     descriptions, types from probe results)  │
                    │                                             │
                    │  4. MCP Generation (emits OpenAI function-   │
                    │     calling schema + stdio/HTTP server)      │
                    └──────────────────────┬──────────────────────┘
                                           │
                                           ▼
                    ┌─────────────────────────────────────────────┐
                    │         Generated MCP Server                │
                    │  • VS Code Copilot connects via stdio       │
                    │  • Chat UI at /api/chat (SSE streaming)     │
                    │  • Any MCP-compatible agent can consume     │
                    └─────────────────────────────────────────────┘
```

### Discovery Coverage (29 source types)

| Category | Source Types |
|----------|-------------|
| Native PE | DLL/EXE exports (pefile), Ghidra decompilation |
| .NET | Reflection-based method extraction |
| COM / TLB | Registry CLSID scan, type library parsing |
| CLI | Argument extraction from help output |
| GUI | UIA control walk, menu enumeration (pywinauto) |
| SQL | Stored procs, views, tables, triggers |
| Scripting | Python, PowerShell, Shell, Batch, VBScript, Ruby, PHP |
| JS/TS | JavaScript + TypeScript function extraction |
| RPC | Interface scanning |
| Legacy specs | OpenAPI, JSON-RPC, WSDL, CORBA IDL, JNDI, PDB symbols |

All source types produce the **same uniform JSON schema** — a PE export, a Python function, and a SQL stored procedure look identical to the MCP generator.

### Cloud Architecture

| Component | Technology | Purpose |
|-----------|-----------|---------|
| Pipeline API | FastAPI on Azure Container Apps | Runs discovery, explore, generation |
| Web UI | FastAPI on Azure Container Apps | Upload → Select → Generate → Chat wizard |
| LLM | Azure OpenAI GPT-4o | Powers the explore engine + chat |
| Storage | Azure Blob Storage | Per-job artifacts (schemas, findings, vocab) |
| Secrets | Azure Key Vault | All secrets via Managed Identity — no env vars |
| Monitoring | Application Insights | Custom telemetry spans + Azure Monitor Workbook |
| CI/CD | GitHub Actions (OIDC) | Auto-deploy on push to main — no stored secrets |
| Orchestration | .NET Aspire | Local multi-container dev (`dotnet run`) |
| GUI Bridge | Self-hosted Windows runner VM | GUI/COM analysis in CI + production |

Both containers scale to zero when idle (zero cost) and auto-scale on HTTP load.

---

## Current Status — Week 11/16

**End-to-end pipeline is live in Azure.** Upload a DLL → automatic discovery → LLM-driven probing → MCP schema generation → interactive chat with tool execution.

### What Works Today

- **Full discovery pipeline** — 29/29 demo targets pass across all source types
- **Cloud deployment** — both containers live, CI/CD auto-deploying on every push
- **VS Code Copilot integration** — generated MCP server connects natively to Copilot Chat (type `#mcp-notepad`, ask Copilot to open Notepad and type text — it calls tools through the MCP protocol)
- **Chat UI** — shows tool calls, arguments, and live execution results
- **Explore engine** — LLM-driven probing with deterministic fallback, gap detection, sentinel calibration, vocabulary learning
- **Static analysis** — PE version info, IAT capabilities, binary string harvesting, Capstone disassembly for sentinel values

### Demo Targets

| Target | Type | Result |
|--------|------|--------|
| `calc.exe` | WinUI3/MSIX | 55 invocables, live GUI control via chat |
| `notepad.exe` | Win32 | Type, save, open, append — controlled by Copilot |
| `contoso_cs.dll` | Custom DLL (no source) | 13 exports, 7/13 currently succeeding (see impediments) |

---

## Current Impediments

> **This is the primary blocker for demo readiness.**

The explore engine — which automatically probes undocumented DLL functions to learn their behavior — has a **schema enrichment pipeline** with several data-flow bugs that were identified and fixed this week. These bugs caused the enriched parameter names and descriptions discovered by the LLM to be lost before they reached the final MCP schema.

### Bug Summary (all fixed, awaiting verification run)

| ID | Bug | Impact | Fix |
|----|-----|--------|-----|
| D-5 | Parameter semantic naming skipped on wholesale replacement path | Params kept raw Ghidra names (`param_1`, `param_2`) instead of learned names (`customer_id`, `balance`) | `790dd3a` |
| D-8 | Deterministic fallback success went unrecorded | Functions that return 0 on success were marked as errors | `5310428` |
| D-9 | Stale invocables snapshot | Backfill read a stale copy of the invocable map, overwriting enrichments | `5310428` |
| D-10 | No direction inference on wholesale params | All params marked as required inputs, even output buffers | `5310428` |
| D-11 | Ground-truth override didn't rewrite finding text | Synthesis read stale "all probes returned sentinel codes" text → concluded functions were undocumented → skipped enrichment | `82c29e8` |

### Net Effect

In the last pipeline run (job `7af1301e`), the MCP schema **froze after the second checkpoint** — all subsequent enrichment was silently lost. The schema showed 7/13 functions succeeding, but 6 functions still had raw Ghidra decompiled descriptions like `"Recovered by Ghidra static analysis. Original C signature: undefined4 CS_GetOrderStatus(…)"` instead of meaningful descriptions.

D-11 was the root cause of the freeze — the other bugs (D-8, D-9, D-10) were masked by it. All fixes are committed and pushed (`82c29e8`). **Next step: verify with a fresh pipeline run.**

### Remaining Risk

- **LLM variability** — probe quality varies run-to-run; the same function may get rich descriptions in one run and sparse descriptions in the next. *CSA Chad suggested using Azure AI Foundry's model comparison feature to evaluate prompt performance across models (GPT-4o vs GPT-4o mini etc.).*
- **CS_GetVersion misclassification** — this function returns a version constant (`0x20301` = version 2.3.1), which the sentinel detector misclassifies as an error code. Needs special handling for constant-return functions.

---

## Quick Start

### Prerequisites

- Windows 10/11, Python 3.8+, Git
- For cloud: pipeline is live at `mcp-factory-pipeline.icycoast-8ddfa278.eastus.azurecontainerapps.io`

### Installation

```powershell
git clone https://github.com/evanking12/mcp-factory.git
cd mcp-factory
pip install -r requirements.txt
```

### 1. Capabilities Demo (29 source types)

```powershell
python scripts/demo_all_capabilities.py
```

Runs 29 live analyses across all supported source types — PE exports, .NET, COM, CLI, GUI, SQL, scripting languages, RPC, legacy protocols. Every output produces the same JSON schema.

### 2. Live App Demo (AI controls Windows)

```powershell
# Start a pre-built server
python mcp_factory.py --serve notepad
# Open http://localhost:5000 and try:
# "Open notepad, type a short poem, save it as poem.txt"
```

### 3. Full Pipeline on Any Binary

```powershell
# Discovery → selection TUI → server generation in one command
python mcp_factory.py --target C:\Windows\System32\notepad.exe --description "text editor"
```

### 4. VS Code Copilot Integration

The generated MCP server connects directly to VS Code Copilot Chat:

1. Open this repo in VS Code (`.vscode/mcp.json` is already configured)
2. Open Copilot Chat (`Ctrl+Alt+I`)
3. Type: `#mcp-notepad open a new file and type hello world`
4. Copilot calls `file_new` then `type_text` through the MCP stdio protocol — Notepad opens and text appears

### 5. Cloud Pipeline (Azure)

Upload any DLL through the web UI at `mcp-factory-ui.icycoast-8ddfa278.eastus.azurecontainerapps.io`:
1. **Upload** — drag a DLL or paste an installed-app path
2. **Select** — review discovered invocables, toggle by confidence level
3. **Generate** — produces MCP schema + server
4. **Chat** — interactive tool-calling chat with live execution

---

## Explore Engine Deep Dive

The explore engine is the core differentiator — it's an LLM-driven autonomous agent that probes undocumented functions to learn their behavior without source code.

**How it works per function:**
1. **Sentinel calibration** — calls the function with known-bad inputs to establish baseline error codes
2. **Boundary probing** — systematically varies parameters to find valid input patterns
3. **Vocabulary learning** — accumulates domain knowledge (ID formats, error codes, value semantics) across functions
4. **Finding recording** — saves successful call patterns with semantic descriptions
5. **Deterministic fallback** — for simple init/getter functions, tries a zero-arg call pattern
6. **Gap detection** — identifies functions that need clarification and generates targeted questions
7. **Schema synthesis** — rewrites raw Ghidra parameter names (`param_1`) to semantic names (`customer_id`) based on probe evidence

**Configuration profiles:**

| Profile | Rounds | Tool Calls | Use Case |
|---------|--------|-----------|----------|
| dev | 3 | 5 | Fast iteration |
| stabilize | 5 | 10 | Quality runs |
| deploy | 8 | 15 | Production |

---

## Repository Structure

```
mcp-factory/
├── mcp_factory.py              # Single-command entry point (--target / --serve)
├── api/                        # Cloud pipeline (FastAPI)
│   ├── main.py                 # Routes: /api/analyze, /api/generate, /api/chat, /api/jobs
│   ├── explore.py              # LLM explore engine orchestrator
│   ├── explore_phases.py       # Sentinel calibration, boundary probing
│   ├── explore_vocab.py        # Vocabulary learning (IDs, error codes, semantics)
│   ├── explore_prompts.py      # LLM prompt construction
│   ├── generate.py             # MCP schema generation from enriched invocables
│   ├── storage.py              # Blob persistence (findings, invocables, vocab)
│   ├── executor.py             # DLL/CLI/GUI/bridge dispatch
│   ├── chat.py                 # Agentic SSE chat loop
│   ├── search.py               # Azure AI Search vector indexing
│   └── static_analysis.py      # PE analysis, IAT, Capstone disassembly
├── src/discovery/              # Local discovery pipeline (22+ analyzers)
├── src/ui/                     # Interactive invocable selection TUI
├── src/generation/             # MCP server template generation
├── ui/                         # Cloud web UI (FastAPI, proxies to pipeline)
├── generated/                  # Pre-built MCP servers (notepad, calculator)
├── infra/                      # Bicep IaC (workbook, runner VM)
├── aspire/                     # .NET Aspire app host
├── scripts/                    # Tooling (save-session, demos, bridge, CI)
├── tests/                      # Test fixtures for all source types
├── sessions/                   # Captured pipeline run snapshots
├── docs/                       # ADRs, architecture, schemas
└── .github/workflows/          # CI/CD (OIDC, 4-job pipeline)
```

## Deployment

Two containers auto-deployed on every push to `main` via GitHub Actions (OIDC, no stored secrets):

- **Pipeline** (`Dockerfile`) — `uvicorn api.main:app` on port 8000. Runs discovery, reads Key Vault secrets via Managed Identity.
- **UI** (`Dockerfile.ui`) — `uvicorn ui.main:app` on port 8080. Proxies `/api/*` to the pipeline.

**Local development with .NET Aspire:**
```powershell
cd aspire/AppHost && dotnet run   # requires .NET 8 SDK + Docker Desktop
```

## Documentation

| Document | Description |
|----------|-------------|
| [Architecture](docs/architecture.md) | System design and component overview |
| [Sections 2-3](docs/sections-2-3.md) | Binary discovery implementation (29 source types) |
| [Product Flow](docs/product-flow.md) | Full pipeline walkthrough |
| [Schemas](docs/schemas/) | JSON schema contracts |
| [ADRs](docs/adr/) | Architecture decision records |
| [Troubleshooting](docs/TROUBLESHOOTING.md) | Common issues and solutions |

---

**Sponsored by Microsoft** | Mentored by Microsoft Engineers  
**USF CSE Senior Design Capstone — Spring 2026**  
_Last updated: March 20, 2026_

---

## FERPA Compliance Statement

MCP Factory is developed and operated in compliance with FERPA and all applicable data-privacy regulations.

- **No student PII collected or stored.** No names, student IDs, or email addresses are processed.
- **Uploaded binaries are ephemeral.** Stored under randomized job IDs in Azure Blob Storage; lifecycle policies delete after 24 hours.
- **No conversation data persisted.** Chat messages to Azure OpenAI are not logged. Azure OpenAI does not store prompt/completion data via API.
- **Access restricted to project team.** Managed Identity authentication on all Azure resources; no anonymous write access.
- **Resources scoped to project subscription** — not shared with other courses or students.

---

## References

| Topic | URL |
|---|---|
| Model Context Protocol (MCP) overview | https://learn.microsoft.com/en-us/azure/ai-services/openai/how-to/function-calling |
| Azure OpenAI function calling | https://learn.microsoft.com/en-us/azure/ai-services/openai/how-to/function-calling |
| Azure Container Apps overview | https://learn.microsoft.com/en-us/azure/container-apps/overview |
| Azure Container Apps environment | https://learn.microsoft.com/en-us/azure/container-apps/environment |
| Azure Blob Storage overview | https://learn.microsoft.com/en-us/azure/storage/blobs/storage-blobs-overview |
| Azure Key Vault secrets | https://learn.microsoft.com/en-us/azure/key-vault/secrets/about-secrets |
| Managed identities for Azure resources | https://learn.microsoft.com/en-us/azure/active-directory/managed-identities-azure-resources/overview |
| Azure Container Registry | https://learn.microsoft.com/en-us/azure/container-registry/container-registry-intro |
| Application Insights (Azure Monitor) | https://learn.microsoft.com/en-us/azure/azure-monitor/app/app-insights-overview |
| .NET Aspire overview | https://learn.microsoft.com/en-us/dotnet/aspire/get-started/aspire-overview |
| .NET Aspire app host | https://learn.microsoft.com/en-us/dotnet/aspire/fundamentals/app-host-overview |
| GitHub Codespaces devcontainer reference | https://docs.github.com/en/codespaces/setting-up-your-project-for-codespaces/adding-a-dev-container-configuration |
| Dev Containers specification | https://containers.dev/implementors/spec/ |
| Azure Cost Management budgets | https://learn.microsoft.com/en-us/azure/cost-management-billing/costs/tutorial-acm-create-budgets |
| pefile library (PE analysis) | https://github.com/erocarrera/pefile |
| Windows `winreg` module | https://docs.python.org/3/library/winreg.html |
| COM object registration (CLSID) | https://learn.microsoft.com/en-us/windows/win32/com/com-class-objects-and-clsids |
| Windows App Paths registry key | https://learn.microsoft.com/en-us/windows/win32/shell/app-registration |
| FastAPI documentation | https://fastapi.tiangolo.com/ |
| Azure SDK for Python | https://learn.microsoft.com/en-us/azure/developer/python/sdk/azure-sdk-overview |
