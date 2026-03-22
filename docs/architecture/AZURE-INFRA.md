# Azure Infrastructure Reference

> Migrated from docs/architecture.md (now deleted).
> This is the Azure resource map and blob registry for the pipeline.

---

## Azure Architecture

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
            AOAI[Azure OpenAI\nmcp-factory-openai\ngpt-4o · gpt-4-1 · gpt-4-1-mini · o4-mini]
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

---

## Blob Registry

Each job writes to the `artifacts` container under `{job_id}/`. The `uploads` container holds raw binaries.

| Blob key | Written by | Consumed by | Contains |
|----------|-----------|-------------|---------|
| `uploads/{job_id}/input{suffix}` | `/api/analyze` upload | explore worker | Raw binary (DLL/EXE) |
| `{job_id}/status.json` | API pipeline (all stages) | UI polling, session snapshot | Job status, explore_phase, gap_count, question_count |
| `{job_id}/mcp_schema_t0.json` | `generate.py` | Session diff, schema_evolution.json | Pre-explore baseline schema |
| `{job_id}/mcp_schema.json` | `generate.py` → patched by `_patch_invocable` | Chat as tool definitions, session snapshot | Enriched OpenAI function-calling schema |
| `{job_id}/invocables_map.json` | `generate.py` → enriched by explore | `executor.py`, backfill, gap resolution, chat | Full invocable registry with criticality, depends_on, param names |
| `{job_id}/vocab.json` | `explore_vocab.py` during probe, gap answers on refine | Chat system message, explore system message | id_formats, value_semantics, error_codes, notes, description |
| `{job_id}/findings.json` | `_save_finding` (append) / `_patch_finding` (update) | Chat system message, synthesis, backfill | Per-function probe results, working calls, status |
| `{job_id}/static_analysis.json` | `static_analysis.py` phase-0 | Session ZIP, explore system prompt hints | PE version, IAT capabilities, binary strings, Capstone sentinels |
| `{job_id}/api_reference.md` | `_synthesize` phase-6 | Backfill (LLM input) | Synthesized human-readable API doc — NOT in chat context |
| `{job_id}/behavioral_spec.py` | `_synthesize` phase-6 | Human reference only | Typed Python stub — NOT in chat context |
| `{job_id}/sentinel_calibration.json` | explore phase-1 | Diagnostic script, T-02 transition check | Calibrated sentinel code map |
| `{job_id}/gap_resolution_log.json` | `_attempt_gap_resolution` + mini-sessions | Session snapshot, diagnostic script | Per-function gap resolution attempts and outcomes |
| `{job_id}/harmonization_report.json` | finalize phase | Session snapshot | Function outcome consistency summary |
| `{job_id}/session-meta.json` | Pipeline (all phases) | `collect_session.py`, contract evaluation | Required contract file: job_id, component, phases, timestamps |
| `{job_id}/stage-index.json` | Pipeline (each stage exit) | `collect_session.py`, transition evaluator | Required contract file: per-stage status, artifacts, checks |
| `{job_id}/transition-index.json` | `cohesion.py` evaluator | `collect_session.py`, CI gate | Required contract file: T-01..T-16 status, severity, detail |
| `{job_id}/cohesion-report.json` | `cohesion.py` evaluator | `collect_session.py`, CI gate, DASHBOARD.md | Required contract file: hard_fail, pipeline_verdict, totals |

### What chat actually sees

Only these two blobs are injected into the chat system message (loaded once at turn 0):
1. `vocab.json` → `_vocab_block()` → vocab section in system prompt
2. `findings.json` → `_load_findings()` → findings section in system prompt

NOT loaded into chat: `api_reference.md`, `behavioral_spec.py`, `mcp_schema.json` (client sends tools in POST body), `static_analysis.json`.

---

## Container App URLs

| Service | URL |
|---------|-----|
| Pipeline API | `https://mcp-factory-pipeline.icycoast-8ddfa278.eastus.azurecontainerapps.io` |
| UI | `https://mcp-factory-ui.icycoast-8ddfa278.eastus.azurecontainerapps.io` |
