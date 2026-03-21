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

For a detailed breakdown of each stage, see [Explore Engine](docs/EXPLORE-ENGINE.md) and [Workflow](docs/WORKFLOW.md).

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
| `contoso_cs.dll` | Custom DLL (no source) | 13 exports, 7/13 currently succeeding (see challenges below) |

`contoso_cs.dll` is in the repo at `tests/fixtures/contoso_legacy/` — or you can download it directly here: [contoso_cs.dll](https://github.com/evanking12/mcp-factory/raw/refs/heads/main/tests/fixtures/contoso_legacy/contoso_cs.dll)

---

## Key Documents

| Document | Description |
|----------|-------------|
| [MVP Thesis](docs/MVP-THESIS.md) | What the MVP is, who it's for, coverage expectations |
| [Pipeline Cohesion](docs/PIPELINE-COHESION.md) | Inter-stage data flow analysis and bug resolution (D-1 → D-11) |
| [Roadmap](docs/ROADMAP.md) | Priority-ordered work items — probe depth, UI, deliverables |
| [Workflow](docs/WORKFLOW.md) | Pipeline architecture reference with data flow diagram |
| [Explore Engine](docs/EXPLORE-ENGINE.md) | Per-function probe strategy, configuration profiles, key source files |

---

## Current Challenges

### Pipeline self-enrichment

The explore engine probes each DLL function and learns parameter names, error codes, and calling patterns — but getting those results to reliably flow back into the final MCP schema has been the primary engineering challenge. Multiple data-flow bugs were identified and fixed (D-5 through D-11, all committed). The pipeline's multi-stage architecture means each stage must correctly read, enrich, and persist the invocable map for the next stage. See [Pipeline Cohesion](docs/PIPELINE-COHESION.md) for the full analysis.

### UI for iterative deployment

Tuning the explore engine requires rapid edit → deploy → run → inspect cycles. The current workflow (push to main → GitHub Actions → Azure Container Apps → start a job → wait → download session) is too slow for debugging probe quality. Building effective diagnostics controls into the UI and streamlining the feedback loop is an active focus. See [Roadmap](docs/ROADMAP.md) for planned UI work.

### LLM variability

The same DLL function can get rich semantic descriptions in one run and sparse results in the next, because GPT-4o's probe strategy varies. Mitigations include deterministic fallback probes, minimum probe floors per function, and vocabulary accumulation across functions — but model variance remains an inherent challenge of LLM-driven reverse engineering. **Next step:** evaluate prompt behavior across models (GPT-4o vs GPT-4o mini vs others) using [Azure AI Foundry model comparison](https://learn.microsoft.com/en-us/azure/ai-studio/).

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
│   └── vm-ops/                 # Azure VM diagnostic scripts (33 files)
├── tests/                      # Test fixtures for all source types
│   └── fixtures/contoso_legacy/# contoso_cs.c source + build instructions
├── sessions/                   # Captured pipeline run snapshots
├── docs/                       # Architecture, pipeline docs, schemas, roadmap
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

## Project Requirements

MCP Factory was built to satisfy the Microsoft-sponsored USF CSE Senior Design capstone. The table below maps each requirement to its implementation.

| Requirement | Coverage |
|-------------|----------|
| **§1 — Definition of a binary** | 29 source types: PE DLL/EXE, COM/DCOM, RPC, JNDI, SOAP/WSDL, CORBA IDL, JSON-RPC, SQL, Python, PowerShell, JS/TS, Ruby, PHP, Batch, VBScript, .NET, PDB, OpenAPI, GUI (UIA), CLI, Registry |
| **§2 — Specifying the target** | Upload binary via Web UI or `--target` CLI; free-text hints file for expected functions; supports individual files and installed-instance directories |
| **§3 — Displaying invocable functions** | Discovery produces a uniform invocable list with confidence scoring (HIGH/MEDIUM/LOW); TUI and Web UI allow toggle/deselect before generation |
| **§4 — Generating the MCP architecture** | `generate.py` emits OpenAI function-calling schema + `server.py` (stdio/HTTP); auto-deploys and connects to chat |
| **§5 — Verifying the output** | Chat UI at `/api/chat` shows tool calls, arguments, and live execution results; downloadable session snapshots |
| **§6a — Azure cloud resources** | Container Apps (pipeline + UI), Blob Storage, Key Vault, Azure OpenAI GPT-4o, Application Insights, Log Analytics — all in `mcp-factory-rg` |
| **§6b — Microsoft tooling** | .NET Aspire local orchestration, VS Code + Copilot, GitHub + GitHub Actions CI/CD, GitHub Copilot MCP integration |
| **§6c — Microsoft docs** | [Azure Container Apps](https://learn.microsoft.com/en-us/azure/container-apps/), [Azure OpenAI](https://learn.microsoft.com/en-us/azure/ai-services/openai/), [MCP spec](https://modelcontextprotocol.io/), [.NET Aspire](https://learn.microsoft.com/en-us/dotnet/aspire/) |
| **§6d — Budget ≤ $150/month** | Both containers scale to zero when idle; Azure OpenAI pay-per-token; total ~$40-60/month active |
| **§6e — FERPA compliance** | No student PII collected; synthetic test data only; see [compliance statement](#ferpa-compliance-statement) below |
| **§6f — Access restricted** | Repo private to team; Azure resources restricted to project accounts; API key-gated |
| **§7 — Communication** | Weekly sponsor meetings; Teams/email updates; work tracked in [Roadmap](docs/ROADMAP.md) |

---

## Cloud Architecture

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

## Discovery Coverage (29 source types)

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
