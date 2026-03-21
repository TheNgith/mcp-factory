# Pipeline Model Sweep Automation Plan

Status: Draft v1
Owner: Pipeline architecture
Scope: GitHub Actions `workflow_dispatch` matrix for automated model/parameter sweeps with strict capture scoring

## 1. Goal

Automate comparative runs so we can identify the best model and probe-budget combination
for contract stability, throughput, and transition quality.

## 2. Matrix Dimensions

Use a `workflow_dispatch` matrix over:

1. `scenario`
- `explore_only`
- `answer_gaps`

2. `model`
- example: `gpt-4o`, `gpt-4-1-mini`, `gpt-4-1`

3. `max_rounds`
- example: `3`, `5`, `7`

4. `max_tool_calls`
- example: `6`, `10`, `14`

Optional fifth dimension:

5. `cap_profile`
- `dev`, `stabilize`, `deploy`

## 3. Per-Matrix-Leg Steps

Each leg should:

1. Create/trigger run with `explore_settings` from matrix values.
2. If scenario is `answer_gaps`, submit standardized answer payload.
3. Save strict snapshot with:
- `scripts/save-session.ps1`
- `--enforce-hard-fail`
4. Parse and export metrics from:
- `human/dashboard-row.json`
- `human/collect-session-result.json`
5. Upload leg-level artifact bundle.

## 4. Scoring Fields

Rank legs using this priority:

1. `contract_valid` (must be true)
2. `hard_fail` stability on repeat inputs
3. `transition_fail_count` (lower is better)
4. `capture_quality` (`complete` only for promotion)
5. runtime (lower is better, all else equal)

Secondary signals:

1. stage fail count
2. failed transition IDs
3. evidence completeness for stage-06 when scenario is `answer_gaps`

## 5. Determinism Requirement

For each selected top candidate, run a second identical leg and compare:

1. `contract_valid`
2. `hard_fail`
3. `transition_fail_count`
4. `failed_transitions`

If these differ materially, mark candidate unstable and down-rank.

## 6. Suggested Workflow Structure

Proposed file:

- `.github/workflows/model-sweep.yml`

Jobs:

1. `matrix-run`
- Executes all matrix legs
- Emits per-leg JSON summary

2. `aggregate`
- Collects all per-leg summaries
- Produces ranked markdown and JSON
- Uploads final comparison artifact

3. `optional-promote-check`
- Validates top-N candidates meet strict gating criteria

## 7. Example `workflow_dispatch` Inputs

1. `api_url`
2. `component`
3. `dll_source`
4. `hints_profile`
5. `use_case_profile`
6. `answer_payload_profile`
7. `repeat_top_candidates` (bool)

## 8. Example Matrix (Initial)

- scenario: `[explore_only, answer_gaps]`
- model: `[gpt-4-1-mini, gpt-4o]`
- max_rounds: `[3, 5]`
- max_tool_calls: `[6, 10]`

This yields 16 legs before repeats.

## 9. Artifact Contract for Each Leg

Each leg should upload:

1. `run-input.json` (matrix inputs)
2. `run-metrics.json` (parsed metrics)
3. strict saved session folder
4. `score-row.json`

Minimum required fields in `run-metrics.json`:

1. `scenario`
2. `model`
3. `max_rounds`
4. `max_tool_calls`
5. `contract_valid`
6. `hard_fail`
7. `transition_fail_count`
8. `capture_quality`
9. `runtime_seconds`
10. `session_folder`

## 10. Gating Recommendation

Define gates for automatic acceptance:

1. `contract_valid == true`
2. `capture_quality == complete`
3. `hard_fail` deterministic across repeat run
4. `transition_fail_count` below configured threshold

If gate fails, candidate is retained for analysis but not selected for default profile.

## 11. Relationship to Web UI Controls

This automation should consume the same control schema documented in:

- `docs/architecture/PIPELINE-CONTEXT-ENGINEERING-UI-CONTROLS.md`

This keeps manual UI runs and automated matrix runs comparable.

## 12. Next Implementation Steps

1. Add `.github/workflows/model-sweep.yml` with matrix + aggregator.
2. Add parser script for `collect-session-result.json` + `dashboard-row.json`.
3. Add `sweep-results.md` publishing in workflow artifacts.
4. Run first reduced matrix and set default `balanced` baseline from top stable candidate.
