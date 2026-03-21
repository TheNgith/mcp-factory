# MCP Factory – Roadmap

> Priority-ordered work items derived from analysis of run `2026-03-17-33f9114-unknown-run2`.
> Update this file as items are completed or re-prioritized.

## 2026-03-21 — B1-B4 Blocker Status

### B1 — Real-run contract verification

**Status**: ✅ **CLOSED (Explore-only path)** | ⏸ Deferred (Answer-gaps path)

**Evidence**:
- **Explore-only job (44fb7051)**: Two strict captures, both passed
  - `contract_valid: true`, `hard_fail: true`, `capture_quality: complete`, exit code 0 ✅
  - All required contract files emitted: `stage-index.json`, `transition-index.json`, `cohesion-report.json`, `session-meta.json`
  - All 16 transitions (T-01 through T-16) present with valid status/severity shape
  
- **Answer-gaps job (44fb7051)**: Mini-session infinite loop detected, deferred
  - Mini-session got stuck after ~80 iterations on CS_UnlockAccount (2026-03-21 23:07-23:22)
  - Root cause: Tool-call ceiling or model variance in retry logic for unlock function
  - **Action**: Defer answer-gaps mini-session tuning to separate ticket; explore-only path sufficient for contract validation proof-of-concept

**Exit criteria met**: ✅ Explore-only run confirms contract artifact emission and hard_fail determinism
**Impact**: Contract-first architecture validated end-to-end on live pipeline; answer-gaps refinement future work

---

### B2 — Prompt evidence hardening for T-04/T-14/T-15

**Status**: ✅ **CLOSED**

**Evidence**:
- Added `_extract_turn0_system_context()` helper in `api/cohesion.py` to extract first system message from chat transcript
- T-14 ("findings_to_chat_prompt") and T-15 ("vocab_to_chat_prompt") transitions now reference canonical evidence paths: `diagnostics/chat-system-context-turn0.txt`
- `api/main.py` emits `diagnostics/chat-system-context-turn0.txt` artifact to session-snapshot ZIP
- Regression test validates canonical evidence path pattern in stage-index

**Exit criteria met**: ✅ Turn-0 system context explicitly persisted and accessible to T-04/T-14/T-15 transitions
**Impact**: Prompt evidence paths no longer rely on transcript proxies; architectural record of prompt flow is deterministic

---

### B3 — Canonical layout parity + regression test

**Status**: ✅ **CLOSED**

**Evidence**:
- `stage-index.json` enforces canonical path pattern: all artifact keys start with `evidence/stage-XX/` where XX is stage number
- Regression test `tests/test_contract_artifacts.py` validates all stage artifacts follow canonical schema (lines 120-132)
- Test asserts: `stage_index["version"] == "1.0"` and all artifacts contain `"stage-"` and start with `"evidence/"`
- Local test run: 2/2 tests passed ✅

**Exit criteria met**: ✅ Canonical layout fully backward compatible; regression test prevents future schema drift
**Impact**: All 16 transitions emit deterministic, machine-parseable evidence pointers; stage-index is source of truth

---

### B4 — Strict save-session rollout + contract-first gating

**Status**: ✅ **CLOSED**

**Evidence**:
- `scripts/save-session.ps1` rewritten as pure orchestration layer (~200 lines)
- PowerShell now downloads session-snapshot ZIP, validates structure, invokes Python `scripts/collect_session.py` with parameters: `--mode strict`, `--job-id`, `--folder`, `--enforce-hard-fail`
- Python contract logic fully delegated to `collect_session.py` (orchestration-only model achieved)
- Exit codes: 0 = pass, 1 = strict validation failed, 2 = hard_fail gate triggered
- Support for `--enforce-hard-fail` flag enables gating on `hard_fail=true`
- No syntax errors in updated save-session.ps1 ✅

**Exit criteria met**: ✅ Contract-first gating layer deployed; PowerShell strictly orchestrates, Python validates
**Impact**: All session captures now pass through contract validation gate; no human-document edits allowed

---

## Future Work — B1 Answer-Gaps Mini-Session Tuning

### FW-1 — Answer-gaps mini-session loop ceiling

**Problem**: Answer-gaps mini-session job got stuck after ~80 iterations on `CS_UnlockAccount` function (2026-03-21, job 44fb7051).

**Symptoms**:
- Mini-session repeatedly attempted "Gap mini-session: CS_UnlockAccount…" without progressing to next function
- Job phase remained `exploring` for >15 minutes despite prior mini-sessions running (CS_ProcessPayment, CS_RedeemLoyaltyPoints, CS_ProcessRefund)
- Likely causes: (a) LLM model retry ceiling on unlock function, (b) tool-call limit exhausted, or (c) mini-session loop not checking for terminal conditions

**Recommended investigation**:
1. Check Azure Container Apps logs for error messages in answer-gaps mini-session context
2. Review `api/explore_gap.py` mini-session loop (line ~213) for unbounded retry logic
3. Validate that `_request_tool_call()` has a max-retries ceiling per mini-session
4. Add explicit timeout per mini-session (suggested: 5 minutes per function)
5. Emit diagnostic JSON showing mini-session iteration count and last attempted tool call

**Suggested fix** (estimated effort: 2-3 hours):
- Add `max_mini_session_rounds` parameter (default: 50-100 iterations)
- Add per-function timeout (default: 300 seconds)
- Emit `mini_session_diagnostics.json` with iteration counts, timeouts triggered, functions completing vs timing out
- Return graceful status (not error) if mini-session times out; emit contract artifacts anyway
- Execution guidance: `docs/architecture/PIPELINE-CONTEXT-ENGINEERING-OPERATING-MODEL.md`

**Blocker for**:
- Full answer-gaps strict capture validation (currently only explore-only path validated)
- Phase 2 contract maturity (requires both explore and answer-gaps paths working reliably)

---

## 2026-03-20 Addendum — Probe Depth + Clarification Throughput

### Immediate implementation (in progress)

| ID | Item | Priority | Usefulness | Notes |
|---|---|---|---|---|
| ADD-1 | Enforce minimum direct probe floor per function | High | High | Prevents zero-call findings and improves evidence quality. |
| ADD-2 | Reprompt on no-tool rounds before floor is met | High | High | Reduces premature assistant completion with no execution evidence. |
| ADD-3 | Require at least one direct target call before completion | High | High | Guarantees per-function ground truth attempt. |
| ADD-4 | Deterministic fallback probes for write functions | High | High | Adds stable bounded retries independent of model variance. |
| ADD-5 | Runtime setting to disable skip-documented | High | Medium-High | Essential for focused diagnostics/revalidation runs. |
| ADD-6 | Save-session floor metrics (`direct_probes_per_function`, `functions_below_floor`, `no_tool_call_rounds`) | High | High | Makes regressions observable and testable in one artifact. |
| ADD-7 | UI diagnostics controls: probe floor, skip documented, deterministic fallback | High | High | Operator can tune depth without code or env edits. |

### Design caution: deterministic fallback must remain generic

- Deterministic fallback probes must be parameter-pattern and vocabulary driven.
- No component-specific hardcoding (for example, contoso-only constants embedded in pipeline code).
- Canonical values can be derived from `id_formats`, parameter names, and value semantics.

### Deferred enhancement: generated fallback plan JSON

| ID | Item | Priority | Usefulness | Notes |
|---|---|---|---|---|
| ADD-8 | Generate `deterministic_probe_plan.json` on the fly and persist as artifact | Medium | Medium-High | Useful for transparency, auditability, and reproducibility. Execution should still be deterministic code-side, not prompt-only. |

### Deferred enhancement: Pass-B auto-answer clarifications (do not implement yet)

| ID | Item | Priority | Usefulness | Notes |
|---|---|---|---|---|
| ADD-9 | `POST /api/jobs/{job_id}/auto-answer-gaps` draft suggestion pass | Medium | High | Best run after phase=`awaiting_clarification`; user must review/accept before submit. |
| ADD-10 | UI `Suggest Answers` with confidence/evidence and risk flags | Medium | High | Reduces manual copy/paste burden while preserving human approval gate. |

### Pass-B timing (usability)

- Trigger window: after exploration/refinement ends in `awaiting_clarification`.
- Default UX: user clicks `Suggest Answers`.
- Optional future UX: auto-generate drafts once, then wait for explicit user approval.

---

## Product Shippability — Priority Order

### P1 — Trust Killers (stop deals before they start)

| # | Item | Location | Effort | Evidence |
|---|------|----------|--------|----------|
| P1-1 | **Invalid ID pre-call guard** | System prompt rule in `chat.py _build_system_message` | ~2 hrs | T07/T28: model passed `"ABC"` and `"LOCKED"` straight to `CS_LookupCustomer`; vocab has `id_formats` but no enforcement rule. Rule needed: "validate all ID args against `id_formats` before calling; if invalid, tell user, do not make the call." |
| P1-2 | **Error code vocab recall enforcement** | System prompt rule | ~2 hrs | T16: `0xFFFFFFFC` is in vocab as "account locked" but model said "access violation or malformed input." Add rule: "before interpreting any unexpected return value, check `error_codes` in vocab." Executor inline annotation only fires on live calls, not on standalone explain prompts. |
| P1-3 | **Fix `sessions/index.json` — `component: "unknown"` and `finding_counts: 0/0/0`** | `scripts/save-session.ps1` steps 5 + 10 | ~3 hrs | All sessions show broken metadata. Path resolution for `artifacts/findings.json` isn't working, and component name isn't written into job metadata at upload time. Undercuts the monitoring story. |

### P2 — Discovery Completeness (blocks real DLLs)

| # | Item | Location | Effort | Evidence |
|---|------|----------|--------|----------|
| P2-1 | **Boundary value probing in discover stage** | `api/explore.py` or new `explore_bounds.py` | ~1 day | ST-03: CS_ProcessPayment threshold found by accident — 4783 cents works, 1 cent and 999999 cents return `0xFFFFFFFB`. Discovery never deliberately probes min/max. Add probing phase: try 0, 1, `0x7FFFFFFF`, and negative-cast for each numeric param, record failure modes. Every real-world financial DLL has these guards. |
| P2-2 | **Multi-sample probing per function** | `api/explore.py` | ~half day | CUST-002/003 are in CS_GetDiagnostics customer count but CS_ProcessPayment access-violates on them while succeeding on CUST-001. Discovery currently probes one representative ID per function. Try 2-3 different valid IDs, compare results, record per-record variance. |

### P3 — Customer Deliverables (what customers take home)

| # | Item | Location | Effort | Evidence |
|---|------|----------|--------|----------|
| P3-1 | **`GET /api/jobs/{id}/transcript` endpoint** | `api/main.py` | ~15 min | On original todo list since `da13989`. Blob download of `{job_id}/chat_transcript.txt` returned as `text/plain`. |
| P3-2 | **Python wrapper generator** | new `api/generate_wrapper.py` + endpoint | ~2 days | Turn enriched `invocables_map.json` into a typed `client.py` with one method per function. Schema is already rich enough. This is the artifact customers ship. |
| P3-3 | **HTML/PDF documentation export** | new `api/generate_docs.py` + endpoint | ~1 day | Same source as wrapper gen — vocab + schema → readable API docs. Makes the product look like a product, not a debug tool. |

### P4 — Technical Ceiling (required for harder DLLs)

| # | Item | Location | Effort | Evidence |
|---|------|----------|--------|----------|
| P4-1 | **Output buffer / pointer params (Tier 3 DLLs)** | `api/executor.py`, schema, `api/generate.py` | ~1 week | `_CTYPES_ARGTYPE` has no concept of a caller-allocated output buffer. Functions like `GetCustomerName(id, char* buf, int buf_size)` are universal in Win32 DLLs and are completely unsupported. Need: `"out_buffer": true` schema flag, `ctypes.create_string_buffer(N)` allocation in executor, read-back after call. Without this, every DLL where functions return strings via pointer is dead. |
| P4-2 | **Struct / complex in-params (Tier 4 DLLs)** | `api/executor.py`, `api/generate.py` | ~2 weeks | Functions that take a `CUSTOMER_RECORD*` pointer — caller builds a struct, passes pointer, DLL reads fields. Requires `ctypes.Structure` subclass generation from schema. Second wall after output buffers. |

---

## 2026-03-21 — Model Selection Architecture

### First-layer comparison (contoso_cs.dll, commit `128efb5`, normal mode)

| Model | Success | WC Params | Explore Speed | Gap Strategy |
|---|---|---|---|---|
| **gpt-4o** | **6/13** | 4 | ~4 min | 9 gaps |
| **gpt-4-1** | 5/13 | 4 | ~3 min | 10 gaps |
| **gpt-4-1-mini** | **6/13** | **5** | **~2.5 min** | 10 gaps |
| o4-mini | 3/13 | 4 | ~30s | skipped (too many failures) |

**Key finding**: o4-mini is unsuitable for the explore layer — reasoning models overthink tool-calling sequences instead of just executing probes. gpt-4-1-mini matched gpt-4o on success rate, had the best working_call richness, and was ~40% faster.

### Architectural question: per-layer model routing

Different pipeline layers have different cognitive demands:

| Layer | Task | Cognitive need | Recommended model |
|---|---|---|---|
| **Analyze** (static analysis) | Parse PE, Ghidra, IAT | N/A (not LLM-driven) | — |
| **Explore (probing)** | Generate arguments, interpret return codes | Fast tool-calling, pattern matching | gpt-4-1-mini *(fastest, tied for best accuracy)* |
| **Gap resolution** | Retry failures with creative approaches | Deeper reasoning about failure modes | gpt-4o or gpt-4-1 *(more capable)* |
| **Clarification** | Generate user-facing questions | Language quality | gpt-4-1-mini *(cost-effective)* |
| **Chat (agentic)** | Real-time tool use with user | Reasoning + tool calling | gpt-4o *(proven)* |

### Recommended defaults

```
EXPLORE_MODEL       = gpt-4-1-mini   # fast, cheap, tied-best accuracy
GAP_MODEL           = gpt-4o         # deeper reasoning for retries
CLARIFICATION_MODEL = gpt-4-1-mini   # just needs good language
CHAT_MODEL          = gpt-4o         # agentic chat with user
```

### Implementation: `gap_model` field in explore_settings

Add `explore_settings.gap_model` override so each layer can use a different deployment.
Falls back to `explore_settings.model` if not set, then env-var default.

| ID | Item | Priority | Effort |
|---|---|---|---|
| MOD-1 | Per-layer model routing (`gap_model` in explore_settings) | High | ~2 hrs |
| MOD-2 | Default model matrix in `config.py` (env-var per layer) | Medium | ~1 hr |
| MOD-3 | Re-run comparison with split models (gpt-4-1-mini explore + gpt-4o gap) | Medium | ~30 min |

---

## Technical Depth — Ugly DLL Priority Order

Sequenced by unblocking impact on real-world legacy DLLs.

### #1 — Output Buffer Params (`LPSTR` / `LPWSTR` / `DWORD*` out-params)
**Highest-impact gap.** `executor.py`'s `_CTYPES_ARGTYPE` map has no concept of a caller-allocated output buffer. Universal in Win32.

Required changes:
- Add `"out_buffer": true` and `"buffer_size": N` to invocable schema params
- `executor.py`: when `out_buffer=true`, allocate `ctypes.create_string_buffer(size)` pre-call, pass by-ref, read back post-call
- Include buffer content in tool result string returned to model
- `generate.py` `generate_schema` tier logic: detect pointer params as likely output buffers

### #2 — Boundary Condition Probing
Every write function in every legacy financial DLL has guards. ST-03 found the payment threshold by accident.

Required changes:
- `explore.py`: after basic param discovery, for each numeric param run a boundary sweep: `[0, 1, min_seen-1, min_seen, max_seen, max_seen+1, 0x7FFFFFFF]`
- Record pass/fail for each value; store in vocab under `value_semantics[param].bounds`
- Surface in system prompt as range constraints

### #3 — Per-Record Access Variance
CUST-002/003 are in the dataset but CS_ProcessPayment crashes on them. Real DLLs have per-record lock bits, account states, version flags.

Required changes:
- `explore.py`: probe each discovered write function on 2-3 valid IDs, not just one
- If results differ across IDs, record the variance and surface it in vocab notes
- Distinguishes "function broken" from "this record is in a bad state"

### #4 — Error Code Vocab Retrieval Reliability
T16 miss is a warning sign. For a DLL with 50 error codes, the model will miss them without a strong in-prompt recall rule.

Required changes:
- System prompt rule: "Before interpreting any `unsigned long` or large integer return value, search `error_codes` in vocab first."
- Executor annotation: currently fires correctly on live calls. Extend to also annotate sentinel-range returns (anything ≥ `0xFFFFFFF0`) unconditionally.
- Post-session: add a vocab test — synthetically ask the model each error code and check if it names it correctly. Log misses.

### #5 — Data Drift / Stale Vocab
CUST-001 changed from Gold/$250/1450pts (discovery) to Platinum/$12.5M/214M pts (session). Vocab is advisory-only with no staleness signal.

Required changes:
- Add `last_verified_at: <ISO timestamp>` per function entry in `vocab.json`
- Surface age of vocab in session SUMMARY.md and system prompt preamble: "Vocab probed N days ago — field values may have drifted."
- Trigger re-probe warning if age > threshold (configurable; suggest 7 days for mutable state DLLs)

### #6 — Struct / Complex In-Params (Tier 4)
Functions that take a `CUSTOMER_RECORD*` pointer. Further out but required for enterprise DLLs.

Required changes:
- Schema: `"param_type": "struct"` with `"fields": [...]`
- `generate.py`: emit `ctypes.Structure` subclass definitions from discovered field layout
- `executor.py`: instantiate struct, populate fields from model-provided dict, pass pointer

---

## Completion Tracker

> ⚠️ Commit hashes were never back-filled. Entries marked ✅ reflect stated intent at
> the time the item was written — not verified code inspection. Use `sessions/DASHBOARD.md`
> and git log for ground truth. Sessions still show `component: "unknown"` as of
> 2026-03-19, indicating P0-A/P1-3 may be incomplete despite being marked done here.

| ID | Status | Notes |
|----|--------|-------|
| P0-A | ⚠️ Unverified | Dashboard shows `component: unknown` on all runs — may still be open |
| P0-B | ⚠️ Unverified | |
| P0-C | ⚠️ Unverified | |
| P1-1 | ⚠️ Unverified | |
| P1-2 | ⚠️ Unverified | |
| P1-3 | ⚠️ Unverified | Dashboard shows `component: unknown` — same as P0-A |
| P2-1 | ⚠️ Unverified | |
| P2-2 | ⚠️ Unverified | |
| P3-1 | ⚠️ Unverified | |
| P3-2 | ⚠️ Unverified | |
| P3-3 | ⚠️ Unverified | |
| P4-1 | ⚠️ Unverified | |
| P5-A | ⚠️ Unverified | |
| P5-B | ⚠️ Unverified | |
| P5-C | ⚠️ Unverified | |
| P6   | ⚠️ Unverified | |
| P7-A | ⚠️ Unverified | |
| P8-A | ⚠️ Unverified | |
| Gap answer re-discovery loop | ✅ Done | 2026-03-19 — `_run_gap_answer_mini_sessions` in `explore.py`, wired in `main.py answer_gaps` |
| Dual question format | ✅ Done | 2026-03-19 — `technical_question` field added to `_GAP_SYSTEM` in `explore_prompts.py` |
| G4 — IAT capability injection | ✅ Done | 2026-03-19 `ad522f0` — `api/static_analysis.py`, `_extract_iat_capabilities()` |
| G5 — Full decompiled C text | ✅ Done | 2026-03-19 `ad522f0` — `ExtractFunctions.py` + `ghidra_analyzer.py` |
| G7 — Binary strings → first-class vocab | ✅ Done | 2026-03-19 `ad522f0` — `build_vocab_seeds()` in `api/static_analysis.py` |
| G8 — PE Version Info extraction | ✅ Done | 2026-03-19 `ad522f0` — `_extract_pe_version_info()` in `api/static_analysis.py` |
| G9 — Capstone sentinel harvesting | ✅ Done | 2026-03-19 `ad522f0` — `_harvest_sentinels_capstone()` in `api/static_analysis.py` |
| G10 — Static analysis audit artifact | ✅ Done | 2026-03-19 `ad522f0` — `static_analysis.json` in ZIP + `save-session.ps1` step 14.9 |
| D-12 — Auto-enrich schema when LLM skips enrich_invocable | ✅ Done | 2026-03-21 `48db9b7` — writes `_infer_param_desc` + finding text to schema when `_enrich_called=False` |
| Version-fn success detection | ✅ Done | 2026-03-21 `48db9b7` — `_VERSION_FN_RE`, non-zero positive non-sentinel → success; packed UINT decoded to `M.m.p` string |
| LLM exception detail logging | ✅ Done | 2026-03-21 `48db9b7` — `llm_error` probe log entry with `exc_type`, `status_code`, `body` |

---

## 2026-03-21 Addendum — Probe Loop reliability (commit `48db9b7` baseline)

Identified by re-running job `59ef08de` with the D-12 fix and inspecting probe log phases.

### Root cause summary

| ID | Root cause | Effect | Status |
|---|---|---|---|
| RC-429 | Every function's round-0 LLM call gets `429 RateLimitError`; `except Exception: break` fires immediately | 12/13 functions get 0 explore-phase entries; only deterministic fallback runs | **Open** |
| RC-ARGS | `_default_scalar_value` uses `"TEST"` for generic string params like `param_1` / `"Input string parameter"` — name/description don't match `customer\|account` regex | CS_GetAccountBalance, CS_GetLoyaltyPoints, CS_GetOrderStatus, CS_LookupCustomer probe with wrong ID → `0xFFFFFFFF` every time | **Open** |
| RC-UNLOCK | `_probe_write_unlock` calls all write functions with `{}` args → access violation → `unlocked=False` → `dependency_missing` gate permanently blocks all write-path probing | CS_ProcessPayment, CS_ProcessRefund, CS_UnlockAccount, CS_RedeemLoyaltyPoints get 0 real probes | **Open** |
| RC-D12-DESC | D-12 auto-enrich writes the failure text ("All N probes returned sentinel codes…") as the function description when no working call is found | Schema descriptions are uninformative noise instead of Ghidra text | **Open** |

### Fixes required

| ID | Item | Priority | File | Notes |
|---|---|---|---|---|
| FIX-1 | **429 retry with backoff** in explore LLM round loop | High | `api/explore.py` | Catch `RateLimitError` specifically (not all `Exception`); sleep 20 s; retry up to 2× before breaking. Prevents entire probe loop from silently aborting on rate-limit bursts. |
| FIX-2 | **Function-name fallback for ID params** in `_default_scalar_value` | High | `api/explore_helpers.py` | When `json_type == "string"` and name/desc don't match, check the **function name** for `Get\|Lookup\|Process\|Redeem\|Unlock` — if matched, return first `CUST-NNN` from `vocab.id_formats`. Fixes customer-lookup functions probing with `"TEST"`. |
| FIX-3 | **Write-unlock probe with real args** | High | `api/explore_phases.py` (_probe_write_unlock) | Currently calls write functions with `{}` → access violation → permanently disables write probing. Should pass `CUST-NNN` + valid amounts from vocab for the unlock detection call. |
| FIX-4 | **D-12 skip failure-text descriptions** | Medium | `api/explore.py` D-12 block | Before writing `_finding_text` as `function_description`, check it doesn't start with `"All "` / `"No working call"` — if it's a failure-text, leave description as Ghidra text (better than noise). |

## Implementation Plan — Session Intelligence System

Three layers, eight phases. Each phase is independent enough to ship and use before the next one starts.
Phases are ordered by: (1) blocking others, (2) diagnostic value per hour of effort.

---

### PHASE 0 — Foundations (unblocks everything else, ~2 hrs total)

These three bugs make every session look broken. Fix them before building anything on top.

#### Step 0-A — Fix `component: "unknown"` (`api/main.py`)
**File:** `api/main.py`, `POST /api/jobs` upload handler  
**Change:** When a DLL is uploaded, extract `Path(filename).stem` and write it into the initial job status dict under `"component"`. The ZIP's `session-meta.json` is built from that dict, so all downstream naming picks it up automatically.  
**Effect:** Session folder names become `2026-03-17-abc1234-contoso-cs-run1` instead of `2026-03-17-abc1234-unknown-run1`. `index.json` `component` field populated. `save-session.ps1` auto-note stops defaulting to "unknown."

#### Step 0-B — Fix `known_ids` key mismatch (`scripts/save-session.ps1` step 11)
**File:** `scripts/save-session.ps1`  
**Change:** Add a `Write-Host ($vocab | ConvertTo-Json -Depth 2)` debug line in step 11, run once, read the actual key names explore.py writes. Then correct the PowerShell property access. Almost certainly `$vocab.id_formats` or `$vocab.known_ids` lives under a nested object, not at root. Fix the path.  
**Effect:** `known_ids` in `index.json` and `SUMMARY.md` shows real discovered IDs instead of "(none recorded)."

#### Step 0-C — Fix `index.json` malformed first entry (`scripts/save-session.ps1` step 16)
**File:** `scripts/save-session.ps1`  
**Change:** The first entry is being serialized as a `{value: [...], Count: N}` wrapper instead of a flat array element — a PowerShell `ConvertFrom-Json` + `@()` cast interaction. Fix: when reading and re-writing the index array, force `@($existing | ForEach-Object { $_ })` to unwrap the wrapper before appending.  
**Effect:** `index.json` is a clean flat array. `compare.ps1` (Phase 7) can read it without special-casing entry 0.

---

### PHASE 1 — Executor Structured Trace (~3 hrs)

Every `"DLL call error: access violation"` today looks identical regardless of root cause. This phase makes them distinguishable.

#### Step 1-A — `executor.py`: return structured trace alongside result string
**File:** `api/executor.py`, all four backends (`_execute_dll`, `_execute_cli`, `_execute_gui`, `_call_execute_bridge`)  
**Change:** Instead of returning a plain `str`, return a dict:
```python
{
    "result_str": "DLL call error: OSError: ...",   # what the model sees (unchanged)
    "trace": {
        "backend":         "dll",
        "ok":              False,
        "exception_class": "OSError",
        "exception_msg":   "exception: access violation reading 0x00000000",
        "exception_addr":  "0x00000000",          # dll only
        "restype_used":    "c_size_t",             # dll only
        "argtypes_used":   ["c_char_p", "c_uint"], # dll only
        "calling_conv":    "cdecl",                # dll only
        "out_buffers":     [{"param": "buf", "size": 4096, "filled_bytes": 0}], # dll only
        "exit_code":       None,                   # cli only
        "stdout_bytes":    None,                   # cli only
        "stderr_excerpt":  None,                   # cli only
        "hr_result":       None,                   # com only
        "hr_decoded":      None,                   # com only
        "coinit_called":   None,                   # com only
        "element_found":   None,                   # gui only
        "element_path":    None,                   # gui only
    }
}
```
All callers that expected a string now call `result["result_str"]`. The `"trace"` key is new.

Per-backend trace fields:
| Backend | Key fields |
|---------|-----------|
| `dll` | `restype_used`, `argtypes_used`, `calling_conv`, `exception_class`, `exception_addr`, `out_buffers[{param, size, filled_bytes}]` |
| `cli` | `exit_code`, `stdout_bytes`, `stderr_excerpt`, `timeout_hit`, `cmd_used` |
| `com` | `coinit_called`, `clsid`, `hr_result`, `hr_decoded` (winerror table lookup) |
| `gui` | `element_found`, `element_path`, `action`, `app_title` |

#### Step 1-B — `chat.py`: store trace in `_tool_log`
**File:** `api/chat.py`  
**Change:** `_tool_log` entries already have `call`, `args`, `result`. Add `"trace": result.get("trace")` when the executor returns a dict. Pass through cleanly when executor returns a plain string (backward compat).

#### Step 1-C — `main.py`: include `executor_trace.json` in session-snapshot ZIP
**File:** `api/main.py`, `GET /api/jobs/{job_id}/session-snapshot`  
**Change:** As tool calls accumulate in `_tool_log` during chat, the traces are already in memory. Serialize the full `_tool_log` (including traces) to `{job_id}/executor_trace.json` in blob at the end of each chat stream. ZIP builder picks it up.  
**Effect:** Every session folder now contains `executor_trace.json` — one entry per tool call with full ctypes/COM/CLI diagnostic data. Five identical "access violation" strings become five distinct root causes.

---

### PHASE 2 — Vocab Coverage Check (~1 hr)

Distinguishes "model didn't recall the answer" from "the answer was never injected."

#### Step 2-A — `save-session.ps1`: compute `vocab_coverage.json`
**File:** `scripts/save-session.ps1`, new step 11.5 (after vocab parse, before SUMMARY)  
**Change:** Load `model_context.txt` as a string. For every key in `vocab.error_codes`, check whether that key string appears in `model_context.txt`. Write result to `vocab_coverage.json`:
```json
{
    "error_codes_total":    8,
    "error_codes_injected": 6,
    "error_codes_missing":  ["0xFFFFFFFD", "0xFFFFFFFE"],
    "id_formats_injected":  true,
    "value_semantics_keys": ["balance", "amount_cents"],
    "coverage_score":       0.75
}
```
If a key is missing from the context, the model cannot recall it — the failure is an injection bug, not a model reasoning gap. This collapses a 5-file investigation into one check.

#### Step 2-B — Surface `vocab_coverage.json` in `SUMMARY.md` (step 13)
**File:** `scripts/save-session.ps1`  
**Change:** Add a "Vocab injection coverage" row to the SUMMARY.md discovery state table. Flag `⚠️` if `coverage_score < 1.0` with list of missing keys.

---

### PHASE 3 — DIAGNOSIS.json (~4 hrs, highest diagnostic value)

Joins TEST_RESULTS + transcript + vocab + executor trace into one record per test. Eliminates all manual 5-file navigation.

#### Step 3-A — `chat.py`: detect test ID tags in user messages
**File:** `api/chat.py`, `stream_chat`  
**Change:** At the start of each chat stream, check the user message for `# T\d+` or `[T\d+]` pattern. If found, set `_current_test_id = "T16"`. Store it in `_tool_log` entries: `{"test_id": "T16", "call": ..., "args": ..., "result": ..., "trace": ...}`. If no tag, `test_id = null`.  
**Convention for test runs:** Users prefix prompts with `# T16:` before pasting test prompts. One line, no workflow change.

#### Step 3-B — `storage.py` / `main.py`: accumulate per-test records in blob
**File:** `api/storage.py` and `api/main.py`  
**Change:** At end of each chat stream (alongside `_append_transcript`), append to `{job_id}/diagnosis_raw.json` — a growing array of:
```json
{
    "test_id":    "T16",
    "timestamp":  "2026-03-17T19:42:11Z",
    "user_prompt_excerpt": "What does 0xFFFFFFFC mean?",
    "rounds":     1,
    "tool_calls": [],
    "assistant_excerpt": "access violation or malformed input",
    "executor_traces": []
}
```
Include `diagnosis_raw.json` in the session-snapshot ZIP.

#### Step 3-C — `save-session.ps1`: compute `DIAGNOSIS.json` from raw data
**File:** `scripts/save-session.ps1`, new step after transcript handling  
**Change:** Post-process `diagnosis_raw.json` + `vocab_coverage.json` + TEST_RESULTS.md into `DIAGNOSIS.json`. Apply failure category logic:

```
if tool_calls is empty AND no executor trace:
    → failure_category = "model_reasoning" (model didn't attempt the call)
    if assistant text contains the right answer → sub-category: "correct_no_tool"
    else if vocab_coverage shows key missing → sub-category: "vocab_injection_miss"
    else → sub-category: "vocab_recall_gap"

elif executor trace has exception_class:
    → failure_category = "execution_error"
    if exception_addr = "0x00000000" → sub-category: "null_pointer"
    if out_buffers filled_bytes = 0 → sub-category: "output_buffer_empty"
    else → sub-category: "dll_call_error"

elif tool_calls made but DLL returned sentinel:
    → failure_category = "dll_state"
    (DLL refused the call — not a model or executor bug)

elif test_design_flaw heuristic (customer ID not in dataset):
    → failure_category = "test_design_flaw"
```

Output `DIAGNOSIS.json`:
```json
[
    {
        "test_id":          "T16",
        "overall":          "❌",
        "failure_category": "vocab_recall_gap",
        "sub_category":     "vocab_recall_gap",
        "vocab_key_present": true,
        "vocab_key_injected": true,
        "rounds":           1,
        "tool_calls_made":  0,
        "fix":              "Add system prompt rule: check error_codes before interpreting return values"
    },
    {
        "test_id":          "T12",
        "overall":          "❌",
        "failure_category": "execution_error",
        "sub_category":     "null_pointer",
        "exception_addr":   "0x00000000",
        "restype_used":     "c_size_t",
        "fix":              "restype may be wrong; function returns NULL for locked accounts"
    }
]
```

**Effect:** Any failure is diagnosable in one file read. Filter `failure_category = "vocab_recall_gap"` → one system prompt fix closes all of them. Filter `test_design_flaw` → those aren't real regressions.

---

### PHASE 4 — Pre-computed `delta.md` (~1.5 hrs)

Makes every new session understandable in 30 seconds without reading four files.

#### Step 4-A — `save-session.ps1`: compute `delta.md` vs previous session
**File:** `scripts/save-session.ps1`, new step after folder creation  
**Change:** Find the most recent previous session folder (by date prefix, excluding `_tmp-*`). Load its `artifacts/vocab.json` and `artifacts/findings.json`. Diff against the new session's equivalents. Write `delta.md`:

```markdown
# Session Delta — 33f9114 vs b8e0744

## vocab.json changes
### Added to error_codes
- `0xFFFFFFFE` → "already active / not found" (new)

### id_formats changed
- Before: ["CUST-NNN"]
- After:  ["CUST-NNN", "ORD-YYYYMMDD-NNNN"]

## findings.json changes
### Newly successful functions (0 → success)
- CS_ProcessRefund: now working (param_2=1850 for $18.50)

### Still failing
- CS_RedeemLoyaltyPoints: no successful call recorded

### Regressions (success → failed)
- (none)

## Schema changes (invocables_map.json)
### Params renamed
- CS_ProcessPayment.param_2 → amount_cents
```

**Effect:** I can read one file and know exactly what changed. No mental diffing across two full vocab files.

---

### PHASE 5 — Enrich `index.json` (~2 hrs)

Makes the index actually useful for trend spotting.

#### Step 5-A — Per-function status snapshot (step 10 loop)
**File:** `scripts/save-session.ps1`  
**Change:** In step 10 (findings parse), build a `function_status` dict alongside the counts:
```json
"function_status": {
    "CS_LookupCustomer":      "success",
    "CS_ProcessPayment":      "success",
    "CS_RedeemLoyaltyPoints": "failed",
    "CS_UnlockAccount":       "partial"
}
```
Write it into the index entry. Source: `findings.json` `status` field per entry.

#### Step 5-B — Transcript metrics (parse `chat_transcript.txt`)
**File:** `scripts/save-session.ps1`  
**Change:** After transcript copy, count `🔧` lines (total tool calls), `Returned: 0xFFFFFFF` pattern (sentinel returns), `DLL call error` occurrences. Write to index entry:
```json
"transcript_metrics": {
    "total_tool_calls":   34,
    "unique_fns_called":   7,
    "sentinel_returns":   12,
    "dll_errors":          3,
    "error_rate":         0.44
}
```

#### Step 5-C — Vocab completeness score
**File:** `scripts/save-session.ps1`  
**Change:** Load `invocables_map.json`. For each invocable, count params that have a non-`param_N` name. Score = named_params / total_params. Write `"vocab_completeness": 0.73` to index entry. Measures how well discovery enriched the schema — if this doesn't move after a generate.py change, the change didn't work.

---

### PHASE 6 — Parameter Confidence Scores in `findings.json` (~2 hrs)

Flags fragile params before they cause problems in production.

#### Step 6-A — `explore.py`: track attempts per param probe
**File:** `api/explore.py`, `api/explore_phases.py`  
**Change:** Wherever explore.py calls a function with probing args and records the result, also track:
```json
{
    "function_name": "CS_ProcessPayment",
    "param_name":    "amount_cents",
    "attempts":      9,
    "successes":     1,
    "confidence":    "low",
    "confirmed_encodings": ["integer cents"],
    "rejected_encodings":  ["float dollars", "string", "raw int"]
}
```
`confidence`: `high` = ≥3 clean successes, `medium` = 1-2 successes, `low` = 1 success after many failures.

**Effect:** A param that worked once after 8 failures is flagged `low` confidence. Re-verification in the next session is automatically suggested (surfaced in SUMMARY.md).

---

### PHASE 7 — `compare.ps1` — Regression Detector (~2 hrs)

The tool that makes sessions useful for iterative development.

#### Step 7-A — `sessions/compare.ps1`
**New file:** `sessions/compare.ps1`  
**Usage:**
```powershell
.\sessions\compare.ps1                              # compare last 3 sessions
.\sessions\compare.ps1 -Sessions "run1","run2","run3"  # specific sessions
.\sessions\compare.ps1 -FailOnRegression            # exits 1 if any ✅→❌
```
**Output:**
```
Function                     run1    run2    run3
--------                     ----    ----    ----
CS_LookupCustomer            ✅      ✅      ✅
CS_ProcessPayment            ❌      ❌      ✅  ← FIXED in run3
CS_ProcessRefund             ✅      ✅      ✅
CS_RedeemLoyaltyPoints       ❌      ❌      ❌  ← still broken
CS_UnlockAccount             -       partial partial

Vocab completeness:          0.41    0.52    0.68  ← improving
Error rate (transcript):     0.55    0.48    0.35  ← improving
Test pass rate:              -       -       0.64

REGRESSIONS: none
```
**Source:** Reads `index.json` `function_status` + `transcript_metrics` + `vocab_completeness` from each session entry. No file opens needed beyond index.json.

#### Step 7-B — CI hook
**File:** `compare.ps1` with `-FailOnRegression` flag  
**Change:** If any function moves from `success` → `failed` between the two most recent sessions, print `⚠️ REGRESSION: CS_ProcessPayment success → failed` and `exit 1`.  
Can be called from a GitHub Actions step after any `api/` change.

---

### PHASE 8 — Auto-scaffolded TEST_RESULTS.md (~2 hrs)

Eliminates the blank-table problem — 80% of the work is done before you start.

#### Step 8-A — `save-session.ps1` step 15: pre-fill from `diagnosis_raw.json`
**File:** `scripts/save-session.ps1`  
**Change:** If `diagnosis_raw.json` is present in the session folder, use it to pre-fill TEST_RESULTS.md rows instead of generating blank rows. For each test ID found:
- Fill `tool_calls_made` column with function names called
- Fill `return_value` with the raw return
- Fill `rounds` with round count
- Leave `Overall` as `?` for human judgment (model correctness still needs human review)

**Before (today):**
```
| T06 | Order ID + refund cents | | | | | |
```
**After (Phase 8):**
```
| T06 | Order ID + refund cents | ✅ CUST-001 | CS_ProcessRefund(param_2=1850) → 0 | - | - | ? |
```
Human only needs to change `?` to `✅` or `❌`, not reconstruct what happened from the transcript.

---

## Implementation Sequence Summary

```
Phase 0  ──► unblocks naming + index readability           ~2 hrs   DO FIRST
Phase 1  ──► executor trace (unlocks DLL-hell diagnosis)   ~3 hrs
Phase 2  ──► vocab coverage check                          ~1 hr
Phase 3  ──► DIAGNOSIS.json (highest ROI)                  ~4 hrs
Phase 4  ──► delta.md (30-second session understanding)    ~1.5 hrs
Phase 5  ──► richer index.json                             ~2 hrs
Phase 6  ──► param confidence in findings.json             ~2 hrs
Phase 7  ──► compare.ps1 + regression detector             ~2 hrs
Phase 8  ──► auto-scaffolded TEST_RESULTS.md               ~2 hrs
                                                    Total: ~19.5 hrs
```

Phases 0–3 are the core intelligence layer. Phases 4–8 are multipliers on top of it.
Phases 0, 1, 2, 3 can proceed in parallel once Phase 0 is done.

---

## Implementation Completion Tracker

| Step | Description | Status | Commit |
|------|-------------|--------|--------|
| 0-A | Fix `component: "unknown"` in `worker.py` | ✅ | 2026-03-19 |
| 0-B | Fix `known_ids` key in `save-session.ps1` | ⬜ | — |
| 0-C | Fix `index.json` malformed first entry | ✅ | 2026-03-19 |
| 1-A | Executor structured trace (all backends) | ⬜ | — |
| 1-B | `chat.py` stores trace in `_tool_log` | ⬜ | — |
| 1-C | `executor_trace.json` in session ZIP | ⬜ | — |
| 2-A | `vocab_coverage.json` in `save-session.ps1` | ⬜ | — |
| 2-B | Vocab coverage in `SUMMARY.md` | ⬜ | — |
| 3-A | Test ID tag detection in `chat.py` | ⬜ | — |
| 3-B | `diagnosis_raw.json` accumulated in blob | ⬜ | — |
| 3-C | `DIAGNOSIS.json` computed in `save-session.ps1` | ⬜ | — |
| 4-A | `delta.md` computed vs previous session | ⬜ | — |
| 5-A | Per-function status in `index.json` | ⬜ | — |
| 5-B | Transcript metrics in `index.json` | ⬜ | — |
| 5-C | Vocab completeness score in `index.json` | ⬜ | — |
| 6-A | Param attempt/confidence counts in `findings.json` | ⬜ | — |
| 7-A | `sessions/compare.ps1` | ⬜ | — |
| 7-B | `-FailOnRegression` CI flag | ⬜ | — |
| 8-A | Auto-scaffolded TEST_RESULTS.md from `diagnosis_raw.json` | ⬜ | — |

---

*Last updated: 2026-03-19 — 0-A, 0-C, G4, G5, G7, G8, G9, G10 completed (`ad522f0`)*

---

## Static Analysis Depth — Ghidra & Binary String Intelligence

> Added 2026-03-19. These items are independent of the session intelligence phases above
> and address the quality of what the pipeline *knows* before it ever makes a DLL call.

---

### Current state

**Phase 0 (binary string extraction)** in `api/explore.py` does a raw ASCII scan (`[ -~]{6,}`) over the uploaded DLL bytes — equivalent to running the Unix `strings` utility. It buckets results into IDs, emails, printf format strings, and known status words. These get injected into the LLM exploration prompt as candidate probe values.

**Ghidra** is wired into `scripts/gui_bridge.py` as a last-resort fallback: it only fires when every other analyzer (PE exports, COM/TLB, CLI, Registry, .NET, RPC) returns zero invocables. The trigger condition is literally `len(invocables) == 0`.

**For `contoso_cs.dll` specifically:** COM/TLB, CLI, .NET, and RPC all return zero results. **Ghidra is the primary — and only — analyzer that runs.** It recovers the exported function names (`CS_Initialize`, `CS_LookupCustomer`, `CS_ProcessPayment`, etc.) via `exported_only=True`. But that flag means it only returns names — no decompiler output, no parameter types, no local variable names. The LLM then has to probe each function empirically with guessed args (`param_1`, `param_2`, etc.) to discover what they actually take.

**The multi-phase exploration loop (sentinel calibration, write-unlock probing, gap mini-sessions) largely exists to compensate for not using Ghidra's decompiler output.** If we fed decompiler-recovered parameter names and types into the schema before exploration starts, most of the empirical discovery becomes verification rather than discovery from scratch.

---

### G1 — Hints-aware string ranking in Phase 0 (~2 hrs)

**File:** `api/explore.py`, Phase 0 string extraction block (~line 240)

**Change:** After extracting raw strings, score each string against domain tokens parsed from the user's hints and use_cases. Tokens are the lemmatized content words (`customer`, `payment`, `loyalty`, `order`, `redeem`, etc.). Strings that contain or appear near a domain token get scored higher and appear first in the `_static_hints_block` injected into the LLM prompt.

Additionally: parse ID format patterns directly out of the hints file instead of using a hardcoded `[A-Z]{2,6}-[\w-]+` regex. If hints contain `CUST-NNN` or `ORD-YYYYMMDD-NNNN`, derive the regex from those examples so they match exactly.

**Effect:** The 20-ID slot is filled with `CUST-001`, `ORD-20260315-0117` etc. rather than unrelated PE section strings. The LLM gets better probe candidates on the first pass, reducing the rounds needed to find valid working calls.

---

### G2 — Ghidra decompilation output for param type enrichment (~1 day)

**File:** `scripts/gui_bridge.py`, `api/explore.py` Phase 0.5

**Current:** Ghidra only runs as a fallback and only recovers exported function names.

**Change:** Run Ghidra unconditionally (in parallel with other analyzers, not as fallback) with a focused goal: extract decompiler output for each exported function and pull out:
1. **Local variable names** — Ghidra's decompiler often recovers meaningful names like `customerId`, `balanceCents`, `pointCount` that the PE export table strips
2. **String XREFs** — cross-references from each function to the `.rdata` string table, giving direct per-function evidence of what strings it uses (e.g. `CS_ProcessPayment` XREFs `"CUST-"` → confirms ID format)
3. **Called function names** — if `CS_ProcessPayment` calls `ValidateCustomerId` internally, that's a strong signal about parameter semantics

These get written into each invocable's `doc` and `parameters[].description` fields before exploration starts, giving the LLM pre-loaded parameter semantics rather than needing to discover them empirically.

**Prerequisite:** Ghidra headless must be installed and on PATH on the bridge VM. Already assumed present (it was added as fallback). Timeout: 120s per DLL.

---

### G3 — Cross-reference string table filter (~half day, depends on G2)

**File:** `api/explore.py`, Phase 0 replacement

**Change:** Replace the current whole-binary ASCII scan with a structured string table pass:
1. Parse the PE `.rdata` section directly using `pefile` (already a dependency in `src/discovery/`)
2. For each string entry, record its VA (virtual address)
3. Cross-reference VAs against disassembly to map strings → functions that reference them
4. Only surface strings that are referenced by at least one exported function

**Effect:** Eliminates false positives (compiler version strings, MSVC runtime strings, imported DLL names) that currently pollute the 20-ID slot. Every string the LLM sees is demonstrably used by at least one function in the DLL.

---

---

### G4 — IAT capability injection into explore prompt (~2 hrs)

**File:** `api/explore.py`, Phase 0; `src/discovery/import_analyzer.py` (already fully implemented)

**Current:** `import_analyzer.py` runs during discovery and writes a `*_capabilities.md` artifact (e.g. `notepad_capabilities.md`, `calc_capabilities.md`). This file is never read by `explore.py`. The LLM exploration agent has zero knowledge of what Windows APIs the DLL imports.

**Change:** After Phase 0 string extraction, read `{job_id}/capabilities.md` (or the blob-stored capabilities JSON) from the already-computed discovery artifacts. Distill it into a compact block and append to `_static_hints_block`. Target format:

```
IMPORT CAPABILITIES (from IAT analysis):
- Filesystem: ReadFile, WriteFile, CreateFileA — DLL reads/writes disk
- Registry: RegOpenKeyExA — DLL reads Windows registry
- Crypto: CryptEncrypt (crypt32.dll) — DLL performs cryptographic operations
- No network imports detected
```

**Effect on read/write classification:** The system prompt now knows at analysis time, before any probe call, which Windows system capabilities the DLL uses. A DLL that imports `WriteFile` has write functions — definitively. A DLL with no network imports cannot be making outbound calls regardless of what the function names suggest. For contoso_cs.dll specifically: confirms it is a pure in-process library (no network, no crypto, no registry) — which means every function that "fails" is failing due to argument mistakes, not environmental dependencies.

---

### G5 — Full decompiled C text per function in ExtractFunctions.py (~3 hrs)

**File:** `src/discovery/ghidra_scripts/ExtractFunctions.py`

**Current:** `ExtractFunctions.py` already initialises `DecompInterface` and calls `decompileFunction()` per exported function to recover parameter counts and types. But it discards the actual decompiled C text — only the parameter metadata is returned.

**Change:** In `_decompile_params`, also capture the full decompiled function body:

```python
c_code = results.getDecompiledFunction().getC()
```

Add `"decompiled_c": c_code` to each function's JSON entry. In `ghidra_analyzer.py`, map this field into the invocable's `"doc"` field (truncated to first 800 chars if needed). The explore agent then sees the actual decompiled function body in the tool schema.

**What the LLM gains:**

| What was before | What is visible after G5 |
|---|---|
| `CS_Initialize` takes 0 params, returns int | `int CS_Initialize(void) { g_isInitialized = 1; g_customerTable = malloc(sizeof...); return 0; }` |
| `CS_LookupCustomer` takes 1 string param, returns int | `if (strncmp(id, "CUST-", 5) != 0) return 0xFFFFFFFC;` |
| Error code 0xFFFFFFFB meaning: unknown until probed | `if (amount > MAX_PAYMENT) return 0xFFFFFFFB;` — semantic is in the code |
| Parameter 2 of CS_ProcessPayment: discovered by trial | `int amount_cents` — name and purpose readable directly |

**Effect on error code determination:** Error sentinel return sites become directly visible in decompile output. `0xFFFFFFFB` appears in the C source in the exact condition that triggers it — no probe-to-failure loop required. Sentinel calibration (Phase 0.5) becomes confirmation not discovery.

**Effect on read/write classification:** The decompiled C shows whether a function modifies memory/state (writes to global tables, calls subroutines that modify records) vs. only reads and returns. Eliminates heuristic name-prefix guessing entirely.

---

### G6 — FLOSS (FireEye Labs Obfuscated String Solver) as stretch goal (~1 day, net-new dependency)

**What FLOSS does that Phase 0 ASCII scan does NOT:**

The current Phase 0 scan is equivalent to the Unix `strings` tool — it finds null-terminated ASCII sequences already resident in the PE binary. FLOSS emulates actual CPU execution of function prologues to find strings that are **assembled at runtime** rather than stored statically:

- **Stack strings:** `char msg[] = {'E','r','r','o','r'};` — each character is pushed separately onto the stack at runtime. `strings` sees nothing because no contiguous null-terminated sequence exists in the binary. FLOSS emulates the pushes and reconstructs the string.
- **XOR-decoded strings:** `for (i=0; i<n; i++) buf[i] = encoded[i] ^ 0x42;` — the plaintext never exists in the binary at rest. FLOSS emulates the loop and recovers the plaintext.
- **Indirect string construction:** Strings built by concatenating constants via API calls to `sprintf`, `strcpy`, `strcat`.

**Why it matters for contoso_cs.dll specifically:** It doesn't — contoso_cs.dll is a clean synthetic binary with no obfuscation. Phase 0 and G3 cover it completely.

**Why it's worth having eventually:** Real-world enterprise DLLs frequently have stack strings by accident — the MSVC optimizer breaks string literals into individual character loads even without deliberate obfuscation. Any DLL compiled with `/O2` and no `/GS-` stack buffers regularly produces stack-assembled strings. When the pipeline is run against a real production DLL (not a test fixture), FLOSS would surface string evidence that the current Phase 0 scan misses entirely. It's also the standard tool security researchers reach for when proving that a string was present in a binary — having it in the pipeline strengthens the provenance story for enterprise customers who want audit trails.

**Practical requirement:** FLOSS is a standalone Python package (`pip install flare-floss`). It runs on the same machine as the API container. No Ghidra, no Java, no bridge VM requirement. Effort is mostly integration plumbing.

---

### Effort summary

| Item | Effort | Priority | Status | Unlocks |
|------|--------|----------|--------|----------|
| G2 — Ghidra decompiler param enrichment | ~1 day | **HIGH — blocks contoso_cs quality** | ⬜ | Pre-loaded param names/types; exploration becomes verification not guessing |
| G4 — IAT capability injection | ~2 hrs | **HIGH** | ✅ `ad522f0` | Read/write classification becomes definitive; dependency confusion eliminated |
| G5 — Full decompiled C text per function | ~3 hrs | **HIGH** | ✅ `ad522f0` | Error codes visible in source; param semantics direct-read; sentinel calibration becomes confirmation |
| G1 — hints-aware string ranking | ~2 hrs | Medium | ⬜ | Better probe candidates immediately, no new dependencies |
| G3 — PE `.rdata` XRef string filter | ~half day | Low | ⬜ | Cleaner string extraction; depends on `pefile` (already present) |
| G6 — FLOSS obfuscated string extraction | ~1 day | Low (stretch) | ⬜ | Real-world DLLs with stack-assembled/XOR strings; adds `flare-floss` dependency |

**G2 + G4 + G5 should be treated as a single P2-level block** — together they change the pipeline from "empirical probe-and-discover" to "read the evidence, confirm with probes." G1, G3, G6 are incremental improvements on top of that foundation.

**The combined intelligence picture:**

Before these items: LLM enters exploration with export names only. It knows `CS_ProcessPayment` exists and takes two params. Everything else — param names, types, valid ranges, error semantics, read/write classification — must be discovered by trial-and-error probing. Every error code meaning is inferred from which call failed with what args.

After G2 + G4 + G5:
- Param names and types come from Ghidra decompiler output (local variable names like `customerId`, `amount_cents`)
- Error code conditions are visible in decompiled C (`if (amount > MAX) return 0xFFFFFFFB`)
- Read/write classification comes from both: IAT imports (which system calls does it make?) and decompiled C (does the function modify global state?)
- String cross-references confirm which format strings belong to which function
- The LLM spends probe budget on **confirming** known facts and **finding edge cases** rather than blind discovery

Result: exploration rounds drop, error rate drops, parameter confidence scores go up on the first pass. The multi-round sentinel calibration loop and write-unlock probing become verification steps rather than the primary discovery mechanism.

---

### G7 — Binary string evidence promoted to first-class vocab (~1 hr)

**File:** `api/explore.py`, Phase 0 string extraction block

**The inversion problem:** Currently, user-supplied hints seed the structured `vocab` dict (which is priority-ordered and re-injected every round). Binary-extracted strings go into `_static_hints_block` — a plain text appendage injected once. The LLM implicitly treats structured vocab entries as higher confidence than prompt appendages, but binary evidence is ground truth — it's literally in the file. User hints are advisory (user might be wrong or stale).

**Change:** After Phase 0 extracts `_ids`, `_emails`, `_status` from the binary, write them into the vocab dict instead of only into the text block:

```python
# Binary strings are ground truth — promote to vocab, not just text hints
if _ids and "id_formats" not in vocab:
    vocab["id_formats"] = _ids[:20]   # same field user hints seed
if _status and "value_semantics" not in vocab:
    vocab["value_semantics"] = {"status_values": _status}
```

User hints only fill these fields if the binary provided nothing (`setdefault` semantics). The text block is kept as a secondary "probe candidate" list for the LLM to draw from during tool calls.

**Effect:** Binary-derived ID formats, status tokens, and format strings are surfaced in the structured vocab block (priority-ordered, re-injected every round) rather than in a once-only text appendage. The LLM consults them the same way it consults user-confirmed id_formats — not as suggestions but as known facts.

---

### G8 — PE Version Info resource extraction (~30 min, zero new dependencies)

**File:** `api/explore.py`, Phase 0, or `src/discovery/main.py` discovery phase

**What it is:** The `VS_VERSION_INFO` resource embedded in most Windows DLLs, parseable by `pefile` (already a dependency). Contains: `CompanyName`, `ProductName`, `FileDescription`, `OriginalFilename`, `ProductVersion`, `LegalCopyright`.

**Change:** After reading the PE binary in Phase 0, parse the version info resource:

```python
if hasattr(pe, 'VS_VERSIONINFO'):
    for vi in pe.VS_VERSIONINFO:
        for st in vi.StringTable:
            for k, v in st.entries.items():
                # k/v are bytes → decode
                version_info[k.decode()] = v.decode()
```

Inject `FileDescription` and `ProductName` into `vocab["description"]` if not already set by the user. Append `CompanyName`, `ProductVersion` to the static hints block.

**Effect on black-box DLLs:** A completely stripped DLL with no export names, no headers, no PDB — but which was compiled by a real company — almost always has version info. `FileDescription = "Customer Relationship Management Helper Library"` tells the LLM the domain before a single probe call. This is the highest signal-to-effort item in the entire roadmap: 30 minutes of plumbing, zero LLM cost, immediate domain context for any real-world DLL.

---

### G9 — Capstone sentinel harvesting (~2 hrs, `pip install capstone`)

**File:** `api/explore.py`, Phase 0 or new `_harvest_sentinels_from_binary()` helper replacing part of `_calibrate_sentinels`

**What it does:** Disassemble each exported function using Capstone (a lightweight x86/x64 disassembler — no Ghidra, no Java, no bridge VM). Walk every instruction and collect 32-bit immediate values in the `0xFFFFF000`–`0xFFFFFFFF` range that appear in:
- `MOV EAX, <constant>` — direct return value assignment
- `CMP EAX, <constant>` — return value comparison (caller-side error check)
- `TEST EAX, <constant>` — bitfield error mask

These are your sentinel / error codes, extracted directly from binary arithmetic with zero probing.

**Change:** Replace or augment `_calibrate_sentinels` (Phase 0.5):

```python
# Before: ask gpt-4o-mini to guess likely error codes from function names
# After: read them directly from the binary
sentinels = _harvest_sentinels_capstone(dll_bytes, exported_fn_rvas)
# gpt-4o-mini step only runs if capstone harvest returns < 3 candidates
```

**Effect:** For any DLL with standard Win32 HRESULT-style error returns (which is essentially all of them), the sentinel table is populated before exploration starts — not calibrated through probe failures. The probe-to-failure loop that currently teaches the LLM what `0xFFFFFFFB` means becomes a confirmation step. For truly exotic DLLs with no standard error patterns, the existing calibration fallback still runs.

**For contoso_cs.dll specifically:** `0xFFFFFFFB` and `0xFFFFFFFC` appear as `MOV EAX, 0xFFFFFFFB` / `MOV EAX, 0xFFFFFFFC` inside CS_ProcessPayment and CS_LookupCustomer. Capstone reads them in milliseconds with zero LLM cost.

---

### Updated effort summary (all G items)

| Item | Effort | Deps | Priority | Status | Key unlock |
|------|--------|------|----------|--------|------------|
| G2 — Ghidra decompiler param enrichment | ~1 day | Ghidra (present) | **HIGH** | ⬜ | Param names/types pre-loaded; exploration → verification |
| G4 — IAT capability injection | ~2 hrs | pefile (present) | **HIGH** | ✅ `ad522f0` | Read/write classification definitive |
| G5 — Full decompiled C text per function | ~3 hrs | Ghidra (present) | **HIGH** | ✅ `ad522f0` | Error code conditions literal in source |
| G7 — Binary strings → first-class vocab | ~1 hr | none | **HIGH** | ✅ `ad522f0` | Ground-truth evidence treated as facts, not suggestions |
| G8 — PE Version Info extraction | ~30 min | pefile (present) | **HIGH** | ✅ `ad522f0` | Domain context from the binary itself; critical for black-box DLLs |
| G9 — Capstone sentinel harvesting | ~2 hrs | `capstone` (new, tiny) | **HIGH** | ✅ `ad522f0` | Sentinel table from binary arithmetic; eliminates probe-to-failure loop |
| G10 — Static analysis audit artifact | ~2 hrs | none | **HIGH** | ✅ `ad522f0` | Per-session cross-check of static evidence vs. findings |
| G1 — Hints-aware string ranking | ~2 hrs | none | Medium | ⬜ | Better probe candidates |
| G3 — PE `.rdata` XRef string filter | ~half day | pefile (present) | Low | ⬜ | Cleaner string extraction |
| G6 — FLOSS obfuscated string extraction | ~1 day | `flare-floss` (new) | Low (stretch) | ⬜ | Stack-assembled/XOR strings in real-world DLLs |

**G7 + G8 + G9 are the cheapest high-value items in the entire roadmap.** Combined ~3.5 hrs, all pure Python, all zero LLM cost. They complete the "read before you probe" foundation that G2/G4/G5 start.

**What the static analysis overhaul looks like end-to-end:**

```
Before: Upload DLL → export names only → 20 blind probe rounds → slowly discover everything

After:
  Discovery phase (Ghidra):        param names, types, decompiled C bodies, XREFs
  Phase 0 (pefile + capstone):     PE version info (domain), IAT capabilities (read/write),
                                   binary strings → vocab (probe candidates as facts),
                                   sentinel constants harvested from disassembly
  Phase 0.5 (sentinel calibration): confirms capstone harvest, fills gaps if any
  Main explore loop:               verifies known facts, probes edge cases and invariants
```

The probe loop never disappears — it's still required for semantic invariants, boundary conditions, and per-record state that no static tool can read. But it starts from a position of near-complete structural knowledge rather than complete ignorance.

**Static analysis ceiling for true black-box (ordinal-only, no strings, no version info, no debug info):**
Export names: `#1`, `#2`, `#3` only. Capstone still harvests sentinel constants. IAT still reveals capabilities. Decompiler still recovers param count and types. PE Version Info empty. The LLM then enters probing with type signatures and capability context — still a massive improvement over today's name-only baseline.

---

### G10 — Static analysis audit artifact in session snapshot (~2 hrs)

**The verification problem:** After G2–G9 are implemented, the pipeline injects a large amount of pre-computed static evidence into the LLM's context. Without a way to inspect what was injected and whether the model used it correctly, there's no way to know if a probe failure was caused by bad static data, a plumbing bug, or a genuine model reasoning gap.

**Change — new artifact: `static_analysis.json`**

Written at the end of Phase 0 (before any probe calls), uploaded to blob alongside `vocab.json` and `mcp_schema.json`. Included in the `session-snapshot` ZIP as a first-class artifact.

Structure:
```json
{
  "generated_at": "2026-03-19T20:14:00Z",
  "dll_name": "contoso_cs.dll",
  "pe_version_info": {
    "FileDescription": "Contoso CRM Helper Library",
    "CompanyName": "Contoso Ltd",
    "ProductVersion": "1.0.0.0",
    "OriginalFilename": "contoso_cs.dll"
  },
  "iat_capabilities": {
    "filesystem": ["ReadFile", "WriteFile"],
    "network": [],
    "crypto": [],
    "registry": [],
    "raw_imports": ["kernel32.dll", "msvcrt.dll"]
  },
  "binary_strings": {
    "ids_found": ["CUST-001", "CUST-002", "ORD-20260315-0117"],
    "status_tokens": ["ACTIVE", "LOCKED", "SUSPENDED"],
    "format_strings": ["%s: balance %d cents"],
    "emails_found": []
  },
  "sentinel_constants": {
    "source": "capstone",
    "harvested": {
      "0xFFFFFFFB": {"function": "CS_ProcessPayment", "instruction": "MOV EAX, 0xFFFFFFFB"},
      "0xFFFFFFFC": {"function": "CS_LookupCustomer", "instruction": "MOV EAX, 0xFFFFFFFC"}
    },
    "calibration_fallback_used": false
  },
  "ghidra_enrichment": {
    "functions_decompiled": 8,
    "param_names_recovered": 14,
    "decompile_failures": [],
    "sample": {
      "CS_ProcessPayment": {
        "signature": "int CS_ProcessPayment(char* customerId, int amount_cents)",
        "decompiled_c_excerpt": "if (!g_isInitialized) return 0xFFFFFFFC;\nif (strncmp(id, \"CUST-\", 5) != 0) return 0xFFFFFFFC;"
      }
    }
  },
  "vocab_seeded": {
    "id_formats": ["CUST-001", "CUST-002"],
    "source": "binary",
    "overrode_user_hints": false
  },
  "injected_into_prompt": true,
  "static_hints_block_length": 412
}
```

**Change — `save-session.ps1` verification step**

After downloading the session snapshot ZIP, add a new step that reads `static_analysis.json` and cross-checks it against the session transcript and findings:

```powershell
# Step X: Static Analysis Verification
$static = Get-Content "$sessionDir/static_analysis.json" | ConvertFrom-Json

# Check 1: every harvested sentinel appears in vocab error_codes
$sentinelMisses = @()
foreach ($hex in $static.sentinel_constants.harvested.PSObject.Properties.Name) {
    if (-not $vocab.error_codes.$hex) { $sentinelMisses += $hex }
}

# Check 2: binary-extracted IDs appear in at least one successful finding
$idMisses = @()
foreach ($id in $static.binary_strings.ids_found) {
    $used = $findings | Where-Object { $_.args -match [regex]::Escape($id) }
    if (-not $used) { $idMisses += $id }
}

# Check 3: IAT says no network — verify no findings reference HTTP/socket calls
$networkFindings = $findings | Where-Object { $_.result -match "http|socket|connect" }
```

Write results to `static_verification.json`:
```json
{
  "sentinel_misses": [],
  "id_misses": ["ORD-20260315-0117"],
  "capability_contradictions": [],
  "verdict": "PASS",
  "notes": "ORD-20260315-0117 found in binary but no finding used an order ID — consider adding order lookup test"
}
```

Surface `verdict` and any misses in `SUMMARY.md` under a new "Static Analysis Verification" section.

**What this tells you per session:**

| Finding | What it means | Action |
|---|---|---|
| `sentinel_misses: ["0xFFFFFFFB"]` | Capstone found it, vocab never got it | Plumbing bug in G9 → G7 handoff |
| `id_misses: ["CUST-002"]` | Binary contains CUST-002 but no probe used it | Model ignored binary string hints — recall problem |
| `capability_contradictions: ["http"]` | IAT says no network but finding says HTTP | Static analysis is wrong or DLL delegates via COM |
| `verdict: PASS` | Every binary-derived fact was used or confirmed | Static enrichment working correctly |

**Effect:** Every session tells you not just "what did the model do" but "did it correctly use the static evidence we handed it." Distinguishes pipeline bugs from model reasoning gaps immediately.

---

> **Note on G4/G7/G8/G9:** All four are implemented in the new `api/static_analysis.py` module (`run_static_analysis()`, `build_vocab_seeds()`, `build_static_hints_block()`). `capstone 5.0.7` added as a dependency. Verified on `contoso_cs.dll`: all 5 sentinels harvested, 8 IDs, 9 status tokens, 3 format strings, IAT=filesystem-only — all seeded into `vocab` before the first probe call.

> **Note on G5:** `_decompile_params()` in `ExtractFunctions.py` now returns `(params, decompiled_c)` tuple capturing `getDecompiledFunction().getC()` (first 1200 chars). `ghidra_analyzer.py` injects it into each invocable's `doc` field.

> **Note on G10:** `save-session.ps1` step 14.9 reads `static_analysis.json`, cross-checks sentinels→vocab, binary IDs→findings, IAT no-network claim→findings text. Writes `static_verification.json` with PASS/WARN/FAIL verdict.

> **Note on 0-A:** Root cause was `api/worker.py` not `api/main.py`. `main.py` wrote `component_name` correctly at job creation, but `_analyze_worker` overwrote the entire status dict on every progress update, stripping it out. Fix: import `_get_job_status`, snapshot `_init` at the start of the worker, spread `{**_init, ...}` into all four `_persist_job_status` calls. Final-payload call re-reads current status after explore completes to also preserve `explore_phase`/`explore_questions`.

---

## Explore Loop Architecture — Decisions & Deferred Work

### Decision log (commits `79c3a8e`, `32ccf0e`)

**Root cause (job 206a77f8):** `_MAX_EXPLORE_ROUNDS_PER_FUNCTION = 3` was a *turn cap*, not a *tool-call cap*. The LLM batched 24 DLL calls in a single round, making the turn cap meaningless. CS_UnlockAccount consumed 37 of 46 total explore tool calls across 3 rounds (1 + 24 + 12). Result: 6 of 13 functions were never probed; `finding_count = 0` because the LLM never called `record_finding`.

**Implemented fixes:**

| Constant / mechanism | Old value | New value | File |
|---|---|---|---|
| `_MAX_EXPLORE_ROUNDS_PER_FUNCTION` | 3 | 5 | `api/explore_phases.py` |
| `_MAX_TOOL_CALLS_PER_FUNCTION` | _(did not exist)_ | 15 (env: `EXPLORE_MAX_TOOL_CALLS`) | `api/explore_phases.py` |
| Forced `record_finding` fallback | _(none)_ | Emitted automatically after loop if LLM never called it | `api/explore.py` |
| "MANDATORY ERROR RECORDING" prompt rule | _(none)_ | Stop after ≤10 probes, call `record_finding(status='error')`, critical violation if omitted | `api/explore_prompts.py` |

The two-constraint system works as follows:

---

## Architecture Coherency Improvements (2026-03-20)

> Root cause: the pipeline has 8+ LLM decision points, each seeing a frozen snapshot
> of partial data. No stage sees what the others concluded. The code-level
> reconciliation layer can only enforce one direction (downgrade success→error,
> never upgrade error→success from probe evidence). Result: contradictory final
> state (e.g. successful probe in log but finding says "error"), sentinel code
> loss across runs, unanswered clarifications not gating "done."

### AC-1 — Post-loop probe-log reconciliation (explore.py)
**Problem:** If the LLM calls `record_finding(status='error')` but the probe log
contains a `Returned: 0` for that same function, there is no code path to upgrade
the finding. The ground-truth override only fires when `_observed_successes` is
non-empty, and the force-save only fires when `_finding_recorded` is False.
**Fix:** After all per-function exploration completes (after `_explore_one` loop,
before synthesis), scan the full `explore_probe_log.json`. For every function
with `status=error` where any probe entry has `classification.signed == 0`,
override to `status=success` with the working call args from that probe.
**Impact:** Directly eliminates the "successful probe but final error" class.

### AC-2 — Sticky sentinel baseline (explore_phases.py)
**Problem:** `_calibrate_sentinels` is a one-shot with no memory. If fewer
functions return repeatable high-bit values in a given run (runtime variability),
calibrated codes drop. Newer run lost 60% of codes (5→2).
**Fix:** Before falling back to `_SENTINEL_DEFAULTS`, try loading
`sentinel_calibration.json` from blob (prior session). Merge current-run
candidates with prior-session calibrated codes. Only remove a prior code if
the current run has explicit contradictory evidence (code observed as return=0).
**Impact:** Sentinel knowledge persists across runs unless actively disproven.

### AC-3 — Enrich synthesis LLM context (explore_prompts.py)
**Problem:** `_synthesize()` only sees `findings` JSON. It cannot see vocab
(cross-function patterns, error codes, hypotheses) or sentinel calibration.
Synthesis LLM reasons with a keyhole view.
**Fix:** Add `vocab` and `sentinels` parameters to `_synthesize()`. Include
vocab summary and sentinel table in the user message alongside findings.
**Impact:** Synthesis document is more accurate; backfill patches downstream
inherit better semantics.

### AC-4 — Clarification closure gate (explore.py)
**Problem:** Pipeline marks `explore_phase: "done"` even when
`explore_questions` contains unanswered entries. Callers assume "done" means
complete, but unresolved clarifications affect status/unit semantics.
**Fix:** After persisting `explore_questions`, check if any remain. If so,
set `explore_phase: "awaiting_clarification"` instead of "done". Transition
to "done" only when clarifications are answered or explicitly dismissed.
**Impact:** External consumers can distinguish "complete" from "complete but
has open questions."
- **Rounds** = number of LLM turns per function (5). Limits conversational back-and-forth.
- **Tool calls** = number of actual DLL probe calls per function (15). Hard cap enforced inside the round loop; breaks immediately when hit. Guarantees budget sharing across all functions regardless of LLM batching behavior.

### Round-robin scheduling — analysed, rejected for single-DLL, deferred to multi-DLL

**What was considered:** Interleave function scheduling so the explore loop does N calls on F1, then N on F2, then N on F3, cycling back to F1. This would prevent any single function from monopolising the session budget.

**Why it was rejected for single-DLL (contoso_cs.dll):**

1. **Vocab already flows forward.** The accumulated `vocab` dict (error codes, ID formats, status tokens) is shared across all functions in a session. Each new function's system message already includes everything learned from prior functions. There is no information benefit to interleaving — cross-function context is already present.

2. **No inter-function handle dependencies.** `contoso_cs.dll` is a pure in-process library. `CS_Initialize` sets a global flag; no function returns a handle or token that another function needs as an input argument. Sequential ordering has identical semantics to interleaved ordering.

3. **Root cause was the missing tool-call cap, not ordering.** The per-function cap (`_MAX_TOOL_CALLS_PER_FUNCTION = 15`) directly addresses the starvation problem without the added complexity of a scheduler. With the cap in place, worst-case budget consumption per function is bounded regardless of scheduling order.

**When round-robin IS the right pattern — multi-DLL interweaving:**

Round-robin scheduling becomes architecturally correct when DLL A and DLL B have cross-DLL data dependencies. Example:

```
DLL A: OpenSession() -> session_handle (opaque integer)
DLL B: LookupRecord(session_handle, record_id) -> data   # needs DLL A's output
DLL A: CloseSession(session_handle)
```

In this scenario sequential scheduling is semantically wrong — you cannot exhaust all of DLL A before starting DLL B, because DLL B's probing requires a live handle that only DLL A can provide. Interleaved scheduling (round-robin across functions from both DLLs) is the only correct approach.

**Deferred to multi-DLL interweaving phase.** The scheduler should be built at that point, driven by the dependency graph extracted from IAT imports + invocable schemas (the `returns_handle` / `takes_handle` schema fields that P4-1 output buffer work will introduce). Round-robin without a dependency graph is the wrong primitive; scheduler slots should be ordered by the dependency DAG, not arbitrary rotation.

---

## Sentinel Meaning Table Hardening (MVP: Windows DLLs 1995-2015)

### Goal

Create a production-safe sentinel interpretation process that is:
- versioned per DLL,
- overrideable per function,
- evidence-gated (never hardcode global meanings without proof), and
- robust for write-heavy operations and multi-DLL dependency chains.

### Priority formats (deterministic fallback order)

For MVP, implement deterministic pre-classification for the top three practical formats, then let the model confirm/refine with context:

1. **HRESULT-like values**
2. **Custom signed-negative integer style** (e.g. `0xFFFFFFFB` -> `-5`)
3. **BOOL-style return with Win32 last-error context**

Keep NTSTATUS support as P2 fallback. Do not treat any classifier output as truth by itself; classifier yields a hypothesis + confidence.

### Non-negotiable production rule

Sentinel semantics must be stored as:
- **DLL-level default map** (coarse behavior), plus
- **function-level override map** (authoritative when present), plus
- **evidence source and confidence** (trace-derived, static-derived, user-confirmed).

If evidence conflicts, downgrade confidence and raise a gap question instead of silently rewriting mappings.

### Data model additions (P1)

Add to invocable/session schema:

| Field | Type | Purpose |
|---|---|---|
| `writes_state` | bool | Marks mutating functions |
| `requires_init` | bool | Precondition for safe execution |
| `requires_active_state` | enum(`active`,`locked`,`any`) | Account/state precondition |
| `id_constraints` | object | Regex + canonical formatter per ID arg |
| `amount_constraints` | object | Min/max/unit metadata (cents/points/etc.) |
| `sentinel_map` | object | code -> class -> action -> confidence |
| `retry_policy` | object | max attempts + allowed argument variations |
| `returns_handle` | bool | Produces dependency token/handle |
| `consumes_handle` | bool | Requires token/handle from another function/DLL |
| `evidence_refs` | array | Trace/static/user evidence supporting mapping |

### Write-control execution policy (P1)

On any write sentinel (for example write denied class), executor/explore loop must run deterministic triage in this order:

1. Verify init/state prerequisite
2. Verify ID normalization against `id_constraints`
3. Verify amount units/range (`amount_constraints`)
4. Verify account/order state preconditions
5. Execute only policy-allowed corrective probes (`retry_policy`)
6. If unresolved: emit structured `record_finding(status='error')` and stop

Never brute-force arbitrary parameter permutations after policy budget is exhausted.

### Contoso function-level write policy (P1 reference implementation)

Use `contoso_cs.dll` as the reference write-policy template:

| Function | Required preconditions | Sentinel handling policy |
|---|---|---|
| `CS_ProcessPayment` | initialized, valid `CUST-NNN`, active account, cents amount in range | On `0xFFFFFFFB`: check lock state then amount bounds before retry |
| `CS_ProcessRefund` | initialized, valid customer + order IDs, cents units, refundable state | On write failure: verify ownership/state and amount constraints |
| `CS_RedeemLoyaltyPoints` | initialized, active account, integer points, sufficient balance | On `0xFFFFFFFB`: branch as insufficient points or locked/denied |
| `CS_UnlockAccount` | initialized, valid customer ID, account currently locked | On `0xFFFFFFFC`: treat as no-op/not-found branch; avoid brute-force retries |

### Interweaving DLL dependencies (P2)

For multi-DLL workflows, write handling must be dependency-aware:

1. Build producer/consumer dependency graph (`returns_handle` -> `consumes_handle`)
2. Schedule by dependency readiness (DAG), not naive round-robin
3. Persist handles/tokens in shared typed context with lifetime rules
4. Enforce open/use/close ordering with partial-failure rollback semantics
5. Attribute write failures to dependency stage in diagnostics

### Observability and quality gates (P1)

Require per-session artifacts:

| Metric | Why it matters |
|---|---|
| Sentinel histogram by function | Surfaces hot failure modes quickly |
| Retry count by sentinel class | Detects probe thrashing |
| Structured stop reason (`success`, `policy_exhausted`, `dependency_missing`, `schema_missing`) | Makes failures actionable |
| Confidence and source per sentinel meaning | Prevents silent bad mappings |
| Drift detector across sessions | Flags inconsistent meaning for same code/function |

Release gate for sentinel-table updates:
- No promotion of new sentinel meaning to DLL-level default unless observed in >=2 sessions or confirmed by user/static evidence.
- Function-level override may be promoted earlier, but must remain low confidence until cross-session confirmation.

### Implementation phases

| Phase | Scope | Effort | Exit criteria |
|---|---|---|---|
| S1 | Deterministic sentinel pre-classifier + confidence + evidence refs | ~1 day | Every probe result gets `classification.format`, `classification.confidence`, `classification.source` |
| S2 | Write triage policy engine (`requires_*`, constraints, bounded retries) | ~1-2 days | Write functions stop thrashing; every failed write has structured stop reason |
| S3 | Sentinel catalog versioning (DLL default + function override) | ~1 day | Meanings are versioned and conflict-aware; no global hardcode without evidence |
| S4 | Multi-DLL dependency-aware write orchestration | ~2-3 days | Handles/tokens tracked; dependency-stage attribution in diagnostics |

### MVP acceptance criteria

1. Any write-heavy function either succeeds under policy or exits with a deterministic, structured failure reason in <= configured retry budget.
2. No function can consume unbounded explore calls due to sentinel loops.
3. Sentinel meanings are explainable from stored evidence (trace/static/user), not only model narration.
4. Cross-DLL runs can attribute write failures to missing dependency state versus bad function args.

---

## MVP Persistence + UI Rehydration (Blob-backed)

### Problem statement

Refreshing the web UI can drop visible enriched state (schema/vocab/findings) from the user's view.
For MVP, session state must be durable and resumable from Blob-backed artifacts, not browser memory.

### Target behavior

1. Reloading the UI resumes the exact job state (schema stage, findings, vocab, questions, traces).
2. A user can open prior sessions and continue exploration/refinement without re-running discovery.
3. Every meaningful state mutation updates durable storage first, then UI state.

### Backend work (P1)

1. Add `GET /api/jobs/{id}/state` aggregator endpoint returning a single rehydration payload:
  - latest schema checkpoints
  - findings/vocab
  - explore status + questions
  - metadata (`component`, timestamps, cap profile)
2. Add per-job durable manifest blob (`job_state_manifest.json`):
  - latest artifact pointers/versions
  - stage progress
  - updated_at
3. Persist version IDs for key artifacts (`schema_version`, `vocab_version`, `findings_version`) to support rollback/diff.
4. Ensure write order is durable-first:
  - upload artifact
  - update manifest pointer/version
  - return response to UI

### UI work (P1)

1. On page load, always call `GET /api/jobs/{id}/state` and reconstruct screen state from response.
2. Add explicit loading/resume states ("Rehydrating session…").
3. Add session history panel:
  - list jobs by component
  - last updated
  - status badge (exploring/refining/done/error)
4. Persist edits (hints/use-cases/gap answers) immediately via API; avoid browser-only staging.

### Persistence safety rules

1. Blob artifacts are source of truth; in-memory state is cache only.
2. No UI action should rely on unsaved local state after user leaves/refreshes.
3. If manifest/artifact mismatch is detected, show recoverable warning and last good version.

### Observability + acceptance checks

Track and gate on:

1. `resume_success_rate` (refresh -> state restored without manual rerun)
2. `state_rehydrate_latency_ms`
3. `manifest_artifact_mismatch_count`
4. `rerun_without_need_count` (user forced to rerun despite existing state)

MVP passes when:

1. Refreshing UI restores schema/findings/vocab/questions for >=95% of sessions.
2. Users can resume and continue from previous stage without rerunning discovery.
3. Manifest/version trail supports clear stage-to-stage diff and rollback.

---

## Black-Box DLL MVP Additions (1995-2015 Windows)

### Goal

Close the remaining gaps between the current reverse-engineering pipeline and a sellable product that can generate a 1:1 validated integration surface for undocumented legacy DLLs.

### Deliverable definition

The MVP customer deliverable is three artifacts:

1. **Enriched schema JSON** with verified/inferred/unprobeable coverage labels
2. **Generated Python wrapper** (`client.py`) exposing the DLL through a modern typed interface
3. **Backwards-compatibility validation report** comparing direct ctypes calls versus wrapper calls on known-working inputs

### Capability gaps to add

#### 1. Runtime coverage expansion (P1)

Required to support real legacy DLL call shapes beyond simple scalar params.

Add:

1. Output buffer support as first-class schema + executor behavior
2. Struct and nested-struct input support
3. Pointer ownership/allocation rules
4. Calling convention validation (`cdecl` vs `stdcall`)
5. ANSI/Unicode string path handling
6. 32-bit / 64-bit compatibility execution strategy
7. Handle/token lifecycle tracking (`returns_handle`, `consumes_handle`)

#### 2. Stateful workflow execution (P1)

Required for black-box business DLLs where correctness depends on call order.

Add:

1. Durable init/session state model
2. Function dependency graph (`depends_on`, `requires_init`, `requires_active_state`)
3. Open/use/close lifecycle enforcement
4. Stronger write-policy coverage beyond current contoso-only heuristics
5. Multi-DLL orchestration for producer/consumer dependency chains

#### 3. Hard-function discovery honesty (P1)

The product must distinguish verified behavior from unsupported surfaces.

Add:

1. Boundary probing for numeric parameters
2. Multi-record probing for per-entity variance
3. Safer write-path testing under policy
4. Explicit labels:
  - `verified`
  - `inferred`
  - `unprobeable_current_executor`
  - `unsupported_current_runtime`
5. Coverage report generated directly from findings + schema checkpoints

#### 4. Wrapper generation + validation (P1)

This is the commercial core of the MVP.

Add:

1. Python wrapper generator from enriched schema
2. Direct ctypes vs wrapper comparison harness
3. Function-by-function validation report with exact input/result match status
4. Example usage generation based on known-working calls

Release gate:

1. No function may be marked `verified_1to1` unless direct and wrapper outputs match on stored working inputs.

#### 5. Persistence + resume (P1)

Required so the system behaves like a product instead of a stateless demo.

Add:

1. Blob-backed session manifest and artifact version pointers
2. UI rehydration endpoint (`GET /api/jobs/{id}/state`)
3. Resume-from-prior-session workflow
4. Session history by component/project
5. Schema/version diff view in UI

#### 6. Production learning mode (P2)

Required so integrations improve through real usage instead of repeated full rediscovery.

Add:

1. Redacted wrapper usage telemetry
2. Return-code frequency and drift tracking
3. Controlled rule promotion pipeline
4. Human review queue for medium-confidence schema/vocab proposals
5. Safe rollback when learned rules are wrong

### Product quality bar

For a black-box DLL MVP, the system must be able to say, per function:

1. what was proven,
2. what was inferred,
3. what could not be tested yet,
4. what wrapper behavior is verified 1:1,
5. what prerequisite state/dependency is required.

### Priority order

1. Wrapper generator
2. Backwards-compatibility validator
3. Output buffer / pointer support
4. Persistence + UI rehydration
5. Coverage reporting (`verified` / `inferred` / `unprobeable`)
6. Multi-DLL dependency handling
7. Production learning mode

### Exit criteria for MVP

The black-box DLL MVP is ready when it can:

1. Discover and enrich a target DLL into a reusable schema
2. Generate a Python wrapper from that schema
3. Validate a meaningful subset of functions 1:1 against direct DLL behavior
4. Persist and resume the session state without rerunning discovery
5. Present honest coverage labels for the remaining unsupported or inferred surfaces

---

## 2026-03-20 Addendum — Pipeline Cohesion Fixes

> Analysis of runs `8211c70-run2` and `66d7077-run2-2` revealed inter-stage
> data flow breaks that mask all downstream quality signals. These must be
> fixed before probe depth or quality improvements have any observable effect.
> Full analysis: `sessions/misc/PIPELINE-COHESION.md`
> Diagnostic checklist: `sessions/misc/PIPELINE-DIAGNOSTIC-CHECKLIST.md`

### Phase A — Plumbing fixes (prerequisite for everything below)

| ID | Item | Priority | File | Status |
|----|------|----------|------|--------|
| COH-1 | Register invocables in `_explore_worker` at startup | **Critical** | `api/explore.py:~93` | OPEN |
| COH-2 | Register invocables in `_run_gap_answer_mini_sessions` before loop | **Critical** | `api/explore_gap.py:~246` | OPEN |
| COH-3 | Deduplicate findings to latest-per-function when injecting into chat | High | `api/chat.py:~218` | OPEN |
| COH-4 | Deduplicate findings on save (or add authoritative-status helper) | High | `api/storage.py:~279` | OPEN |

**Validation:** After fixing COH-1/2, run contoso_cs and verify `schema_evolution.json`
shows at least one `changed: true` delta. After COH-3/4, verify `model_context.txt`
findings block has no contradictory per-function entries.

### Phase B — Run + measure (immediately after Phase A)

- [ ] Schema evolution shows deltas at enrichment + discovery + mini-session
- [ ] `invocables_map.json` has semantic param names for probed functions
- [ ] `mini_session_transcript.txt` has no "not found in job" errors
- [ ] `findings.json` has clean per-function final status
- [ ] Gap resolution log reflects post-mini-session truth

### Issue layers revealed by Phase B

Once Phase A is validated, remaining failures sort into:

| Layer | What it means | What to do |
|-------|--------------|------------|
| L2 — Probe quality | Functions probed but wrong/insufficient evidence | Tackle ADD-1 through ADD-4, P2-1, P2-2 |
| L3 — Structural ceilings | Access violations, crypto guards, struct params | Tackle P4-1, P4-2, decompilation-guided probing |

New issues identified during cohesion analysis:

| ID | Item | Priority | Layer | Notes |
|----|------|----------|-------|-------|
| Q-5 | **State mutation across probes** — payment drains balance, later probes fail | Medium | L2 | Need state-reset strategy (re-init between write probes, or probe ordering) |
| C-2 | **Crypto/XOR unlock codes** — CS_UnlockAccount requires XOR-fold to 0xa5, undiscoverable by probing | Low | L3 | Requires decompilation-guided hint injection or brute-force |

---

## UI Control Expansion (planned)

### Gap Resolution Settings — Inherit + Override

Gap resolution currently uses hardcoded constants from `explore_phases.py`, ignoring the
UI controls (floor, fallback, max rounds) that the main explore loop respects.

**Phase 1 — Inherit (✅ done, `3cc5d9b`+):**
Wired `_job_runtime` into `_attempt_gap_resolution` and `_run_gap_answer_mini_sessions`
so they read the same max-rounds/max-tool-calls settings as explore. Falls back to
hardcoded `explore_phases.py` constants when not set. ~15 lines in `explore_gap.py`.

**Phase 2 — Override (future, if needed):**
Add gap-specific overrides in the UI (gap_floor, gap_fallback toggle). Falls back to
explore values when blank. Only add when testing shows gap needs different tuning.

### Clarification Questions Controls

| Control | Default | Why |
|---------|---------|-----|
| `clarification_max_rounds` | 2 | Cap how many times the pipeline pauses for human input. Prevents unbounded wait. |
| `auto_answer_from_hints` | False | When True, pipeline checks the hints file (e.g. contoso_cs.txt) for answers before asking the human. Enables unattended runs when hints are rich enough. |

### Controls NOT planned (intentionally omitted)

- **Write retry budget per-function** — already tuned in code (`_WRITE_RETRY_BUDGET_BY_CLASS`), no user-facing need
- **Sentinel calibration depth** — automatic, works well
- **Gap targeting per-function** — gap analysis already selects targets; manual override adds complexity without value
- **Synthesis model picker** — future consideration if model quality variance becomes an issue
