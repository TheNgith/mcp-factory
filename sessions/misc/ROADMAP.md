# MCP Factory – Roadmap

> Priority-ordered work items derived from analysis of run `2026-03-17-33f9114-unknown-run2`.
> Update this file as items are completed or re-prioritized.

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

---

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
