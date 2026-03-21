# MCP Factory – Pipeline Cohesion Analysis

> Created 2026-03-20 from analysis of runs `8211c70-contoso-cs-run2` and
> `66d7077-contoso-cs-run2-2` (job `dd66fb6a`).
> This document tracks inter-stage data flow issues and their resolution status.
> Update after each fix and validation run.

---

## Pipeline Stage Map

```
 ┌──────────┐    ┌──────────┐    ┌─────────┐    ┌─────────┐    ┌──────┐
 │ Generate │───►│ Discover │───►│ Refine/ │───►│  Chat   │───►│Output│
 │          │    │          │    │ Gap-Ans │    │         │    │      │
 └────┬─────┘    └────┬─────┘    └────┬────┘    └────┬────┘    └──────┘
      │               │              │              │
  registers        builds          builds        loads
  invocables       local           local         from
  globally         inv_map         inv_map       blob
      │               │              │              │
      ▼               ▼              ▼              ▼
 ┌───────────────────────────────────────────────────────────┐
 │            _JOB_INVOCABLE_MAPS  (in-memory registry)      │
 │            _JOB_FINDINGS        (in-memory cache)         │
 │            Blob Storage         (durable persistence)     │
 └───────────────────────────────────────────────────────────┘
```

---

## Layer Model

The pipeline has three layers of issues. **Layer 1 masks Layers 2–3** — you
cannot diagnose probe quality or structural ceilings while data plumbing is
broken.

### Layer 1 — PLUMBING (data doesn't reach where it should)

These are code bugs where one stage produces data that never reaches the next.

| ID | Disconnect | Files | Status |
|----|-----------|-------|--------|
| D-1 | **Mini-sessions don't register invocables** — `_run_gap_answer_mini_sessions` builds a local `inv_map` but never calls `_register_invocables`, so `_patch_invocable` can't find functions when `enrich_invocable` fires. Result: schema stays frozen across all mini-session checkpoints. | `explore_gap.py:239` → `executor.py:637` → `storage.py:383` | **OPEN** |
| D-2 | **Findings are append-only with no deduplication** — `_save_finding` appends every entry. `CS_ProcessPayment` accumulates 4 entries (error, error, success, success). Chat injects all of them into the system message — the LLM sees conflicting signals. Gap resolution log snapshots pre-loop state showing "error" for functions that succeeded during the loop. | `storage.py:279` → `chat.py:218` | **OPEN** |
| D-3 | **Explore worker doesn't register invocables** — same pattern as D-1 but in the main `_explore_worker`. Works only when the container is warm from `/api/generate`. On container recycle (Azure scale-to-zero), enrichment silently fails. | `explore.py:92` | **OPEN** |
| D-4 | **Vocab knowledge doesn't flow to schema** — this is a *symptom* of D-1/D-3. Vocab correctly learns param meanings (`balance in cents`, `customer_id is CUST-NNN`), but that knowledge reaches the schema only via `enrich_invocable`, which is broken. | vocab.json → invocables_map.json (no bridge) | **Resolves with D-1/D-3** |

### Layer 2 — PROBE QUALITY (data reaches but is wrong/insufficient)

Visible only after Layer 1 is fixed. These affect whether the LLM discovers
the right information during probing.

| ID | Issue | Evidence from last run | Roadmap ref |
|----|-------|----------------------|-------------|
| Q-1 | Sentinel calibration misses write-path codes | Calibrated only `0xFFFFFFFF`, `0xFFFFFFFE`. Missed `0xFFFFFFFB` (write denied) and `0xFFFFFFFC` (account locked) from user hints. | P1-2 / #4 |
| Q-2 | No boundary probing for numeric params | CS_ProcessPayment threshold found by accident (100 works, 1 doesn't). No deliberate min/max sweep. | P2-1 |
| Q-3 | Single-ID probing | Only CUST-001 tested. CUST-002/003 may behave differently (per-record state). | P2-2 |
| Q-4 | Shallow probe depth | Average 2.5 probes/function. Many functions get 0–1 tool calls before auto-finding records failure. | ADD-1 through ADD-4 |
| Q-5 | State mutation across probes | Payment probes drain CUST-001 balance. Later redemption/payment probes fail because balance is 0 — not because the function is broken. No state reset between functions. | New |

### Layer 3 — STRUCTURAL CEILINGS (can't discover with current approach)

Require architectural changes, not tuning.

| ID | Issue | Evidence | Roadmap ref |
|----|-------|----------|-------------|
| C-1 | **Output buffer params** — `undefined *` direction="out" params cause access violations because executor passes strings instead of allocating buffers. CS_GetOrderStatus, CS_LookupCustomer, CS_GetDiagnostics all affected. | Mini-sessions: `CS_GetOrderStatus({"param_1":"ORD-20260315-0117","param_3":64})` → access violation | P4-1 |
| C-2 | **Crypto/XOR unlock codes** — `CS_UnlockAccount` XOR-folds the code string and checks `== 0xa5`. Undiscoverable by probing unless brute-forcing or using decompilation hints. | Decompiled code in invocables_map.json | New — decompilation-guided probing |
| C-3 | **Struct in-params** — functions requiring caller-built structs. Not hit in contoso_cs but will block real-world DLLs. | N/A for this DLL | P4-2 |

---

## Confirmed Data Flow Issues (evidence table)

| What should happen | What actually happens | Proof |
|----|----|----|
| Schema evolves at enrichment, discovery, gap-resolution, mini-session | **66d7077-run2-2**: zero delta across ALL 9 checkpoints (10871 bytes every time) | `schema_evolution.json` |
| `enrich_invocable` patches param names into invocables_map | Returns `"function 'CS_ProcessPayment' not found in job 'dd66fb6a'"` | `mini_session_transcript.txt` |
| Gap answers improve write-function outcomes AND update schema | Findings flip to success (CS_ProcessPayment, CS_ProcessRefund) but schema unchanged | findings.json vs schema_evolution.json |
| **8211c70** (earlier run, same code, warm container) had enrichment working | Pre→post-enrichment: `changed: true, size_delta_bytes: -10588` | 8211c70 schema_evolution.json |
| Findings per function should be authoritative | CS_ProcessPayment has 4 entries: error, error, success, success. Harmonization counts 15 "errors" total. | findings.json, harmonization_report.json |

---

## Fix Plan

### Phase A — Fix plumbing (blocks everything else)

| Fix | File | Line | Change | Risk |
|-----|------|------|--------|------|
| A-1 | `api/explore.py` | ~93 | Add `_register_invocables(job_id, invocables)` after building local `inv_map` at top of `_explore_worker` | Low |
| A-2 | `api/explore_gap.py` | ~246 | Add `_register_invocables(job_id, invocables)` after building local `inv_map`, before mini-session loop | Low |
| A-3 | `api/storage.py` | ~279 | Add deduplication: when injecting findings into chat, use only the latest entry per function | Medium |
| A-4 | `api/chat.py` | ~218 | When building findings_block, deduplicate to most-recent entry per function | Low |

### Phase B — Validate (one run after Phase A)

Run contoso_cs discovery + gap answers. Check:
- [ ] `schema_evolution.json` shows `changed: true` at enrichment + discovery + mini-session
- [ ] `invocables_map.json` has semantic param names (not `param_1`) for probed functions
- [ ] `findings.json` has clean per-function status
- [ ] `harmonization_report` counts match deduplicated function outcomes
- [ ] `_patch_invocable` success messages appear in mini_session_transcript (not "not found")

### Phase C — Diagnose Layer 2/3 (based on Phase B data)

With clean plumbing, the Phase B run reveals:
- Which functions enrich successfully → Layer 1 verified
- Which functions still fail despite enrichment → Layer 2 (probe quality)
- Which functions access-violate regardless → Layer 3 (structural ceiling)

Then prioritize Layer 2/3 fixes based on actual failure data.

---

## Post-Run Diagnostic Checklist

Run this after **every** session save to catch regressions. See
`PIPELINE-DIAGNOSTIC-CHECKLIST.md` for the full automated + manual steps.

Quick version:

1. **Schema moved?** — `schema_evolution.json`: at least one delta `changed: true`
2. **Enrichment applied?** — `invocables_map.json`: check 3 functions for semantic param names
3. **Findings clean?** — `findings.json`: latest entry per function matches expected outcome
4. **Vocab coverage?** — `vocab_coverage.json`: `coverage_score > 0` and no `error_codes_missing`
5. **Sentinel calibration?** — `sentinel_calibration.json`: codes match known DLL sentinels
6. **Mini-sessions enriched?** — `mini_session_transcript.txt`: no "not found in job" messages
7. **Gap resolution effective?** — `gap_resolution_log.json`: at least one function flipped status

---

## Change Log

| Date | What changed | Evidence |
|------|-------------|----------|
| 2026-03-20 | Initial analysis: identified D-1 through D-4, Layer model, fix plan | Runs 8211c70 + 66d7077 |
| 2026-03-20 | Phase A fixes (COH-1–4) committed. Validation run 3cc5d9b (job 22204c7b). See § Run 3cc5d9b Assessment below. | Run 3cc5d9b vs 8211c70 |
| 2026-03-20 | Commit `9b46b3b`: D-5 param auto-enrichment, D-6 gap resolution log, D-7 gap_count metric, dev profile bumped to tool_calls=5 + clarification enabled. Bridge cache TTL set to indefinite. | Code review + prior run analysis |
| 2026-03-20 | Commit `790dd3a`: D-5 wholesale path (backfill params renamed), Ghidra boilerplate description guard. | Run 159c017 vs 3cc5d9b |
| 2026-03-20 | D-8 force-record success gap, D-9 stale invocables refresh, D-10 direction inference on wholesale path. CS_CalculateInterest dropped because deterministic fallback found success but no finding was saved. Gap resolution reverted discovery schema because backfill/gap read stale invocables snapshot. | Run 159c017 analysis |
| 2026-03-20 | D-11: ground-truth override now rewrites finding text to match patched status. Run `5310428` (job 7af1301e): 7 success/6 error — D-8 working (CS_CalculateInterest captured), but schema frozen 02-07 because synthesis saw misleading "sentinel codes" text on patched-success findings → generated no backfill patches. | Run 5310428 analysis |

---

## Run 3cc5d9b Assessment (job 22204c7b) — Phase A validation

**Config:** dev profile, floor=3, max_rounds=2, max_tool_calls=3, gap=✓, clarify=□, deterministic_fallback=✓  
**Comparison baseline:** 8211c70 (job 1259f0e5), deploy profile, max_rounds=3, max_tool_calls=10

### Phase A Fix Verification

| Fix | What we expected | What happened | Verdict |
|-----|-----------------|---------------|----------|
| A-1 (explore.py register invocables) | Schema changes at enrichment even on cold container | ✅ Schema changed at 3 stages (enrichment, discovery, gap-resolution) | **FIXED** |
| A-2 (explore_gap.py register invocables) | Gap resolution changes schema (checkpoint 05 ≠ 04) | ✅ 05-post-gap-resolution differs from 04 (delta = -6040 bytes) | **FIXED** |
| A-3/A-4 (chat.py findings dedup) | System message uses latest finding per function only | ✅ Harmonization shows 10 success / 12 error, no patched_error_to_success contradictions | **FIXED** — dedup working in prompt |

### Disconnect Status Update

| ID | Previous Status | New Status | Notes |
|----|----------------|------------|-------|
| D-1 | OPEN | **CLOSED** | A-2 fixed. Gap resolution now registers invocables. Schema evolves at gap checkpoint. |
| D-2 | OPEN | **MITIGATED** | A-3/A-4 fixed chat dedup. Raw findings.json still append-only (by design). Prompt sees only latest per function. |
| D-3 | OPEN | **CLOSED** | A-1 fixed. Explore worker registers invocables on entry. Schema enrichment works on cold containers. |
| D-4 | Resolves with D-1/D-3 | **PARTIALLY OPEN** | Schema descriptions are enriched (not raw Ghidra), but param names are still `param_N`. Vocab knowledge flows into descriptions but NOT into param key renaming. See new D-5. |

### New Issues Found

| ID | Disconnect | Files | Status |
|----|-----------|-------|--------|
| D-5 | **Param names never renamed** — `enrich_invocable` updates descriptions and `required` fields but never renames `param_1` → `customer_id`. Schema params are `param_N` across ALL sessions. Enrichment changes *what the description says* but not the param key the LLM uses to construct calls. | `storage.py` `_patch_invocable` | **FIXED** (`9b46b3b` per-param path, `790dd3a` wholesale path) — auto-derive semantic names from param descriptions via regex when LLM omits `name` key |
| D-6 | **Auto gap-resolution writes no log** — `_attempt_gap_resolution` (code-driven retry) doesn't write `gap_resolution_log.json`. Only `_run_gap_answer_mini_sessions` (user-answer path) writes the log. Result: session snapshot has no gap_resolution_log.json even when gap resolution ran and changed the schema. | `explore_gap.py` `_attempt_gap_resolution` | **FIXED** (`9b46b3b`) — auto path now writes gap_resolution_log.json with per-function status/confidence/attempts |
| D-7 | **Gap count = 0 despite failed functions** — `session-meta.json` shows `gap_count: 0` even though 3 functions remain in error state. The gap count is set from clarification questions (which were disabled), not from actual function failures. Misleading metric. | `main.py` session-snapshot endpoint | **FIXED** (`9b46b3b`) — gap_count now counts unresolved functions (status≠success); question_count added as separate field |
| D-8 | **Deterministic fallback success not saved** — When the LLM makes 0 direct probe calls but the deterministic fallback gets return=0, force-record fires only for the error branch (`not _observed_successes`). The success branch silently falls through because `_patch_finding` can't find a non-existent finding to update. Result: functions like CS_CalculateInterest are correctly probed but never appear in findings.json. | `explore.py` `_explore_one` forced record block | **FIXED** (this commit) — added success branch: when `_observed_successes` is truthy but `_finding_recorded` is False, save a success finding |
| D-9 | **Stale invocables snapshot poisons backfill + gap resolution** — `_explore_worker` takes an `invocables` snapshot at start. Discovery enriches params via `_patch_invocable` (updating `_JOB_INVOCABLE_MAPS`), but backfill and gap resolution still read the stale snapshot. Backfill rebuilds wholesale param lists from stale data → `_patch_invocable` overwrites enriched params. Gap resolution's system message uses stale descriptions. Net effect: gap resolution schema reverts discovery gains (observed as exact +N / -N byte deltas in schema_evolution). | `explore.py` ~line 1090 → `explore_vocab.py:300` → `storage.py:488` | **FIXED** (this commit) — refresh `invocables` from `_get_current_invocables(job_id)` before backfill and gap resolution |
| D-10 | **Direction inference missing on wholesale param path** — Backfill's wholesale parameter replacement in `_patch_invocable` applies D-5 naming but not direction inference. Ghidra tags all `byte *` params as `direction: "out"`, but most are input strings (customer IDs, order IDs). This causes them to be excluded from `required` in the final MCP schema. | `storage.py` `_patch_invocable` wholesale block | **FIXED** (`5310428`) — direction inference regex now runs on all wholesale-replaced params |
| D-11 | **Ground-truth override doesn't rewrite finding text** — When error auto-record writes `"All N probes returned sentinel codes…"` and then ground-truth override patches `status` to `"success"`, the `finding` text is left unchanged. Synthesis LLM reads the misleading error text → concludes function is undocumented → backfill generates no patches → schema stays frozen for those functions. In run `5310428`, schema checkpoints 02-07 were identical (14,417 bytes) because 6 functions still had Ghidra boilerplate descriptions. | `explore.py` ground-truth override block → `storage.py` `_patch_finding` | **FIXED** (this commit) — ground-truth override now includes a rewritten `finding` field describing the successful call |

### Probe Quality (Layer 2) — Now Visible

With Layer 1 plumbing fixed, Layer 2 issues are now measurable:

| ID | Issue | Evidence from 3cc5d9b | Severity |
|----|-------|----------------------|----------|
| Q-1 | Sentinel calibration misses write-path codes | Still only 2 codes calibrated (0xFFFFFFFE, 0xFFFFFFFF). 0xFFFFFFFB and 0xFFFFFFFC from hints still not auto-calibrated. | Medium |
| Q-4 | Shallow probe depth — now **mitigated** | avg 4.6 probes/fn (60 probes / 13 fn). Floor=3 working. Previous run averaged ~2.5. | **Improved** |
| Q-6 | **dev vs deploy cap profile regression** | New run uses dev (max 3 tool calls) vs previous deploy (max 10). CS_UnlockAccount and CS_ProcessRefund regressed — these write functions need more probing rounds to discover init+unlock+call sequence. | High — config-sensitive |

### Findings Comparison (function-level)

| Function | 8211c70 | 3cc5d9b | Delta |
|----------|---------|---------|-------|
| CS_Initialize | success | success | = |
| CS_GetVersion | success | success | = |
| CS_GetDiagnostics | success | success | = |
| CS_GetAccountBalance | success | success | = |
| CS_GetLoyaltyPoints | success | success | = |
| CS_LookupCustomer | success | success | = |
| CS_CalculateInterest | success | success | = |
| CS_ProcessPayment | success | success | = |
| CS_RedeemLoyaltyPoints | success | success | = |
| CS_GetOrderStatus | error | **success** | **+1** |
| CS_UnlockAccount | success | **error** | **-1** |
| CS_ProcessRefund | success | **error** | **-1** |
| entry | error | error | = |

**Net: 11→10 success.** Getting similar coverage with 1/3 the tool calls per function is a meaningful efficiency gain. The 2 regressions are config-driven (cap profile), not plumbing regressions.

### Schema Quality Comparison

| Metric | 8211c70 | 3cc5d9b |
|--------|---------|----------|
| Post-discovery size | 15,022 B | 16,049 B (+1 KB more content) |
| Descriptions enriched | Yes (semantic) | Yes (semantic + return values) |
| Param names renamed | ❌ still `param_N` | ❌ still `param_N` (D-5 fix in `9b46b3b` — validate next run) |
| Ghidra decompiled C in final schema | Yes (some functions) | No (stripped by enrichment) |
| Gap resolution schema change | Yes (delta -10588) | Yes (delta -6040) |
