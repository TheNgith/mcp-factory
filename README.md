# MCP Factory

> Automated generation of Model Context Protocol servers from Windows binaries

**Project:** USF CSE Senior Design Capstone - Microsoft Sponsored  
**Objective:** Enable AI agents to interact with Windows applications through automated MCP server generation

## Team

| Role | Member | GitHub |
|------|--------|--------|
| Lead & Sections 2-3 | Evan King | [@evanking12](https://github.com/evanking12) |
| Section 4 (MCP Generation) | Layalie AbuOleim | [@abuoleim1](https://github.com/abuoleim1) |
| Section 4 (MCP Generation) | Caden Spokas | [@bustlingbungus](https://github.com/bustlingbungus) |
| Section 5 (Verification) | Thinh Nguyen | [@TheNgith](https://github.com/TheNgith) |

## Business Scenario

Enterprise organizations need AI-powered customer service that can invoke existing internal tools lacking API documentation or modern integration points. MCP Factory bridges this gap by automatically analyzing Windows binaries and generating standards-compliant Model Context Protocol servers.

## Azure Resource Status

| Resource | Name / ID | Status |
|---|---|---|
| Subscription ID | `abb10328-e7f1-4d4a-9067-c1967fd70429` | ✅ Active |
| Tenant ID | `bfddc1f5-1e88-471c-9b5e-44611ddd3c22` | ✅ Active |
| Resource Group | `mcp-factory-rg` / eastus | ✅ Created |
| Managed Identity | `mcp-factory-identity` | ✅ Created |
| Identity principalId | `ef864658-442a-4dc1-b8d9-37f01f79fe8d` | ✅ Created |
| Identity clientId | `f70e3ce7-3790-49de-95b0-2de22fc0adbe` | ✅ Created |
| Key Vault | `mcp-factory-kv` / `mcp-factory-kv.vault.azure.net` | ✅ Created |
| Storage Account | `mcpfactorystore` | ✅ Created |
| Blob — uploads | `mcpfactorystore/uploads` | ✅ Created |
| Blob — artifacts | `mcpfactorystore/artifacts` | ✅ Created |
| Container Registry | `mcpfactoryacr` / `mcpfactoryacr.azurecr.io` | ✅ Created |
| ACA Environment | `mcp-factory-env` / eastus | ✅ Created |
| ACA Default Domain | `calmsmoke-c4f97e21.eastus.azurecontainerapps.io` | ✅ Created |
| App Service | `mcp-factory-web` | ⏭ Skipped (VM quota = 0, pivoted to ACA) |
| ACA App — pipeline | `mcp-factory-pipeline` | 🔵 Pending — needs Docker image pushed |
| ACA App — web UI | `mcp-factory-ui` | 🔵 Pending — needs Docker image pushed |
| Azure OpenAI | not yet provisioned | 🔵 Pending |
| App Insights | not yet provisioned | 🔵 Pending |

**Remaining to reach a fully live demo** (run `scripts\finalize_azure.ps1`):
1. Provision Azure OpenAI + deploy `gpt-4o` model
2. Provision Application Insights
3. Store secrets in Key Vault
4. Build + push both Docker images → ACR
5. Update ACA apps with the image + environment variables

## Current Status — Week 8 / 16

> **Summary:** Discovery (§2-3) complete. MCP generation (§4) demo-ready. Cloud verification UI (§5) wired. Azure core infrastructure provisioned — two remaining steps block the live cloud demo: Azure OpenAI + App Insights provisioning, then Docker image build + push.

- [x] **Sections 2-3: Hybrid Discovery Engine** — **COMPLETE**
  - ✅ PE DLL/EXE, .NET, COM/TLB, RPC, CLI, SQL, 9 scripting languages, all §1 legacy protocols
  - ✅ `--target` accepts a file **or** an installed directory (`C:\Program Files\AppD\`) — §2.a
  - ✅ `--registry` flag scans HKLM App Paths, Uninstall keys, and COM CLSID registrations — §1.c
  - ✅ Uniform `{ name, kind, confidence, description, return_type, parameters, execution }` schema
  - ✅ 29/29 demo targets pass across all 10 source-type sections
- [x] **Section 4: MCP Generation** — **DEMO-READY**
  - ✅ `python mcp_factory.py --target <file>` runs full pipeline in one command
  - ✅ Flask MCP server with `/tools`, `/invoke`, `/chat`, `/download/invocables`
  - ✅ Chat UI at `http://localhost:5000`; shows tool calls + live execution results
  - ✅ Working demos: Calculator (55 invocables, WinUI3) and Notepad (Win32)
- [x] **Section 5: Verification UI** — **COMPLETE (cloud)**
  - ✅ FastAPI web UI (`ui/main.py`) — 4-step wizard: Upload → Select → Generate → Chat
  - ✅ Installed-path input field (§2.b) — paste `C:\Program Files\AppD\` directly
  - ✅ Chat tab sends `invocables` metadata so the pipeline actually executes tool calls
  - ✅ Download schema JSON button
  - ✅ Optional API-key guard (`UI_API_KEY` env var) — §6 access restriction
- [x] **Section 6: Azure Infrastructure** — **INFRASTRUCTURE COMPLETE, deployment in progress**
  - ✅ Resource Group, Managed Identity, Key Vault, Storage (uploads + artifacts), ACR, ACA Environment all provisioned
  - ✅ Managed Identity wired: Storage Blob Data Contributor + Cognitive Services OpenAI User + Key Vault Secrets User
  - 🔵 Azure OpenAI resource + `gpt-4o` deployment — **not yet provisioned**
  - 🔵 Application Insights — **not yet provisioned**
  - 🔵 Docker images — built locally, **not yet pushed** to `mcpfactoryacr.azurecr.io`
  - 🔵 ACA apps `mcp-factory-pipeline` + `mcp-factory-ui` — containers created, **awaiting image + env vars**
- [x] **Sponsor Requirements (§6 checklist)**
  - ✅ Azure Cloud (compute, storage, networking, OpenAI) — provisioned
  - ✅ GitHub + GitHub Copilot — in use
  - ✅ VS Code — dev environment
  - ✅ .NET Aspire app host — `aspire/AppHost/Program.cs`
  - ✅ GitHub Codespaces — `.devcontainer/devcontainer.json`
  - ✅ Microsoft docs cited — References section in this README
  - ✅ Budget alert script — `scripts/setup_budget_alert.ps1` ($150/month cap)
  - ✅ FERPA compliance statement — below

**Approach:** A **Hybrid Discovery Engine** that intelligently routes any target file to the appropriate analyzers based on detected capabilities, producing a uniform MCP JSON contract that §4 consumes directly.

## Platform Requirements

This tool is designed around Windows. If you are on a Mac, see [docs/mac-compatibility.md](docs/mac-compatibility.md).

## Prerequisites

**Required (install manually on Windows 10/11):**
- **PowerShell** 5.1+ (built into Windows 10+)
- **Python** 3.8+ — Download from [python.org](https://www.python.org/downloads/)
  - Add Python to PATH during installation (checked by default)
- **Git** — Download from [git-scm.com](https://git-scm.com)


## Installation

**Prerequisites:** Git, Python 3.8+, and Visual Studio Build Tools (for `dumpbin.exe`).

```powershell
# Clone and run the demo
git clone https://github.com/evanking12/mcp-factory.git
cd mcp-factory
pip install -r requirements.txt
python scripts/demo_all_capabilities.py
```

**Troubleshooting:** If you have multiple Python versions installed and `python` points to Python 2.x, use:
```powershell
py -3 -m pip install -r requirements.txt
py -3 scripts/demo_all_capabilities.py
```

## Quick Start

### 1. Capabilities Demo ⚡ (The "It Works" Demo)

**What you'll see:**

`demo_all_capabilities.py` runs 29 live analyses across all supported source types and reports per-target results:

```text
MCP FACTORY — ALL CAPABILITIES DEMO
=====================================

[Section 1: Native PE]
  kernel32.dll     ...  exports_mcp.json      1491 invocables
  user32.dll       ...  exports_mcp.json      1037 invocables
  zstd.dll         ...  exports_mcp.json        16 invocables

[Section 2: .NET Assemblies]
  System.dll       ...  dotnet_methods_mcp.json  143 invocables
  mscorlib.dll     ...  dotnet_methods_mcp.json   48 invocables

[Section 3: COM / Type Library]
  shell32.dll           ...  com_objects_mcp.json        482 invocables
  oleaut32.dll          ...  com_objects_mcp.json         12 invocables
  stdole2.tlb           ...  com_objects_mcp.json         50 invocables

[Section 4: CLI Tools]
  cmd.exe               ...  cli_mcp.json                  8 invocables
  git.exe               ...  cli_mcp.json                 35 invocables

[Section 5: SQL]
  sample.sql            ...  sql_file_mcp.json            14 invocables
  sqlite3.dll           ...  exports_mcp.json             16 invocables

[Section 6: Scripting Languages]
  sample.py             ...  python_script_mcp.json        5 invocables
  sample.ps1            ...  powershell_script_mcp.json    4 invocables
  sample.sh             ...  shell_script_mcp.json         5 invocables
  sample.bat            ...  batch_script_mcp.json         4 invocables
  sample.vbs            ...  vbscript_mcp.json             4 invocables
  sample.rb             ...  ruby_script_mcp.json          4 invocables
  sample.php            ...  php_script_mcp.json           5 invocables

[Section 7: JavaScript / TypeScript]
  sample.js             ...  javascript_mcp.json           6 invocables
  sample.ts             ...  typescript_mcp.json           5 invocables

[Section 8: RPC Interfaces]
  lsass.exe             ...  rpc_mcp.json                  8 invocables

[Section 9: Legacy Protocols & Spec Formats]
  sample_openapi.yaml   ...  openapi_spec_mcp.json         9 invocables
  sample_jsonrpc.json   ...  jsonrpc_spec_mcp.json         5 invocables
  sample.wsdl           ...  wsdl_file_mcp.json            7 invocables
  sample.idl            ...  corba_idl_mcp.json           12 invocables
  sample.jndi           ...  jndi_config_mcp.json         12 invocables
  zstd.pdb              ...  pdb_file_mcp.json           871 invocables

[Section 10: Directory Scan (§2.a installed-instance)]
  scripts/  (dir)       ...  scripts_scan_mcp.json      1184 invocables

Summary: 29 succeeded  0 skipped  (29 total)
```

**Why this matters:** Every output — regardless of source type — produces the same JSON schema that §4 consumes. A PE export, a Python function, and a SQL stored procedure all look identical to the MCP generator.

**Confidence levels:**
- **guaranteed:** Explicit metadata (type annotations, doc comments + return type)
- **high:** Partial docs or exported with header match
- **medium:** Pattern-matched, best effort
- **low:** Minimal information (symbol name only)

### 2. Live App Demo ⚡ (The "It Controls Windows" Demo)

**Start a pre-built server and chat with it:**

```powershell
# Calculator (WinUI3/MSIX, 55 invocables — digit buttons, operators, scientific functions)
python mcp_factory.py --serve calculator-test2

# Notepad (classic Win32 — type, save, open, append)
python mcp_factory.py --serve notepad
```

Open [http://localhost:5000](http://localhost:5000) and try:
- *"Open the calculator and compute the square root of 144, then multiply by 7"*
- *"Open notepad, type a short poem, save it as poem.txt, then reopen it and append a title"*

The chat UI shows every tool call, its arguments, and the raw result from the live application window.

**Run the full pipeline on any binary:**

```powershell
# Discovery → selection TUI → server generation in one command
python mcp_factory.py --target C:\Windows\System32\notepad.exe --description "text editor"
```

### Analyze a Specific File or Installed Directory

```powershell
# Native DLL
python src/discovery/main.py --target "C:\Windows\System32\kernel32.dll" --out artifacts

# Script file
python src/discovery/main.py --target "path\to\service.py" --out artifacts

# OpenAPI / WSDL / IDL / JNDI / PDB — same command, format auto-detected
python src/discovery/main.py --target "api\openapi.yaml" --out artifacts

# Installed application directory (§2.a) — walks tree, analyses every recognized file
python src/discovery/main.py --target "C:\Program Files\MyApp\" --out artifacts
```

### Analyze Windows CLI Tools

```powershell
python src/discovery/cli_analyzer.py "C:\Windows\System32\ipconfig.exe"
```

Run the selection UI to review discovered tools and choose what the MCP server exposes:

```powershell
# Single file
python src/ui/select_invocables.py --target tests/fixtures/vcpkg_installed/x64-windows/bin/zstd.dll

# Installed directory (§2.a) — same flag, directory support is transparent
python src/ui/select_invocables.py --target tests/fixtures/scripts/

# With a free-text hint (§2.b) — highlights matching rows in the table
python src/ui/select_invocables.py --target zstd.dll --description "compress decompress streaming"

# From an already-generated discovery JSON (skip re-analysis)
python src/ui/select_invocables.py --input artifacts/discovery-output.json
```

The UI defaults **guaranteed + high confidence ON**, medium + low OFF (§3.b). Commands: `<n>` toggle row, `3-10` range, `g` guaranteed+high only, `m` toggle medium, `l` toggle low, `a`/`n` all/none, `f <text>` filter, `done` save → `artifacts/selected-invocables.json`.

## Repository Structure
```
mcp-factory/
├── mcp_factory.py                 # Single-command entry point (--target / --serve)
├── scripts/
│   ├── demo_all_capabilities.py   # Main demo — 29 targets, all source types (10 sections)
│   ├── demo_legacy_protocols.py   # Standalone 6-target suite for spec-gap analyzers
│   ├── validate_features.py       # Validation suite
│   └── analyze_json_anomalies.py  # Hygiene verification
├── src/discovery/                 # Sections 2-3: Discovery pipeline
│   ├── main.py                    # CLI orchestrator — single file OR directory walk (§2.a)
│   ├── classify.py                # File-type detection (22+ source types)
│   ├── exports.py                 # Native PE exports (pefile)
│   ├── pe_parse.py                # .NET reflection
│   ├── com_scan.py                # COM registry + TLB scanning
│   ├── cli_analyzer.py            # CLI argument extraction
│   ├── gui_analyzer.py            # GUI discovery — UIA button walk, menu enumeration, app-type detection
│   ├── rpc_scan.py                # RPC interface scanning
│   ├── sql_analyzer.py            # SQL stored procs, views, tables, triggers
│   ├── script_analyzer.py         # Python, PowerShell, Shell, Batch, VBScript, Ruby, PHP
│   ├── js_analyzer.py             # JavaScript + TypeScript
│   ├── openapi_analyzer.py        # OpenAPI 3.x / Swagger 2.x + JSON-RPC 2.0
│   ├── wsdl_analyzer.py           # SOAP / WSDL 1.1
│   ├── idl_analyzer.py            # CORBA IDL interfaces
│   ├── jndi_analyzer.py           # JNDI bindings (.properties, Spring XML)
│   ├── pdb_analyzer.py            # PDB debug symbols (dbghelp.dll)
│   └── schema.py                  # Unified Invocable schema → MCP JSON
├── src/ui/
│   └── select_invocables.py       # Interactive §3 selection UI (rich table, confidence filter)
├── src/generation/                # Section 4: MCP server generation
│   ├── section4_select_tools.py
│   └── section4_generate_server.py  # Flask server template + chat UI template
├── generated/                     # Pre-built servers (ready to --serve)
│   ├── calculator-test2/          # Calculator — 55 invocables, WinUI3/MSIX
│   └── notepad/                   # Notepad — classic Win32, type/save/open/append
├── tests/
│   └── fixtures/scripts/          # Sample files for all supported source types
│       ├── sample_openapi.yaml    # OpenAPI 3.0 fixture (9 operations)
│       ├── sample_jsonrpc.json    # JSON-RPC 2.0 fixture (5 methods)
│       ├── sample.wsdl            # WSDL 1.1 fixture (7 operations)
│       ├── sample.idl             # CORBA IDL fixture (12 methods)
│       └── sample.jndi            # JNDI fixture (12 bindings)
├── demo_output/unified/           # Generated demo artifacts (one sub-dir per target)
└── docs/
    ├── adr/                       # Architecture Decision Records (ADR-0001 → ADR-0008)
    ├── copilot-log/entries.md     # Session-by-session development log
    ├── sections-2-3.md            # §2-3 feature coverage reference (29/29 targets)
    └── schemas/                   # JSON schema contracts for Section 4
```

## Team Responsibilities

- **Sections 2-3 (Binary Analysis):** Evan King - DLL/EXE export discovery, header matching, tiered output
- **Section 4 (MCP Generation):** Layalie AbuOleim, Caden Spokas - JSON schema generation, tool definitions
- **Section 5 (Verification):** Thinh Nguyen - Interactive UI, LLM-based validation
- **Integration & Deployment:** Team effort - Azure deployment, CI/CD, documentation

## Section 4: MCP Server Generation

### Full pipeline (discovery → selection → server)

```powershell
# Run the entire pipeline on any target in one command
python mcp_factory.py --target C:\Windows\System32\calc.exe --description "calculator"

# Skip re-discovery — load an existing discovery JSON directly
python mcp_factory.py --input artifacts/discovery-output.json

# Generate the server without auto-launching it
python mcp_factory.py --target zstd.dll --skip-launch

# Suppress the browser auto-open (e.g. headless / CI)
python mcp_factory.py --serve notepad --no-browser
```

This runs discovery, opens the selection TUI, then generates and starts the server.

| Flag | Description |
|------|-------------|
| `--target FILE_OR_DIR` | Binary, script, or directory to analyse — runs full discovery |
| `--serve COMPONENT` | Skip pipeline entirely; start a pre-built server from `generated/` |
| `--input JSON` | Skip discovery; load an existing `discovery-output.json` directly |
| `--description TEXT` | Free-text hint that highlights matching rows in the selection TUI |
| `--no-browser` | Do not auto-open the browser after the server starts |
| `--skip-launch` | Stop after generation — do not start the server |

### Start a pre-built server

```powershell
python mcp_factory.py --serve notepad
python mcp_factory.py --serve calculator-test2
```

### Manual pipeline steps

```powershell
# 1. Discovery
python src/discovery/main.py --target <file> --out artifacts

# 2. Select invocables (interactive TUI)
python src/ui/select_invocables.py --target <file>
# Writes: artifacts/selected-invocables.json

# 3. Generate server
python src/generation/section4_generate_server.py
# Writes: generated/<name>/server.py + static/index.html

# 4. Run the server
cd generated/<name>
cp .env.example .env  # fill in OPENAI_API_KEY
python server.py
```

Verify with:
```powershell
curl http://localhost:5000/tools
# Open http://localhost:5000 in browser for chat UI
```


## Data Contract Stability (for Section 4)

Section 2-3 produces a stable JSON schema that Section 4 teams depend on:

- **Schema:** [docs/schemas/discovery-output.schema.json](docs/schemas/discovery-output.schema.json) - Formal JSON Schema
- **Versioning:** Breaking changes → v2.0. See [CHANGELOG.md](CHANGELOG.md)
- **For Section 4 teams:** Pin schema version in MCP generation to prevent drift

## Contributing

This is an active capstone project. For development setup and workflow guidelines, see [CONTRIBUTING.md](CONTRIBUTING.md).

## Documentation

| Document | Description |
|----------|-------------|
| [Project Description](docs/project_description.md) | Original sponsor requirements (Sections 1-7) |
| [Architecture](docs/architecture.md) | System design and component overview |
| [Sections 2-3 Details](docs/sections-2-3.md) | Binary discovery implementation |
| [Product Flow](docs/product-flow.md) | Full pipeline (Sections 2-5) |
| [Schemas](docs/schemas/) | JSON schema contracts for Section 4 |
| [ADRs](docs/adr/) | Architecture decision records |
| [Troubleshooting](docs/TROUBLESHOOTING.md) | Common issues and solutions |

---

**Sponsored by Microsoft** | Mentored by Microsoft Engineers  
_Last updated: March 4, 2026 — Section 4 demo-ready: Calculator + Notepad servers working end-to-end_

---

## FERPA Compliance Statement

MCP Factory is developed and operated in compliance with the Family Educational Rights and Privacy Act (FERPA) and all other applicable data-privacy regulations.

- **No student PII is collected or stored.** The system does not collect, process, or retain names, student IDs, email addresses, or any other personally identifiable information.
- **Uploaded binaries are ephemeral.** Files uploaded through the web UI are written to Azure Blob Storage solely for the duration of the analysis pipeline job. Blobs are stored under a randomized job ID; no filename-to-identity mapping is created. Blob lifecycle management policies delete uploaded files after 24 hours.
- **No conversation data is persisted.** Chat messages sent to Azure OpenAI through the `/api/chat` endpoint are not logged to persistent storage. Azure OpenAI does not store prompt/completion data by default when accessed via API.
- **Access is restricted to the project team.** Both Azure Container Apps are deployed with Microsoft Entra ID–backed Managed Identity authentication; no anonymous write access is permitted to storage or AI services. The UI endpoint can be further hardened with a shared API key (`UI_API_KEY` environment variable, see Gap #8 above).
- **Azure resources are scoped to the project subscription** (`abb10328-e7f1-4d4a-9067-c1967fd70429`) and are not shared with other courses or students.

Questions regarding data handling should be directed to the project sponsor contact.

---

## References — Microsoft Documentation

The following Microsoft Learn pages and official documentation were used in the design and development of this project:

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
