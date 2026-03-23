# Pipeline Context Engineering UI Controls

Status: Draft v2 — updated for Q14/Q15/Q16/Q17
Owner: Pipeline architecture
Scope: Web UI controls that map directly to pipeline explore runtime, answer-gaps behavior,
       run-set orchestration (Q15), adaptive sentinel calibration (Q16), and the autonomous
       coordinator agent (Q17)

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

---

## 8. Q14 — Merged Schema Controls (union_merger.py)

Q14 adds a **schema accumulator**: after multiple sessions finish, their findings are
merged into a single `accumulated-findings.json` via a UNION strategy. The merger
itself has no UI-facing knobs — it is invoked automatically by the orchestrator (Q15)
after all legs complete. Observable output appears in `merged-schema/merge-summary.json`.

### Observability panel (read-only, no tuning required)

| Dashboard field | Where | What it tells you |
|---|---|---|
| `union_success` | `run-set-summary.json` | Count of sessions whose findings were merged cleanly |
| `functions_total` | `merge-summary.json` | Deduplicated functions seen across all legs |
| `functions_success` | `merge-summary.json` | Functions with at least one successful call evidence |
| `merge_id` | `run-set-summary.json` | Stable ID for this merged schema artifact |

> **Architectural note:** No UI input is needed for Q14 because the merge strategy is
> deterministic (UNION by invocable signature). Future knobs may include a
> `dedup_strategy` selector (`union` / `intersection` / `best_leg`) if cross-run signal
> quality diverges significantly.

---

## 9. Q15 — Run-Set Orchestrator Controls (run_set_orchestrator.py)

Q15 introduces **parallel A/B leg dispatch**: N control legs share a baseline profile
while M ablation legs each test a distinct prompt variant. All legs run concurrently,
then the union merger collects the results.

These controls map to fields in the **run-set definition JSON** (`--run-set-def`).

### 9.1 Run-set definition controls

| UI Label | Key (run_set_def JSON) | Type | Default | Notes |
|---|---|---|---|---|
| Control leg count | `n_control` | int | `3` | Replicated baseline runs that establish the control median [why?](#n-control-and-m-ablation) |
| Ablation leg count | `m_ablation` | int | `3` | Concurrent ablation variants to test [why?](#n-control-and-m-ablation) |
| Ablation profile list | `ablation_profiles` | string[] | `[]` | Ordered list of profile IDs from `prompt_profiles.json` [why?](#prompt-profiles) |
| Analyze phase timeout | `analyze_timeout_sec` | int | `900` | Per-leg wall-clock timeout for static analysis phase (seconds) [why?](#phase-timeouts) |
| Explore phase timeout | `explore_timeout_sec` | int | `2400` | Per-leg wall-clock timeout for full probe+finalization (seconds) [why?](#phase-timeouts) |
| Run set name | `run_set_id` | string | auto-generated | Human-readable tag carried through to every dashboard-row [why?](#ablation-tags) |
| DLL path | `base_config.dll` | string | `tests/fixtures/contoso_legacy/contoso_cs.dll` | Repo-relative path to the binary under analysis |
| Hints file | `base_config.hints_file` | string | `""` | Repo-relative path to a hints text file; used by Q16 sentinel pre-seeding [why?](#sentinel-pre-seeding) |

### 9.2 Profile-level overrides (prompt_profiles.json)

Each profile entry may include per-leg explore overrides that shadow the base config:

| Field | Type | Effect |
|---|---|---|
| `max_rounds` | int | Overrides global `max_rounds` for this leg only |
| `max_tool_calls` | int | Overrides global `max_tool_calls` for this leg only |
| `ablation_variable` | string | Variable family being tested (`prompt_framing`, `vocab_ordering`, etc.) |
| `ablation_value` | string | Human-readable description of the variant value [why?](#prompt-profiles) |

### 9.3 Ablation tags (propagated automatically, not user-set)

These fields are automatically injected into each session's `dashboard-row.json` and
`session-meta.json` after leg completion. They are read-only in the UI but should be
displayed in any run-set results panel:

`run_set_id` · `prompt_profile_id` · `layer` · `ablation_variable` · `ablation_value` · `coordinator_cycle` · `playbook_step`

---

## 10. Q16 — Adaptive Sentinel Calibration Controls

Q16 makes the sentinel table **self-expanding**: new high-bit return codes discovered
mid-run are automatically named and appended to the sentinel table at the stage boundary.
It also tracks write-unlock probe outcomes per session.

### 10.1 Input controls

| UI Label | Key | Location | Default | Notes |
|---|---|---|---|---|
| Hints file | `hints_file` | `base_config.hints_file` in run-set-def | `""` | Plain-text hints with known error codes; pre-seeds sentinel candidates before the LLM naming call [why?](#sentinel-pre-seeding) |

### 10.2 Observable metrics (read-only, display in session results panel)

| Field | Source | Meaning |
|---|---|---|
| `sentinel_new_codes_this_run` | `dashboard-row.json` | Count of net-new sentinel codes named during this session [why?](#sentinel-calibration) |
| `write_unlock_outcome` | `dashboard-row.json` | `resolved` / `blocked` / `not_attempted` — result of the write-unlock probe [why?](#write-unlock-probe) |
| `write_unlock_sentinel` | `dashboard-row.json` | Sentinel code that blocked write access, if any (e.g. `0xFFFFFFFB`) |

### 10.3 Behavioral notes

Stage-boundary re-calibration is **automatic and always on**. There is currently no UI
toggle to disable it. If you see unexpectedly large `sentinel_new_codes_this_run` values
(e.g. > 5 per session), that is a signal the hints file is sparse and should be enriched
before starting a long coordinator run.

---

## 11. Q17 — Coordinator Agent Controls (run_coordinator.py)

Q17 adds a **closed-loop improvement controller** that drives multiple orchestrator
run-sets autonomously. The coordinator iterates through a playbook (Step A → B) until
a stopping condition is met, promoting winning prompt profiles and writing per-cycle
and final Markdown reports.

### 11.1 Coordinator CLI controls

| UI Label | CLI flag | Type | Default | Notes |
|---|---|---|---|---|
| Max coordinator cycles | `--max-cycles` | int | `10` | Hard ceiling on total cycles; triggers `max_cycles_reached` stopping [why?](#coordinator-stopping-conditions) |
| Component | `--component` | string | `contoso_cs` | Pipeline component slug passed to each run-set |
| Hints file | `--hints-file` | string | `""` | Forwarded to every run-set for Q16 sentinel pre-seeding |
| DLL path | `--dll` | string | auto-detected | Override DLL path when running against non-default fixtures |
| Output directory | `--output-dir` | string | `sessions/_runs` | Root for all run-set sub-directories and coordinator reports |
| State file | `--state-file` | string | `sessions/coordinator-state.json` | JSON file tracking cycle state; required for `--resume` [why?](#coordinator-state) |
| Resume | `--resume` | flag | off | Continue from an interrupted run; without this flag, re-running on an existing active state file will be blocked |

### 11.2 Playbook variable queue

The coordinator sweeps a fixed ordered list of variable families. The default order is:

```
["prompt_framing", "vocab_ordering", "context_density", "tool_budget"]
```

This order is currently baked into `_VARIABLE_FAMILIES` in `run_coordinator.py`.

**To expose in UI:** Allow the operator to reorder or subset the variable family queue
before starting the coordinator. Each item corresponds to a set of profiles in
`prompt_profiles.json` where `ablation_variable` matches that family name.

[why?](#playbook-variable-queue)

### 11.3 Embedded thresholds (future UI knobs)

These are currently hard-coded in the coordinator logic but are natural candidates for
operator override:

| Threshold | Current value | Effect |
|---|---|---|
| Plateau detection window | 3 consecutive cycles with no promotion | Triggers `plateau_reached` stopping [why?](#coordinator-stopping-conditions) |
| Promotion criterion | `ablation_fs >= control_median` AND no gate regression | Minimum bar for a variant to be promoted |
| Step A capture quality requirement | `capture_quality == "complete"` | If any control leg has degraded capture, coordinator halts [why?](#capture-quality) |

### 11.4 Coordinator output artifacts (display in UI)

| Artifact | Path | Description |
|---|---|---|
| Per-cycle report | `<output_dir>/cycle-N-report.md` | Markdown: step, variable, control median, promotion decision, sentinel counts |
| Final report | `<output_dir>/coordinator-final-report.md` | Written on any stopping condition with recommended next action |
| State file | `--state-file` path | JSON with `current_cycle`, `playbook_step`, `stopping_reason`, `cycles_completed[]` |

### 11.5 Stopping conditions reference

| Reason | Trigger | Recommended action |
|---|---|---|
| `max_cycles_reached` | Cycle count hit `--max-cycles` | Review final report; run again with `--resume` and higher `--max-cycles` if the queue is not exhausted |
| `plateau_reached` | 3 consecutive cycles with no promotion | Re-examine the variable queue; try switching to a different variable family or enriching hints |
| `baseline_regression` | Step A control median drops below established baseline | Investigate infra or API changes; do not promote anything until baseline is stable |
| `capture_unreliable` | Step A sees `capture_quality != complete` | Check save-session ZIP integrity and cohesion report; may be transient — retry with `--resume` |
| `run_set_failed` | Orchestrator subprocess exits non-zero | Check orchestrator logs; likely an API or timeout issue |
| `playbook_exhausted` | Variable queue emptied and no more variables to test | All variable families swept; move to release candidate validation |

---

## 12. Inline "Why?" Links — Architectural Decision

**Yes, this is the right architectural decision.**

Placing a `[why?]` anchor link under each control is a well-established pattern for
operator-facing tooling:

- **Contextual** — the explanation appears at the moment of decision, not buried in a
  separate concepts doc the operator must hunt for.
- **Auditable** — the link makes it explicit that each control maps to a documented
  pipeline mechanism, not an arbitrary guess.
- **Low-maintenance** — anchor targets live in the same file as the controls, so they
  move together when sections are renamed.

The risk is renderer variance: GitHub, VS Code preview, and other markdown parsers all
auto-generate heading anchors from heading text, but normalize differently (spaces →
hyphens, case-folded to lowercase). Writing heading targets in lowercase with only
hyphens (as done in this doc) is universally safe across all major renderers.

**For the Web UI implementation:** render the "why?" as a collapsible inline tooltip or
an `<details>` panel rather than a same-page anchor, so the operator does not lose
their scroll position. The tooltip text can be sourced from the same doc via a static
build step.

---

## Appendix A — Background Reference

### n-control-and-m-ablation

Each run-set has N *control legs* (all using the `baseline` profile) and M *ablation
legs* (each using a distinct experimental profile). The control legs provide the
statistical floor: the coordinator uses a **median** across the N control
`functions_success` scores as the promotion threshold. Using N≥3 averages out
session-level noise from non-deterministic LLM calls. Using only N=1 would make
promotion decisions fragile. M typically matches N so each ablation variant gets a
comparable amount of evidence.

### prompt-profiles

`prompt_profiles.json` is the catalog of named prompt variants. Each entry sets one
`ablation_variable` (the dimension being changed, e.g. `vocab_ordering`) and describes
the `ablation_value` (what changed, e.g. `error_codes_first`). The orchestrator reads
this file to know what explore-settings overrides to pass to each leg. Adding a new
variant means adding an entry here — no code changes required.

### phase-timeouts

The explore pipeline has two sequential phases per leg:

1. **Analyze** (~120–300 s): static analysis, IAT parsing, sentinel harvest, vocab seeding
2. **Explore** (~600–2400 s): chat-based probe loop, gap resolution, finalization, cohesion

Timeouts prevent a hung API call from stalling an entire run-set. Set
`analyze_timeout_sec` conservatively high (900 s) since it rarely exceeds 3 minutes.
`explore_timeout_sec` should scale with `max_rounds × max_tool_calls × num_functions`.

### ablation-tags

Every session in a run-set is tagged with `run_set_id`, `prompt_profile_id`, `layer`,
`ablation_variable`, `ablation_value`, `coordinator_cycle`, and `playbook_step`. These
tags are written into `session-meta.json` by the API server (via `cohesion.py`) and
injected as a fallback by the orchestrator after save-session completes. Without these
tags, cross-session comparisons in the coordinator and dashboard are impossible.

### sentinel-calibration

The sentinel table maps high-bit return codes (e.g. `0xFFFFFFFB`) to human-readable
names. The initial table comes from `static_analysis.py` (harvested from binary
disassembly) and the hints file. After the probe loop completes, the pipeline scans for
any return codes it encountered that are not yet in the table and calls the LLM to name
them. `sentinel_new_codes_this_run` counts how many such codes were added. A high count
(> 3) is a signal to update the hints file for the next run.

### sentinel-pre-seeding

The hints file is a plain-text file with lines of the form
`0xFFFFFFFC = account locked`. Before the LLM naming call, the Q16 pre-seed block parses
these lines and injects any matching codes into the candidate table. This prevents the
LLM from inventing names for codes that already have known meanings in your domain.

### write-unlock-probe

Before the main probe loop, the pipeline attempts to call a write-classified function
(one marked `write_blocked_by` in the vocab) to determine whether write access is
available. The outcome — `resolved`, `blocked`, or `not_attempted` — is stored in
`write_unlock_outcome`. If blocked, `write_unlock_sentinel` records the exact error code
so the operator knows what access control gate is active. This prevents wasted probe
budget on write functions when the environment does not permit mutation.

### coordinator-state

The state file records the full coordinator history: current cycle, current playbook step,
which baseline profile is currently active, the baseline `functions_success` score, and
the complete `cycles_completed` list. On `--resume`, the coordinator reloads this file
and continues exactly where it left off. Without `--resume`, starting on an existing
state file is blocked to prevent accidentally overwriting a meaningful run history.

### coordinator-stopping-conditions

Stopping conditions are designed to fail fast on signal that the pipeline is unhealthy
(`capture_unreliable`, `baseline_regression`, `run_set_failed`) vs. informational
completion (`max_cycles_reached`, `playbook_exhausted`, `plateau_reached`). The plateau
window of 3 consecutive no-promotion cycles is intentionally short: if three full sweep
cycles cannot beat the control median, either the variable family has no signal or the
baseline is already near ceiling.

### capture-quality

`capture_quality` is emitted by the cohesion phase and takes values `complete` or
`partial`. A value of `partial` indicates that one or more required artifacts were
missing from the save-session ZIP (e.g. no `stage-index.json` or no findings). The
coordinator treats any control leg with `partial` capture as untrustworthy because its
`functions_success` score may under-report actual pipeline performance.

### playbook-variable-queue

The variable families tested by the coordinator represent orthogonal dimensions of the
system prompt and explore configuration:

| Family | What varies |
|---|---|
| `prompt_framing` | Preamble tone, instruction ordering, role framing |
| `vocab_ordering` | Which vocab section appears first: error_codes vs. id_formats vs. value_semantics |
| `context_density` | How many prior findings are included in the context window |
| `tool_budget` | `max_rounds` / `max_tool_calls` allocation per function |

Testing them in this order prioritizes the highest-leverage variables first. Framing and
vocab ordering typically have the largest impact on model behavior; budget tuning is a
diminishing-return optimization best left for last.
