# QUESTIONS

Purpose: open-ended questions that should drive the next architecture and
implementation decisions. Each question includes a suggested answer direction
so planning stays actionable.

## 1) Coverage Ceiling: What is the real blocker class for each unresolved function?

Why this question matters:
- "8/13" is only useful if unresolved functions are classified by root cause,
  not just counted as failures.

Suggested answer direction:
- Build a per-function blocker taxonomy:
  1. state-dependent handle chain,
  2. missing domain value constraints,
  3. argument-shape ambiguity,
  4. structural ceiling (likely unprobeable in MVP).
- Define one primary blocker class per unresolved function.

## 2) Progression: What exact changes should move dev runs from 8/13 to 10+?

Why this question matters:
- Without explicit progression targets, retries become random tuning.

Suggested answer direction:
- Define a 3-step progression plan with measurable milestones:
  1. S-02 argument quality improvements,
  2. S-06 structural classifier improvements,
  3. clarification-tier enablement.
- Set expected gain per step and rollback criteria.

## 3) S-02 Arguments: Should we keep a generic DLL argument list?

Why this question matters:
- Generic lists provide coverage, but uncontrolled use causes repetition.

Suggested answer direction:
- Yes: maintain a generic argument catalog.
- Add adaptive ranking so model picks top-N candidates by evidence score.
- Block repeated calls unless one mutation dimension changes.

## 4) Sentinel Discovery: How should unknown return codes become durable knowledge?

Why this question matters:
- The long-term goal requires autonomous sentinel interpretation.

Suggested answer direction:
- Add confidence-scored sentinel lifecycle:
  1. observed,
  2. correlated with failure class,
  3. behavior-validated,
  4. promoted to stable vocabulary.
- Require at least two independent observations before promotion.

## 5) Clarification Tier: When should operator Q&A be enabled?

Why this question matters:
- Clarification should be strategic, not a fallback every time probes fail.

Suggested answer direction:
- Enable only after bounded retries show no progress and stable failure pattern.
- Ask one constrained question per function with direct mapping to retry logic.
- Record accepted answers in machine-readable format for future runs.

## 6) Clarification Quality: What makes a clarification question valid?

Why this question matters:
- Poorly formed questions produce vague answers that cannot improve probes.

Suggested answer direction:
- Require each question to include:
  1. target function,
  2. observed failure evidence,
  3. one missing fact,
  4. expected answer format,
  5. retry policy that uses the answer.

## 7) Instrumentation: Which warn-tier transitions should be closed first?

Why this question matters:
- Not all instrumentation has equal ROI.

Suggested answer direction:
- Prioritize T-04 and T-05 first because they govern probe quality confidence.
- Emit explicit artifacts:
  1. probe_user_message_sample.txt,
  2. argument-selection reason fields,
  3. static-hints digest matching evidence.

## 8) Documentation Scope: How much thought should be documented?

Why this question matters:
- Over-documenting slows shipping; under-documenting repeats mistakes.

Suggested answer direction:
- Document only decisions that change behavior, gates, or evaluation semantics.
- Keep this rule:
  1. if it affects code path, promotion gate, or KPI target, document it,
  2. if it is temporary brainstorming without a decision, keep it in session notes.

## 9) Beyond MVP: What does 0/13 -> 13/13 without hints require?

Why this question matters:
- This is the north-star system, not current MVP scope.

Suggested answer direction:
- Define a post-MVP architecture track with explicit capabilities:
  1. dynamic state graph reconstruction,
  2. typed memory/struct inference,
  3. active experiment planner across model ensembles,
  4. autonomous sentinel semantics induction.
- Track this as a separate roadmap lane so MVP delivery stays on schedule.

## 10) Multi-model Parallelism: What should be parallelized now vs later?

Why this question matters:
- Parallel runs can improve evidence diversity but may create merge noise.

Suggested answer direction:
- Near-term: run independent model/profile sweeps in parallel and compare scores.
- Mid-term: merge only machine-truth artifacts with confidence weighting.
- Long-term: use cross-model consensus as a proposal, never automatic truth.

## 11) Natural Next Step: Code now, or questions first?

Why this question matters:
- The repo is stable enough that one targeted code step has high value.

Suggested answer direction:
- Do both, in order:
  1. finalize acceptance criteria in docs (done in this file + architecture docs),
  2. implement one concrete code increment: S-02 argument-candidate ranking and
     selection-reason logging,
  3. re-run A/B strict captures and measure uplift.

## 12) Success Criteria: What should prove we are moving toward the north star?

Why this question matters:
- Progress must be measured by outcomes, not effort.

Suggested answer direction:
- Track a compact KPI set:
  1. verified coverage delta by profile,
  2. unresolved-to-clarified conversion rate,
  3. warn-tier transition count trend,
  4. deterministic hard-fail consistency across repeats.
- Require KPI improvement before expanding system complexity.

## 13) Front-and-Center Metadata: What should be easiest for AI to read first?

Why this question matters:
- Distributed metadata increases analysis time and creates avoidable ambiguity.

Suggested answer direction:
- Standardize a compact run-manifest block for every saved session and A/B compare.
- Put these fields at top-level in one JSON object:
  1. run identity: `job_id`, `component`, `saved_at`, `commit`, `commit_msg`,
  2. runtime controls: `mode`, `model`, `max_rounds`, `max_tool_calls`,
     `gap_resolution_enabled`, `clarification_questions_enabled`,
  3. input identity: `hints_sha256`, `use_cases_sha256` plus short previews,
  4. contract gate: `contract_valid`, `hard_fail`, `capture_quality`,
  5. cohesion totals: stage/transition pass-fail-warn-partial counts,
  6. outcome totals: `functions_success`, `functions_error`, unresolved function names,
  7. determinism signals for A/B: `deterministic`, `differences`.

Implementation note:
- Keep full detailed artifacts for deep diagnosis, but require this compact
  manifest to be present for every run so models can triage in one read.

## 14) Tournament Strategy: Should a production run select the best evidence per stage across parallel sessions?

Why this question matters:
- A real SaaS product for SMBs cannot ship a single-run result when any individual
  run may miss functions due to budget exhaustion, model variance, or context-window
  pressure. Running N parallel sessions with different profiles (hint variants, model
  temperatures, tool-budget settings) and then selecting the strongest evidence at each
  stage before synthesizing the final output is the most natural path to a reliable,
  shippable wrapper — but only if "best" can be defined precisely enough to automate
  the selection. Without a concrete scoring rubric, tournament selection just becomes
  manual cherry-picking, which does not scale as a product.

Suggested answer direction:
- Yes: run N parallel sessions and apply a tournament selection pass before synthesis.
  Define "best" at four granularities:

  1. Per-function winner (which session's probe result ships for this function):
     - Prefer `status=success` with `has_return=true` over success without a return.
     - Among multiple successes, rank by `confidence` score descending.
     - If no session succeeded, prefer the session with the richest failure evidence
       (most unique sentinel codes observed, most probe variant attempts).

  2. Per-stage artifact winner (which session's stage artifact becomes authoritative):
     - Prefer the session whose stage-level `capture_quality` is highest.
     - As a tiebreaker, prefer the session with the fewest `warn`/`partial` cohesion
       transitions (i.e. the session that moved most stage gates to `pass`).

  3. Per-probe argument quality (which session's argument selection is most credible):
     - Rank argument source quality in descending order:
         a. `vocab.id_formats` (real extracted string IDs — highest signal),
         b. `static_analysis.binary_strings`,
         c. `heuristic.*` sources,
         d. `default.*` / fallback sources (lowest signal, use only if no other
            session has higher-quality coverage for this function).
     - This ensures the final wrapper is backed by the strongest available evidence
       rather than whichever run happened to finish first.

  4. Determinism as a tiebreaker:
     - When two sessions produce otherwise equal evidence, prefer the session where
       A/B legs of the same call agree (`deterministic: true`).
     - Deterministic results indicate the system is reliable enough to trust without
       a clarification tier.

- Implementation sketch for MVP:
  1. After all N parallel sessions complete, build a per-function evidence table
     indexed by `function_name × session_id`.
  2. Apply the four-tier ranking above to assign one authoritative session per
     function.
  3. Re-run the synthesis and schema-generation step using only the winning evidence
     rows — the output `client.py` and `enriched_schema.json` are then composites
     drawn from the best-performing session per element, not a single session's output.
  4. Record provenance: every synthesized field should carry a `source_run_id` and
     `selection_reason` so the customer (or a support engineer) can audit why any
     particular probe result was chosen.

- Connection to existing questions:
  - Q10 ("Multi-model Parallelism") asked about parallelizing runs and merging
    artifacts. Q14 extends that into a full selection architecture: it answers *what*
    to merge, *how* to rank candidates, and *what provenance trails* the product must
    emit. Q10 is a prerequisite; Q14 is the product-level answer.
  - Q12 ("Success Criteria") KPIs should be measured against the *tournament output*,
    not individual session outputs, to reflect real product quality.
  - Q13 ("Front-and-Center Metadata") compact manifest fields should be emitted per
    session so that the tournament pass can compare sessions in a single structured
    read without re-parsing full artifact trees.

## 15) Context Ablation: Should each stage run N identical-context sessions PLUS M deliberate context-variant sessions that change one prompt variable at a time?

Why this question matters:
- Q14 asks which parallel run "won" and how to select it. Q15 asks *why* it won.
  If all N parallel runs vary only by random sampling, the tournament tells you the
  best result but not which context decisions produced it. To build a product that
  reliably generates high-quality outputs, you need to know which context variables
  (prompt framing, vocab emphasis, hint ordering, budget, tool sequence) are load-bearing
  at each stage — and which are noise. Without deliberate one-variable-at-a-time
  variation, you cannot separate signal from luck. This is the difference between a
  pipeline that works and a pipeline you understand well enough to improve confidently.

Suggested answer direction:
- Yes: structure the parallel run set in two layers per stage:

  Layer 1 — Robustness runs (N identical-context):
  - All N runs share exactly the same prompt, context, hints, and settings.
  - Purpose: reduce noise. A stage is "reliably solved" only when the majority of
    identical-context runs converge on the same outcome.
  - Recommended N: 3–5 for MVP (odd number, simple majority rule).
  - These feed directly into the Q14 tournament selection for the final output.

  Layer 2 — Ablation runs (M context-variant, one variable changed per run):
  - Each of the M runs changes exactly ONE context variable from the Layer 1 baseline.
  - Purpose: causal discovery. If changing variable X in one run improves a stage
    outcome that was failing in all Layer 1 runs, X is load-bearing for that stage.
  - Recommended M: 3–5 variants per batch (covering the highest-priority variable
    families first).
  - These do NOT directly feed the tournament output; they feed a separate
    "context learning log" that drives prompt/config improvement across batches.

- Variables to vary, ordered by expected impact on stage quality:
  1. Prompt framing: rephrase the instruction — e.g. "probe this function carefully"
     vs "your job is to find valid argument combinations" vs "treat this as unknown
     territory; explore systematically". One variant per run.
  2. Vocab/hint ordering: put highest-confidence static IDs first vs last vs omitted.
     Tests whether the model front-loads or recency-biases static hints.
  3. Context density: verbose context (all prior findings) vs minimal context
     (only current-stage facts) vs no-init (no prior context at all).
  4. Tool budget: low (8), medium (16), high (24). If a stage only passes at budget=24,
     that stage's task is underspecified and needs a targeted prompt fix, not more budget.
  5. Temperature / sampling (post-MVP): keep fixed for MVP; only vary after Layer 1
     robustness is confirmed across 2–3 consecutive batches.

- What to record per ablation run:
  - Which variable was changed and what the exact change was.
  - Stage-level outcome: pass / warn / fail per transition gate.
  - Function-level outcome: how many functions reached success vs warn vs unresolved.
  - Comparison delta: outcome compared to the Layer 1 majority baseline for the
    same stage — better / same / worse.

- Decision rule for promoting a context variant to new baseline:
  - A variant becomes the new Layer 1 baseline if it improves at least one stage
    gate from warn/fail to pass AND does not regress any other stage gate.
  - Require improvement in at least 2 of 3 Layer 2 ablation runs (not just 1) before
    promoting, to avoid promoting a lucky outlier.

- Connection to existing questions:
  - Q14 ("Tournament Strategy") defines the selection mechanism for Layer 1 runs.
    Q15 defines how the context that feeds those runs improves over time.
  - Q7 ("Instrumentation") and Q12 ("Success Criteria") both assume the context is
    fixed across measurements. Q15 formalizes the process for deliberately unfixing it
    in controlled, one-variable-at-a-time increments.
  - The variable isolation strategy already documented in
    MVP-TRANSITION-AUTOMATION-FINDINGS.md is the embryonic form of Q15: one variable
    family per batch, three variants against one control. Q15 elevates this from a
    debugging tactic into a first-class product architecture principle.

### Q15 Implementation — What it actually takes

**Full picture of what needs to be built:**

1. Prompt profile system:
   - Prompts are currently hardcoded in `api/explore_prompts.py`.
   - Need: a parameterizable profile mechanism — a small set of named prompt
     fragments that can be swapped per run without code changes.
   - Each Layer 2 ablation run receives a profile that differs in exactly one
     fragment (e.g. instruction framing, hint ordering, verbosity). Everything
     else is identical to the Layer 1 control.
   - Estimated effort: ~half day.

2. Run-set orchestrator:
   - A script (or extension of `run_batch_parallel.py`) that launches N+M runs in
     parallel, tags each with its profile name, and waits for all to complete.
   - N = identical-context control runs (Layer 1).
   - M = ablation runs, one variable changed per run (Layer 2).
   - Outputs: per-run `transition-readiness.json` + per-run findings schema.

3. Accumulated knowledge merger (the key architectural distinction from Q14):
   - Q14 ("Tournament Strategy") selects a WINNER per function — one run's result
     becomes authoritative.
   - Q15's accumulator is a UNION: every valid finding from every run contributes
     to the final schema, regardless of which run it came from.
   - A run that only produced ONE function's result that no other run found is
     still a valuable contributor. The goal is the most complete schema possible,
     not the schema from the best single run.
   - Merger logic per function:
       a. Collect all `status=success` findings across all runs for that function.
       b. Merge: prefer the highest-confidence result, but include any unique
          evidence (arg values, return semantics, boundary conditions) that other
          runs discovered and the highest-confidence run missed.
       c. Record `source_run_id` and `selection_reason` per field so the merge
          is fully auditable.
       d. If NO run produced a success for a function: merge the richest failure
          evidence (most unique error codes seen, most probe variants attempted)
          as the best available characterization of that function's behavior.
   - This accumulated schema is the authoritative stage output — not any single
     run's schema.

4. Stage-by-stage knowledge pipeline:
   - The accumulation does not happen once at the end. It happens at each stage
     boundary, and the accumulated schema from stage N is the input context for
     all runs in stage N+1.
   - Flow:
       Stage 1 (pre-probe/probe): N+M runs → accumulated findings schema
       Stage 2 (synthesis): N+M runs, each seeded with accumulated Stage 1 schema
       Stage 3 (post-probe/harmonization): N+M runs, seeded from Stage 2 output
       ...and so on through finalize.
   - This means each stage's parallel runs start from the richest possible
     knowledge base — the union of everything all prior-stage runs discovered —
     rather than any one run's partial view.
   - Write function unlocking follows naturally: if any run in Stage 1 finds the
     sentinel code that unlocks a write path, that code is in the accumulated
     schema that feeds Stage 2. Every Stage 2 run starts knowing the unlock key,
     not just the lucky run that found it.

5. What "implementing Q15" means in practice for the repository:
   - Phase 1 (available now, no new code): run N=3 identical control runs,
     compare convergence. Are findings deterministic? If yes, Layer 1 is validated.
   - Phase 2 (~half day): prompt profile mechanism + per-run profile tagging.
   - Phase 3 (~1–2 days): accumulated knowledge merger script that reads all
     post-run schema artifacts and emits one composite schema per stage boundary.
   - Phase 4 (~1 day): stage-by-stage orchestrator that wires Phase 3 output
     as the seeded input for the next stage's N+M run-set.

## 16) Adaptive Sentinel Calibration: Should sentinel codes be re-attempted at every stage boundary, with parallel probe variants for each attempt?

Why this question matters:
- Sentinel calibration currently runs once as Phase 0.5 — before any exploration
  begins — with empty-arg calls to every exported function. If the run returns few
  high-bit return values (due to init state, runtime variance, or probe ordering),
  the sentinel table is sparse. From that point on, the pipeline operates with
  incomplete error semantics for every subsequent stage.
- This matters because one unknown sentinel code can block an entire write path.
  When `0xFFFFFFFB` is unknown, the system cannot distinguish "payment limit
  exceeded" from "initialization failure" from "corrupt input" — three situations
  requiring completely different responses. Every stage from S-02 onward interprets
  that code incorrectly or conservatively until it is resolved.
- Sentinel unlock cascades: once one code's meaning is confirmed, it often
  implies the meaning of adjacent codes — a DLL that uses `0xFFFFFFFB` for "limit
  exceeded" typically uses `0xFFFFFFFC` for "invalid input" and `0xFFFFFFFD` for
  "not found." Resolving one systematically narrows the search space for the rest.
- The current one-shot approach cannot exploit evidence that accumulates mid-run:
  a successful probe in S-02 may surface a code that was never seen in Phase 0.5,
  but there is no mechanism to feed that observation back into the sentinel table
  for the remaining functions.

Suggested answer direction:
- Yes: make sentinel calibration opportunistic and continuous, not a one-time pass.

  1. Initial calibration (Phase 0.5, current):
     - Keep the empty-arg sweep to establish a baseline sentinel table before any
       LLM probe rounds begin.
     - Add: seed from `vocab["error_codes"]` (Q-1 fix) so hint-derived codes are
       pre-populated even if the empty-arg sweep misses them.

  2. Opportunistic re-calibration after each stage boundary:
     - After Stage 1 (probe loop) accumulation, scan all probe-log entries across
       all N+M runs for any new high-bit return values not in the current sentinel
       table.
     - Submit newly observed candidates to the sentinel LLM call (same as Phase
       0.5 but incremental — only the new codes, not the full sweep).
     - Update the accumulated schema's sentinel table before Stage 2 runs begin.
     - Any Stage 2 run that has a newly resolved sentinel starts with richer
       error semantics than the Stage 1 runs had.

  3. Parallel sentinel probe variants (Layer 2 for sentinels):
     - When a code remains unresolved after both Phase 0.5 and Stage 1, launch
       M dedicated sentinel probe runs before Stage 2 — each with one varied
       prompt framing specifically designed to elicit that code.
     - Example variants:
         a. "This code appears when the function enforces a constraint. Probe the
            function with extreme argument values to trigger the constraint."
         b. "This code appears when required state has not been established. Probe
            the initialization chain to identify what pre-conditions it enforces."
         c. "This code appears after a resource limit is exceeded. Probe with
            progressively increasing numeric arguments to find the threshold."
     - Record which variant's probe caused the code to appear and in what context.
       That probe is the evidence the sentinel's meaning is based on.

  4. Unlock cascade exploitation:
     - Once a code is resolved, check the resolved meaning against a cascade
       pattern table:
         "limit exceeded" → adjacent code is likely "below minimum" or "range error"
         "not found" → adjacent code is likely "already exists" or "duplicate"
         "permission denied" → adjacent code is likely "not initialized" or "locked"
     - Use cascade patterns to generate probe hypotheses for the next adjacent
       unresolved code, rather than treating each code as independent.

  5. Write function unlock gating:
     - The write-unlock probe (`_probe_write_unlock` in `explore_phases.py`)
       currently runs once at the start and, if it fails, permanently gates all
       write-path functions as `dependency_missing`.
     - With adaptive calibration: if Stage 1 accumulation resolves a new sentinel
       that explains why write-unlock was failing (e.g. the code meant "not
       initialized" and initialization needs a specific arg value), re-run
       write-unlock before Stage 2 with the newly resolved context.
     - A write function that was permanently blocked in Stage 1 may become
       accessible in Stage 2 given better sentinel context. Unlocking even one
       write function often reveals call patterns that unlock others (because
       write functions commonly share initialization dependencies).

- Connection to existing questions:
  - Q15 ("Context Ablation") Layer 2 is the mechanism for varying sentinel probe
    prompts. Q16 defines what specifically to vary and when to trigger it.
  - Q14 ("Tournament Strategy") accumulator includes sentinel table as a field —
    the most completely resolved sentinel table from all runs is the one that
    feeds the next stage, just like the most complete function findings.
  - G9 (Capstone sentinel harvesting, already implemented) provides the pre-probe
    baseline by reading sentinel constants directly from binary instructions. Q16
    is the runtime complement: harvesting sentinels that Capstone missed because
    they only appear under specific runtime conditions, not in static code paths.

## 17) Autonomous Coordinator Agent: Should a persistent agent own the improvement loop — running batches, applying the UNION merger, evaluating gates, and promoting baselines without human intervention?

Why this question matters:
- Q15 and Q16 both require repeated cycles: run N+M batch → evaluate → decide
  whether to promote → update knowledge base → pick next variable → repeat.
  If a human must initiate each cycle, the system's iteration speed is bounded by
  human availability, not compute. At scale — multiple DLLs, multiple models,
  multiple prompt families — this loop cannot be supervised manually.
- The coordinator agent is not a new concept: it is Q14 + Q15 + Q16 composed into
  an autonomous decision layer. Q14 defines what "best" means for a function. Q15
  defines how to vary context and when to promote a variant. Q16 defines how to
  build and refine the sentinel table. The coordinator agent is the entity that
  runs all three of these in sequence and decides what to do next.
- Without a coordinator, Q15 and Q16 are offline analysis tools — a human reads
  FINDINGS.md and decides what to change. With a coordinator, they become a
  self-steering improvement engine. The difference is whether the pipeline learns
  between runs or only within runs.

Suggested answer direction:
- Yes: build a coordinator agent with a defined operating contract, a playbook it
  exhausts in order, and a structured report it produces when it has no more
  autonomous moves to make.

  **Operating contract (what the agent is allowed to do autonomously):**
  1. Read `docs/architecture/FINDINGS.md`, `QUESTIONS.md`, and `sessions/_runs/baseline/BASELINE.md`
     as its knowledge base at the start of each decision cycle.
  2. Read the Q15 context learning log to determine which variables have been tested
     and what their outcomes were.
  3. Dispatch N+M run-sets via the run-set orchestrator (Q15 Phase 2).
  4. Apply the UNION merger (Q15 Phase 3) after each run-set completes.
  5. Evaluate gate outcomes against the current baseline using the transition
     readiness evaluator.
  6. If a variant passes the promotion criteria (Q15 decision rule: improves ≥1 gate,
     no regressions, confirmed in ≥2/3 ablation runs), promote it to new Layer 1
     baseline and update FINDINGS.md with a new batch entry.
  7. Apply Q16 adaptive sentinel re-calibration at each stage boundary automatically.
  8. Continue until the playbook is exhausted OR a stopping condition is met.
  9. Emit a structured report (see below) and halt — never take an action outside
     the playbook without human approval.

  **The playbook (ordered list of moves the coordinator exhausts before stopping):**
  - Phase A — Robustness confirmation:
      Run N=3 identical control-baseline runs. If findings converge (Layer 1
      validated), proceed. If not, flag instability and halt for human review.
  - Phase B — Highest-impact variable sweep (Q15 Layer 2):
      For each variable family in priority order (prompt framing → vocab ordering
      → context density → tool budget), run M=3 ablation variants:
        B1. Does this variant improve any gate for any function vs. control baseline?
        B2. If yes and promotion criteria met: promote, update baseline, move to next family.
        B3. If no improvement after 3 variants: mark this family as exhausted for
            current baseline, move to next.
  - Phase C — Sentinel calibration (Q16):
      After each stage boundary in Phase B runs, apply opportunistic re-calibration.
      If new sentinel codes are resolved: update accumulated schema, re-run write-unlock,
      check whether newly unblocked write functions change gate outcomes.
  - Phase D — Write-unlock exploitation:
      If any Phase B or C run resolved a write-unlock sentinel, run a dedicated
      write-function batch (N=3 control + the newly-resolved sentinel context).
      Record which write functions moved from blocked → success.
  - Phase E — Model sweep (deferred until budget is available):
      Repeat Phases A–D with model variant swapped. Keep all other context fixed.
      Purpose: determine how much of the ceiling is model capability vs. context quality.
  - Phase F — Multi-DLL generalization (deferred until 10+/13 on contoso_cs.dll):
      Apply the same playbook to a second DLL of similar complexity. If the same
      prompt profile achieves similar function coverage without DLL-specific tuning,
      the technique is generalized.

  **Stopping conditions (the agent halts and reports):**
  1. All playbook phases are exhausted.
  2. Function coverage has not improved across 3 consecutive batches (plateau reached).
  3. A gate regression is observed and cannot be explained by the ablation variable.
  4. Budget limit reached (configurable: max_batches or max_api_spend).
  5. A new sentinel is observed that the coordinator cannot classify — requires human
     domain expert input before proceeding.

  **Structured report format (emitted at halt):**
  - Cycles run: N
  - Current baseline: function_success_count / total, gate pass rates
  - Variables promoted: list with before/after gate deltas
  - Variables exhausted without improvement: list
  - Stopping reason: which condition triggered
  - Recommended human action: one specific next step that requires human judgment
    (e.g. "provide domain meaning for sentinel 0xFFFFFFF9", or "extend hints file
    with order ID format for CS_GetOrderStatus")
  - Artifacts: paths to FINDINGS.md batch entries, UNION-merged schema, ablation log

  **How the coordinator fits into the repository:**
  - Entry point: `scripts/run_coordinator.py` (new file, ~300 lines)
  - Knowledge base reads: read-only access to docs/architecture/ + sessions/
  - Dispatch: calls `scripts/run_set_orchestrator.py` (Q15 Phase 2) → waits for all runs
  - Merge: calls `scripts/union_merger.py` (Q15 Phase 3) on completed run-set
  - Evaluate: calls `api/transition_readiness.py` evaluate_session on merged output
  - Promote: appends batch entry to FINDINGS.md, updates sessions/_runs/baseline/ symlink
  - Report: writes `sessions/coordinator-report-{timestamp}.md` structured report

  **AI-first design constraint:**
  - Every artifact the coordinator reads or writes must conform to Q13 (compact manifest
    block at top-level, machine-readable in one pass).
  - The coordinator's own state is serialized to `sessions/coordinator-state.json` after
    each cycle so it can be resumed exactly where it left off after an interruption.
  - The structured report is written in a format that another AI agent (or the same
    agent in a future session) can read in one pass to understand exactly what was tried,
    what was promoted, and what the recommended next action is.

  **What the coordinator does NOT do:**
  - It does not modify any API code (`api/*.py`). Code changes are always human-initiated
    based on the coordinator's report. The coordinator's job is to exhaust what the
    current code can do, not to change the code.
  - It does not make irreversible decisions. Promotion merely updates a pointer; the
    prior baseline run-set remains intact in `sessions/_runs/baseline/`.
  - It does not run Phase E (model sweep) or Phase F (multi-DLL) without explicit human
    approval, even if the playbook reaches those phases, because they carry significant
    cost implications.

- Connection to existing questions:
  - Q13 ("Front-and-Center Metadata") is a prerequisite: the coordinator can only
    evaluate runs efficiently if the compact manifest is present per run.
  - Q14 ("Tournament Strategy") defines the per-function selection logic the coordinator
    uses when building the UNION merged output.
  - Q15 ("Context Ablation") defines the N+M run structure and promotion criteria the
    coordinator follows in Phases A–B.
  - Q16 ("Adaptive Sentinel Calibration") defines the sentinel update logic the
    coordinator applies in Phase C.
  - Q9 ("Beyond MVP") north-star: the coordinator is the first concrete step toward
    an autonomous improvement engine. Phases E and F are the path from MVP to north-star.

### Q17 Implementation Preconditions

Before `run_coordinator.py` can make a promotion decision in one read, three groups of
fields must be added to every run's `session-meta.json` and passed through to the
compact manifest (`human/dashboard-row.json`). These are not architectural decisions —
they are pass-through tags written by the orchestrator and copied by `collect_session.py`.

**Group B — Ablation tagging (required for Q15):**

| Field | Type | Set by | When |
|---|---|---|---|
| `prompt_profile_id` | string | `run_set_orchestrator.py` | At job dispatch |
| `layer` | int (1 or 2) | `run_set_orchestrator.py` | At job dispatch |
| `ablation_variable` | string or null | `run_set_orchestrator.py` | At job dispatch |
| `ablation_value` | string or null | `run_set_orchestrator.py` | At job dispatch |
| `run_set_id` | string (UUID) | `run_set_orchestrator.py` | At job dispatch |

Without these fields the coordinator cannot distinguish a control run from an ablation
variant. It cannot compute a delta. Promotion decisions are impossible.

**Group C — Sentinel state (required for Q16):**

| Field | Type | Set by | When |
|---|---|---|---|
| `write_unlock_outcome` | string enum | `api/explore_phases.py` | After write-unlock probe |
| `write_unlock_sentinel` | string or null | `api/explore_phases.py` | After write-unlock probe |
| `sentinel_new_codes_this_run` | int | `api/explore_phases.py` | After each stage boundary |

Values for `write_unlock_outcome`: `"blocked"`, `"resolved"`, `"not_attempted"`.
Without these fields the coordinator cannot tell whether a blocked write-function is
newly unblocked by a sentinel resolution, and cannot trigger Phase D of the playbook.

**Group D — Coordinator state (required for Q17):**

| Field | Type | Set by | When |
|---|---|---|---|
| `coordinator_cycle` | int | `run_set_orchestrator.py` | At job dispatch |
| `playbook_step` | string | `run_set_orchestrator.py` | At job dispatch |

Without these the coordinator cannot group runs by cycle or trace which playbook step
produced which batch — essential when reading across many session folders after a
re-start.

**Implementation effort for all three groups:**
- `api/worker.py` or `api/explore.py`: accept Groups B+D as job parameters, write to
  `session-meta.json` — ~1 hour (pure pass-through, no logic).
- `api/explore_phases.py`: emit Group C fields to `session-meta.json` at write-unlock
  probe and stage boundary — ~2 hours.
- `scripts/collect_session.py`: copy all six new `session-meta.json` fields into
  `human/dashboard-row.json` — ~30 minutes (pure copy, no logic).

**Human decisions required before implementation begins:**
1. N and M per cycle — recommended: N=3 (Layer 1 control), M=3 (Layer 2 ablation).
   More runs = more signal, more cost. Owner decides the tradeoff.
2. Ablation variable order — recommended priority: prompt framing → vocab ordering →
   context density → tool budget. Owner may reorder or drop families.
3. Promotion bar — recommended: improve ≥1 gate, no regressions, confirmed in ≥2/3
   Layer 2 runs. Owner may tighten (require 3/3) or loosen (require 1/3) this.
4. Starting component — contoso_cs.dll (current target). Confirm or redirect.
5. Max batches before coordinator self-halts — recommended: 10 cycles. Owner sets the
   cost ceiling for autonomous operation before a human check-in is required.

No other human decisions are needed. Everything else is an engineering choice with a
clear answer documented in Q15/Q16/Q17.
