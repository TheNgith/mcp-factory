# Pipeline Context Engineering UI Controls

Status: Draft v1
Owner: Pipeline architecture
Scope: Web UI controls that map directly to pipeline explore runtime and answer-gaps behavior

## 1. Purpose

This document defines operator-facing controls that should be exposed in the Web UI so
context-engineering behavior is reproducible and auditable.

All controls below map to `explore_settings` in API job creation.

## 2. API Mapping (UI -> explore_settings)

| UI Label | Key | Type | Allowed Range / Values | Notes |
|---|---|---|---|---|
| Run mode | `mode` | enum | `dev`, `normal`, `extended` | Sets default budget profile |
| Cap profile | `cap_profile` | enum | `dev`, `stabilize`, `deploy` | Overrides mode defaults |
| Max rounds per function | `max_rounds` | int | 1-12 | Probe loop round budget |
| Max tool calls per function | `max_tool_calls` | int | 1-24 | Hard cap for function-level call budget |
| Max functions per run | `max_functions` | int | 1-500 | Limits exploration breadth |
| Minimum direct probes | `min_direct_probes_per_function` | int | 1-5 | Enforces floor before early stop |
| Skip documented functions | `skip_documented` | bool | true/false | Useful for focused retest |
| Deterministic fallback | `deterministic_fallback_enabled` | bool | true/false | Keep enabled for stability |
| Enable gap resolution | `gap_resolution_enabled` | bool | true/false | Required for answer-gaps flow |
| Enable clarification questions | `clarification_questions_enabled` | bool | true/false | Controls unresolved-question loop |
| Explore model override | `model` | string | deployment name | Optional per-run model routing |

## 3. Recommended UI Presets

### Fast triage

Goal: short feedback cycle and quick regression detection.

Suggested values:

- `mode=dev`
- `cap_profile=dev`
- `max_rounds=2`
- `max_tool_calls=5`
- `max_functions=50`
- `min_direct_probes_per_function=1`
- `deterministic_fallback_enabled=true`
- `gap_resolution_enabled=false`

Expected artifacts:

- Full contract files for explore-only runs
- Lower evidence depth in `evidence/stage-02-probe-loop/probe-log.json`
- Faster run completion, useful for smoke checks

### Balanced

Goal: default operational mode for daily quality work.

Suggested values:

- `mode=normal`
- `cap_profile=deploy`
- `max_rounds=5`
- `max_tool_calls=10`
- `max_functions=50`
- `min_direct_probes_per_function=1`
- `deterministic_fallback_enabled=true`
- `gap_resolution_enabled=true`

Expected artifacts:

- Strong contract completeness in strict saves
- Useful transition evidence across T-01..T-16
- Practical runtime with moderate depth

### Deep

Goal: maximum coverage and difficult-function diagnosis.

Suggested values:

- `mode=extended`
- `cap_profile=deploy`
- `max_rounds=7`
- `max_tool_calls=14`
- `max_functions=100`
- `min_direct_probes_per_function=2`
- `deterministic_fallback_enabled=true`
- `gap_resolution_enabled=true`

Expected artifacts:

- Rich probe evidence and stronger synthesis input
- Longer runtime and higher token/tool usage
- Better for release-candidate baselines and model comparisons

## 4. Answer-gaps Guardrails (Must Enforce)

UI controls should include guardrails to prevent long/hanging mini-sessions:

1. Per-function timeout selector (default 300s)
2. Max mini-session rounds (default 6)
3. Max mini-session tool calls (default 12)
4. No-progress stop threshold (default 2 rounds)
5. Duplicate-call repeat ceiling (default 2)

Behavior rule:

- If timeout or no-progress threshold is reached, mini-session must classify and exit,
  not continue indefinitely.

## 5. Additional UI Controls To Implement

1. Scenario selector:
- `explore_only`
- `answer_gaps`

2. Strict save toggle:
- Save-session mode `strict` vs `compatibility`

3. Hard-fail enforcement toggle:
- Pass through to save-session `--enforce-hard-fail`

4. Model matrix mode (advanced):
- Submit multiple runs with different `model`, `max_rounds`, `max_tool_calls`

5. Evidence health panel:
- Show contract_valid, hard_fail, capture_quality, transition_fail_count,
  stage_fail_count from `human/collect-session-result.json`

## 6. Preset Artifact Expectations

For all presets, strict save should produce:

1. `session-meta.json`
2. `stage-index.json`
3. `transition-index.json`
4. `cohesion-report.json`
5. `human/dashboard-row.json`
6. `human/session-save-meta.json`

For answer-gaps runs specifically, strict save should also include:

1. `evidence/stage-06-gap-resolution/mini-session-transcript.txt`
2. `evidence/stage-06-gap-resolution/schema-pre-mini-session.json`
3. `evidence/stage-06-gap-resolution/schema-post-mini-session.json`
4. `diagnostics/mini-session-diagnostics.json` (when implemented)

## 7. Operator Runbook (Minimum)

1. Pick preset (`balanced` by default).
2. Execute run.
3. Save strict snapshot.
4. Confirm contract validity from `human/collect-session-result.json`.
5. If answer-gaps run is degraded, switch to `deep` only for unresolved functions,
   not global repeated retries.
