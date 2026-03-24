# Pipeline Checkpoint System

## Problem Statement

The pipeline takes 30-45 minutes per full run. 10/12 functions on `contoso_cs.dll`
are consistently successful across 4 consecutive test runs. Re-probing them wastes
API budget and time. We need a way to "lock in" verified functions and focus
iteration cycles only on the remaining failures.

## Checkpoint Design

### What Gets Checkpointed

A **checkpoint** is a snapshot of proven pipeline state for a specific DLL, containing:

| Artifact | Purpose |
|---|---|
| `checkpoint-meta.json` | DLL hash, timestamp, function count, success count |
| `checkpoint-findings.json` | Full findings array — only `status: "success"` entries with verified `working_call` |
| `checkpoint-vocab.json` | Accumulated vocabulary (id_formats, sentinel codes, semantic names) |
| `checkpoint-sentinels.json` | Calibrated sentinel table with human-readable names |
| `checkpoint-init-sequence.json` | Winning initialization sequence (if discovered) |
| `checkpoint-invocables.json` | Enriched invocable schemas with semantic param names |

### Checkpoint Lifecycle

```
[Full Run] ──→ [Verify 10/12 success] ──→ [Create Checkpoint]
                                                  │
                                                  ▼
[Focused Run] ──→ [Load Checkpoint] ──→ [Skip checkpointed fns]
       │                                    │
       │              ┌─────────────────────┘
       │              ▼
       └──→ [Probe ONLY failing fns with full context from checkpoint]
                      │
                      ▼
              [If new success → Update Checkpoint]
              [If still failing → Log attempt, try next strategy]
```

### Checkpoint Creation Rules

A function is eligible for checkpointing when ALL of:
1. `status == "success"` in findings
2. `working_call` is non-null (we have reproducible args)
3. The function returned 0 in at least 2 separate pipeline runs (consistency)
4. The function is NOT `entry` (DllMain — not a real API function)

### Checkpoint Loading (Focused Run Mode)

When `runtime.checkpoint_id` is provided:
1. Load checkpoint artifacts from blob storage
2. Seed `ctx.vocab`, `ctx.sentinels`, `ctx.findings` from checkpoint
3. Mark checkpointed functions in `ctx.already_explored`
4. Run Phase 0.5 (sentinel calibration) — fast, uses checkpoint sentinels as base
5. Run Phase 1 (write-unlock) — uses checkpoint init sequence + new strategies
6. Run Phase 3 (probe loop) — **ONLY for non-checkpointed functions**
7. Run all post-probe phases normally (synthesis, verification, gap resolution)

### Focused Run Benefits

| Metric | Full Run | Focused Run |
|---|---|---|
| Functions probed | 13 | 2-3 |
| LLM calls | ~50-80 | ~10-15 |
| 429 rate limit risk | High | Low |
| Wall clock time | 30-45 min | 8-12 min |
| API cost | ~$2-3 | ~$0.50 |

### Storage Path

```
checkpoints/
  contoso_cs/
    latest/           ← symlink to most recent
    2026-03-24-v1/
      checkpoint-meta.json
      checkpoint-findings.json
      checkpoint-vocab.json
      checkpoint-sentinels.json
      checkpoint-init-sequence.json
      checkpoint-invocables.json
    2026-03-24-v2/    ← after a focused run improves results
      ...
```

In Azure Blob Storage: `checkpoints/{dll_hash}/{version}/`

### API Integration

```
POST /api/jobs/{job_id}/explore
{
  "checkpoint_id": "contoso_cs/2026-03-24-v1",   // load this checkpoint
  "focus_functions": ["CS_UnlockAccount", "CS_RedeemLoyaltyPoints"],  // only probe these
  "runtime": { ... }
}
```

## Current Checkpoint Candidates (contoso_cs.dll)

### Verified Across 4 Runs (Checkpoint-Ready)

| Function | Status | Working Call | Runs Verified |
|---|---|---|---|
| CS_Initialize | success | `{}` | 4/4 |
| CS_GetVersion | success | `{}` | 4/4 |
| CS_GetDiagnostics | success | `{param_2: 64}` | 4/4 |
| CS_GetAccountBalance | success | `{param_1: "CUST-001"}` | 4/4 |
| CS_GetLoyaltyPoints | success | `{param_1: "CUST-001"}` | 4/4 |
| CS_GetOrderStatus | success | `{param_1: "ORD-20040301-0042", param_3: 64}` | 4/4 |
| CS_LookupCustomer | success | `{param_1: "CUST-001", param_3: 64}` | 4/4 |
| CS_CalculateInterest | success | `{param_1: 0, param_2: 0, param_3: 0}` | 4/4 |
| CS_ProcessPayment | success | `{param_1: "CUST-001", param_2: 1}` | 3/4 |
| CS_ProcessRefund | success | `{param_1: "CUST-001", param_2: 1, param_3: "..."}` | 4/4 |

### Still Failing (Focus Targets)

| Function | Blocker | Root Cause |
|---|---|---|
| CS_UnlockAccount | Bridge treats `byte *` as output buffer | Bridge VM has old executor code; args arrive as null |
| CS_RedeemLoyaltyPoints | Depends on CS_UnlockAccount | Account must be unlocked first (flag bit 2) |

## Critical Bridge Fix: DLL State Persistence

### Root Cause (Discovered 2026-03-24)

The bridge was calling `FreeLibrary(lib._handle)` after every `/execute` call.
This unloaded the DLL from memory, destroying ALL in-memory state:
- Customer records loaded by `CS_Initialize`
- Account unlock flags set by `CS_UnlockAccount`
- Loyalty points earned by `CS_ProcessPayment`

This is why CS_RedeemLoyaltyPoints always returned `0xFFFFFFFB` (insufficient
points): loyalty points earned via CS_ProcessPayment were lost before
CS_RedeemLoyaltyPoints could run.

### Fix Applied

In `scripts/gui_bridge.py`:
- Added `_DLL_CACHE` — keeps loaded DLL handles across `/execute` calls
- Added `_flush_dll_cache()` — called by `/analyze` (before overwriting DLL files)
- Added `POST /flush-dll-cache` endpoint — called by pipeline at session start
- Removed `FreeLibrary` from the per-call `finally` block

In `api/explore.py`:
- `_explore_worker` now calls `POST /flush-dll-cache` at the start of each run
  so each pipeline session starts with clean DLL state

### Expected Impact

| Function | Before (FreeLibrary) | After (Cached DLL) |
|---|---|---|
| CS_UnlockAccount | 0xFFFFFFFE (XOR code works but state lost) | **Should return 0** (state persists) |
| CS_RedeemLoyaltyPoints | 0xFFFFFFFB (0 loyalty points — state lost) | **Should return 0** (points from ProcessPayment persist) |
| All other functions | Working (auto-initialize on each load) | Still working |

## Bridge vs Native Execution

### Current Architecture (Linux + Bridge, NOW WITH DLL CACHING)

```
Pipeline (Linux Container)
    │
    │  POST /flush-dll-cache  (at session start)
    │  POST /execute  (DLL stays loaded between calls)
    ▼
Bridge (Windows VM) — _DLL_CACHE keeps DLL in memory
    │
    │  _execute_dll_bridge() — DLL state persists across calls
    ▼
DLL (ctypes)  ← state from CS_Initialize, CS_UnlockAccount persists
```

### Proposed Future Architecture (Native Windows)

```
Pipeline (Windows Container or Local)
    │
    │  _execute_dll() — no bridge, no serialization
    ▼
DLL (ctypes)  ← direct ctypes, lowest latency
```

### Migration Path

1. **Immediate (done)**: DLL caching in bridge — fixes state persistence
2. **Short-term**: Run pipeline locally on Windows (5080 machine) for testing
3. **MVP**: Deploy as Windows Container App on Azure
4. **Production**: Windows Container App with auto-scaling

## Implementation Priority

1. ~~**Fix bridge DLL caching**~~ — DONE (state persistence)
2. **Restart bridge on VM** — required to pick up the fix
3. **Implement checkpoint creation** — `POST /api/jobs/{job_id}/checkpoint`
4. **Implement checkpoint loading** — `checkpoint_id` parameter in explore
5. **Focused run mode** — skip checkpointed functions in Phase 3
6. **Local Windows testing** — eliminate bridge entirely for dev iteration
