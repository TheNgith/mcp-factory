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

## .cursorrules

Created at repo root. Contains:
- Canonical names for all core concepts
- File layout reference
- Code style rules (no cryptic abbreviations, no import aliases)
- Pipeline phase reference
- Testing conventions
