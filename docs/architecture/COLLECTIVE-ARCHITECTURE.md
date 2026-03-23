# Collective Architecture — Macro/Micro Coordinator System

> Created 2026-03-23. Captures the architectural vision for a two-tier coordinator
> system with circular feedback, cross-run knowledge harvesting, and progressive
> sentinel resolution. This is the design document for what comes after the
> current single-pipeline MVP.

---

## The Problem Statement

The current pipeline runs once, produces findings, and stops. The coordinator
(Q17) sits outside, launches complete pipeline runs, compares scorecards, and
promotes configurations. But nobody is making decisions INSIDE a run — no agent
says "Phase 3 just discovered a new sentinel code, let me immediately re-try
the write-unlock before moving to Phase 4."

The result: knowledge gained at each stage is only used by later stages in the
same run. It's never fed back to earlier stages, never shared across parallel
runs, and never used to revise the questions we're asking.

---

## Two-Tier Coordinator Design

### Macro Coordinator (cross-run orchestrator)

**What it is**: The current Q17 coordinator, evolved. Sits outside the pipeline.
Dispatches N parallel pipeline runs, collects results, promotes configurations,
harvests knowledge across runs.

**Its specific purpose**:
1. Dispatch N baseline runs in parallel to establish trunk stability
2. For each variable family, dispatch N controls + M ablation variants
3. After each batch completes, read all session artifacts and decide:
   - Which configuration produced the best `functions_success`?
   - Did ANY run discover something new? (new sentinel code, write-unlock cracked, previously-failed function now succeeds)
   - If yes: harvest that knowledge and inject it into the NEXT batch's starting context
4. Maintain a **cumulative knowledge base** across all runs:
   - Best-known sentinel table (union of all runs' calibrations)
   - Best-known working_calls for each function (from whichever run cracked it)
   - Best-known init sequence (from whichever run got write-unlock to succeed)
5. Generate the "winner" schema that gets promoted as the starting context for the next iteration cycle

**What documentation it produces**:
- `coordinator-cycle-N-report.md` — per-cycle summary (already exists)
- `cumulative-knowledge-base.json` — NEW: union of all sentinel tables, working_calls, init sequences across all runs
- `promotion-history.json` — which configurations were promoted and why
- `coordinator-final-report.md` — stopping condition and recommended next steps (already exists)

### Micro Coordinators (per-stage agents)

**What they are**: Lightweight decision agents embedded INSIDE the pipeline at
each stage boundary. They don't launch sub-runs. They make in-flight decisions
about whether to re-probe, what to re-probe, and what context to inject into
the next stage.

**Each micro coordinator has**:
1. A base context specific to its stage's purpose
2. Access to all ExploreContext state accumulated so far
3. A small playbook of actions it can take
4. A decision function: "given what I now know, should I act?"

**The micro coordinators and their playbooks**:

#### MC-1: Post-Calibration (after Phase 0.5)
- **Base context**: Sentinel table just built from empty-arg sweep
- **Decision**: Are there ambiguous sentinels? (codes that could mean "not init" or "wrong args")
- **Action**: If ambiguous, ask the LLM: "Which of these codes specifically means 'not initialized' vs 'invalid argument' vs 'access denied'? The distinction matters because we need to know which error means 'try a different init mode' vs 'try different args'."
- **Risk you raised**: Could this hurt us by over-interpreting sentinel codes early? Yes — if the LLM assigns wrong meanings, those propagate through the entire run. Mitigation: tag early-stage sentinel meanings as `confidence: low` and allow later stages to revise them.

#### MC-2: Post-Write-Unlock (after Phase 1)
- **Base context**: Init modes tried, write-test results, blocking sentinel
- **Decision**: If unlock failed, what specific pattern did we see?
- **Action**: Classify the failure mode:
  - All init modes return same sentinel → init mode doesn't matter, something else is needed
  - Some init modes return different sentinels → the mode matters, try more modes
  - Init returns 0 but write still fails → init worked but write needs different args
- **Feeds into Phase 3**: The write_unlock_block injected into prompts should reflect this classification, not just "WRITE MODE NOT YET UNLOCKED"

#### MC-3: Post-Probe-Loop (after Phase 3, before Phase 4)
- **Base context**: All probe results, sentinel catalog, vocab growth
- **Decision**: Did any function's probe accidentally discover the write-unlock sequence?
- **Action**: Scan probe log for any write function that returned 0. If found, that's the unlock sequence. Extract the init call that preceded it and the args used. Update ctx.unlock_result.
- **Also**: Re-probe write-unlock with updated sentinel knowledge (already implemented as Q16§5, but only triggers on new sentinel codes — should ALWAYS re-probe here)

#### MC-4: Post-Synthesis (after Phase 6)
- **Base context**: The full api-reference.md the LLM just produced
- **Decision**: Does the synthesis document describe an init sequence we haven't tried?
- **Action**: Parse the "Initialization" section of api-reference.md. If it describes a specific init mode or sequence we haven't tested, try it. The LLM that wrote the synthesis may have inferred the pattern from cross-function evidence.
- **Targeted prompt**: "Based on this API reference, what is the exact CS_Initialize(mode=?) call needed before write operations? What mode value is described or implied?"

#### MC-5: Post-Verification (after Phase 7b)
- **Base context**: Verification results — which functions are truly verified, which are inferred
- **Decision**: Do verified read functions give us data we can use as write function args?
- **Action**: If `CS_GetAccountBalance(CUST-001)` is verified, extract the customer ID format. If `CS_GetOrderStatus(ORD-20040301-0042)` is verified, extract the order ID format. Feed these as concrete args into a write-unlock re-probe.
- **This is the "chain read outputs → write inputs" strategy**

#### MC-6: Post-Gap-Resolution (after Phase 8)
- **Base context**: Gap resolution may have cracked a dependency
- **Decision**: Did gap resolution successfully probe any previously-failed function?
- **Action**: Final write-unlock attempt with all accumulated knowledge. If gap resolution discovered that `CS_Initialize(mode=2)` is the right init, use it.

---

## The N × M Parallel Run Strategy

### Current approach (N + M sequential):
```
Coordinator dispatches:
  3 control runs (N=3, layer 1)
  3 ablation runs (M=3, layer 2, one variable change each)
Total: 6 runs, one variable tested per cycle
```

### Proposed approach (N × M parallel):
```
For each of N=3 parallel baseline runs:
  Launch M=3 ablation variants simultaneously
Total: N × M = 9 runs per cycle, but EACH baseline gets its own ablation set

Why this matters:
- Current: "tool_budget=8 is better than tool_budget=5" — one data point per ablation
- Proposed: "tool_budget=8 is better across ALL 3 baselines" — 3 data points per ablation
- Statistical confidence goes from "one run said so" to "3/3 runs agreed"
```

### Cross-run knowledge harvesting:

The macro coordinator already reads `dashboard-row.json` from each run. The
new capability: read `findings.json` from each run and BUILD A UNION.

```
Run A: 8/12 functions succeeded, including CS_GetOrderStatus
Run B: 7/12 functions succeeded, but cracked CS_UnlockAccount (!)
Run C: 8/12 functions succeeded, same as Run A

Macro coordinator sees:
  - CS_UnlockAccount was cracked by Run B with args {param_1: "ACCT-001"}
  - Runs A and C didn't crack it
  - HARVEST: Add CS_UnlockAccount's working_call to cumulative knowledge base
  - INJECT: Next cycle starts with CS_UnlockAccount as a known-good call
```

This is the "collective hivemind" — each run explores slightly differently
(different LLM temperatures, different ablation variables, different random
seeds for deterministic fallback). The macro coordinator collects the BEST
discovery from each run and builds a cumulative knowledge base.

---

## Circular Feedback Loop

### Iteration 1 (cold start):
```
Input:  DLL binary + user hints
Output: findings.json (8/12 succeeded), api-reference.md, sentinel-table, vocab
```

### Iteration 2 (warm start):
```
Input:  DLL binary + user hints + iteration-1 findings + iteration-1 api-reference + iteration-1 sentinel-table
Output: findings.json (10/12 succeeded), updated api-reference.md, refined sentinel-table
```

### Iteration 3 (hot start):
```
Input:  DLL binary + user hints + iteration-2 cumulative knowledge base
Output: findings.json (12/12 succeeded), final api-reference.md, complete sentinel-table
```

The key: **each iteration's OUTPUT becomes the next iteration's INPUT context**.
The api-reference.md from iteration 1 becomes part of the system prompt for
iteration 2. The LLM in iteration 2 already knows what iteration 1 discovered.

**What specifically feeds forward**:
1. `findings.json` → injected as ALREADY DISCOVERED block in system message (already works)
2. `api-reference.md` → injected as supplementary context for write-function probes (new)
3. `sentinel-calibration.json` → pre-seeds Phase 0.5 so calibration doesn't start from scratch (partially implemented via sticky baseline)
4. `vocab.json` → pre-seeds Phase 0a so vocab has full cross-function knowledge from day 1 (partially implemented)
5. `cumulative-knowledge-base.json` → NEW: the macro coordinator's harvested best-of-all-runs knowledge

---

## Sentinel Codes Are the Foundation — Your Question Answered

> "For those write functions allow the MVP — and are therefore only unlocked
> from knowing the sentinel codes, and without good sentinel codes we wouldn't
> be able to work reliably across DLLs and across systems yes?"

**Yes, exactly.** Sentinel codes are the Rosetta Stone for each DLL. Without
correctly interpreting them, the pipeline can't distinguish between:
- "This function needs different args" (keep trying)
- "This function needs initialization first" (call init, then retry)
- "This function is permanently locked" (stop trying)
- "This function succeeded" (record and move on)

For contoso_cs.dll, the critical sentinel is 0xFFFFFFFB = "write denied". But
the pipeline doesn't know whether "write denied" means "wrong init mode" or
"init not called at all" or "wrong customer ID" — and that distinction is
exactly what determines which strategy will crack it.

**Across DLLs**: Every DLL has its own sentinel vocabulary. A banking DLL might
use 0x80040001 for "not authenticated", while a hardware DLL might use
0xC0000005 for "access violation". The sentinel calibration pipeline (Phase 0.5)
is designed to be generic, but the INTERPRETATION of sentinels is DLL-specific.
The micro coordinator at MC-1 should ask the LLM to classify sentinels into
actionable categories (auth-required, init-required, wrong-args, permanent-failure)
rather than just naming them.

---

## The Phase 0.5 Naming Risk

> "What are the chances that this hurts us overall because it's closer to the
> foundation of the context?"

Real risk. If Phase 0.5 assigns "invalid parameter" to a code that actually
means "not initialized", every subsequent stage will treat init failures as
parameter problems and try different params instead of different init sequences.

**Mitigation**:
1. Tag Phase 0.5 sentinel meanings with `source: "calibration"` and `confidence: "low"`
2. Allow Phase 3 probe results to REVISE sentinel meanings — if 3 functions all
   return the same code with valid-looking args, it's probably "not init" not "wrong args"
3. Stage-boundary recalibration (already exists) can re-name codes based on probe evidence
4. The micro coordinator MC-3 should explicitly check: "Are we seeing the same sentinel
   from functions that have very different parameter patterns? If so, it's likely an
   init/auth issue, not a parameter issue."

---

## Write-Unlock as a Dedicated Stage

> "This could be a whole other stage? maybe?"

Yes. The current Phase 1 is a deterministic brute-force sweep (8 init modes ×
28 credentials × 3 arg sets). It doesn't use the LLM at all. A dedicated
LLM-powered write-unlock stage would:

1. Read the sentinel table and classify the blocking sentinel
2. Read all known-good calls from read functions (what IDs, what formats)
3. Ask the LLM: "Given this sentinel table, these known-good IDs, and this
   function signature, what is the MOST LIKELY init→write sequence? Generate
   5 candidate sequences ranked by probability."
4. Test each candidate sequence against the DLL
5. If one works: record the unlock sequence and update ctx.write_unlock_block

This could run as Phase 3.5 (after the main probe loop, before reconciliation)
or as a standalone "write-unlock focus mode" that the coordinator dispatches
specifically when `write_unlock_outcome: blocked` persists across multiple runs.

---

## Implementation Priority

### Immediate (this session):
1. Improve write-function probe prompt — targeted init→write testing instruction
2. Add 4 re-probe checkpoints in `explore.py` (after Phase 4, 6, 7b, 8)
3. Increase tool budget for write functions to 14 (they need init+write+retry cycles)

### Next session:
4. Implement micro coordinator MC-3 (post-probe-loop) — scan for accidental write-unlock
5. Implement circular feedback: iteration N's findings become iteration N+1's starting context
6. Harvest cross-run knowledge in macro coordinator

### Future:
7. Full micro coordinator system (MC-1 through MC-6)
8. N × M parallel run strategy
9. Cumulative knowledge base with cross-DLL sentinel transfer learning

---

## Enterprise Vision

In production, the system runs for hours or days against a customer's DLL fleet:

```
Hour 0:  Cold start. Pipeline knows nothing. 4/12 verified.
Hour 1:  First iteration complete. Sentinel table built. 8/12 verified.
Hour 2:  Second iteration with warm start. Init sequence cracked. 10/12 verified.
Hour 4:  Third iteration. Write functions unlocked. 12/12 verified.
Hour 6:  Coordinator promotes best configuration. API reference generated.
Hour 8:  Pipeline moves to next DLL in the folder. Transfers sentinel patterns.
Hour 24: All DLLs in the folder analyzed. Complete 1:1 MCP wrapper generated.
```

The "overfitting" you described is the product: "This pipeline has been running
for 24 hours and found out everything about your systems and is now ready to
create a 1:1 wrapper." That IS the value proposition. Each iteration tightens
the fit. Each run builds on the last. The collective approach ensures that no
single run's failure blocks progress — if any parallel run cracks a function,
that knowledge is harvested and shared.

---

## Open Questions

1. How much starting context is too much? If iteration 2 starts with iteration 1's
   full api-reference.md, does that bias the LLM toward confirming what it already
   "knows" instead of discovering new things?

2. Should micro coordinators have their own tool budget, or do they share with the
   phase they're attached to?

3. For N × M parallel runs: how do we handle rate limiting? 9 concurrent pipeline
   runs each making LLM calls = 9× the API load. May need to stagger or queue.

4. Cross-DLL sentinel transfer: if DLL-A uses 0xFFFFFFFB for "write denied" and
   DLL-B uses the same code, should we pre-seed DLL-B's sentinel table? Risk of
   wrong transfer vs. benefit of faster calibration.

5. The coordinator currently has no way to MODIFY the pipeline's behavior mid-run.
   Should the micro coordinators be able to signal the macro coordinator? ("I just
   cracked write-unlock in this run — tell the other parallel runs to try the same
   init sequence.")
