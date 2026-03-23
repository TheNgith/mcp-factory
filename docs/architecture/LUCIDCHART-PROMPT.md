# Lucidchart AI Prompt — MCP Factory Pipeline Flowchart

> Copy everything below the line into the Lucidchart AI "Build a diagram" → "Flowchart" prompt.
> Then review and adjust layout. The arrows and data flows are exact from the codebase.

---

Create a detailed flowchart of a software pipeline with 16 phases, 6 micro coordinators at stage boundaries, and a macro coordinator swim lane. Use a top-to-bottom layout with a right-side swim lane. Each phase is a rounded rectangle. Data objects are parallelograms. Decision points are diamonds. Micro coordinators are orange diamonds. Use color coding described below.

## EXTERNAL INPUTS (at the top, in gray parallelograms)

1. "DLL Binary" — the uploaded DLL file
2. "User Hints" — text hints about error codes, IDs, use cases
3. "Prior Session Vocab" — vocabulary from previous runs (optional)
4. "explore_settings" — per-request config: max_rounds, max_tool_calls, max_functions, instruction_fragment, context_density, gap_resolution_enabled, model
5. "Cumulative Knowledge Base" — (from Macro Coordinator) union of all prior runs' best sentinel tables, working_calls, init sequences

All five external inputs flow into a central shared state object:

6. "ExploreContext (shared state)" — large rounded rectangle in the center-left, dashed border, light yellow background. Label it with these fields grouped by producer:
   - Identity: job_id, runtime config, LLM client, model name
   - From Phase 0.5: sentinels (error code map)
   - From Phase 0a: vocab (semantic accumulator), use_cases_text
   - From Phase 0b: static_hints_block, dll_strings
   - From Phase 1: unlock_result, write_unlock_block
   - From Phase 2: init_invocables (ordered list)
   - From Phase 3: sentinel_catalog, already_explored set

Draw ExploreContext on the left side. Each phase reads from it and writes back to it. Show bidirectional arrows between ExploreContext and each phase.

## BLOB STORAGE (on the right side, in a vertical stack of blue parallelograms)

These are artifacts persisted to Azure Blob Storage. Draw them on the right side:
- sentinel-calibration.json
- vocab.json
- static-analysis.json
- write-unlock-probe.json
- explore-config.json
- probe-log.json
- findings.json
- api-reference.md
- backfill-report.json
- verification-report.json
- harmonization-report.json
- session-meta.json
- transition-index.json
- behavioral-spec.py
- cumulative-knowledge-base.json (shared across runs)

Each phase that uploads an artifact gets a rightward arrow to the corresponding blob.

## PIPELINE PHASES (center column, top to bottom)

### Phase 0.5: Sentinel Calibration (teal box)
- Arrow IN from "DLL Binary": reads DLL exports list
- Arrow IN from "User Hints": parses hint error codes
- Arrow IN from "Cumulative Knowledge Base": pre-seeds with best-known sentinel table from prior runs
- Action: Calls every function with empty args. Clusters non-zero high-bit return values. LLM names unknown codes.
- Arrow OUT to ExploreContext: writes ctx.sentinels
- Arrow OUT to blob: uploads sentinel-calibration.json
- Arrow DOWN to MC-1

### MC-1: Sentinel Ambiguity Check (orange diamond)
- Decision: "Are there ambiguous sentinels that could mean init-required OR wrong-args?"
- YES path: Action box "Ask LLM to classify blocking sentinel into categories: auth-required / init-required / wrong-args / permanent-failure"
- NO path: Skip
- Both paths merge DOWN to Phase 0a
- NOTE (yellow sticky): "RISK: Wrong meanings here propagate through entire run. Tag with confidence:low, allow later stages to revise."

### Phase 0a: Vocab Seed (teal box)
- Arrow IN from ExploreContext: reads ctx.sentinels
- Arrow IN from "Prior Session Vocab": loads prior vocab from blob (optional)
- Arrow IN from "User Hints": reads use_cases text
- Arrow IN from "Cumulative Knowledge Base": pre-seeds with known-good working_calls from prior runs
- Action: Seeds vocabulary with sentinel meanings, user hints, ID formats.
- Arrow OUT to ExploreContext: writes ctx.vocab, ctx.use_cases_text
- Arrow DOWN to Phase 0b

### Phase 0b: Static Analysis (teal box)
- Arrow IN from "DLL Binary": reads raw DLL bytes
- Arrow IN from ExploreContext: reads ctx.vocab (to avoid overwriting user-set keys)
- Action: PE header, IAT imports, binary string extraction (IDs, emails), Capstone disassembly for sentinel constants.
- Arrow OUT to ExploreContext: writes ctx.static_hints_block, ctx.dll_strings, ctx.static_analysis_result. Also enriches ctx.vocab with binary evidence.
- Arrow OUT to blob: uploads static-analysis.json
- Arrow DOWN to Phase 1

### Phase 1: Write-Unlock Probe (orange box)
- Arrow IN from ExploreContext: reads ctx.invocables, ctx.dll_strings, ctx.vocab
- Action: Tries to unlock write mode. Strategy: (1) no-param init call, (2) mode-based init with modes 0,1,2,4,8,16,256,512, (3) credential sweep from binary strings. After each init attempt, tests write functions with real args built from vocab id_formats and dll_strings.
- Arrow OUT to ExploreContext: writes ctx.unlock_result (unlocked: true/false), ctx.write_unlock_block (text injected into write-function prompts)
- Arrow OUT to blob: uploads write-unlock-probe.json
- Arrow DOWN to MC-2

### MC-2: Write Failure Pattern Classification (orange diamond)
- Decision: "What pattern did the write-unlock failure show?"
- Three exit arrows from diamond:
  - "All modes → same sentinel" → note: "Init mode doesn't matter — something else needed (auth? different function?)"
  - "Different sentinels per mode" → note: "Mode matters — more modes to try. Expand sweep."
  - "Init=0 but write fails" → note: "Init works but write needs correct args"
- All three merge DOWN to Phase 2

### Phase 2: Curriculum Order (teal box)
- Arrow IN from ExploreContext: reads ctx.invocables
- Arrow IN from "explore_settings": reads max_functions cap
- Action: Sorts functions — init functions first, then ascending uncertainty score. Caps to max_functions.
- Arrow OUT to ExploreContext: writes ctx.invocables (reordered), ctx.init_invocables
- Arrow DOWN to Phase 3

### Phase 3: Probe Loop — MAIN WORK (large green box, double border)
- This is the biggest phase. Make it visually prominent.
- Arrow IN from ExploreContext: reads ALL fields — sentinels, vocab, static_hints_block, dll_strings, init_invocables, write_unlock_block, unlock_result
- Arrow IN from "explore_settings": reads max_rounds, max_tool_calls, min_direct_probes, instruction_fragment, context_density, skip_documented, deterministic_fallback_enabled, model
- Action: For each function in curriculum order, runs an LLM agent loop. The LLM calls the DLL, observes results, records findings via enrich_invocable and record_finding tools. After LLM, runs deterministic fallback (systematic arg combinations). Each function gets max_rounds conversation rounds and max_tool_calls DLL calls.
- Sub-box inside Phase 3: "Per-function _explore_one loop" containing: Build system message from all prior findings → Build user message with instruction_fragment + static_hints_block + write_unlock_block → LLM responds with tool calls → Execute DLL → Record finding
- WRITE FUNCTION SPECIAL PROMPT (sub-box, dashed): "For write functions: 'Your ONLY job is to find the init sequence that makes this write function return 0 instead of 0xFFFFFFFB. Try CS_Initialize with EACH mode, then IMMEDIATELY try the write function with real args. Report which mode gets you past the sentinel.'" — tool budget: 14 (not 8)
- Arrow OUT to ExploreContext: accumulates into ctx.sentinel_catalog, ctx.already_explored, updates ctx.vocab (cross-function learning)
- Arrow OUT to blob: appends to probe-log.json, writes findings.json
- Arrow DOWN to "Stage-Boundary Recalibration"
- FEEDBACK LOOP (curved arrow back to self): "Cross-function vocab learning — each probe enriches vocab for the next function"

### Stage-Boundary Recalibration — Q16§4 (purple box)
- Arrow IN from blob: reads probe-log.json
- Arrow IN from ExploreContext: reads ctx.sentinels
- Action: Scans probe log for return values > 0x80000000 not already in sentinel table. Calls LLM to name new codes. Updates sentinel table.
- Arrow OUT to ExploreContext: updates ctx.sentinels with new codes
- Arrow DOWN to MC-3

### MC-3: Post-Probe Write-Unlock Scan (orange diamond) — CRITICAL
- Arrow IN from blob: reads probe-log.json
- Decision: "Did ANY write function return 0 during probing? (accidental write-unlock)"
- YES arrow: "Extract init sequence from preceding calls. Update ctx.unlock_result. WRITE-UNLOCK DISCOVERED."
- NO arrow: "Re-probe write-unlock with ALL accumulated knowledge (ALWAYS, not just when new sentinel codes found)"
- Action box on NO path: "Run _probe_write_unlock with updated sentinels + vocab + all known-good IDs"
- Both paths merge DOWN to Phase 4
- NOTE (yellow sticky): "CRITICAL: This is the main improvement point. Currently only re-probes when new sentinel codes are found. Should ALWAYS re-probe here."

### Phase 4: Reconcile (purple box)
- Arrow IN from blob: reads probe-log.json, findings.json
- Action: Cross-reference probe log vs findings. Functions that returned 0 during probing but whose LLM finding says "error" are upgraded to "success". Only upgrades, never downgrades.
- Arrow OUT to blob: patches findings.json (error → success where probe log proves it)
- Arrow DOWN to Phase 5

### Phase 5: Sentinel Catalog (purple box)
- Arrow IN from ExploreContext: reads ctx.sentinel_catalog, ctx.vocab
- Action: Persists cross-probe sentinel evidence. Promotes confident sentinel codes into vocab error_codes.
- Arrow OUT to ExploreContext: updates ctx.vocab["error_codes"]
- Arrow OUT to blob: uploads sentinel-catalog.json, updates vocab.json
- Arrow DOWN to Phase 6

### Phase 6: Synthesize (blue box)
- Arrow IN from blob: reads findings.json
- Arrow IN from ExploreContext: reads ctx.invocables, ctx.vocab, ctx.sentinels
- Action: LLM generates api-reference.md — complete human-readable API documentation from all findings.
- Arrow OUT to blob: uploads api-reference.md
- Arrow OUT: passes report text to MC-4 (direct function return, not via blob)
- Decision diamond: "Synthesis succeeded?"
  - YES arrow: continues to MC-4
  - NO arrow: skips to Phase 10

### MC-4: Synthesis Init Inference (orange diamond)
- Arrow IN from blob: reads api-reference.md "Initialization" section
- Decision: "Does the synthesis describe an init sequence we haven't tried?"
- YES arrow: Action box "Test inferred init sequence → write-unlock re-probe with semantic knowledge"
- NO arrow: Continue to Phase 7
- Both merge DOWN to Phase 7

### Phase 7: Backfill (blue box)
- Arrow IN: receives api-reference.md report from Phase 6
- Arrow IN from ExploreContext: reads ctx.invocables
- Action: LLM uses synthesis doc to enrich parameter descriptions. Generic "param_1" becomes "customer_id". Adds units, entity references, example values.
- Arrow OUT to ExploreContext: updates ctx.invocables, ctx.inv_map with semantic names
- Arrow OUT to blob: uploads backfill-report.json, updates invocables schema
- Arrow DOWN to Phase 7b

### Phase 7b: Enrichment Verification — NEW (blue box, dashed border)
- Arrow IN from blob: reads findings.json (for working_call args)
- Arrow IN from ExploreContext: reads ctx.inv_map, ctx.init_invocables
- Action: For each function with status=success and a working_call, actually executes that call against the DLL. This closes the enrichment→verification loop. If return=0, marks "verified". If return=sentinel, marks "inferred" (plausible but unproven).
- Arrow OUT to blob: patches findings.json with verification field, uploads verification-report.json
- Arrow DOWN to MC-5

### MC-5: Read→Write Arg Chain (orange diamond)
- Arrow IN from blob: reads verification results
- Decision: "Do verified read functions provide data usable as write function args?"
- YES arrow: Action box "Chain: CS_GetAccountBalance(CUST-001)=25000 → use CUST-001 for CS_ProcessPayment. Re-probe write-unlock with these concrete, verified args."
- NO arrow: Continue to Phase 8
- Both merge DOWN to Phase 8

### Phase 8: Gap Resolution + Clarification (red box)
- Decision diamond before Phase 8: "gap_resolution_enabled?"
  - NO arrow: skips gap resolution, writes empty explore_questions, continues to MC-6
  - YES arrow: enters Phase 8
- Arrow IN from blob: reads findings.json (to find failed functions)
- Arrow IN from ExploreContext: reads ALL fields — invocables, sentinels, vocab, use_cases_text, inv_map, tool_schemas
- Action part 1 — Gap Resolution: Second-pass LLM probing of failed functions. The system message now includes all successful findings as context, so the LLM knows what worked on other functions. Includes KNOWN-GOOD CALLS block with verified working_calls.
- Action part 2 — Clarification Questions: LLM generates structured questions about remaining unknowns for human review.
- Arrow OUT to blob: updates findings.json, writes explore_questions to job status, saves schema snapshots
- Arrow DOWN to MC-6

### MC-6: Final Write-Unlock Attempt (orange diamond)
- Decision: "Did gap resolution crack any dependency? Is write still blocked?"
- YES + still blocked: Action box "Final write-unlock attempt with ALL accumulated knowledge: updated sentinels + verified read outputs + gap resolution discoveries + semantic param names"
- NO or unlocked: Continue to Phase 9
- Both merge DOWN to Phase 9

### Phase 9: Behavioral Spec (gray box)
- Arrow IN: receives api-reference.md report
- Arrow IN from ExploreContext: reads ctx.invocables
- Action: LLM generates a typed Python behavioral specification with docstrings from findings + synthesis.
- Arrow OUT to blob: uploads behavioral-spec.py
- Arrow DOWN to Phase 10

### Phase 10: Harmonize (gray box)
- Arrow IN from blob: reads probe-log.json, findings.json
- Action: Final non-LLM pass. Cross-references probe log one last time. Upgrades any remaining error findings where probe log shows return=0. Counts final success/error/other totals.
- Arrow OUT to blob: uploads harmonization-report.json, patches findings.json
- Arrow DOWN to Finalize

### Finalize: Contract Artifacts (gray box, thick border)
- Arrow IN from ExploreContext: reads ctx.vocab, ctx.run_started_at, ctx.sentinel_new_codes_this_run
- Arrow IN from blob: reads explore_questions from job status
- Action: Synthesizes one-sentence domain description for vocab. Evaluates AC-4 closure gate (done vs awaiting_clarification vs canceled). Calls emit_contract_artifacts which evaluates all 18 cohesion transitions (T-01 through T-18).
- Arrow OUT to blob: uploads final vocab.json, session-meta.json, transition-index.json, stage-index.json, dashboard-row.json
- Arrow RIGHT to Macro Coordinator: "Session artifacts available for cross-run comparison"
- Terminal node: "Session Complete" (rounded rectangle, dark gray)

## MACRO COORDINATOR (separate swim lane on the far right)

Draw a separate vertical swim lane labeled "Macro Coordinator (cross-run)" on the far right side of the diagram. This runs OUTSIDE the pipeline — it dispatches and evaluates complete pipeline runs.

Nodes in this lane (top to bottom):
1. "Initialize: Load prompt profiles, state file" (start node)
2. "Step A: Dispatch N=3 baseline runs" (green box)
3. "Wait for all N runs to complete"
4. "Read dashboard-row.json + findings.json from each run"
5. Decision diamond: "Trunk stable? (median_fs >= baseline)"
   - NO: "STOP — baseline regression" (red terminal)
   - YES: continue
6. "Step B: Dispatch N=3 controls + M=3 ablation variants" (blue box)
   - Note: "Future: N × M = 9 parallel runs (each baseline gets its own ablation set)"
7. "Wait for all runs to complete"
8. "Read ALL session artifacts from ALL runs"
9. "HARVEST: Union all findings — if ANY run cracked a function, take its working_call" (orange box)
10. Decision diamond: "Any ablation beat control median?"
    - YES: "Promote configuration. Update baseline profile."
    - NO: "No promotion."
11. "Build cumulative-knowledge-base.json: best sentinel table + best working_calls + best init sequence"
12. Arrow LEFT back to pipeline input: "Inject cumulative knowledge into next iteration"
13. Decision diamond: "Stopping condition? (plateau / max_cycles / regression / queue exhausted)"
    - YES: "Write final report" (terminal)
    - NO: Arrow back to step 2 (loop)

### Horizontal arrows from Macro to Pipeline:
- "cumulative-knowledge-base.json" → Phase 0.5 (pre-seed sentinel table)
- "cumulative-knowledge-base.json" → Phase 0a (pre-seed vocab with known-good calls)
- "cumulative-knowledge-base.json" → Phase 3 (inject as ALREADY DISCOVERED findings)

### Horizontal arrows from Pipeline to Macro:
- Finalize → "Session artifacts" → Macro step 4/8 (read results)

## N × M PARALLEL RUN STRATEGY (small inset diagram in bottom-right)

```
Macro Coordinator dispatches per cycle:
├── Baseline Run 1 ──┬── Ablation 1a (variable X, value A)
│                     ├── Ablation 1b (variable X, value B)
│                     └── Ablation 1c (variable X, value C)
├── Baseline Run 2 ──┬── Ablation 2a
│                     ├── Ablation 2b
│                     └── Ablation 2c
└── Baseline Run 3 ──┬── Ablation 3a
                      ├── Ablation 3b
                      └── Ablation 3c

Total: N=3 × M=3 = 9 parallel runs per cycle
Each ablation shares its baseline's starting context
Statistical confidence: 3 data points per ablation, not 1
```

## CIRCULAR FEEDBACK LOOP (show as a large curved arrow)

Draw a large curved arrow from "Finalize: Session Complete" all the way back up to the External Inputs at the top, labeled:

"ITERATION CYCLE: Output becomes input. findings.json → ALREADY DISCOVERED. api-reference.md → system prompt context. sentinel-calibration.json → Phase 0.5 pre-seed. vocab.json → Phase 0a pre-seed. Each iteration tightens the fit."

## COLOR CODE LEGEND (at the bottom)

- Teal (#00BCD4): Setup / Foundation phases (0.5, 0a, 0b, 2)
- Orange (#FF9800): Write-Unlock phases (1, Q16§5 re-probe) AND Micro Coordinators (MC-1 through MC-6)
- Green (#4CAF50): Probe Loop — Main Work (3)
- Purple (#9C27B0): Post-Probe Analysis (Stage-Boundary, 4, 5)
- Blue (#2196F3): Synthesis / Enrichment (6, 7, 7b)
- Red (#F44336): Retry / Gap Resolution (8)
- Gray (#607D8B): Final phases (9, 10, Finalize)
- Light yellow: ExploreContext shared state
- Light blue parallelograms: Blob storage artifacts
- Separate swim lane: Macro Coordinator (cross-run)

## CRITICAL ARROWS TO GET RIGHT

These are the most important data flow arrows:

1. Phase 0.5 → ctx.sentinels → MC-1 → Phase 0a (sentinels classified then feed vocab)
2. Phase 0b → ctx.dll_strings → Phase 1 (binary strings used for credential sweep and write-test args)
3. Phase 0a → ctx.vocab → Phase 1 (vocab id_formats used to build write-test args)
4. Phase 1 → MC-2 → ctx.write_unlock_block → Phase 3 (unlock status + failure pattern injected into prompts)
5. Phase 3 → probe-log.json → Stage-Boundary Recalibration → MC-3 (probe results trigger re-calibration and write-unlock scan)
6. MC-3 → ctx.write_unlock_block (may upgrade to WRITE MODE ACTIVE)
7. Phase 6 → api-reference.md → MC-4 (synthesis may describe untested init sequence)
8. Phase 7b → verification results → MC-5 (verified read outputs chain into write args)
9. Phase 8 → gap resolution findings → MC-6 (last chance write-unlock)
10. Finalize → Macro Coordinator → cumulative-knowledge-base.json → Phase 0.5/0a/3 (CIRCULAR FEEDBACK)
11. Phase 3 self-loop: cross-function vocab learning
12. Macro Coordinator self-loop: N×M dispatch → harvest → inject → repeat

## NOTES / CALLOUTS

Add these as yellow sticky-note shapes:

Note 1 (attached to Phase 1): "Deterministic sweep: 8 init modes × 28 credential strings × 3 arg sets per write function. ~250 DLL calls. No LLM."
Note 2 (attached to Phase 3): "Write functions get 14 tool calls (not 8). Prompt says: 'Your ONLY job is to find the init sequence that makes this return 0 instead of 0xFFFFFFFB.'"
Note 3 (attached to MC-3): "ALWAYS re-probes write-unlock here, not just when new sentinels found. This is the biggest improvement point."
Note 4 (attached to Phase 7b): "Closes the enrichment→verification loop. Proves working_call args by executing them. 2/9 verified in testing."
Note 5 (attached to Macro Coordinator harvest step): "COLLECTIVE HIVEMIND: If Run B cracks CS_UnlockAccount but Runs A and C don't, take Run B's working_call and inject it into the next iteration for ALL runs."
Note 6 (attached to circular feedback arrow): "Enterprise: 'This pipeline ran for 24 hours and discovered everything about your systems.' Each iteration tightens. Overfitting IS the product."
Note 7 (attached to MC-1): "RISK: Wrong sentinel meanings at Phase 0.5 propagate through entire run. Mitigate: confidence tags, later stages can revise."
