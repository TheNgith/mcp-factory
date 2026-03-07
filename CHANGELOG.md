# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.3.0] - 2026-03-07

### Added
- **GitHub Actions CI/CD** (`.github/workflows/ci-cd.yml`) — three-job pipeline: `test` (pytest on Python 3.10), `build` (Docker build + push to ACR with SHA tag), `deploy` (az containerapp update → smoke-test `/health`). Uses OIDC federation — no stored secrets. One-time setup instructions are in the workflow file header.
- **App Insights custom telemetry** (`api/main.py`) — OpenCensus `AzureExporter` + `Tracer` initialised alongside the existing `AzureLogHandler`. New `_ai_span()` context manager emits trace spans + structured `custom_dimensions` log events to Application Insights. Custom events: `discovery_complete` (file count, invocable count), `generate_complete` (tool count, component name), `chat_complete` (rounds, tool calls total, duration).
- **KV duplicate secrets cleanup** (`scripts/cleanup_kv_secrets.ps1`) — deletes `storage-account` and `appinsights-connection-string` (shadows of the canonical `azure-storage-account` and `appinsights-connection` secrets). Supports `--DryRun`. Soft-deletes then purges.

### Fixed
- **`aspire/AppHost/AppHost.csproj`** — corrected project structure to use `<IsAspireHost>true</IsAspireHost>` + `Sdk="Microsoft.NET.Sdk"` (matching the official `dotnet new aspire-apphost` template). The existing `<Sdk Name="Aspire.AppHost.Sdk">` / `<Sdk Name="Aspire.Hosting.Sdk">` top-level element was the wrong pattern for Aspire 8.2.2. Removed `Aspire.Hosting.Docker` (9.x only) and `Aspire.Hosting` (transitive). Removed `.WaitFor(pipeline)` (9.x only API).
- **Workload manifest band mismatch** — `dotnet workload install aspire` wrote to `sdk-manifests\8.0.100` but SDK 8.0.418 reads from `8.0.400`. `WorkloadManifest.json` / `.targets` promoted to `8.0.400` slot. `AppHost` now builds cleanly: `0 Warning(s)  0 Error(s)`.



### Fixed
- **`aspire/AppHost/Program.cs` — UI port mismatch** — `WithHttpEndpoint(port: 3000, targetPort: 3000)` was wrong; `Dockerfile.ui` runs uvicorn on 8080. Corrected to `port: 8080, targetPort: 8080`.
- **`aspire/AppHost/Program.cs` — missing App Insights parameter** — `APPLICATIONINSIGHTS_CONNECTION_STRING` was wired in ACA via `secretref:` but absent from the Aspire orchestration. Added as a `secret: true` parameter so local Docker runs also receive telemetry config.
- **`.gitignore` — .NET build outputs** — `aspire/**/bin/`, `aspire/**/obj/`, `aspire/**/.vs/` added so `dotnet build` artifacts do not dirty the repo.

### Changed
- Aspire `Program.cs` comments updated: corrected OpenAI endpoint example, clarified that `AZURE_CLIENT_ID` is ACA-only (locally `DefaultAzureCredential` uses `az login`), added explicit port mapping notes.



### Added
- **Full Azure deployment** — both ACA containers (`mcp-factory-pipeline`, `mcp-factory-ui`) live and publicly accessible. End-to-end flow (analyze → generate → chat) verified against `calc.exe`.
- **Azure OpenAI** — `mcp-factory-openai` resource provisioned; `gpt-4o` (2024-11-20, 10K TPM) deployment active and accessed via Managed Identity.
- **Application Insights** — `mcp-factory-insights` wired to both containers via `APPLICATIONINSIGHTS_CONNECTION_STRING` secret.
- **`_extract_invocables()` helper** (`api/main.py`) — normalises any discovery JSON shape (wrapped `{metadata, invocables, summary}` dict _or_ legacy flat array) to a clean list. Fixes the root cause of the "metadata / invocables / summary" fake-tool bug.
- **Multi-file output merge** (`_run_discovery`) — collects all `*_mcp.json` files produced by a single run (EXEs emit `_cli_mcp.json` + `_gui_mcp.json`), merges and de-duplicates by `name`. Fixes single-file truncation bug.
- **Preserved original filename** (`/api/analyze`) — uploaded file is written to `<tmp_dir>/<original_name>` instead of `tmpXXX_<suffix>`, so the discovery pipeline derives the correct base name (e.g. `calc` not `tmpXXX`).
- **`flattenInvocables` guard** (`api/main.py`) — defensive unwrap in the `/api/generate` and `/api/execute` paths to tolerate both wrapped and flat payloads from the UI.
- **`description` field fallback** (`/api/generate`) — resolve order is `doc → description → signature → name` so thin-schema invocables always produce a usable tool description.

### Changed
- ACA pipeline revision incremented to `mcp-factory-pipeline--0000003`.
- README Azure Resource Status table updated to ✅ Live for all resources.



### Added
- **Interactive invocable selection UI** — `src/ui/select_invocables.py`: rich terminal table with confidence-based defaults (`guaranteed`+`high` on, `medium`+`low` off), toggle/range/filter commands, description hint highlighting (Section 2.b), writes `selected-invocables.json` for Section 4 consumption
- **Hybrid binary detection** — selection UI automatically detects multi-output binaries (e.g. `shell32.dll` produces both native exports and COM interfaces), merges them into one unified list with a `Source` column, announces the merge clearly
- **`src/ui/` module** — new package for user-facing entry points

### Changed
- **Flat invocable schema** — `Invocable.to_dict()` in `schema.py` now emits the clean LLM-ready contract: `name`, `kind`, `confidence`, `description`, `return_type`, `parameters[]`, `execution`. Removed pipeline-internal wrappers: `tool_id`, `ordinal`, `rva`, `confidence_factors`, `signature{}`, `documentation{}`, `evidence{}`, `mcp{}`, `metadata{}`
- **`parameters` promoted to flat list** — was a string inside `signature.parameters`; now a top-level list of `{name, type, required, description}` matching OpenAI/Anthropic function-calling spec
- **`confidence` added `guaranteed` level** — four levels now: `guaranteed` > `high` > `medium` > `low`; scoring is factor-first (data measured before label set)
- **`section4_select_tools.py`** — removed `schema_version` from required-key validation; reads flat `description` field; drops `schema_version` from output
- **`docs/schemas/discovery-output.schema.json`** — rewritten to match flat schema; removed `schema_version`, `tier`, `evidence`, `confidence_factors`, `signature{}`, `documentation{}`; added `return_type`, flat `parameters[]`, `execution{}`, `guaranteed` confidence level

### Fixed
- `_get_execution_metadata()` in `schema.py`: `dotnet` and `com` source types had duplicate `"method"` dict keys — Python silently discarded the first, losing `"dotnet_reflection"` / `"com_automation"`. Fixed to use explicit `method`/`function_name`/`type_name` keys
- `cli` source type fell through to `method: "unknown"` — now correctly emits `method: "subprocess"` with `executable_path` and `arg_style`

## [1.0.0] - 2026-01-20

### Added
- **Direct PE parsing** - Native PE header export extraction (optional dumpbin fallback)
- **Forwarded export resolution** - Maps export chains to real targets
- **Digital signature extraction** - Identifies signed/unsigned binaries and publishers
- **Confidence scoring** - Transparent reasoning for export invocability (Low/Medium/High)
- **Structured logging** - Production-ready logging with error handling
- **GitHub Actions CI** - Automated tests on Python 3.8, 3.10, 3.11 (Windows)
- **Data contract schema** - [docs/schemas/v1.0.json](docs/schemas/v1.0.json) for Section 4 integration
- **Test suite** - 5 fixture tests validating PE parsing + header matching on zstd.dll + sqlite3.dll

### Changed
- Refactored monolithic `csv_script.py` into 8 modular files (schema, classify, pe_parse, exports, headers_scan, docs_scan, com_scan, main)
- Python version requirement: 3.8+ (was 3.6+, now enforced via pyproject.toml)

### Fixed
- PE header parsing handles forwarded exports correctly
- Export deduplication preserves ordinal data
- Signature extraction works on both signed and unsigned binaries

## [0.1.0] - 2026-01-19

### Added
- Initial discovery prototype (Sections 2-3)
- DLL export extraction via dumpbin
- Header file prototype matching
- Tiered CSV/Markdown output (5 levels: full → metadata only)
- PowerShell automation (run_fixtures.ps1, setup-dev.ps1)
- Test fixtures (zstd.dll: 187 exports, sqlite3.dll: 294 exports)

---

## Versioning Policy

### Breaking Changes (Major Version)
- Changes to the JSON schema structure (field removal, type change, required field addition)
- Python version requirement increases (e.g., 3.8 → 3.10)
- Output format changes that break Section 4 parsing

### Non-Breaking Changes (Minor Version)
- New optional fields in JSON output
- New command-line options
- Performance improvements
- New analyzer modules (as long as existing output remains valid)

### Patches (Patch Version)
- Bug fixes
- Documentation updates
- Internal refactoring

**For Section 4 Teams:** Always pin to a major version (e.g., v1.x.x) to ensure compatibility. Breaking changes will increment the major version number.
