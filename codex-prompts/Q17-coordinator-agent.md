# Codex Prompt — Q17: Autonomous Coordinator Agent (`scripts/run_coordinator.py`)

## What This Implements

Q17 ("Autonomous Coordinator Agent") in `docs/architecture/QUESTIONS.md`.

A coordinator script that drives the full Q15/Q16 improvement loop autonomously:
reads its own state, picks the next playbook step, dispatches a run-set, evaluates
the results, decides promotion, updates its state, and repeats — up to `max_cycles`
times before stopping and writing a structured report.

**Prerequisites**: Q14 (`union_merger.py`), Q15 (`run_set_orchestrator.py`,
ablation tagging fields), and Q16 (sentinel emission fields) must be implemented first.
This script calls those pieces — it does not reimplement them.

## Context: What Already Exists After Q14/Q15/Q16

- `scripts/union_merger.py` — merges N+M session findings into one accumulated schema
- `scripts/run_set_orchestrator.py` — dispatches N+M runs, waits, invokes merger
- `scripts/prompt_profiles.json` — defines all ablation variable families and profiles
- `human/dashboard-row.json` in each session now contains:
  `prompt_profile_id`, `layer`, `ablation_variable`, `ablation_value`, `run_set_id`,
  `coordinator_cycle`, `playbook_step`, `write_unlock_outcome`,
  `sentinel_new_codes_this_run`, `functions_success`, `hard_fail`, `capture_quality`
- `sessions/_runs/baseline/` — the current baseline (364 folders, 4/13 success median)

## Coordinator State File: `sessions/coordinator-state.json`

The coordinator reads and writes this file on every cycle. If it does not exist,
the coordinator initializes it with defaults.

```json
{
  "component": "contoso_cs",
  "current_cycle": 0,
  "playbook_step": "A",
  "current_baseline_profile": "baseline",
  "baseline_functions_success": 4,
  "baseline_run_set_id": null,
  "cycles_completed": [],
  "playbook_variable_queue": [
    "prompt_framing",
    "vocab_ordering",
    "context_density",
    "tool_budget"
  ],
  "stopping_reason": null,
  "max_cycles": 10,
  "output_dir": "sessions/_runs"
}
```

`playbook_variable_queue` is consumed left-to-right. When a variable family produces
a promotion, the queue is not reset — the coordinator continues to the next family.
When the queue is exhausted, the coordinator moves to stopping reason `"playbook_exhausted"`.

## Playbook Steps

### Step A — Robustness confirmation

Run N=3 identical control runs (`profile="baseline"`). Check:
- Do all 3 sessions have `capture_quality: "complete"`?
- Does the median `functions_success` match `baseline_functions_success`?

If yes: Layer 1 (trunk) is stable. Proceed to Step B.
If no (high variance): write stopping reason `"layer1_unstable"`, emit report, halt.

For the very first coordinator run (cycle 1), Step A establishes the current baseline
rather than validating an existing one. Record the median as `baseline_functions_success`
and the `run_set_id` as `baseline_run_set_id`.

### Step B — Variable sweep

Pop the next variable family from `playbook_variable_queue`. Look up its three profiles
in `prompt_profiles.json` (all profiles where `ablation_variable` matches the family name).

Dispatch a run-set:
- N=3 control runs (`profile="baseline"`, `layer=1`)
- M=3 ablation runs, one per profile in the variable family (`layer=2`)

After the run-set completes and merger runs:
1. Compute the **control median**: median `functions_success` across the 3 N runs.
2. For each M run, compute its gate delta vs. control median:
   - Read `human/dashboard-row.json` for the M run.
   - Compare gate_pass/fail/warn counts vs. the N median.
   - Compare `functions_success` vs. N median.
3. Apply the **promotion decision rule**:
   - A profile is a promotion candidate if:
     a. Its `functions_success` >= control median AND at least one gate improved.
     b. No gate regressed (nothing that was passing in N went to fail/warn in this M run).
   - A profile is **promoted** if it meets promotion criteria in ≥2 of 3 M runs.
4. If promoted:
   - Update `current_baseline_profile` to the winning profile.
   - Update `baseline_functions_success` to the promoted run's median.
   - Record in `cycles_completed` with `promoted: true` and gate delta.
5. If not promoted:
   - Record in `cycles_completed` with `promoted: false`.
6. Move to next variable family. If queue empty: Step F (report + halt).

### Step C — Sentinel check (runs automatically after each Step B batch)

After the merger completes for a Step B run-set, check if any M run has
`sentinel_new_codes_this_run > 0`. If yes:
- Record the newly resolved codes in `cycles_completed` entry.
- Check if `write_unlock_outcome` changed from `"blocked"` to `"resolved"` in any run.
- If write-unlock resolved: add `"write_unlock_newly_resolved": true` to the cycle entry.
  This is a signal for the human report but does not change the playbook automatically.

### Step D — Write-unlock re-probe (only if write_unlock_newly_resolved)

If Step C found a newly resolved write-unlock sentinel, dispatch one additional
N=3 control run using the updated sentinel context (the coordinator passes the newly
resolved sentinel code as extra context). Record whether write functions now succeed.

### Stopping conditions

Check after every cycle. Stop and write report if any is true:
1. `playbook_variable_queue` is empty — `"playbook_exhausted"`
2. Three consecutive cycles with no improvement (same `functions_success`, no gate moves) — `"plateau_reached"`
3. A cycle's N control runs produce worse median than `baseline_functions_success` — `"baseline_regression"` (halt for human review)
4. `current_cycle >= max_cycles` — `"max_cycles_reached"`
5. Any N control run has `capture_quality != "complete"` two cycles in a row — `"capture_unreliable"`

## Output: Cycle Report

After every cycle (whether or not it ended with a stopping condition), write:
`sessions/coordinator-cycle-{N}-report.md`

Format:
```markdown
# Coordinator Cycle {N} Report
Generated: {iso8601}
Component: contoso_cs
Playbook step: B
Variable tested: prompt_framing

## Run-Set
Run set ID: runset-...
Control runs (N=3): median functions_success=4, all capture_quality=complete
Ablation runs (M=3):
  - framing-systematic: functions_success=5, T-05=pass (was warn) → CANDIDATE
  - framing-explore:    functions_success=4, no gate change
  - framing-find-combos: functions_success=4, no gate change

## Promotion Decision
Promoted: YES — framing-systematic (improved T-05 in 2/3 M runs, no regressions)
New baseline profile: framing-systematic
New baseline functions_success: 5

## Sentinel Status
New sentinel codes this cycle: 0
Write-unlock status: blocked (0xFFFFFFFB — unchanged)

## Current Coverage
Functions succeeded: 5/13
Functions still blocked: CS_GetAccountBalance, CS_LookupCustomer, CS_GetOrderStatus,
  CS_ProcessPayment, CS_GetLoyaltyPoints, CS_RedeemLoyaltyPoints, CS_ProcessRefund,
  CS_UnlockAccount

## Next Step
Cycle {N+1}: test variable family = vocab_ordering
Hypothesis: placing static IDs first may help functions that need CUST-/ORD- format.

## Recommended Human Action
None required — coordinator will proceed to cycle {N+1} automatically.
```

## Output: Final Report (on halt)

`sessions/coordinator-final-report.md` — same format but includes:
- All cycles_completed entries summarized
- Stopping reason and explanation
- Recommended human action (what only a human can resolve)
- What to give Codex next (if code changes are needed)

## CLI Interface

```bash
python scripts/run_coordinator.py \
  --component contoso_cs \
  --api-url https://... \
  --api-key YOUR_KEY \
  --output-dir sessions/_runs \
  --max-cycles 10 \
  [--state-file sessions/coordinator-state.json] \
  [--resume]  # if state file exists, continue from current_cycle
```

`--resume` re-reads `coordinator-state.json` and continues. Without `--resume`,
the coordinator starts fresh and initializes the state file (fails if one already
exists with `stopping_reason` null, to prevent accidental double-start).

## Files to Create

- `scripts/run_coordinator.py` — new file, ~350 lines

## Files to NOT Modify

- `api/*.py` — all API behavior is set by Q15/Q16
- `scripts/union_merger.py`
- `scripts/run_set_orchestrator.py`
- Any existing session folders

## Acceptance Criteria

1. `python scripts/run_coordinator.py --component contoso_cs --api-url ... --api-key ...`
   initializes `sessions/coordinator-state.json` with `current_cycle: 0`.
2. With `--max-cycles 1`, completes one Step A cycle, writes
   `sessions/coordinator-cycle-1-report.md`, updates state file to `current_cycle: 1`.
3. Stopping reason `"max_cycles_reached"` causes `coordinator-final-report.md` to be
   written. Script exits 0.
4. `--resume` resumes from existing state file without overwriting it.
5. If any run-set fails (orchestrator exits non-zero): coordinator records the cycle as
   failed, writes a report noting the failure, then halts with `stopping_reason: "run_set_failed"`.
   It does not silently skip failed cycles.
6. No modifications to `api/*.py` or any existing session folders.
7. `python -m py_compile scripts/run_coordinator.py` passes.
