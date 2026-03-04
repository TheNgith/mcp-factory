
## 2026-01-19 — Fixture Harness + Robust Parser

**Task/Issue:** Complete discovery pipeline with automated testing, dumpbin auto-detection, robust export parsing, header matching, and tiered outputs.

**Copilot Prompts Used:**
- "Create a Python 3 script (csv_script.py) that runs dumpbin /exports on a DLL and generates tiered CSV/Markdown reports. Requirements: (1) Robust regex parser for dumpbin format with ordinal, hint, RVA, function name, and optional forwarding (handle both '00077410' and '--------' RVA); deduplicate by name; debug output if zero exports. (2) Header file scanning: recursively find .h/.hpp files, parse C/C++ function prototypes using regex, extract return types, parameters, and Doxygen-style doc comments (/// and /**); handle comment spans and nested parentheses. (3) Tiered analysis: Tier 1 (exports+headers+docs), Tier 2 (exports+headers), Tier 3 (exports+demangle), Tier 4 (exports only), Tier 5 (metadata). (4) CSV columns: Function, Ordinal, Hint, RVA, ForwardedTo, ReturnType, Parameters, Signature, DocComment, HeaderFile, Line, Demangled, DocFiles. (5) CLI args: --dll, --headers, --docs, --out, --tag, --exports-raw, --dumpbin, --undname, --no-demangle. (6) Auto-detect dumpbin.exe from VS 2022 locations if not specified. (7) Support manifest mode vcpkg."
- "Create PowerShell script (run_fixtures.ps1) that: accepts -VcpkgExe, -Triplet (default x64-windows), -OutDir (default artifacts), -DumpbinExe (optional, auto-detect), -ScriptPath; runs vcpkg install from tests/fixtures/ using manifest mode; locates DLLs in tests/fixtures/vcpkg_installed/<triplet>/bin/; runs Python script on zstd + sqlite3 with --headers and --tag; verifies outputs exist; prints sanity check (count ZSTD_ and sqlite3_ entries)."
- "Create tests/fixtures/vcpkg.json with zstd and sqlite3 dependencies in manifest mode format."

**Output Accepted:**
- Complete csv_script.py structure with all 5 tiers
- Header file parsing with regex for prototypes
- Doxygen comment extraction logic
- run_fixtures.ps1 with vcpkg manifest mode workflow
- Auto-detection for dumpbin in VS 2022 paths
- CSV schema with 13 columns including Hint, RVA, ForwardedTo
- vcpkg.json manifest with zstd and sqlite3

**Manual Changes:**
1. **Fixed dumpbin detection bug:** Original check_tool_available() was called even when --dumpbin was a full path. Changed to: if args.dumpbin == "dumpbin", use PATH check; else validate Path(args.dumpbin).exists().
2. **Fixed vcpkg manifest mode paths:** Changed from Join-Path $VcpkgRoot "installed\\$Triplet" to Join-Path $ManifestRoot "vcpkg_installed\\$Triplet" (manifest mode installs locally, not to vcpkg root).
3. **Rewrote export parser regex:** Updated from r"^\s*(\d+)\s+([0-9A-Fa-f]+)\s+([0-9A-Fa-f]+)\s+(\S+)\s*$" to r"^\s*(\d+)\s+([0-9A-Fa-f]+)\s+([0-9A-Fa-f]{8}|-------)\s+([^\s=]+)(?:\s*=\s*(\S+))?" to handle forwarded exports and variable-length fields.
4. **Added debug output:** Print first 50 lines of dumpbin output to stderr if zero exports parsed; print "Debug - Command argv: [...]" to show exact dumpbin invocation.
5. **Updated .gitignore:** Added __pycache__/, *.log, organized into sections with comments.
6. **Removed duplicate directories:** Deleted src/tests/scripts/ (accidental duplicate).
7. **README restructure:** Added Prerequisites section, "What Success Looks Like" with exact filenames and sizes, Troubleshooting section with 3 common issues, clearer Quick Start with numbered steps.

**Result:**
```powershell
.\scripts\run_fixtures.ps1 -VcpkgExe "$env:USERPROFILE\Downloads\vcpkg\vcpkg.exe"
```

Expected outputs in `artifacts/`:
- `zstd_tier2_api_zstd_fixture.csv` (119 KB, 187 exports, 184 matched to headers)
- `zstd_tier4_api_zstd_fixture.csv` (17 KB, basic exports)
- `zstd_tiers_zstd_fixture.md` (tier summary)
- `zstd_exports_raw.txt` (14 KB, raw dumpbin)
- `sqlite3_tier2_api_sqlite3_fixture.csv` (89 KB, 294 exports, 282 matched to headers)
- `sqlite3_tier4_api_sqlite3_fixture.csv` (25 KB, basic exports)
- `sqlite3_tiers_sqlite3_fixture.md` (tier summary)
- `sqlite3_exports_raw.txt` (21 KB, raw dumpbin)

**Notes:**
- Manifest mode vcpkg creates vcpkg_installed/ in the manifest directory, not in vcpkg root
- dumpbin auto-detection searches VS 2022 Community, Professional, Enterprise, BuildTools
- Forwarded exports (RVA = "--------") are captured in ForwardedTo column
- Header matching success rate: ~98% for zstd, ~96% for sqlite3

**References:**
- vcpkg manifest mode: https://github.com/microsoft/vcpkg/blob/master/docs/users/manifests.md
- dumpbin reference: https://learn.microsoft.com/en-us/cpp/build/reference/dumpbin-reference

---


## 2026-01-19 — Initial Scaffolding

**Task/Issue:** Repo scaffolding + Iteration 1 discovery prototype (dumpbin exports → CSV)

**Context:** src/discovery/, initial MVP for DLL export inventory

**Prompt:**
“Build a Python 3 ‘DLL discovery’ tool that takes a Windows DLL and produces a human-readable API artifact suitable for LLM ingestion. Step 1: run dumpbin /exports (or accept --exports-raw) and save raw output + parse a deduped export list (function name + ordinal). Step 2 (when --headers is provided): recursively scan header files to match each export to a prototype, extracting return type, parameters, and nearest doc comment block above the declaration; include source header path + line number. Step 3: write outputs to --out (default next to script): *_exports_raw.txt, *_api.csv (one row per function with Signature + cleaned doc one-liner), and *_api.md (readable per-function report). Add --dumpbin and --undname support (best-effort demangling), robust error handling, and ensure it works in the VS x64 Native Tools prompt. Include an example command for libzstd.dll in the README.”

Copilot output used:

CLI argument parsing (argparse) with --dll, --out, --dumpbin, --exports-raw

subprocess.run() wrapper to invoke dumpbin and capture stdout/stderr

Regex-based parser for dumpbin /exports table lines (ordinal + name extraction)

CSV writer scaffold with stable column order

Writing raw dumpbin output to *_exports_raw.txt for provenance

Manual edits:

Result: Generated example CSV artifact for libzstd; documented run command in README/lab-notes

Notes:

Reference: Visual Studio “x64 Native Tools Command Prompt” requirement for dumpbin.exe availability

Reference: Microsoft Learn/VS docs for VC tools environment (dumpbin/undname availability)

---

## 2026-01-20 — 8-Module Refactoring + CLI Orchestration

**Task/Issue:** Refactor monolithic csv_script.py (883 lines) into 8 modular, testable components with unified CLI interface.

**Copilot Prompts Used:**
- "Create schema.py with Invocable dataclass, ExportedFunc, and CSV/JSON/Markdown writers. Include confidence scoring, publisher detection, and forwarding resolution. Column order: function, ordinal, hint, rva, confidence, is_signed, publisher, is_forwarded, return_type, parameters, signature, doc_comment, header_file, line, demangled, doc_files."
- "Create classify.py: PE/NET file type detection with architecture detection (x86/x64/ARM64). Output: PEType enum (EXE, DLL, SYS, etc.) and architecture (UNKNOWN, X86, X64, ARM64)."
- "Create pe_parse.py: dumpbin /exports parser with robust regex for ordinal, hint, RVA (handle '--------' forwarded), function name, and optional forwarding target. Include retry logic with explicit encoding if parsing fails."
- "Create exports.py: C++ demangling using undname.exe (if available) or fallback to unmunged names. Handle forwarded exports (resolve RVA to actual function). Deduplicate by function name."
- "Create headers_scan.py from csv_script.py lines 214-455: recursively scan headers, extract prototypes with return types and parameters, parse Doxygen comments (/// and /* */)."
- "Create com_scan.py: stubs for COM type library scanning (TLB/registry). Support .NET reflection stubs."
- "Create docs_scan.py: parse documentation files and correlate with exports."
- "Create complete main.py orchestrator: single CLI entry point accepting --dll, --exe, --headers, --docs, --out, --tag. Implement 5-tier pipeline (Tier 1: all metadata, Tier 5: minimal). Output CSV, JSON, and Markdown formats. Color-coded logging."

**Output Accepted:**
- 8 separate Python modules, each with single responsibility
- schema.py with stable ExportedFunc dataclass
- CLI parsing with argparse
- 5-tier output logic
- CSV/JSON/Markdown writers
- Auto-detection for tools (dumpbin, undname)

**Manual Changes:**
1. **Unified imports structure:** Added src/discovery/__init__.py to make package importable
2. **Standardized logging:** All modules use logging.getLogger(__name__), color-coded console output
3. **Error handling:** Try-catch wrappers for subprocess calls (dumpbin, undname not always available)
4. **CSV stability:** Locked column order to prevent drift during development
5. **Tier logic:** Tier 1 (max detail) → Tier 5 (minimal) implemented as filter on ExportedFunc fields

**Result:**
```powershell
python src/discovery/main.py --dll "C:\path\to\library.dll" --headers "C:\path\to\include" --out "./output"
```

Outputs:
- `library_tier1_api.csv` (full: 16 columns)
- `library_tier2_api.csv` (headers matched: 14 columns)
- `library_tier4_api.csv` (minimal: 8 columns)
- `library_tiers.md` (summary)
- `library_exports_raw.txt` (provenance)

**Notes:**
- Each module can be unit-tested independently
- ExportedFunc dataclass is the data contract (immutable)
- Tier logic applies column filtering, not row filtering (all exports shown at all tiers)
- Color codes: ANSI 32 (green), 33 (yellow), 31 (red) for HIGH/MEDIUM/LOW confidence

**Files Created:**
- src/discovery/schema.py (180 lines)
- src/discovery/classify.py (80 lines)
- src/discovery/pe_parse.py (120 lines)
- src/discovery/exports.py (95 lines)
- src/discovery/headers_scan.py (220 lines)
- src/discovery/com_scan.py (40 lines, stubs)
- src/discovery/docs_scan.py (35 lines, stubs)
- src/discovery/main.py (150 lines)

**Modules Created:**
1. **schema.py** (80 lines) — Invocable dataclass, ExportedFunc, MatchInfo; write_csv(), write_json(), write_markdown(), write_tier_summary()
2. **classify.py** (85 lines) — FileType enum, classify_file(), get_architecture() for x86/x64/ARM64
3. **pe_parse.py** (100 lines) — EXPORT_LINE_RE regex, run_dumpbin(), parse_dumpbin_exports(), get_exports_from_dumpbin()
4. **exports.py** (110 lines) — demangle_with_undname(), deduplicate_exports(), resolve_forwarders() stub, classify_export_safety() stub
5. **headers_scan.py** (260 lines) — iter_header_files(), build_comment_spans(), extract_doc_comment_above(), find_prototype_in_header(), scan_headers()
6. **docs_scan.py** (45 lines) — scan_docs_for_exports() for .md/.txt/.rst/.adoc files
7. **com_scan.py** (50 lines) — Stubs: scan_com_registry(), parse_type_library(), enumerate_idispatch()
8. **main.py** (300 lines) — ArgumentParser, pipeline orchestration, 5-tier output (CSV, JSON, Markdown)

**Output Parity Verified:**
- zstd.dll: 187 exports, 184 matched to headers (98%)
- sqlite3.dll: 294 exports, 282 matched to headers (96%)
- CSV format 100% identical to baseline


**Result:**
1,030 lines of modular, testable code replacing 883-line monolith. Each module has single responsibility. Zero breaking changes.

**References:**
- ADR-0002: Modular Analyzer Architecture


---

## 2026-01-20 — Production Polish + Developer Experience

**Task:** Prepare codebase for Microsoft review and team onboarding. Add error handling, comprehensive docstrings, logging, automated setup, and basic tests.

**Copilot Output Accepted:**
- setup-dev.ps1: One-command dev environment validation (checks Python, VS Build Tools, Git; bootstraps fixtures; runs smoke test)
- test_fixtures.py: 5 pytest-style tests validating zstd/sqlite3 fixture analysis works correctly
- Logging integration in main.py: Replaced 10 print statements with logging module
- Enhanced docstrings: PE parsing, export deduplication, signature extraction functions


**Files Modified:**
- scripts/run_fixtures.ps1: +8 lines (error handling)
- scripts/setup-dev.ps1: NEW (60 lines, dev environment setup)
- src/discovery/main.py: +8 lines (logging imports + setup + 6 replacements)
- tests/test_fixtures.py: NEW (180 lines, fixture validation tests)
- docs/copilot-log/entries.md: This entry



**Verified:**
- zstd.dll: 187 exports, 184 matched (98.4%)
- sqlite3.dll: 294 exports, 282 matched (95.9%)
- All 5 tiers generating correctly
- Logging output clean and structured
- Setup script validates all prerequisites

**Next Steps:**
- Commit and push (ready for Monday Microsoft conversation)
- Week 2: Begin .NET reflection analysis (Section 2 item 2)

---

## 2026-01-21 — Confidence Scoring + Frictionless Setup (12 commits)

**Task:** Implement human-readable confidence analysis with color-coded output. Fix path resolution bugs in setup scripts. Add professional boot checks. Achieve one-command reproducible setup on clean Windows machines.

**Implementation Details:**

1. **Confidence Scoring** (commit aff6871, a1fec1e)
   - `score_confidence(export, matches, is_signed, forwarded_resolved) -> tuple(confidence_level, reasons)`
   - 6 factors: header_match, doc_comment, signature_complete, parameter_count, return_type, non_forwarded
   - Thresholds: HIGH (>=6 factors), MEDIUM (>=4 factors), LOW (<4 factors)
   - `generate_confidence_summary()` prints color-coded breakdown (RED for LOW, YELLOW for MEDIUM, GREEN for HIGH)
   - Sample outputs per confidence level + improvement suggestions

2. **Color-Coded Terminal Output**
   - ANSI codes: RED (\033[91m), YELLOW (\033[93m), GREEN (\033[92m)
   - Display order: LOW -> MEDIUM -> HIGH (progression visualization)
   - Example: LOW (RED), MEDIUM (YELLOW), HIGH (GREEN)

3. **Path Resolution Fixes** (commits 3a3c320, 5f73dc3, d5b25c6, e2b39af)
   - **Issue:** setup-dev.ps1 -> run_fixtures.ps1 invocation via `&` operator emptied $PSCommandPath
   - **Solution:** 3-fallback chain in run_fixtures.ps1:
     - Method 1: $PSCommandPath (standard invocation)
     - Method 2: $MyInvocation.MyCommand.Path (& operator invocation)
     - Method 3: Get-Location (last resort)
   - Explicit checks before Split-Path to prevent "empty string" errors
   - Validated on 5+ fresh clone tests

4. **Boot Checks Pattern** (commit e2b39af)
   - Pre-flight validation: repo root, Python 3.8+, Git, PowerShell 5.1+
   - ASCII indicators: [+] for pass (green), [-] for fail (red)
   - "MCP Factory Development Setup" banner with professional styling

5. **Encoding Fix** (commit 6eef026)
   - **Issue:** Unicode checkmarks (✓/✗) corrupted during file creation
   - **Solution:** Replaced with ASCII [+] and [-] (Windows terminal compatible)

**Test Results:**
- zstd.dll: 187 exports (8 HIGH, 176 MEDIUM, 3 LOW) = 98.4% header match
- sqlite3.dll: 294 exports (8 HIGH, 274 MEDIUM, 12 LOW) = 95.9% header match
- Setup time: 30-45 seconds on fresh machines
- Tested on 2+ Windows machines without pre-installed vcpkg ✓

**Files Modified:**
- `scripts/setup-dev.ps1`: Rewritten (100 lines) with boot checks + path detection
- `scripts/run_fixtures.ps1`: +25 lines (path resolution + filtering)
- `src/discovery/main.py`: +35 lines (confidence scoring, color codes)
- `README.md`: Clarity improvements + confidence examples

**Strategic Value:**
- Frictionless one-command deployment (any Windows machine)
- Reproducible on clean systems (proven on 2+ machines)
- Confidence analysis signals production-quality thinking
- Unblocks Section 4 with standardized, scored exports


---

## 2026-01-22 — Experiments Folder: Advanced Research Spikes

**Task:** Establish experiments/ folder for WIP features ahead of main branch. Implement CLI analyzer, debug suite, string extractor, and comprehensive testing infrastructure.

**Context:** experiments/ serves as research sandbox for features not yet merged to src/discovery/. Maintains feature parity tracking via FEATURES_AHEAD.md and detailed roadmap. Origin differences documented to show progression from baseline discovery to advanced multi-surface extraction.

**Copilot Prompts Used:**
- "Create cli_analyzer.py that parses --help, /?, -h, and /help output from Windows EXE files. Extract flags, options, positional arguments, and subcommands. Track evidence (which help format succeeded). Test on git.exe, ipconfig.exe, tasklist.exe, robocopy.exe."
- "Create debug_suite.py: execution gatekeeper with 9 validation breakpoints. Show PASS/WARN/SKIP/ERROR/CRITICAL status for each module. Provide evidence ledger (every claim cites source). Include timing analysis and error summary with actionable recommendations."
- "Create string_extractor.py: fallback mining when exports unavailable. Extract function-like strings from binary. Detect Windows API patterns (Create*, Get*, Set*). Handle ASCII and UTF-16LE. Rank confidence: HIGH (API prefix), MEDIUM (PascalCase), LOW (weak pattern)."
- "Create test_cli_analyzer.py: comprehensive test harness for CLI analyzer. Test on multiple Windows tools, generate JSON + Markdown results, validate argument extraction accuracy."
- "Create FEATURES_AHEAD.md comparing experiments/ features to src/discovery/. Include: feature status (✅ Working, 🔶 Planned), location, test results, integration readiness, module parity table."
- "Create ROADMAP.md with 10-tier feature roadmap (Tier 1: Core Infrastructure → Tier 10: Advanced). Map each tier to Section 2 or Section 3 requirements. Include evidence-first philosophy, current status, next steps with phased checkboxes."

**Output Accepted:**
- cli_analyzer.py (215 lines): Parses 4 help formats, extracts arguments/subcommands
- debug_suite.py (290 lines): 9-breakpoint validation, evidence ledger, timing analysis
- string_extractor.py (180 lines): Binary string mining, Windows API pattern detection
- test_cli_analyzer.py (160 lines): Automated test harness for CLI analyzer
- FEATURES_AHEAD.md: Feature comparison table, readiness assessment, integration status
- ROADMAP.md: 10-tier roadmap with Section 2-3 progress tracker, phased next steps


**Test Results:**
- CLI Analyzer: 7 Windows tools tested (git, ipconfig, tasklist, robocopy, sfc, whoami, systeminfo)
  - 76 total arguments extracted
  - 35 subcommands found
  - 100% success rate
- Debug Suite: Validated on kernel32.dll (1481 exports), git.exe, ipconfig.exe, tasklist.exe
  - 0 critical failures, 1 expected warning (SDK headers)
- String Extractor: Tested as fallback on binaries with missing exports
  - Detects Windows API patterns (Create*, Get*, Set*, etc.)
  - Handles ASCII + UTF-16LE encoding


**Origin Differences (FEATURES_AHEAD.md tracking):**
- **Origin (src/discovery/):** DLL export extraction, header matching (98% accuracy), confidence scoring, tiered CSV/Markdown output
- **Experiments Ahead:** + CLI argument extraction, debug suite validation, string mining fallback, comprehensive test harness
- **Module Parity:** classify.py, pe_parse.py, exports.py, headers_scan.py identical between src/ and experiments/
- **Integration Status:** cli_analyzer.py and string_extractor.py ready to merge (no breaking changes), debug_suite.py usable immediately as validation tool

**Strategic Value:**
- ✅ Sandbox for advanced features without destabilizing main pipeline
- ✅ Feature parity tracking prevents drift
- ✅ Evidence-first philosophy maintained (10-tier roadmap explicitly maps to sponsor requirements)
- ✅ Demonstrates research depth (CLI extraction, debug validation, string mining)
- ✅ Clear integration path (FEATURES_AHEAD.md shows what's ready to merge)


## 2026-01-27  Validation Suite Noise Reduction & Legacy Cleanup

**Task/Issue:**
The validation suite (`scripts/validate_features.py` and `comprehensive_validation_safe.ps1`) was achieving high accuracy but producing generated artifacts that contained "Noise" (empty COM reports for non-COM files like `kernel32.dll`) and "Redundancy" (duplicate legacy JSON files for .NET assemblies). We needed to enforce strict artifact hygiene to prevent downstream ingestion of invalid or empty data.

**Copilot Prompts Used:**
- "run comprehensive_validation_safe.ps1... why is this... medium confidence?" (Initial diagnosis of validation mismatch)
- "do we have other mismatches... realistically scan all those files?" (Led to `main.py` routing review)
- "go through each .json file and try to see if there are any anomalies" (Led to creation of `scripts/analyze_json_anomalies.py`)
- "do both of these solutions" (instruction to modify `main.py` to fix Noise and Redundancy)
- "add demo_output to git ignore"

**Output Accepted:**
- `scripts/analyze_json_anomalies.py`: A new utility to audit the `validation_output/` directory for schema compliance and empty files.
- Refactored `src/discovery/main.py`: Logic changes to suppress empty COM JSON generation and remove legacy `.json` dumps.
- `scripts/comprehensive_validation_safe.ps1`: Updated validation logic to use `get_exports_from_dumpbin`, fixing the "Medium Confidence" issue with `shell32.dll`.

**Manual Changes:**
1.  **Modified `src/discovery/main.py` (Validation Routing):**
    - Implemented "Fall-through" logic: files classified as `COM_OBJECT` (like `shell32.dll`) now *also* undergo Native Export analysis.
    - Updated `PE_EXE` handling to explicitly check for COM registration (fixing "Siloed" analysis).
2.  **Modified `src/discovery/main.py` (Artifact Cleanup):**
    - **Legacy Removal:** Deleted the code block in `analyze_dotnet_assembly` that wrote `_dotnet_methods.json`. Now only `_mcp.json` is generated.
    - **Noise Suppression:** Added a check `if not invocables: return 0` in `analyze_com_object`. The pipeline now silently skips generating COM reports for files with 0 registered objects (e.g., CLI tools, pure kernels).
3.  **Deleted Ad-Hoc Scripts:** Removed `check_confidence.py`, `check_lsass_confidence.py`, `quick_test_prefix.py`, and `test_library_prefix.py` to clean the workspace.
4.  **Gitignore:** Added `demo_output/` to `.gitignore`.

**Result:**
- **Validation Suite:** `python scripts/validate_features.py` now passes **11/11** tests across all 5 categories (Native, .NET, COM, CLI, RPC).
- **Artifact Quality:** `python scripts/analyze_json_anomalies.py` reports **"No anomalies found"**. The `validation_output/` directory contains exactly one valid MCP JSON per valid feature, with no 0-byte or empty-array "ghost" files.

**Notes:**
- **Hybrid Analysis:** The system now correctly identifies files that define multiple interfaces. `shell32.dll` is correctly identified as having *both* COM objects and Native Exports, generating two distinct MCP artifacts.
- **Strict Mode:** The pipeline effectively runs in "Strict Mode" nowif a feature isn't found, no file is created. This differs from the previous behavior of creating empty "placeholder" files.


## 2026-01-29  Architecture Shift: Pure Python Extraction (pefile)

**Task/Issue:** Remove dependency on Visual Studio Build Tools (dumpbin.exe) to allow the CLI to run on any OS (Mac/Linux) and faster on Windows.

**Copilot Prompts Used:**
- "Refactor `pe_parse.py` to replace `dumpbin.exe` dependency with `pefile`. iterate through the Export Directory Table directly to extract function names, ordinals, and RVAs. Handle forwarded exports and ordinal-only functions without spawning a subprocess."
- "Update `import_analyzer.py` to use `pefile` for Import Table analysis. Scan imported DLLs to detect capabilities (e.g. `Ole32.dll` -> COM, `Rpcrt4.dll` -> RPC) using pure Python logic."
- "Make the discovery pipeline cross-platform (Mac/Linux compatible). Update `requirements.txt` to make `pywin32` generic or Windows-only, and wrap COM/TLB analysis in `tlb_analyzer.py` with platform checks to prevent crashes on non-Windows systems."

**Output Accepted:**
- Replaced dumpbin.exe dependency with pefile library (pure Python)
- Rewrote pe_parse.py to parse Export directory using pefile
- Rewrote import_analyzer.py to parse Import directory using pefile
- Updated main.py pipeline to prefer pefile path
- Updated 
equirements.txt to include pefile and conditional pywin32
- Updated 	lb_analyzer.py to handle missing pythoncom gracefully on non-Windows

**Manual Changes:**
- Fixed docstring quote escaping issue in pe_parse.py and import_analyzer.py using ix_quotes.py
- Moved ix_quotes.py to src/discovery/
- Added platform check for pywin32 in requirements.txt (sys_platform == 'win32')

**Result:**
`powershell
python scripts/demo_capabilities.py
`
- kernel32.dll: 1692 exports (HIGH Confidence)
- shell32.dll: 961 exports (GUARANTEED Confidence)
- No dumpbin.exe required
- Mac/Linux compatible

**Notes:**
- Analysis is now CPU-bound instead of I/O-bound (spawning processes)
- pefile provides direct access to memory offsets, removing the need for text scraping dumpbin output
- "Ordinal Only" exports and string decoding (Latin-1 vs UTF-8) are now handled explicitly in Python

---

## 2026-02-22 — Flat Invocable Contract + Selection UI (Sessions 1-4)
**Task/Issue:** Four related issues resolved in one working session:
1. `discovery-output.json` had too many pipeline-internal fields with no value for Section 4 or LLM invocation
2. Confidence scoring set the label *before* measuring data — shell32 showed `confidence: "high"` with all four factors `false`
3. No user-facing UI existed for Sections 2-3 invocable selection
4. Hybrid binaries (e.g. shell32.dll) silently showed only one output set

**Copilot Prompts Used:**
- "Design the invocable contract fields required for an LLM to autonomously call discovered binary functions. Prioritise what Section 4 needs to construct and validate a call (name, description, parameters with types, return type, execution method/path)."
- "Audit the current discovery-output.json schema and remove all pipeline-internal fields that have no value to Section 4 or LLM invocation. Flatten the invocable shape — no wrapper objects."
- "Update schema.py Invocable.to_dict() and section4_select_tools.py to emit and consume the new flat contract. Document the changes in session-changes.md."
- "Build an interactive CLI invocable selection UI (src/ui/select_invocables.py) with confidence-based defaults. Run against zstd.dll and assess whether the output is sufficient for autonomous LLM invocation."
- "When a binary produces multiple output sets (e.g. native exports + COM interfaces), the UI should detect and merge them, notifying the user clearly rather than silently discarding one set."

**Output Accepted:**
- `src/discovery/schema.py` — `to_dict()` rewritten to flat contract; `_parse_parameters_to_list()` added; `_get_execution_metadata()` bug fixes (duplicate `method` keys on dotnet/com; cli fell through to `unknown`)
- `src/generation/section4_select_tools.py` — removed `schema_version` validation; reads flat `description`; drops `schema_version` from output
- `src/ui/select_invocables.py` — new interactive selection UI with rich table, confidence-based defaults, hybrid merge, `--description` hint highlighting
- `docs/schemas/discovery-output.schema.json` — rewritten to flat schema
- `CHANGELOG.md` — 1.1.0 entry added
- `docs/session-changes.md` — full record of all four sessions

**Manual Changes:**
- None — all output accepted as-is after smoke testing

**Result:**
```
python src/ui/select_invocables.py --target tests/fixtures/vcpkg_installed/x64-windows/bin/zstd.dll

→ 187 invocables: 168 guaranteed (89.8%), 15 high (8.0%), 4 medium (2.1%), 0 low
→ guaranteed+high pre-checked; medium/low off by default
→ writes artifacts/selected-invocables.json for Section 4

python -c "from select_invocables import _load_and_merge; ..."  # shell32 hybrid test
→ Hybrid binary detected — 961 COM interfaces + 842 native exports = 1803 total
→ _source_group stripped before output write
```

**Notes:**
- `demo_output/` artifacts are stale (written 2026-01-27 with old schema). Delete and re-run `scripts/demo_capabilities.py` before next demo
- `calling_convention: "stdcall"` is technically wrong on x64 (single ABI) — low priority, Section 4 ignores it
- Section 2.b description hint highlights rows but does not yet feed back into confidence scoring — deferred post-MVP

---

## 2026-02-22 — Multi-Language Binary Support (Sections 2 & 3 expansion)

**Task/Issue:** The pipeline only handled PE DLL/EXE, .NET, and COM targets. Scripting-language files (.py, .ps1, .bat, .vbs, .sh) were classified as `SCRIPT` by `classify.py` but had **no analyzer wired in `main.py`** — they fell through to PE export extraction and produced zero output. SQL, JavaScript, TypeScript, Ruby, and PHP were completely absent from both the classifier and the pipeline.

**Copilot Prompts Used:**
- "Read entries.md and the project description. Identify every binary/file type required by the spec that the current pipeline does not yet handle. Propose a phased implementation plan that gets Section 2 (target acceptance) and Section 3 (invocable display) working for all types. Defer the EXE GUI scanner to last because of complexity."
- "Extend the FileType enum in classify.py with dedicated variants for PYTHON_SCRIPT, POWERSHELL_SCRIPT, BATCH_SCRIPT, VBSCRIPT, SHELL_SCRIPT, JAVASCRIPT, TYPESCRIPT, RUBY_SCRIPT, PHP_SCRIPT, SQL_FILE. Update classify_file() to perform extension-based routing for all new types before the PE magic-byte check."
- "Create script_analyzer.py. Python: AST-based extraction of public def functions and async functions, skipping _private helpers. Extract type annotations, default values, docstrings. PowerShell: regex for function Verb-Noun declarations with param() blocks and .SYNOPSIS comment-based help. Batch/CMD: :label targets with leading REM comments. VBScript: Sub and Function declarations. Shell: function declarations from .sh/.bash/.zsh files with leading # comments. Ruby: def method_name patterns with comment docs. PHP: function/method declarations with PHPDoc blocks and return type hints."
- "Create sql_analyzer.py. Regex-based extraction of CREATE PROCEDURE, FUNCTION, VIEW, TABLE, TRIGGER from T-SQL (SQL Server), PL/pgSQL (Postgres), and ANSI SQL. Handle parenthesised params (ANSI/PL/pgSQL) AND T-SQL bare @param TYPE style (before AS/BEGIN). Support nested types like VARCHAR(20) and DECIMAL(10,2). Extract -- line comments and /* */ block comments as doc strings."
- "Create js_analyzer.py for JavaScript and TypeScript. Extract: named function declarations, const/let arrow functions, TypeScript class methods with visibility modifiers, CommonJS module.exports. Parse JSDoc blocks (/** */) for @param and @returns. Detect CLI entry-points via shebang, process.argv, or commander/yargs imports. Assign confidence: guaranteed (exported+JSDoc+return type), high, medium, low."
- "Wire all new analyzers into main.py. Add analyze_scripting_language() shared pipeline function that calls the right sub-analyzer, writes Markdown + MCP JSON, and prints a summary. Add _SCRIPT_DISPATCH dict mapping each new FileType to (label, analyzer_fn, source_type). Update routing in main() to route any FileType in _SCRIPT_DISPATCH to analyze_scripting_language(). Rename --dll argument to --dll/--target (dest=dll) so users can supply any file type."

**Output Accepted:**
- `src/discovery/classify.py` — 11 new `FileType` enum variants; extension dispatch before PE magic check
- `src/discovery/script_analyzer.py` — NEW (350 lines): Python/AST, PowerShell, Batch, VBScript, Shell, Ruby, PHP analyzers
- `src/discovery/sql_analyzer.py` — NEW (200 lines): T-SQL + PL/pgSQL + ANSI SQL; depth-counted paren balancer; T-SQL bare @param style
- `src/discovery/js_analyzer.py` — NEW (290 lines): JS/TS function/arrow/method extraction; JSDoc parser; CLI detection
- `src/discovery/main.py` — `_SCRIPT_DISPATCH` dict; `analyze_scripting_language()` function; `--dll/--target` alias; updated ANALYZER_REGISTRY

**Manual Changes:**
- None — all output accepted after smoke testing

**Smoke Test Results:**
```
Python:  2 invocables — compress() [guaranteed], decompress() [guaranteed]
SQL:     4 objects   — GetCustomerById EXEC (T-SQL params correct), FormatPhone SELECT (VARCHAR(20) correct),
                       GetOrders (DECIMAL(10,2) correct), ActiveOrders VIEW
JS:      2 invocables — greet() [guaranteed, JSDoc+export], add() [medium]
```
- Python `_internal_helper` correctly excluded (leading `_`)
- SQL cross-object param contamination fixed with `next_ddl` window constraint
- SQL nested-paren types fixed with depth-counting `_extract_balanced_parens()`

**Notes:**
- PE EXE GUI scanner (Windows message loop enumeration for invocable menus/dialogs) intentionally deferred — highest complexity, lowest ROI for current milestone
- Ruby and PHP analyzers are regex-based (no AST); sufficient for invocable discovery but won't handle metaprogramming
- TypeScript is routed to `analyze_js` — same JS analyzer handles both; `.ts`/`.tsx` files get an `is_ts=True` flag that enables class-method extraction

**Files Created:**
- `src/discovery/script_analyzer.py` (350 lines)
- `src/discovery/sql_analyzer.py` (200 lines)
- `src/discovery/js_analyzer.py` (290 lines)

**Files Modified:**
- `src/discovery/classify.py`: +13 FileType variants, +15 extension dispatch lines
- `src/discovery/main.py`: +100 lines (`_SCRIPT_DISPATCH`, `analyze_scripting_language`, `--target` alias, import additions)

---

## 2026-02-22 — Spec-Gap Closure: Legacy Protocol & Spec-Format Analyzers

**Task/Issue:** Five invocable source types required by the project specification (§1) had no analyzer: JNDI, SOAP/WSDL, CORBA IDL, OpenAPI/JSON-RPC, and PDB symbols. `sections-2-3.md` explicitly listed them under "What's Not Supported Yet". Additionally `notepad.exe` — a zero-invocable GUI EXE — was still present in `demo_all_capabilities.py`, violating ADR-0005 Strict Hygiene. Also fixed a JS param-naming bug where positional fallback names (`arg0`/`arg1`) were emitted instead of JSDoc `@param` names.

**Copilot Prompts Used:**
- "The pipeline is missing five invocable surface types required by spec §1: JNDI, SOAP/WSDL, CORBA IDL, OpenAPI/JSON-RPC, and PDB symbols. Do all of them. For each: create a new analyzer module in src/discovery/, add the corresponding FileType variant(s) to classify.py, wire the analyzer into the _SCRIPT_DISPATCH dict in main.py, add execution metadata branches to schema.py._get_execution_metadata(), and create a realistic fixture file under tests/fixtures/scripts/. The analyzers should follow the same public-API pattern as script_analyzer.py: a single analyze_X(path: Path) -> List[Invocable] function."
- "Create a standalone demo script scripts/demo_legacy_protocols.py that runs all six new targets (sample_openapi.yaml, sample_jsonrpc.json, sample.wsdl, sample.idl, sample.jndi, zstd.pdb), validates each JSON output for correct structure (invocables[] present, all key fields populated, no execution.method='unknown'), prints a pass/fail table, and exits non-zero on any failure. Once that passes 6/6, integrate all six targets into demo_all_capabilities.py as a new Section 9 titled 'Legacy Protocols & Spec Formats'. Also remove notepad.exe from the PE EXEs section — it is a zero-invocable GUI binary and violates ADR-0005 hygiene policy."
- "Audit the JSON output for each new analyzer. Verify that every invocable has a non-empty description, real parameter names (not arg0/arg1), a concrete execution.method value, and the correct source_type tag. Also check select_invocables.py to ensure _KIND_LABELS has human-readable entries for all six new file-type suffixes so the interactive UI displays them correctly rather than falling through to the raw key."

**Output Accepted:**
- `src/discovery/openapi_analyzer.py` — NEW: OpenAPI 3.x/Swagger 2.x YAML+JSON and JSON-RPC 2.0; emits `openapi_operation` and `jsonrpc_method`
- `src/discovery/wsdl_analyzer.py` — NEW: SOAP/WSDL 1.1 XML via lxml; emits `soap_operation`
- `src/discovery/idl_analyzer.py` — NEW: CORBA IDL regex parser with module/interface namespacing, oneway, in/out/inout params, doc-comment extraction via line-number alignment; emits `corba_method`
- `src/discovery/jndi_analyzer.py` — NEW: JNDI `.properties`/`.jndi` and Spring XML; 8 binding subtypes; emits `jndi_*`
- `src/discovery/pdb_analyzer.py` — NEW: `dbghelp.dll` ctypes enumeration of PDB public symbols; emits `pdb_symbol`
- `tests/fixtures/scripts/sample_openapi.yaml` — 9 operations (Customer CRUD + Orders + Support + Analytics)
- `tests/fixtures/scripts/sample_jsonrpc.json` — 5 JSON-RPC methods (calculator/currency)
- `tests/fixtures/scripts/sample.wsdl` — 7 WSDL 1.1 operations (ContosoCustomerService)
- `tests/fixtures/scripts/sample.idl` — 3 CORBA interfaces × 4–5 methods = 12 total
- `tests/fixtures/scripts/sample.jndi` — 12 JNDI bindings (JDBC, JMS, mail, RMI, EJB, LDAP)
- `scripts/demo_legacy_protocols.py` — Standalone 6-target verification suite
- `src/discovery/classify.py` — 6 new FileType enum values + detection logic
- `src/discovery/main.py` — 5 new imports, 6 `_SCRIPT_DISPATCH` entries, 5 `ANALYZER_REGISTRY` entries
- `src/discovery/schema.py` — 6 new `_get_execution_metadata()` branches (no `method=unknown` for any new type)
- `src/ui/select_invocables.py` — `_KIND_LABELS` extended for all 6 new file type suffixes
- `scripts/demo_all_capabilities.py` — `notepad.exe` removed; Section 9 "Legacy Protocols & Spec Formats" added
- `src/discovery/js_analyzer.py` — Fixed `_JSDOC_PARAM_RE` to capture `type` group and handle `[name=default]`; `_make_invocable` now uses JSDoc param names instead of positional `arg0/arg1` fallback

**Manual Changes:**
- None — all output accepted after smoke testing and bug fixes within session

**Bug Fixes Applied During Session:**
1. **f-string backslash** (`openapi_analyzer.py`): Python <3.12 disallows `\W` inside f-strings — extracted as module constant `_NON_WORD = re.compile(r'\W')`
2. **lxml Comment node crash** (`wsdl_analyzer.py`): `_local(tag)` called `in` on a callable when the lxml node was a Comment/PI — added `isinstance(tag, str)` guard
3. **Demo validator key** (`demo_legacy_protocols.py`): Was checking `tools[]` instead of `invocables[]` in JSON output
4. **IDL IndentationError** (`idl_analyzer.py`): Last edit of the session introduced wrong indentation; fixed by restoring `ret_type`/`params_str`/`sig` assignments at correct indent level inside the method loop

**Result:**
```
python scripts/demo_legacy_protocols.py
→ 6/6 PASS
   sample_openapi.yaml   |  9 invocables | GUARANTEED
   sample_jsonrpc.json   |  5 invocables | GUARANTEED
   sample.wsdl           |  7 invocables | GUARANTEED
   sample.idl            | 12 invocables | GUARANTEED
   sample.jndi           | 12 invocables | HIGH
   zstd.pdb              | 871 invocables | LOW

python scripts/demo_all_capabilities.py
→ 28/28 PASS across 9 sections (up from 23/23 across 8 sections)
```

**Notes:**
- PDB confidence is `low` by design — C public symbols carry no type decorations; filtering to `SymTagFunction` tag=5 would reduce count but not improve confidence
- CORBA IDL doc extraction uses line-number alignment between clean (comment-stripped) and original source, relying on `_strip_comments()` preserving newline counts
- `sections-2-3.md` updated: "What's Not Supported Yet" section cleared; demo count updated to 28/28
- ADR-0006 documents all design decisions for this session

**References:**
- ADR-0006: `docs/adr/0006-spec-gap-closure-legacy-protocol-analyzers.md`
- dbghelp SymEnumSymbols: https://learn.microsoft.com/en-us/windows/win32/api/dbghelp/nf-dbghelp-symenumsymbols
# Copilot Log Entries



## 2026-02-22 — §2.a Directory Walk — Installed-Instance Target

**Task/Issue:** §2.a required "an installed instance" as a valid input (e.g. `c:\Program Files\AppD\`). `main.py` only accepted a single file. This was the only remaining §2-3 compliance gap.

**Copilot Prompts Used:**
- "Implement directory scanning in main.py. When --target is a directory, rglob for all files with recognized extensions, classify each, run discovery on each, and aggregate results. Surface it in select_invocables.py. Add a demo target to demo_all_capabilities.py pointing at a directory. Update sections-2-3.md and entries.md, and create ADR-0007."

**Output Accepted:**
- `RECOGNIZED_EXTENSIONS` frozenset — canonical list of extensions that `classify_file()` handles; used as fast pre-screen before the authoritative content-sniff classification.
- `analyze_directory(dir_path, out_dir, args)` in `main.py` — rglobs tree, classifies files, calls `main()` recursively (with `sys.argv` swap + `finally` restore) for each file, harvests individual `*_mcp.json` artifacts, writes aggregate `<dir>_scan_mcp.json`.
- Six-line directory check in `main()` — added immediately after base-name resolution so the existing single-file path is untouched.
- `select_invocables.py` — changed `--dll` to `--target` in subprocess command; updated `--target` help text to include "or installed directory (e.g. C:\Program Files\AppD\)"; softened "File not found" to "Target not found".
- `demo_all_capabilities.py` — Section 10 "Directory Scan (§2.a)" passes `tests/fixtures/scripts/` (13 files) as target; aggregate invocable count proves the walk works end-to-end.
- `docs/sections-2-3.md` — §2.a status updated to ✅, target count 28→29, demo and test-results rows updated.
- `docs/adr/0007-directory-scan-installed-instance-target.md` — ADR documenting design choices (rglob + RECOGNIZED_EXTENSIONS + classify_file arbitration + recursive main() pattern + per-file sub-dirs + aggregate JSON).

**Manual Changes:**
- None required — all changes applied cleanly.

**Result:**
```powershell
python scripts/demo_all_capabilities.py
```
Section 10 "Directory Scan" now appears in the output table.  Aggregate `scripts_scan_mcp.json` is written to `demo_output/unified/scripts/`.

**References:**
- ADR-0007: `docs/adr/0007-directory-scan-installed-instance-target.md`
- §2.a spec: "Users must be able to provide the system a copy of the target file or an installed instance."

---

## 2026-02-22 — Mac Compatibility Guide + Windows Unicode Fix

**Task/Issue:** Two independent issues: (1) the README still had a blanket "Windows only" warning that discouraged Mac teammates even though discovery, schema, and UI work fine on Mac. (2) `select_invocables.py` crashed on Windows when Rich tried to write Unicode box-drawing characters to `cp1252` stdout.

**Copilot Prompts Used:**
- "Create docs/mac-compatibility.md that documents exactly what works on Mac (pefile parsing, schema writing, select_invocables.py TUI, test fixtures) vs. what requires Windows (dumpbin, pywinauto, ctypes DLL calls, CREATE_NO_WINDOW). Condense the README Platform Requirements section to a single line with a link to the new doc."
- "Fix select_invocables.py Unicode crash on Windows: reconfigure sys.stdout and sys.stderr to UTF-8 before importing Rich, and pass Console(legacy_windows=False) to suppress cp1252 encoding errors."

**Output Accepted:**
- `docs/mac-compatibility.md` — two-section table: "Works on Mac" vs. "Windows-gated"; explains pefile replaced dumpbin so Visual Studio Build Tools are no longer a prerequisite.
- `README.md` — Platform Requirements condensed from a multi-line warning to one line with a link.
- `src/ui/select_invocables.py` — `sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')` added before Rich import; `Console(legacy_windows=False)` passed to prevent cp1252 fallback.
- `requirements.txt` — `rich` pinned (already present, version confirmed).

**Manual Changes:**
- None required.

**Result:**
```powershell
python src/ui/select_invocables.py --target artifacts/zstd_zstd_exports_mcp.json
```
TUI launches without Unicode errors on Windows cp1252 terminals.

**Notes:**
- `pefile` replaced `dumpbin` in an earlier commit, making VS Build Tools optional. `mac-compatibility.md` captures this so Mac teammates don't skip setup unnecessarily.
- `legacy_windows=False` is the Rich-documented fix for Windows consoles that don't support ANSI escape codes.

**References:**
- Commit: `5756fbb`
- Rich docs: https://rich.readthedocs.io/en/stable/console.html#windows-legacy-console

---

## 2026-03-04 — GUI Pipeline Fixes: Exe Launch, UIA-First Backend, Open-File Action

**Task/Issue:** Four bugs in the generated server's GUI execution path blocked the Calculator demo entirely and left Notepad unable to reopen saved files: (1) `_execute_cli` used the wrong key to look up the executable path, producing `WinError 87`. (2) The launch command incorrectly passed the binary name as a CLI argument to itself. (3) MSIX/WinUI3 apps (Calculator) opened two windows — one orphan from the failed win32 Attempt A, one real from Attempt B. (4) `file_open` only triggered the Open dialog but never typed a filename into it.

**Copilot Prompts Used:**
- "Fix _execute_cli: look up executable_path in addition to target_path and dll_path. When the invocable name matches the exe stem, route through _ensure_gui_app instead of Popen so the window is cached immediately."
- "Add preferred_backend param to _ensure_gui_app. When 'uia', skip Attempt A entirely. In _execute_cli, detect preferred_backend by scanning sibling invocables for gui_backend == 'uia'."
- "Increase button_click post-click sleep from 0.1s to 0.25s to prevent WinUI3 dropping rapid repeated presses of the same button."
- "Add open_file action type to _execute_gui mirroring save_as: trigger File > Open, wait for dialog, type filename into the Edit control, confirm with Open button. Upgrade file_open invocable in notepad server to use it with a filename parameter."

**Output Accepted:**
- `_execute_cli` — unified key lookup (`executable_path` → `target_path` → `dll_path`); launch-exe branch (`exe_stem == name`) routes through `_ensure_gui_app(target, preferred_backend=_preferred)` instead of raw `Popen`.
- `_ensure_gui_app(exe_path, preferred_backend="")` — new optional param; `if preferred_backend != "uia":` guard wraps the entire Attempt A block.
- Sibling scan in `_execute_cli` — iterates `INVOCABLES` looking for matching `exe_path` + `gui_backend == "uia"` to set `_preferred`.
- `button_click` handler — sleep changed `0.1 → 0.25` s with explanatory comment.
- `open_file` action type — full dialog-interaction handler: `File → Open` trigger (menu_select / `^o` / `%fo` fallbacks), dialog wait, four-strategy filename control lookup, `type_keys`, Open button click.
- `file_open` invocable in `generated/notepad/server.py` — upgraded from `action_type: menu_click` / no params to `action_type: open_file` / `filename: string` parameter.
- Same `_execute_cli` fixes applied to `generated/calculator-test2/server.py`, `generated/mcp-calc/server.py`, `generated/zstd/server.py`, `generated/test-component/server.py`.

**Manual Changes:**
- None required.

**Result:**
```powershell
# Calculator demo
python mcp_factory.py --serve calculator-test2
# Prompt: "Open the calculator and compute 4 times 2"
# Expected tool calls: calc → press_four → press_multiply_by → press_two → press_equals
# Calculator displays: 8

# Notepad demo
python mcp_factory.py --serve notepad
# Prompt: "Open notepad, type hello, save as demo.txt, open demo.txt, append world, save"
# Expected: full round-trip with one Notepad window
```

**Notes:**
- The `exe_stem == name` heuristic correctly identifies launch invocables for `calc`, `notepad`, `mspaint`, etc. It will not misfire for CLI tools like `zstd` because those invocables have a function name (e.g. `ZSTD_compress`) that does not match the exe stem.
- `preferred_backend` sibling scan is O(n) in invocable count — negligible for all current demo targets.
- Classic Win32 apps (Notepad, Paint) have no `gui_backend` field on their invocables, so `_preferred` stays `""` and Attempt A still fires for them — no regression.
- `0.25 s` per button press adds ~2 s overhead for a 10-step sequence. Acceptable for demo; a future batched `press_sequence` action could eliminate this.

**References:**
- ADR-0008: `docs/adr/0008-gui-pipeline-exe-launch-fixes.md`
- MSIX HWND-diff launch: Attempt B in `_ensure_gui_app`

---

**Task/Issue:** Three separate gaps addressed in one session: (1) §4's generated Flask server had no LLM connection — it could list and invoke tools but had no natural-language interface. (2) §5 had no UI at all. (3) Discovery and invocation subprocesses were spawning visible desktop windows when running EXEs like `notepad.exe` or `cmd.exe`.

**Copilot Prompts Used:**
- "Add a `/chat` endpoint to `section4_generate_server.py` using Azure OpenAI function calling. Read credentials from `.env` via `python-dotenv`. Convert `selected-invocables.json` tools into OpenAI function definitions. Route user messages to the correct tool, execute the binary, and return a natural-language summary of the result. Support conversation history so multi-turn context is maintained."
- "Build a single-page HTML chat UI served from the Flask `/` route. Dark theme, bubble layout, tool output blocks showing function name / args / result, typing indicator, download button for `selected-invocables.json`. No build step — plain HTML/JS."
- "Add `CREATE_NO_WINDOW` subprocess flag to `cli_analyzer.py`, `exports.py`, `classify.py`, and the generated `server.py` to suppress desktop windows when invoking binaries on Windows. Use `getattr(subprocess, 'CREATE_NO_WINDOW', 0)` for cross-platform safety."

**Output Accepted:**
- `/chat` route in `SERVER_TEMPLATE` — accepts `{ message, history }`, builds OpenAI function-calling payload from `INVOCABLES`, executes matched tool via `_execute_tool()`, feeds tool result back for a natural-language follow-up, returns `{ reply, tool_outputs, updated_history }`.
- `_build_openai_functions()` — converts each invocable's `parameters` list into an OpenAI function schema object; gracefully handles invocables with no parameters.
- `_execute_dll()` — `ctypes.CDLL` path for `dll_import` execution method; passes string args as `c_char_p`.
- `_execute_cli()` — `subprocess.run` path for all other methods; uses `CREATE_NO_WINDOW`.
- `/download/invocables` route — serves `selected-invocables.json` as a file download attachment.
- `HTML_TEMPLATE` — full single-page chat UI injected into `static/index.html` at generation time; component name substituted via `__COMPONENT_NAME__` token.
- `.env.example` (root) and per-generated-server `.env.example` — documents both `api.openai.com` (Option A) and Azure OpenAI (Option B) credential formats.
- `requirements.txt` template updated to `flask>=3.0`, `openai>=1.0`, `python-dotenv>=1.0`.
- `_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)` added to `cli_analyzer.py`, `exports.py`, `classify.py`; `creationflags=_NO_WINDOW` passed to every `subprocess.run` call in those modules.

**Manual Changes:**
- None required — all changes applied cleanly and `demo_all_capabilities.py` passed 29/29 after edits.

**Result:**
```powershell
# Regenerate server from existing selected-invocables.json
python src/generation/section4_generate_server.py

# Start the generated server (requires .env with OPENAI_API_KEY)
cd generated\test-component
pip install -r requirements.txt
cp .env.example .env   # fill in API key
python server.py

# Verify
curl http://localhost:5000/tools
# Open http://localhost:5000 in browser for chat UI
```

Generated artifacts:
- `generated/test-component/server.py` — Flask server with `/tools`, `/invoke`, `/chat`, `/download/invocables`
- `generated/test-component/static/index.html` — chat UI
- `generated/test-component/requirements.txt`
- `generated/test-component/.env.example`

Demo suite: `29 succeeded  0 skipped` (unchanged, confirmed post-edit).

**Notes:**
- `OPENAI_BASE_URL` left blank defaults to `api.openai.com`; set to Azure endpoint when subscription is provisioned.
- `gpt-4o-mini` is the default deployment — ~20× cheaper than `gpt-4o` at negligible quality cost for function-calling use.
- `zstd.dll` invocables have empty `description` and `parameters: []` (expected for a raw C DLL). OpenAI routes by function name only; self-descriptive names like `ZSTD_compress` / `ZSTD_decompress` are sufficient for demo.
- `CREATE_NO_WINDOW` uses `getattr(..., 0)` so no platform-specific imports are needed; safe on Mac/Linux.
- Azure sponsored subscription not yet provisioned — email sent to sponsors 2026-02-27. Weekend testing will use personal `api.openai.com` credits (~$5, `gpt-4o-mini`).

**References:**
- OpenAI function calling: https://platform.openai.com/docs/guides/function-calling
- Azure OpenAI compatibility: https://learn.microsoft.com/en-us/azure/ai-services/openai/how-to/function-calling
- `CREATE_NO_WINDOW`: https://learn.microsoft.com/en-us/windows/win32/procthread/process-creation-flags

---