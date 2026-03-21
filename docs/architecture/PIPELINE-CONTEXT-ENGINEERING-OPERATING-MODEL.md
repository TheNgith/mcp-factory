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
2. answer-gaps strict captures are currently degraded in your saved evidence due
   to missing stage-index, transition-index, and cohesion-report.

This indicates runtime/deployment parity is not complete for answer-gaps path.

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

The strict answer-gaps snapshots you provided still show degraded contract
artifacts, so live deployment should be re-validated after latest code publish.
