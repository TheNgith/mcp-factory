# Path to 12/12 Functions on contoso_cs.dll

> Created 2026-03-23 after overnight data collection and detailed analysis.
> Continuation document for all future sessions. Read this first.

---

## Current State (verified overnight 2026-03-23)

### What works
- Pipeline is cohesive: 0 failed transitions across 30 sessions
- T-17 (sentinel_calibration_outcome) and T-18 (write_unlock_probe_outcome): present in all sessions
- Q14 (union merger): working, merges legs correctly
- Q15 (ablation orchestrator): working, all 4 variable families tested, promotions happened
- Q16 (sentinel calibration): Phase 0.5 calibration works, stage-boundary recalibration works
- Q17 (coordinator): full playbook execution with 5 cycles, structured reports
- Naming coherency: cleaned up in previous session
- `functions_success` field: populated everywhere

### What doesn't work — the 3 blockers to 12/12

| Blocker | Affects | Root cause |
|---------|---------|------------|
| B1: Write-unlock is permanent | ProcessPayment, ProcessRefund, RedeemLoyaltyPoints, UnlockAccount | `_write_policy_precheck` returns `dependency_missing` for ALL write fns when `unlock_result.unlocked == False`, and unlock is never re-attempted |
| B2: Write-unlock tests with empty args | All write functions | `_probe_write_unlock` calls write fns with `{}` after init — but write fns need real params (customer IDs, amounts) |
| B3: Enrichment not verified | GetAccountBalance, CS_CalculateInterest, others | Pipeline marks fns as "success" when enrichment/backfill added plausible args, but never executes those args against the DLL |

### Function scoreboard (from overnight leg-c01, 9/13 raw)

| Function | Status | Honest assessment |
|----------|--------|-------------------|
| CS_Initialize | success | Truly verified (return=0 confirmed) |
| CS_GetVersion | success | Truly verified |
| CS_GetCustomerName | success | Truly verified |
| CS_GetLoyaltyPoints | success | Enriched, unverified |
| CS_GetAccountBalance | success | Enriched, unverified |
| CS_CalculateInterest | success | Enriched, unverified |
| CS_ValidateAccount | success | Truly verified |
| CS_GetTransactionHistory | success | Enriched, unverified |
| CS_CheckAccountStatus | success | Truly verified |
| CS_ProcessPayment | error | Write-blocked (dependency_missing) |
| CS_ProcessRefund | error | Write-blocked (dependency_missing) |
| CS_RedeemLoyaltyPoints | error | Write-blocked (dependency_missing) |
| CS_UnlockAccount | error | Write-blocked (dependency_missing) |
| entry | N/A | Not a real function (DLL entry point) |

**Honest count: 5 truly verified, 4 enriched/unverified, 4 write-blocked = 12 real functions**

---

## MVP Definition Update (2026-03-23)

MVP = 12/12 verified functions on contoso_cs.dll, then generalize to a folder of DLLs.
Entry point is excluded (not a real function). "Verified" means the pipeline executed
the function with the enriched args and got return=0 (or expected non-sentinel).

---

## The Three Changes Required

### Change A: Write-unlock re-probe at stage boundaries (Q16 Section 5)

**The problem**: `_probe_write_unlock` runs once at Phase 1. If it fails, `unlocked=False`
is permanent for the entire session. Q16 Section 5 explicitly says to re-try after
sentinel recalibration, but this was never implemented.

**The fix**: After the stage-boundary sentinel recalibration (already at line ~1173 of
`explore.py`), if new sentinel codes were resolved, re-run `_probe_write_unlock` with
updated context. If it succeeds, update `ctx.unlock_result` and `ctx.write_unlock_block`.

**Where to add**: `explore.py` lines 1195-1217, immediately after `ctx.sentinels.update(boundary_resolved)`.

**Code sketch**:
```python
# After stage-boundary recalibration resolves new codes:
if boundary_resolved and not ctx.unlock_result.get("unlocked"):
    logger.info("[%s] re-probing write-unlock with %d new sentinel codes",
                ctx.job_id, len(boundary_resolved))
    ctx.unlock_result = _probe_write_unlock(ctx.invocables, ctx.dll_strings)
    if ctx.unlock_result.get("unlocked"):
        ctx.write_unlock_block = "\nWRITE MODE ACTIVE: ..."
```

### Change B: Write-unlock probe with real args (not empty calls)

**The problem**: `_probe_write_unlock` in `explore_phases.py` line 270 calls write
functions with `_execute_tool(inv_map[_wfn], {})` — empty args. For contoso_cs.dll,
write functions need real parameters (customer_id, amount). An empty-arg call returns
a sentinel even if init succeeded.

**The fix**: Two improvements:
1. After init, test write functions with args derived from vocab hints (id_formats, etc.)
   instead of empty dicts.
2. Allow the probe loop itself to try write functions with a budget (remove the
   permanent block from `_write_policy_precheck`).

**Where to change**:
- `explore_phases.py` `_probe_write_unlock`: Pass vocab/hints to generate real test args
- `explore_helpers.py` `_write_policy_precheck` line 332: Instead of permanently blocking
  on `unlocked=False`, allow a limited retry budget

**The `dependency_missing` policy change**:
```python
# Current (line 332 of explore_helpers.py):
if unlock_result is not None and not unlock_result.get("unlocked"):
    return False, "dependency_missing", "write path requires initialization/unlock sequence"

# New: allow write probes with a budget even when unlock failed,
# because the LLM might discover the right init sequence during probing
if unlock_result is not None and not unlock_result.get("unlocked"):
    # Still inject a warning into the prompt, but don't hard-block
    pass  # Allow the probe to proceed — policy_exhausted will catch bad calls
```

### Change C: Enrichment verification pass (close the loop)

**The problem**: The pipeline marks functions as "success" during backfill/enrichment
when plausible args are inferred, but never verifies them against the DLL. The overnight
data showed 4 functions marked "success" that were never actually executed with their
enriched args.

**The fix**: Add a verification sub-phase after Phase 7 (backfill) that:
1. For each function with `status=success` and a `working_call`:
   - Execute the function with the `working_call` args against the DLL
   - If return=0: mark as `verified`
   - If return=sentinel: mark as `inferred` (enrichment was plausible but unverified)
2. Upload `verification-report.json` as a session artifact
3. Add a new transition T-19 (`enrichment_verification_outcome`)

**Where to add**: New function `_run_phase_7b_verify_enrichment(ctx)` called between
Phase 7 (backfill) and Phase 8 (gap resolution) in `explore.py`.

---

## Implementation Order

1. **Change B first** (remove permanent write block) — this is the highest leverage
   change because it unblocks 4 functions immediately. Even without perfect args,
   the LLM probe loop might discover the right init→write sequence.

2. **Change A second** (re-probe at stage boundaries) — this adds the Q16 Section 5
   "try again with better knowledge" loop.

3. **Change C third** (verification pass) — this is what turns "enriched/unverified"
   into "verified" and is the commercial differentiator.

---

## What Q14-Q17 Still Need

| Question | Implementation | Missing piece |
|----------|---------------|---------------|
| Q14 | `union_merger.py` | Nothing — complete |
| Q15 | `run_set_orchestrator.py` | Nothing — complete |
| Q16 | `explore_phases.py`, `explore.py` | **Section 5: write-unlock re-probe** (Change A) |
| Q17 | `run_coordinator.py` | Phase C (sentinel→re-unlock) and Phase D (write-function batch) depend on Changes A+B |

Q17 Phase C and Phase D are already described in the coordinator playbook but they
can't trigger because Q16 Section 5 was never built. Once Changes A+B are in,
Phase C/D will work automatically — the coordinator already checks `write_unlock_outcome`.

---

## The Write-Unlock Sequence for contoso_cs.dll

Based on overnight probe logs and static analysis:

1. `CS_Initialize()` — must be called first (already handled by curriculum order)
2. Write functions need: customer_id (format: `CUST-XXX`), amount (positive integer)
3. The sentinel `0xFFFFFFFB` means "write not allowed" — this is what the pipeline sees

The init sequence is likely: `CS_Initialize(mode)` where mode must be a specific value.
The pipeline already tries modes 0,1,2,4,8,16,256,512 — but it tests with empty args
on the write function afterward. The fix is to test with `CS_ProcessPayment(CUST-001, 100)`
after each init attempt, not `CS_ProcessPayment()`.

---

## Test Results — 2026-03-23 Afternoon

### Test 1 (commit 90761c3): Write-unlock + verification changes

- **Result**: 9/13 success, 4 error (same count as overnight)
- **Write-unlock**: still blocked (13 attempts, now with real args)
- **Phase 7b**: did NOT run (bug: `working_call.get("args")` returned None
  because `working_call` IS the args dict, not `{args: {...}}`)
- **Write probes**: `schema_missing` policy still blocking deterministic fallback
  (required_any keys didn't match param_N names from fallback)
- **Key insight**: CS_ProcessRefund enriched to "success" via backfill, not probed

### Test 2 (commit 68ee906): Fix schema_missing + Phase 7b format

- **Result**: 9/13 success, 4 error
- **Phase 7b**: WORKING — verified CS_GetDiagnostics and CS_CalculateInterest
- **Write probes**: Now actually probing write functions!
  - CS_ProcessPayment: 2 direct tool calls (was 0), still got sentinels
  - CS_RedeemLoyaltyPoints: 2 direct tool calls (was 0), still got sentinels
  - CS_ProcessRefund: 2 direct tool calls, still got sentinels
  - CS_UnlockAccount: 1 direct tool call, returned 0xFFFFFFFE (account not found)
- **No more policy blocks**: `write_policy_events: []` on all write functions
- **Honest scorecard after Test 2**:

| Function | Status | Verification | Notes |
|----------|--------|-------------|-------|
| CS_Initialize | success | (no args to verify) | Truly verified, return=0 |
| CS_GetVersion | success | (no args) | Truly verified, return=131841 |
| CS_GetDiagnostics | success | **verified** | Phase 7b confirmed |
| CS_CalculateInterest | success | **verified** | Phase 7b confirmed |
| CS_GetAccountBalance | success | unverified | Enriched, param_1=CUST-001 |
| CS_GetLoyaltyPoints | success | unverified | Enriched, param_1=CUST-001 |
| CS_GetOrderStatus | success | unverified | Enriched, param_1=ORD-20040301-0042 |
| CS_LookupCustomer | success | unverified | Enriched, param_1=CUST-001 |
| CS_ProcessPayment | success | unverified | Enriched via backfill, never returned 0 |
| CS_ProcessRefund | error | — | Probed, sentinel returned |
| CS_RedeemLoyaltyPoints | error | — | Probed, sentinel returned |
| CS_UnlockAccount | error | — | Returns 0xFFFFFFFE (account not found) |
| entry | error | — | Not a real function |

### What's left for 12/12

1. **Verify the 4 enriched-but-unverified functions** — Phase 7b needs to verify
   GetAccountBalance, GetLoyaltyPoints, GetOrderStatus, LookupCustomer, ProcessPayment.
   These have working_call args but Phase 7b may not have reached them (need to check).

2. **Crack the write functions** — the LLM gets 8 tool calls and tries, but can't
   find the right init+args combination. Options:
   a. Increase tool budget for write functions specifically
   b. Add the init call as a mandatory prefix in the write-unlock block
   c. Pre-seed the LLM with known-good IDs from static analysis
   d. Multi-stage probing: probe read functions first, use their outputs as write args

3. **Rate limiting (429)** — still kills some LLM probes. Running sequentially helps.

---

## Circular Pipeline Test Results (2026-03-23, commit 4e1f2b6)

### 3-Iteration Test Results

| Metric | Iter 1 (Cold) | Iter 2 (Warm) | Iter 3 (Hot) |
|--------|---------------|---------------|--------------|
| Job ID | 2af1a37c | 9157f914 | d954706d |
| Functions explored | 13/13 | 13/13 | 13/13 |
| Functions success | 8 | — | — |
| Write unlock | **resolved** | blocked | blocked |
| Resolved by | **MC-6** | — | — |
| Verified functions | 2 | 0 | 2 |
| Prior findings seeded | 0 | 8 | 0 |
| Runtime | ~29 min | ~37 min | ~32 min |

### Critical Finding: HOW Write-Unlock Was Cracked

MC-6 (`_mc6_post_gap_resolution`) performed a comprehensive sweep:
- Tried `CS_Initialize()` with 19+ modes (0-16, 32, 64, 128, 256, 512, and no-args)
- After each init, tested each write function with real args from vocab (CUST-001, amounts)
- **CS_ProcessRefund succeeded** — returned 0 with `(param_1="CUST-001", param_2=100)`

**Why CS_ProcessRefund doesn't need the lock**: Reading the Ghidra decompilation:
- `CS_ProcessPayment` checks `(&DAT_180021b6e)[lVar3 * 0xb8] & 2` — if bit 2 is set (account locked), payment is blocked
- `CS_RedeemLoyaltyPoints` checks the same lock bit — returns `0xFFFFFFFC` if locked
- **CS_ProcessRefund does NOT check the lock bit** — it just looks up the customer, adds the refund to their balance, and returns 0
- `CS_UnlockAccount` is the mechanism that clears bit 2 — requires a param_2 string whose bytes XOR to 0xA5

So the "write-unlock resolved" was really "found a write function that doesn't have the lock gate."

### The Real Lock Mechanism (from Ghidra)

```
CS_UnlockAccount(param_1=customer_id, param_2=unlock_code):
  1. strlen(param_2) must be > 3
  2. XOR all bytes of param_2 together
  3. If XOR result == 0xA5 (165 decimal):
     → Clears bit 2 of customer flags (account unlocked)
     → Returns 0
  4. Else: returns 0xFFFFFFFE
```

Functions behind the lock:
- CS_ProcessPayment (checks bit 2)
- CS_RedeemLoyaltyPoints (checks bit 2)

Functions NOT behind the lock:
- CS_ProcessRefund (no bit check — always works with valid customer)
- CS_UnlockAccount (IS the lock mechanism)

### Sentinel Code Map (from Ghidra, definitive)

| Code | Hex | Meaning | Where |
|------|-----|---------|-------|
| 0xFFFFFFFE | -2 | Null argument / invalid parameter | All functions |
| 0xFFFFFFFF | -1 | Customer/entity not found | Lookup functions |
| 0xFFFFFFFC | -4 | Account locked (bit 2 set) | ProcessPayment, RedeemLoyaltyPoints |
| 0xFFFFFFFB | -5 | Insufficient balance/points | RedeemLoyaltyPoints, ProcessPayment |
| 0xFFFFFFFD | -3 | Overflow (balance would wrap) | ProcessRefund |

### Why Iterations 2 and 3 Failed to Benefit

1. **Write-unlock is per-DLL-instance**: CS_Initialize() populates an in-memory customer database. Each container gets a fresh DLL instance. The `CS_ProcessRefund("CUST-001", 100) → 0` result from iteration 1 isn't automatically available in iteration 2's container.

2. **Missing init replay**: The winning sequence wasn't persisted or replayed. Fix committed in 5eb400d:
   - `winning_init_sequence.json` now saved to blob after MC cracks write-unlock
   - Phase 1 of warm/hot starts replays the winning sequence before trying brute-force
   - If replay succeeds, skips the 13-attempt brute-force sweep entirely

3. **Seeding gap**: Iteration 3 showed `prior_findings_seeded: 0` despite having iteration 2 as its prior. Need to investigate the seeding path.

4. **New: re-probe after unlock**: When any MC cracks write-unlock, all previously-failed write functions are immediately re-probed with the now-active init state. Fix committed in 5eb400d.

---

## HOW to Extrapolate This to ALL DLLs — Generic Strategy

### The 4-Layer Unlock Pattern

Every DLL we've studied follows some combination of these layers:

**Layer 1 — Initialization**: Nearly universal. A global state must be set before functions work.
- Pattern: function checks a flag (e.g., `DAT_XXX == 0`) and calls an init routine
- contoso_cs: `CS_Initialize()` → `FUN_180001eb0()` populates database
- Discovery: Call the init function with no args, then with modes 0-16
- Time: ~5 seconds of probing

**Layer 2 — Entity Existence**: Functions need valid entity IDs that exist in the database.
- Pattern: function loops through a table comparing `param_1` against stored IDs
- contoso_cs: Hardcoded IDs `CUST-001`, `CUST-004`, `ORD-20040301-0042`
- Discovery: Static analysis extracts string constants (already in the pipeline)
- Time: Already handled by vocab/hints

**Layer 3 — Access Control / Lock Bits**: Some functions check permission flags on entities.
- Pattern: function checks a bit flag on the entity record before proceeding
- contoso_cs: bit 2 of `DAT_180021b6e[customer_idx]` = account locked
- Discovery: Requires either:
  a) Finding the unlock function (CS_UnlockAccount) and its checksum mechanism
  b) Noticing that different write functions have different error codes (0xFFFFFFFC vs 0xFFFFFFFB)
- Time: This is where most pipeline time is spent

**Layer 4 — Business Logic Guards**: Functions validate business constraints.
- Pattern: balance checks, overflow guards, format validation
- contoso_cs: `param_2 <= balance` for ProcessPayment, no overflow for ProcessRefund
- Discovery: Use known-good values from read functions (Chain Read → Write strategy)
- Time: Quick once Layer 3 is resolved

### Generic Pipeline Strategy for Any DLL

1. **Phase 0**: Static analysis — extract all string constants, hardcoded IDs, format patterns
2. **Phase 0.5**: Sentinel calibration — call every function with null/empty args, map return codes
3. **Phase 1**: Init sweep — try the init function with modes 0-16+ and test a read function
4. **Phase 3**: Probe loop — probe ALL functions (read first, write second) with extracted IDs
5. **MC-3**: Classify write-unlock failure — are errors uniform (init issue) or varied (per-function)?
6. **Phase 7b**: Verify enrichment — execute discovered working_calls against the DLL
7. **MC-5**: Chain read → write — use verified read outputs as write function args
8. **MC-6**: Comprehensive unlock — try every init mode × every ID × every amount

**The key insight**: Not all write functions are equally locked. The pipeline should try ALL write functions independently rather than treating "write-unlock" as a single binary state. CS_ProcessRefund was unlockable without CS_UnlockAccount — the pipeline discovered this because MC-6 tried each write function separately.

### What Would Crack the Remaining Functions

For contoso_cs.dll specifically:
- **CS_ProcessPayment**: Needs CS_UnlockAccount first (bit 2 must be cleared)
- **CS_RedeemLoyaltyPoints**: Same — needs CS_UnlockAccount first
- **CS_UnlockAccount**: Needs a string whose bytes XOR to 0xA5. This is discoverable from Ghidra decompilation but impractical to brute-force via API. The pipeline needs to:
  1. Detect the XOR pattern in the decompiled code
  2. Generate a valid unlock code (e.g., any 4+ byte string XORing to 0xA5)
  3. Example: bytes [0xA5, 0x41, 0x41, 0x41, 0x41] XOR to 0xA5^0x41^0x41^0x41^0x41 = 0xA5 (since pairs cancel)

This is a **code-reasoning task**, not a brute-force task. The LLM needs to read the Ghidra decompilation and understand the XOR checksum pattern. This is exactly the kind of insight that should be fed to the LLM as context during the write-function probe.

---

## Test Results — 2026-03-24 (Post-Restructure, commit a4d4981)

### Pipeline Restructure Completed
The entire `api/explore*.py` flat file layout was restructured into `api/pipeline/`
stage-based package with checkpoint system. All 8 stages have their own subdirectory,
CONTEXT.md manifests, and the checkpoint system is integrated into the orchestrator.

### Run: Job a6d8f6ac (contoso_cs.dll)

| Function | Status | Notes |
|----------|--------|-------|
| CS_Initialize | success | Working call: {} |
| CS_GetDiagnostics | success | Working call: {param_2: 1} |
| CS_GetVersion | success | Working call: {} |
| CS_GetAccountBalance | success | Working call found |
| CS_GetLoyaltyPoints | success | Working call found |
| CS_LookupCustomer | success | Working call found |
| CS_GetOrderStatus | success | Working call found |
| CS_CalculateInterest | success | Working call found |
| CS_ProcessRefund | success | Write fn — no lock gate |
| CS_ProcessPayment | error | Needs unlock first |
| CS_RedeemLoyaltyPoints | error | Needs unlock first |
| CS_UnlockAccount | error | XOR 0xA5 code never tested by LLM |
| entry | error | Not a real function |

**Score: 9/12 real functions**

### Key Findings

1. **Session snapshot now captures reasoning artifacts**: probe-round-reasoning,
   probe-stop-reasons, probe-strategy-summary, sentinel-calibration-decisions,
   param-rename-decisions, synthesis-input-snapshot, backfill-decision-log,
   and all checkpoint data.

2. **Explore endpoint fallback fixed**: Now checks `result.invocables` from job
   status when blob/memory miss (fixes post-deploy 404).

3. **Pipeline thread hung after probe loop**: The stage-boundary re-calibration
   was running `_probe_write_unlock` without a timeout. Fixed with 120s cap
   using ThreadPoolExecutor.

4. **Status updates added**: Each post-probe stage now reports its name
   (S-03: Reconciliation, S-04: Synthesis, etc.) so we can see exactly where
   the pipeline is during the long post-probe phase.

5. **Output pointer params fixed**: `_build_write_test_args` was setting
   `uint *` output params to `1` (integer), causing access violations when
   the DLL tried to write to address 0x1. Now leaves output pointers absent
   so the bridge allocates proper buffers.

6. **CS_UnlockAccount returned 0 once**: With empty args (register garbage).
   The LLM probe loop never tried XOR-valid codes — it used "test", "abcd",
   "unlock", "aaaa" which all XOR to wrong values.

### Root Cause for Missing 3 Functions

The pipeline correctly identifies from Ghidra decompilation:
- XOR target = 0xA5
- Dependency chains: UnlockAccount → ProcessPayment, RedeemLoyaltyPoints
- Flag checks: bit 2

But the code-reasoning XOR codes are only tried in Phase 1, NOT during the
LLM probe loop. The LLM doesn't know about the XOR requirement because the
`doc_comment` (Ghidra decompiled C) context wasn't being surfaced effectively
enough for the LLM to independently discover the pattern.

### What Needs to Happen for 12/12

1. **Ensure `include_doc_comment=true` is passed** so the LLM sees the
   decompiled C code during probing.
2. **Improve Phase 1 code-reasoning**: The XOR codes ARE mathematically
   correct (UTF-8 bytes XOR to 0xA5). The failure was that write-function
   testing after unlock used broken args (output pointer issue — now fixed).
3. **After Phase 1 fix**: If CS_UnlockAccount returns 0 with XOR code,
   then CS_ProcessPayment and CS_RedeemLoyaltyPoints should work because
   the lock bit gets cleared.

---

## Overnight Testing Plan (2026-03-24, for Sonnet)

### Prerequisites — Local Setup

The Azure VM can be deallocated. Run everything locally on Windows:

**Terminal 1 — Bridge (port 9100):**
```powershell
cd C:\Users\evanw\Downloads\capstone_project\mcp-factory
.\.venv\Scripts\python.exe scripts\gui_bridge.py
```
The bridge defaults to port 9100.

**Terminal 2 — API (port 8000):**
```powershell
cd C:\Users\evanw\Downloads\capstone_project\mcp-factory
$env:GUI_BRIDGE_URL = "http://localhost:9100"
$env:GUI_BRIDGE_SECRET = "local-dev-key"
$env:MCP_FACTORY_API_KEY = ""  # disable auth for local testing
.\.venv\Scripts\python.exe -m uvicorn api.main:app --host 0.0.0.0 --port 8000
```

**Note**: Set `PIPELINE_API_KEY=""` or leave unset to skip auth locally.
The bridge secret just needs to match between API and bridge — any non-empty
string works for local dev.

### Test Sequence (8 hours budget)

#### Test 1: Verify Phase 1 Fix (~30 min)
**Goal**: Confirm the output-pointer fix in `_build_write_test_args` lets
Phase 1 properly test write functions after CS_UnlockAccount returns 0.

```powershell
# Upload DLL
$resp = Invoke-RestMethod -Uri "http://localhost:8000/api/analyze" `
  -Method POST -InFile "C:\Users\evanw\Downloads\mcp-test-binaries\contoso_cs.dll"

# Start explore with doc_comment enabled
$body = @{
  hints = "contoso_cs.dll: 12 functions. CS_Initialize(mode) sets up. CS_UnlockAccount needs 4-byte code XORing to 0xA5. include_doc_comment=true."
  use_cases = "Full autonomous exploration with code reasoning."
  include_doc_comment = $true
  max_rounds = 6
  max_tool_calls = 12
} | ConvertTo-Json
Invoke-RestMethod -Uri "http://localhost:8000/api/jobs/$($resp.job_id)/explore" `
  -Method POST -ContentType "application/json" -Body $body
```

Poll until done. Save session. Check:
- Did Phase 1 `_probe_write_unlock` reach code-reasoning strategy?
- Did CS_UnlockAccount return 0 with any XOR code?
- Did write functions (CS_ProcessPayment) return 0 afterward?
- Check `write_unlock_probe.json` and `checkpoints/latest.json`

#### Test 2: Checkpoint-Focused Run (~20 min)
**Goal**: Use checkpoint from Test 1 to skip S-00/S-01 and focus the probe
loop on the 3 failing functions only.

```powershell
$body = @{
  hints = "Focus on CS_UnlockAccount, CS_ProcessPayment, CS_RedeemLoyaltyPoints."
  checkpoint_id = "<job_id_from_test_1>"
  focus_functions = @("CS_UnlockAccount","CS_ProcessPayment","CS_RedeemLoyaltyPoints")
  max_rounds = 8
  max_tool_calls = 14
  include_doc_comment = $true
} | ConvertTo-Json
```

#### Test 3: With Explicit XOR Hint (~20 min)
**Goal**: Give the LLM a direct hint about the XOR mechanism to see if
better context solves the problem.

```powershell
$body = @{
  hints = "CS_UnlockAccount(param_1=customer_id, param_2=unlock_code). The DLL XORs all bytes of param_2 and checks if result == 0xA5. Generate a 4+ byte string whose bytes XOR to 0xA5. Example: bytes 0x41,0x42,0x43,0xE5 XOR to 0xA5. Call CS_Initialize() first, then CS_UnlockAccount(CUST-001, <code>), then test CS_ProcessPayment."
  include_doc_comment = $true
  max_rounds = 8
  max_tool_calls = 14
} | ConvertTo-Json
```

#### Test 4: Circular Pipeline (3 iterations, ~90 min)
**Goal**: Run cold → warm → hot start pipeline with prior_job_id chaining.

- Iteration 1: Fresh start, no prior
- Iteration 2: `prior_job_id = <iter1_job_id>`
- Iteration 3: `prior_job_id = <iter2_job_id>`

Check: Does the winning init sequence persist? Does iteration 2 start with
seeded findings? Do MC coordinators improve across iterations?

#### Test 5: Parameter Sweep (~2 hours)
**Goal**: Find optimal pipeline parameters.

Run 4 configurations in sequence:
1. `max_rounds=4, max_tool_calls=8` (lean)
2. `max_rounds=8, max_tool_calls=14` (generous)
3. `max_rounds=6, max_tool_calls=12, context_density=full` (default)
4. `max_rounds=10, max_tool_calls=20` (maximum)

Compare: functions_success, runtime, verification counts.

#### Test 6: Regression on Other Saved Sessions
**Goal**: Ensure restructure didn't break anything.

Re-run save-session on any existing job IDs from the deployed API and compare
dashboard-row.json metrics against `sessions/_runs/` baselines.

### What to Record for Each Test

Save session with:
```powershell
powershell -File scripts\save-session.ps1 `
  -JobId <id> -ApiUrl "http://localhost:8000" `
  -OutDir "sessions\_runs\2026-03-24-overnight-test-N" `
  -CompatibilityMode
```

For each test, record in `sessions/_runs/<folder>/human/summary.md`:
- Functions success count
- Write unlock outcome
- Which MC coordinator made progress
- Any error stages
- Runtime duration

---

## .cursorrules

Created at repo root. Contains:
- Canonical names for all core concepts
- File layout reference (updated for api/pipeline/ restructure)
- Code style rules (no cryptic abbreviations, no import aliases)
- Pipeline phase reference (11 phases + 4 MC checkpoints)
- Checkpoint system reference
- Testing conventions
