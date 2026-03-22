# Pipeline Context Engineering Operating Model

Status: Draft v1
Owner: Pipeline architecture
Scope: end-to-end pipeline context flow from setup through finalize and answer-gaps

## 1. Why This Exists

The system now has contract-first artifact emission and strict save-session
orchestration. The remaining reliability problem is context quality and bounded
decisioning across the whole pipeline, not only answer-gaps mini-sessions.

This document defines:

1. What questions should be staged at each stage.
2. What context should be injected at each stage.
3. How stage outputs accumulate into intelligent probe design and stable
   causality evidence.

## 2. System Objective

From the MVP thesis, the practical objective is:

1. maximize verified behavior for callable functions,
2. separate inferred from verified decisions,
3. classify true ceilings quickly as unprobeable without infinite retries.

Context engineering exists to make those three outcomes deterministic.

## 3. Global Questions (Asked Every Run)

1. What is already known and machine-verifiable?
2. What is unknown but likely probeable with bounded retries?
3. What appears structurally blocked and should be classified early?
4. Are we introducing new evidence this round, or re-running the same pattern?
5. Does this stage produce artifacts that can be consumed by the next stage
   without transcript interpretation?

## 4. Stage-by-Stage Context Model

### S-00 Setup

Primary questions:

1. Do we have complete invocable coverage?
2. Do we have enough baseline schema shape to start probing safely?

Required injected context:

1. component identity and run profile,
2. initial invocables map and schema t0,
3. user hints and use-cases seed.

Must emit:

1. schema-t0,
2. invocables-map,
3. setup checks.

### S-01 Pre-probe

Primary questions:

1. Which IDs, sentinels, and static hints are now trustworthy?
2. What constraints should shape probe argument generation?

Required injected context:

1. normalized id formats,
2. normalized error code vocabulary,
3. static-analysis block including ids and suspicious constants.

Must emit:

1. vocab snapshot,
2. sentinel calibration,
3. static analysis and pre-probe checks.

### S-02 Probe-loop

Primary questions:

1. What is the minimum call pattern that yields verified success?
2. Which failures are dependency-order vs argument-shape vs structural?
3. Are we increasing evidence diversity each round?

Required injected context:

1. known-good calls from successful findings,
2. staged argument templates from id formats and value semantics,
3. strict retry budget and no-progress policy.

Must emit:

1. probe-log,
2. findings,
3. probe context artifacts used by transition checks.

### S-03 Post-probe

Primary questions:

1. Are findings internally consistent after reconciliation?
2. Are sentinel meanings stable or still ambiguous?

Required injected context:

1. full probe outcomes grouped by function,
2. sentinel confidence markers,
3. conflict notes for contradictory outcomes.

Must emit:

1. harmonization/reconcile report,
2. sentinel catalog,
3. post-probe checks.

### S-04 Synthesis

Primary questions:

1. Did successful findings and vocab semantics reach synthesis input?
2. What function-level guidance is strong enough for backfill?

Required injected context:

1. deduplicated function findings,
2. vocab semantics and error interpretations,
3. explicit unresolved list.

Must emit:

1. synthesis input trace,
2. api reference output,
3. synthesis checks.

### S-05 Backfill

Primary questions:

1. Which schema fields changed and why?
2. Did changes preserve prior semantic gains?

Required injected context:

1. synthesis output,
2. current invocables map,
3. patch safety constraints.

Must emit:

1. backfill result with patch counts,
2. schema-post-backfill,
3. backfill checks.

### S-06 Gap-resolution and Answer-gaps

Primary questions:

1. Which unresolved functions are high-value and likely solvable?
2. Which unresolved functions should be classified as structural ceiling now?
3. Did answer-gaps produce schema evolution and new verified evidence?

Required injected context:

1. per-function unresolved list with last observed codes,
2. domain answers mapped to specific functions,
3. bounded mini-session policy (rounds, tool calls, timeout, no-progress stop).

Must emit:

1. gap-resolution-log,
2. mini-session diagnostics,
3. pre/post mini-session schema snapshots,
4. explicit exit classification per unresolved function.

### S-07 Finalize

Primary questions:

1. Is final phase state coherent with artifacts?
2. Are promotion gates deterministically derivable from machine-truth files?

Required injected context:

1. stage and transition indices,
2. final vocab and schema evolution,
3. gate policy.

Must emit:

1. final status,
2. final vocab,
3. transition and cohesion gate outputs.

## 5. Context Accumulation Rules

1. Every stage consumes machine artifacts first, transcript text second.
2. Every stage must annotate what changed from prior stage context.
3. Context passed forward must be compact and action-oriented, not full replay.
4. When confidence drops, stages emit warn/partial, never silent pass.

## 6. Intelligent Probing Design Policy

Use this sequence for targeted probing design:

1. prerequisite replay from known-good calls,
2. minimal controlled mutation,
3. bounded numeric/format sweeps,
4. deterministic fallback,
5. explicit classify and stop.

Stop conditions are mandatory when no new evidence appears.

## 7. Mini-session Budget and Exit Policy

Recommended defaults:

1. max rounds per function: 6,
2. max tool calls per function: 12,
3. timeout per function: 300s,
4. no-progress stop: 2 rounds,
5. identical-call repeat ceiling: 2.

Allowed terminal outcomes:

1. resolved_success,
2. resolved_inferred,
3. unprobeable_now,
4. needs_structural_support.

## 8. Required Diagnostics for Context Governance

Emit per-function diagnostics for gap and mini-session execution:

1. rounds_used,
2. tool_calls_used,
3. timeout_hit,
4. no_progress_stop,
5. last_tool_name,
6. last_tool_args,
7. last_return_code,
8. exit_reason,
9. classification.

Recommended artifact:

1. diagnostics/mini-session-diagnostics.json

## 9. Current Cohesion Checkpoint

Interpretation of current strict captures:

1. explore-only strict captures are cohesive and contract-valid,
2. answer-gaps strict captures are now also cohesive and contract-valid,
3. repeat A/B captures with unchanged settings are deterministic for gate values.

This indicates runtime/deployment parity is now complete for strict captures.

## 10. Should Sessions Use a New Folder

Recommendation: keep one sessions root and add profile-style naming and tags,
not a new top-level sessions tree.

Use folder-note suffixes and metadata fields such as:

1. context_profile: baseline|strict|context-engineering,
2. scenario: explore_only|answer_gaps,
3. probe_budget_profile: dev|stabilize|deploy|bounded-gap.

Reason: compare tooling and historical baselines remain unified.

## 11. Save-session Architecture Status

Current state in repo:

1. save-session PowerShell is orchestration-only,
2. collect_session.py is Python intelligence and contract validation,
3. root save-session doc is a pointer,
4. architecture save-session doc is canonical.

Operational caveat:

T-04 and T-05 remain warn-tier instrumentation gaps. They do not invalidate
contract cohesion but do reduce confidence about prompt-path observability.

## 12. Progression Path: Dev 8/13 to Higher Coverage

Observed baseline in current dev-mode strict runs:

1. pre-gap: 4/13 resolved,
2. post-gap retry: 8/13 resolved,
3. unresolved functions are primarily state-dependent and require better handle
   continuity or operator context.

Progression path should be staged, not overhauled:

1. raise S-02 argument quality first,
2. raise S-06 structural-classification quality second,
3. enable clarification tier only when S-02 and S-06 are stable.

Target progression envelope:

1. dev profile: 8/13 to 10/13,
2. stabilize profile: 10/13 to 11/13,
3. deploy profile with clarification: 11/13 to 12/13,
4. anything beyond that likely requires non-MVP techniques (dynamic tracing,
   structure recovery, or external constraints).

## 13. Clarification Questions Enable Criteria

Enable clarification output when all of the following are true:

1. function remains unresolved after bounded S-06 retry,
2. repeated attempts produce no novel evidence,
3. last known sentinel/return pattern is stable,
4. missing information is actionable by operator (ID format, lifecycle step,
   required ordering, expected units, known good sample).

Do not enable clarification when:

1. failure is still probe-budget-limited,
2. error patterns are contradictory,
3. stage diagnostics are incomplete.

Minimum acceptance criteria for a valid clarification question:

1. names one concrete function,
2. includes one concrete observed failure pattern,
3. asks for one constrained missing fact,
4. maps expected answer to a specific retry strategy.

## 14. S-02 Argument Strategy: Generic Catalog + Adaptive Reasoning

Use a two-layer argument strategy:

1. generic DLL argument catalog,
2. adaptive per-function ranking from observed outcomes.

Generic catalog should include:

1. IDs (customer/order/account style templates),
2. amount and quantity scales (cents, whole units, edge values),
3. status/state strings,
4. null/empty/safe defaults,
5. prerequisite replay handles from known-good calls.

Selection policy is reasoned, not exhaustive:

1. choose top-N candidates by evidence score,
2. block exact-call repetition above repeat ceiling,
3. require one argument mutation dimension per retry,
4. stop when no progress is detected for configured rounds.

This preserves exploration quality while preventing repetitive brute-force loops.
