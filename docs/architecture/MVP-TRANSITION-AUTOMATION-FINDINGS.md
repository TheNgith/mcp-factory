# MVP Transition Automation Findings

Date: 2026-03-21
Status: Active tracking log
Related plan: docs/architecture/MVP-TRANSITION-AUTOMATION-EXECUTION-PLAN.md

## Current Position

Goal:
- Convert T-04, T-05, T-14, and T-15 into evidence-backed, automatable checks that can be evaluated in one compact readiness summary.

Current known baseline from strict A/B artifacts:
- T-04: warn
- T-05: warn
- T-14: partial
- T-15: partial

Interpretation:
- Runtime determinism is strong, but prompt-path observability is incomplete.

## Variable Isolation Strategy

Principle:
- Isolate one variable family per batch; keep all other settings fixed.

Default fixed configuration:
- mode: dev
- model: gpt-4o
- max_rounds: 2
- max_tool_calls: 5
- gap_resolution_enabled: true

Parallel recommendation (initial):
- 4 legs in parallel per batch (`--runs 4 --max-parallel 4`).
- One control leg + three treatment legs changing the same variable family.

How many variables can be isolated in parallel without losing causal clarity:
- Practically 1 variable family per batch.
- 3 variants of that family can be tested against 1 control in the same batch.
- More than one variable family per batch increases ambiguity and is not recommended for MVP gate work.

## Variable Families and Order

1. Instrumentation coverage family (start here)
- Target: T-04, T-05
- Example changes:
  - probe prompt sample emission fidelity
  - static-id source attribution in fallback arg selection

2. Chat-context artifact family
- Target: T-14, T-15
- Example changes:
  - emit chat system context turn-0 artifact
  - ensure findings/vocab injection trace is persisted

3. Threshold and policy family (only after pass stability)
- Target: regression hardening, not discovery
- Example changes:
  - expected transition status contract strictness
  - CI reporting verbosity and fail thresholds

## Model Change Policy

Do not change models yet.

When to start model changes:
- After at least 2 to 3 consecutive green batches where T-04/T-05/T-14/T-15 are pass under fixed runtime settings.

Why:
- Changing model before instrumentation closure confounds root-cause analysis.
- MVP transition automation must first prove pipeline evidence integrity independent of model variance.

## Batch Findings Log Template

Use one entry per batch run:

- Batch ID:
- Date/time UTC:
- Command:
- Variable family changed:
- Control config:
- Variants tested:
- Transition outcomes:
  - T-04:
  - T-05:
  - T-14:
  - T-15:
- Readiness summary: pass|fail
- Evidence paths:
- Known risks observed:
- Next minimal change:

## Batch 0 (Initialization)

- Batch ID: B0-initialization
- Date/time UTC: 2026-03-21
- Command: pending implementation run
- Variable family changed: none (planning baseline)
- Control config: dev / gpt-4o / rounds=2 / tool_calls=5 / gap=true
- Variants tested: n/a
- Transition outcomes:
  - T-04: warn (baseline expectation)
  - T-05: warn (baseline expectation)
  - T-14: partial (baseline expectation)
  - T-15: partial (baseline expectation)
- Readiness summary: fail (expected)
- Evidence paths:
  - sessions/*strict-a/transition-index.json
  - sessions/*strict-b/transition-index.json
- Known risks observed:
  - Prompt and chat-path artifacts not consistently emitted for transition proof.
- Next minimal change:
  - Implement transition evaluator + tests and run first 4-leg instrumentation batch.

## Immediate Next Action

1. Implement transition gate tests for artifact presence and status contracts.
2. Extend runner output with one readiness JSON/MD per batch.
3. Add CI non-blocking report step.
4. Execute Batch 1 against instrumentation family and log findings here.
