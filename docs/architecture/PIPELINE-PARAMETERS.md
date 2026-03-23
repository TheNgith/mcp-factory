# Pipeline Parameters — Complete Reference

> Every knob the pipeline exposes, what phase it affects, and why you'd change it.

---

## Per-Request Parameters (via `explore_settings` in POST /api/jobs/{id}/explore)

### Probe Loop Controls

| Parameter | Type | Default | Range | Phase | What it does |
|-----------|------|---------|-------|-------|-------------|
| `mode` | enum | `"normal"` | dev/normal/extended | All | Preset bundle that sets defaults for all other params |
| `max_rounds` | int | 5 | 1–12 | Phase 3 (probe loop) | LLM conversation rounds per function. Each round = one LLM response + tool calls. More rounds = deeper exploration but slower. |
| `max_tool_calls` | int | 8 | 1–24 | Phase 3 (probe loop) | Total DLL calls the LLM can make per function. Budget-8 validated overnight as optimal — forces efficient probing. |
| `max_functions` | int | 50 | 1–500 | Phase 2 (curriculum) | Cap on functions explored per session. contoso_cs has 13, so 50 is plenty. For large DLLs (shell32), increase. |
| `min_direct_probes_per_function` | int | 1 | 1–5 | Phase 3 (probe loop) | Floor on how many times the LLM must call the target function directly (not just init/helper fns). Prevents "I called Init and concluded." |
| `cap_profile` | enum | `"deploy"` | dev/stabilize/deploy | Phase 3 | Named preset for rounds+tool_calls. deploy=5/8, stabilize=4/8, dev=3/5. |

### Context Engineering Controls

| Parameter | Type | Default | Phase | What it does |
|-----------|------|---------|-------|-------------|
| `instruction_fragment` | string | `""` (uses default prompt) | Phase 3 (per-function user message) | Custom instruction injected into the LLM's user message for each function probe. Overnight: `"Explore this function systematically. Try all argument type combinations before concluding."` was promoted. When empty, uses default: "Call it with safe probe values..." |
| `context_density` | enum | `"full"` | Phase 3 (system message) | How much prior knowledge is injected into each probe. `full` = all prior findings (default, validated overnight). `minimal` = only current function's prior finding. `none` = cold start per function. |
| `model` | string | `""` (env default) | Phase 3, 6, 8, 9 | Azure OpenAI deployment name for LLM calls. Empty = use OPENAI_EXPLORE_MODEL env var. Set to specific deployment for A/B model testing. |

### Feature Toggles

| Parameter | Type | Default | Phase | What it does |
|-----------|------|---------|-------|-------------|
| `skip_documented` | bool | `true` | Phase 3 | Skip re-probing functions that already have findings from a prior session. Saves cost on re-runs. |
| `deterministic_fallback_enabled` | bool | `true` | Phase 3 | After LLM probing, run a non-LLM fallback that tries systematic arg combinations. Catches cases where LLM missed obvious calls. |
| `gap_resolution_enabled` | bool | `true` | Phase 8 | Enable second-pass probing of failed functions with enriched context from successful ones. Expensive but catches functions that fail on first pass due to missing init knowledge. |
| `clarification_questions_enabled` | bool | `true` | Phase 8 | Generate structured questions about low-confidence findings for human review. |

---

## Per-Request Ablation Tags (via POST body, tracked in session-meta)

| Parameter | Type | Set by | Phase | What it does |
|-----------|------|--------|-------|-------------|
| `prompt_profile_id` | string | orchestrator | Metadata | Which profile from `prompt_profiles.json` was used. Tracked for coordinator analysis. |
| `layer` | int (1/2) | orchestrator | Metadata | Layer 1 = control baseline, Layer 2 = ablation variant. |
| `ablation_variable` | string | orchestrator | Metadata | Which variable family is being tested (prompt_framing, vocab_ordering, context_density, tool_budget). |
| `ablation_value` | string | orchestrator | Metadata | Specific value within the family (e.g. "systematic", "ids_first", "8"). |
| `run_set_id` | string | orchestrator | Metadata | Groups legs from the same batch for UNION merger. |
| `coordinator_cycle` | int | coordinator | Metadata | Which coordinator cycle produced this run. |
| `playbook_step` | string | coordinator | Metadata | Which playbook phase (A=robustness, B=ablation, C=sentinel, D=write-unlock). |

---

## Environment Variables (set on container, not per-request)

### Pipeline Behavior

| Env Var | Default | What it does |
|---------|---------|-------------|
| `EXPLORE_CAP_PROFILE` | `"deploy"` | Default cap profile when not overridden per-request |
| `EXPLORE_MAX_ROUNDS` | from cap profile | Override default max_rounds for all requests |
| `EXPLORE_MAX_TOOL_CALLS` | from cap profile | Override default max_tool_calls for all requests |
| `EXPLORE_MAX_FUNCTIONS` | `50` | Override default max_functions for all requests |
| `EXPLORE_CONCURRENCY` | `1` | Parallel function probes. 1=sequential (best quality). Higher=faster but may hit rate limits. |
| `EXPLORE_ENABLE_GAP_RESOLUTION` | `"1"` | Master switch for gap resolution phase |
| `EXPLORE_ENABLE_STICKY_SENTINEL_BASELINE` | `"0"` | Persist sentinel table across sessions (not yet fully wired) |

### Azure / Model Configuration

| Env Var | Default | What it does |
|---------|---------|-------------|
| `AZURE_OPENAI_ENDPOINT` | `""` | Azure OpenAI endpoint URL |
| `AZURE_OPENAI_DEPLOYMENT` | `"gpt-4o"` | Primary model deployment name |
| `AZURE_OPENAI_REASONING_DEPLOYMENT` | same as above | High-reasoning model for low-quality schemas |
| `OPENAI_EXPLORE_MODEL` | `"gpt-4o-mini"` | Model used in the explore probe loop (cost efficiency) |
| `OPENAI_CHAT_MODEL` | `""` | Override model for chat endpoint only |
| `OPENAI_MAX_TOOLS` | `60` | Max tools registered per LLM call (increase for large DLLs) |
| `GUI_BRIDGE_URL` | `""` | Windows VM bridge for DLL execution |
| `GUI_BRIDGE_SECRET` | `""` | Auth secret for the bridge |
| `PIPELINE_API_KEY` | `""` | API key for pipeline endpoints |

---

## Write Policy Parameters (hardcoded, in explore_helpers.py)

| Parameter | Value | What it does |
|-----------|-------|-------------|
| `_WRITE_RETRY_BUDGET_BY_CLASS` | write_denied=3, not_initialized=3, account_locked=2, invalid_input=3, unknown=2 | Per-sentinel-class retry budget. After N returns of the same sentinel class, stop probing that function. |
| `_WRITE_FN_RE` | `(pay\|redeem\|unlock\|process\|write\|commit\|transfer\|debit\|credit)` | Regex identifying write functions by name. |
| Amount range | 0 < v <= 1,000,000,000 | Bounded range for amount/points/cents parameters |

---

## Phase-by-Phase: What Each Phase Reads and What Controls It

### Phase 0.5 — Sentinel Calibration
- **Purpose**: Build DLL-specific error code vocabulary from user hints + empty-arg sweeps
- **Controls**: None per-request (always runs)
- **Reads**: hints text, invocables list
- **Produces**: `ctx.sentinels` (int→meaning map), `sentinel-calibration.json`

### Phase 0a — Vocab Seed
- **Purpose**: Load/seed cross-function semantic accumulator from prior sessions
- **Controls**: None per-request
- **Reads**: Prior session vocab (if exists), hints, use_cases
- **Produces**: `ctx.vocab` (shared across all function probes)

### Phase 0b — Static Analysis
- **Purpose**: Extract binary strings, IAT imports, sentinel constants from the DLL
- **Controls**: None per-request
- **Reads**: DLL binary (via Ghidra)
- **Produces**: `ctx.static_hints_block`, `ctx.dll_strings`, `static-analysis.json`

### Phase 1 — Write-Unlock Probe
- **Purpose**: Try to flip the DLL from read-only to write-ready
- **Controls**: None per-request (strategy is hardcoded: init modes, credential sweep)
- **Reads**: invocables, dll_strings, vocab
- **Produces**: `ctx.unlock_result`, `ctx.write_unlock_block`, `write-unlock-probe.json`
- **NEW**: Tests write functions with real args from vocab/hints, not empty dicts

### Phase 2 — Curriculum Order
- **Purpose**: Sort functions: init-first, then by uncertainty score
- **Controls**: `max_functions` (caps how many are explored)
- **Reads**: invocables, prior findings (for uncertainty scoring)
- **Produces**: `ctx.init_invocables`, sorted invocable list

### Phase 3 — Probe Loop (main work)
- **Purpose**: Per-function LLM agent loop — discover what each function does
- **Controls**: `max_rounds`, `max_tool_calls`, `min_direct_probes_per_function`, `instruction_fragment`, `context_density`, `skip_documented`, `deterministic_fallback_enabled`, `model`
- **Reads**: Everything from phases 0-2
- **Produces**: findings.json, probe-log.json, enriched invocables

### Post-Phase 3 — Stage-Boundary Recalibration (Q16§4)
- **Purpose**: Scan probe log for new high-bit return codes, name them via LLM
- **Controls**: None (always runs if new codes found)
- **NEW**: Re-probes write-unlock if new sentinel codes are resolved (Q16§5)

### Phase 4 — Reconcile
- **Purpose**: Cross-reference probe log vs findings, upgrade misclassified errors→successes
- **Controls**: None
- **Reads**: probe-log.json, findings

### Phase 5 — Sentinel Catalog
- **Purpose**: Persist cross-probe sentinel evidence for future sessions
- **Controls**: `EXPLORE_ENABLE_STICKY_SENTINEL_BASELINE`

### Phase 6 — Synthesize
- **Purpose**: LLM generates api-reference.md from all findings
- **Controls**: `model`
- **Reads**: All findings, invocables, vocab
- **Produces**: api-reference.md

### Phase 7 — Backfill
- **Purpose**: Enrich schema from synthesis doc (semantic param names, descriptions)
- **Controls**: `model`
- **Reads**: api-reference.md, invocables
- **Produces**: Patched invocables with semantic names

### Phase 7b — Verification (NEW)
- **Purpose**: Execute each success finding's working_call against the DLL
- **Controls**: None (always runs after backfill)
- **Reads**: findings, inv_map, init_invocables
- **Produces**: `verification` field on each finding (verified/inferred/error)

### Phase 8 — Gap Resolution
- **Purpose**: Retry failed functions with enriched context from successful ones
- **Controls**: `gap_resolution_enabled`, `clarification_questions_enabled`
- **Reads**: All findings (including newly enriched), invocables, sentinels, vocab
- **Produces**: Updated findings, clarification questions

### Phase 9 — Behavioral Spec
- **Purpose**: Generate typed Python behavioral specification
- **Controls**: `model`
- **Reads**: findings, invocables, api-reference.md

### Phase 10 — Harmonize
- **Purpose**: Final deterministic pass — cross-reference probe log one more time
- **Controls**: None
- **Reads**: probe-log.json, findings

### Finalize
- **Purpose**: Close vocab, emit contract artifacts (session-meta, transitions)
- **Controls**: None
- **Produces**: session-meta.json, transition-index.json (T-01 through T-18)

---

## Current Write-Unlock Strategy

The write-unlock currently runs at **two** points:

1. **Phase 1** (before probe loop): Tries init modes (0,1,2,4,8,16,256,512), no-param init, credential sweep. Tests write functions with real args from vocab/hints.

2. **Post-Phase 3** (after probe loop): If stage-boundary recalibration discovers new sentinel codes, re-probes write-unlock with updated knowledge.

**NOT YET IMPLEMENTED**: Write-unlock at every stage boundary (after Phase 6 synthesis, after Phase 8 gap resolution). This is Q16 Phase 4 — deferred.

**What would help**: The pipeline should try write-unlock after EACH knowledge-gaining phase, not just after sentinel recalibration. After Phase 6 (synthesis), the pipeline knows more about param meanings. After Phase 8 (gap resolution), it may have cracked a dependency. Each of these is a chance to re-try.
