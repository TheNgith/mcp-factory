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

| ID | Status | Completed in commit |
|----|--------|---------------------|
| P0-A | ✅ Done | next |
| P0-B | ✅ Done | next |
| P0-C | ✅ Done | next |
| P1-1 | ✅ Done | next |
| P1-2 | ✅ Done | next |
| P1-3 | ✅ Done | next |
| P2-1 | ✅ Done | next |
| P2-2 | ✅ Done | next |
| P3-1 | ✅ Done | next |
| P3-2 | ✅ Done | next |
| P3-3 | ✅ Done | next |
| P4-1 | ✅ Done | next |
| P5-A | ✅ Done | next |
| P5-B | ✅ Done | next |
| P5-C | ✅ Done | next |
| P6   | ✅ Done | next |
| P7-A | ✅ Done | next |
| P8-A | ✅ Done | next |

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
| 0-A | Fix `component: "unknown"` in `main.py` | ⬜ | — |
| 0-B | Fix `known_ids` key in `save-session.ps1` | ⬜ | — |
| 0-C | Fix `index.json` malformed first entry | ⬜ | — |
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

*Last updated: 2026-03-17 from run `2026-03-17-33f9114-unknown-run2`*
