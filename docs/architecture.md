# Architecture

## Azure Architecture Diagram

```mermaid
graph TB
    subgraph User["User / Browser"]
        B[Web Browser]
    end

    subgraph GitHub["GitHub"]
        GH[GitHub Actions CI/CD]
        GHR[Self-Hosted Windows Runner]
    end

    subgraph Azure["Azure — mcp-factory-rg (East US)"]
        subgraph Identity["Identity & Secrets"]
            MI[Managed Identity\nmcp-factory-identity]
            KV[Key Vault\nmcp-factory-kv\nguiBridgeUrl · guiBridgeSecret\nopenai-endpoint · storage-account\nappinsights-connection · clientId]
        end

        subgraph Containers["Azure Container Apps — mcp-factory-env"]
            UI[UI Container App\nmcp-factory-ui\nFastAPI SPA]
            PIPE[Pipeline Container App\nmcp-factory-pipeline\nFastAPI + discovery engine]
        end

        subgraph Storage["Storage"]
            BLOB[Blob Storage\nmcpfactorystore\nuploads · artifacts]
            QUEUE[Storage Queue\nanalysis-jobs]
        end

        subgraph AI["AI Services"]
            AOAI[Azure OpenAI\nmcp-factory-openai\ngpt-4o deployment]
            SEARCH[Azure AI Search\nmcpfactory-search\nfree tier]
        end

        subgraph Observability["Observability"]
            AI2[Application Insights\nmcp-factory-insights]
            LOG[Log Analytics\nmcp-factory-logs]
        end

        ACR[Container Registry\nmcpfactoryacr]

        subgraph VNet["Virtual Network — mcpfactory-vnet 10.0.0.0/16"]
            ACA_SUBNET["aca-infra subnet\n10.0.0.0/23\n(ACA outbound)"]
            subgraph VMSubnet["vm subnet — 10.0.2.0/24"]
                VM[Windows 11 Runner VM\nmcpfactory-runner-vm\n10.0.2.4\nGUI Bridge :8090\npywinauto · COM · Registry]
                NSG[NSG: allow 8090\nfrom aca-infra only]
            end
        end
    end

    B -->|HTTPS| UI
    UI -->|proxy /api/*| PIPE
    PIPE -->|secretref| KV
    KV -->|secrets| MI
    MI -->|RBAC| BLOB
    MI -->|RBAC| QUEUE
    MI -->|RBAC| AOAI
    MI -->|RBAC| SEARCH
    MI -->|RBAC| ACR
    PIPE -->|upload / download| BLOB
    PIPE -->|enqueue job| QUEUE
    QUEUE -->|worker poll| PIPE
    PIPE -->|gpt-4o chat + tools| AOAI
    PIPE -->|tool retrieval| SEARCH
    PIPE -->|telemetry| AI2
    UI -->|telemetry| AI2
    AI2 --> LOG
    GH -->|push image| ACR
    ACR -->|pull image| PIPE
    ACR -->|pull image| UI
    GHR -->|CI: GUI tests| VM
    ACA_SUBNET -->|private HTTP :8090| VM
    NSG -.->|blocks internet| VM
```

## Goal
Generate an MCP server/tool schema from existing binaries (DLL/EXE/CLI/repo) by discovering invocable surfaces, normalizing them, enriching metadata, and generating deployable MCP components.

## Pipeline (high level)
1. Acquire target (file upload or installed path)
2. **Hybrid Classification & Routing**
   - Detects all capabilities: Native Exports (`.dll`), COM Server (`HKCR`), .NET Assembly (`CLR`), CLI Tool (`.exe`)
   - Supports multi-paradigm files (e.g., `shell32.dll` = COM + Native)
3. Discover invocable surfaces (exports/help/registry/etc.)
4. **Score confidence** in each surface (4-factor analysis; label derived after data is measured)
5. **Strict Artifact Generation**
   - Output normalized MCP JSON (`*_mcp.json`) using flat invocable contract
   - Suppress empty/invalid outputs ("Silence is Golden")
6. User selects subset -> `selection.json`
7. Generate MCP tools/server + deploy verification instance
8. Verify via chat UI + downloadable outputs

## Components

### Discovery Layer
- **Hybrid Routing Engine** (Section 2-3)
  - `main.py`: Capabilities-based router (Fall-through logic)
  - Handles `hybrid` files (e.g., `notepad.exe` as CLI + COM, `shell32.dll` as Native + COM)
  
- **DLL Export Analysis**
  - `pe_parse.py`: Pure Python import/export extraction (via `pefile`)
  - `exports.py`: Demangling, forwarding, deduplication
  - `headers_scan.py`: Prototype extraction from C/C++ headers (98% match rate)
  - Outputs: MCP JSON (Tier 2-4), Metadata (Tier 5)

- **COM & .NET Analysis**
  - `com_scan.py`: Recursively scans registry for CLSIDs/TypeLibs
  - .NET reflection (System.Reflection) for managed assemblies (future)
  
- **CLI Help Scraper** (future)
  - Parse --help / -h output
  - Extract subcommands and parameters

### Quality & Confidence Layer
- **Confidence Scoring** (new, 2026-01-21)
  - `score_confidence(export, matches, is_signed, forwarded) -> (level, reasons)`
  - 6 factors: header_match, doc_comment, signature_complete, parameter_count, return_type, non_forwarded
  - Tiers: HIGH (≥6), MEDIUM (≥4), LOW (<4)

- **Strict Artifact Hygiene** (new, 2026-01-27)
  - **Noise Suppression**: Files with 0 found features generate NO output
  - **Redundancy Removal**: Deprecated legacy `.json`, standardized on `*_mcp.json`
  - Ensures downstream tools never encounter "Ghost Tools"

### Setup & Automation Layer
- **Boot Checks** (new, 2026-01-21)
  - Pre-flight validation: repo root, Python 3.8+, Git, PowerShell 5.1+
  - Indicators: [+] (pass, green), [-] (fail, red)
  - Fail-fast on missing prerequisites
  
- **Frictionless Deployment** (scripts/)
  - `scripts/setup-dev.ps1`: One-command setup with auto-detection
  - `scripts/run_fixtures.ps1`: Robust path resolution (3-method fallback), vcpkg bootstrap
  - Auto-detects dumpbin, Visual Studio, vcpkg
  - Tested on clean machines (no pre-installed tools)
  
### Schema & Output
- `schema.py`: Invocable dataclass (name, ordinal, signature, confidence, doc)
  - Writers: CSV, JSON, Markdown
  - Supports 5-tier output (exports only → metadata-rich)

### Integration Points
- **Section 4 (MCP Generation)**: Consumes confidence metadata
  - HIGH confidence → auto-generate MCP tool definitions
  - MEDIUM confidence → auto-generate + flag for review
  - LOW confidence → skip or require manual spec
  
- **Future LLM Integration**: Confidence metadata helps Claude/GPT prioritize trustworthy exports
- **Section 5 (UI)**: Displays confidence breakdown, allows filtering by confidence tier

## Design Decisions

| Decision | Rationale | Reference |
|----------|-----------|-----------|
| Modular 8-module architecture | Enable team parallelization, testability, feature expansion | ADR-0002 |
| Confidence scoring with color | Transparency + quality signal + Section 4 prioritization | ADR-0003 |
| Frictionless one-command setup | Reproducibility, professional signal, user empathy | ADR-0003 |
| Header matching for signatures | 98% accuracy enables high-confidence auto-wrapping | Iteration 1 results |
| 5-tier output model | Gradual enrichment; supports various downstream needs | MVP analysis |

## Open Questions
- How to handle confidence in .NET reflection (Section 3)?
- Should users override confidence scores?
- Integration with external documentation sources?
- Semantic analysis (infer safety from function names)?

