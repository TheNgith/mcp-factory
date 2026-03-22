# AI-First Snapshot Contract

Status: Draft v1
Owner: Pipeline architecture
Primary consumers: AI agents running diagnostics, review, regression analysis, and release gating

## 1. Purpose

This contract defines a stable, machine-first snapshot format for each pipeline run.

Goals:
- Detect where inter-stage causality breaks, not just where execution occurred.
- Make every run comparable with deterministic pass/fail outcomes.
- Minimize ambiguity for automated agent analysis.

Non-goals:
- Replace human-readable summaries.
- Depend on log scraping as primary truth.

## 2. Core Principles

1. Single source of truth
- Stage status and transition status come from explicit JSON artifacts, not inferred from free text.

2. Chain of custody
- For each transition A -> B, capture:
  - producer evidence
  - consumer evidence
  - behavior-change evidence

3. Stable paths and IDs
- Artifact paths and transition IDs are immutable across runs.

4. Machine-first, human-second
- JSON is authoritative.
- Markdown summaries are derived.

5. Single schema authority
- This file is the canonical schema source for contract artifacts.
- `docs/PIPELINE-COHESION.md` is normative for transition rationale and measurement logic, not JSON shape.

## 3. Snapshot Layout

Each session snapshot zip must contain the following top-level structure:

- session-meta.json
- stage-index.json
- transition-index.json
- cohesion-report.json
- evidence/
  - stage-00-setup/
  - stage-01-pre-probe/
  - stage-02-probe-loop/
  - stage-03-post-probe/
  - stage-04-synthesis/
  - stage-05-backfill/
  - stage-06-gap-resolution/
  - stage-07-finalize/
- diagnostics/
  - chat-model-context.txt
  - executor-trace.json
  - probe-log.json
  - transcript.txt
- human/
  - summary.md
  - dashboard-row.json

Notes:
- Existing stage files can remain for backward compatibility, but these contract files are mandatory.
- All contract JSON files must be UTF-8 and valid RFC 8259 JSON.

## 4. Required Contract Files

### 4.1 session-meta.json

Required fields:
- job_id: string
- component: string
- run_started_at: number (unix seconds)
- run_finished_at: number (unix seconds)
- code_commit: string
- profile: string
- final_phase: one of [done, awaiting_clarification, canceled, error]

### 4.2 stage-index.json

Purpose: canonical stage ledger.

Schema shape:
{
  "version": "1.0",
  "stages": [
    {
      "id": "S-00",
      "name": "setup",
      "status": "completed",
      "started_at": 0,
      "finished_at": 0,
      "artifacts": ["evidence/stage-00-setup/schema-t0.json"],
      "checks": {
        "schema_generated": true,
        "invocables_map_generated": true
      },
      "errors": []
    },
    {
      "id": "S-04",
      "name": "synthesis",
      "status": "completed",
      "started_at": 0,
      "finished_at": 0,
      "artifacts": [
        "evidence/stage-04-synthesis/synthesis-input.json",
        "evidence/stage-04-synthesis/api-reference.md"
      ],
      "checks": {
        "synthesis_ran": true,
        "synthesis_covers_all_functions": true
      },
      "errors": []
    },
    {
      "id": "S-05",
      "name": "backfill",
      "status": "completed",
      "started_at": 0,
      "finished_at": 0,
      "artifacts": [
        "evidence/stage-05-backfill/backfill-report.json",
        "evidence/stage-05-backfill/schema-post-backfill.json"
      ],
      "checks": {
        "backfill_ran": true,
        "backfill_patches_count": 6
      },
      "errors": []
    }
  ]
}

Status enum:
- completed
- skipped
- failed

### 4.3 transition-index.json

Purpose: explicit causality checks for A -> B transitions.

Schema shape:
{
  "version": "1.0",
  "transitions": [
    {
      "id": "T-01",
      "name": "hints_to_vocab",
      "status": "pass",
      "tier": "PARTIAL",
      "severity": "medium",
      "producer_stage": "S-01",
      "consumer_stage": "S-02",
      "producer_evidence": ["evidence/stage-01-pre-probe/hints.json"],
      "consumer_evidence": ["evidence/stage-02-probe-loop/probe-system-message.txt"],
      "effect_evidence": ["evidence/stage-02-probe-loop/probe-log.json"],
      "reason": "id_formats from hints present in probe inputs"
    }
  ]
}

Transition status enum:
- pass
- fail
- warn
- partial
- not_applicable

Severity enum:
- high
- medium
- low

### 4.4 cohesion-report.json

Purpose: final machine gate output consumed by compare scripts and release checks.

Schema shape:
{
  "version": "1.0",
  "run": {
    "job_id": "...",
    "component": "...",
    "final_phase": "done"
  },
  "totals": {
    "stage_pass": 0,
    "stage_fail": 0,
    "transition_pass": 0,
    "transition_fail": 0,
    "transition_warn": 0,
    "transition_na": 0
  },
  "gates": {
    "hard_fail": false,
    "reasons": []
  },
  "failed_transitions": [],
  "failed_stages": []
}

Hard fail rule:
- hard_fail = true if any high-severity transition has status fail.

## 5. Transition Contract Set (v1)

### 5.1 Data Flow Transitions (T-01..T-16)

These transitions verify that artifacts produced by one stage were consumed by the next and
caused observable behavior change. They are required for every run.

- T-01 hints_to_vocab
- T-02 hint_error_codes_to_sentinels
- T-03 vocab_to_probe_prompt
- T-04 static_analysis_to_probe_prompt
- T-05 static_ids_to_fallback_args
- T-06 probe_findings_to_synthesis
- T-07 synthesis_to_backfill_patches
- T-08 enrich_call_to_schema
- T-09 auto_enrich_to_schema
- T-10 vocab_to_param_names
- T-11 enriched_knowledge_to_synthesis_input
- T-12 gap_answers_to_mini_session_context
- T-13 mini_session_to_schema_evolution
- T-14 findings_to_chat_prompt
- T-15 vocab_to_chat_prompt
- T-16 schema_evolution_present

Severity assignments (required):

| Transition | Severity |
|---|---|
| T-01 | medium |
| T-02 | high |
| T-03 | medium |
| T-04 | medium |
| T-05 | low |
| T-06 | high |
| T-07 | medium |
| T-08 | high |
| T-09 | medium |
| T-10 | medium |
| T-11 | high |
| T-12 | medium |
| T-13 | high |
| T-14 | high |
| T-15 | medium |
| T-16 | high |

Important normalization for v1:
- T-04 must validate probe user prompt content, not only system prompt content.
- T-11 should validate synthesis input includes enriched findings and vocab semantics; synthesis does not directly consume schema blobs.
- T-12 and T-13 are not_applicable unless gap answers were submitted.

### 5.2 Reasoning Quality Transitions (T-17..T-23)

These transitions verify that the model *used* the context it received — not just that the
context was present. They require the reasoning artifacts defined in §14. Until those
artifacts are emitted, these transitions evaluate as `warn` by default.

| ID | Name | What it checks | Severity |
|---|---|---|---|
| T-17 | `probe_reasoning_to_arg_choice` | Model's per-round text mentions the ID format or hint context it then used as an argument. Catches: hints injected but model ignored them and used fallback. | medium |
| T-18 | `synthesis_input_coverage` | `api-reference.md` covers every function present in `synthesis-input-snapshot.json`. Catches: synthesis silently drops functions with error findings. | high |
| T-19 | `gap_answer_to_strategy` | Expert answer text is reflected in the first gap mini-session probe instruction. Catches: expert answer was injected but model reproduced baseline probes unchanged. | medium |
| T-20 | `hint_codes_to_sentinel_catalog` | All hex codes extracted from hints appear in `sentinel-catalog.json`. Extends T-02 with specific code traceability. | medium |
| T-21 | `hypothesis_to_synthesis_input` | Hypotheses generated in S-03 appear in the synthesis input snapshot. Catches: hypothesis stage ran but output was dropped before synthesis. | medium |
| T-22 | `chat_context_to_tool_reasoning` | Model's tool call reasoning text references `id_formats` or error code names from vocab. Catches P1-1/P1-2 failures at the reasoning level, not just the outcome level. | high |
| T-23 | `probe_depth_to_coverage` | Each function received at least `min_direct_probes` direct calls and probe stop reason was `natural` not `cap_hit`. Catches: cap hit before function was fully explored. | medium |

## 6. Stage Evidence Requirements

For each stage, include:
- stage-input.json
- stage-output.json
- stage-context.txt (prompt or deterministic context)
- stage-checks.json

Minimum required evidence by stage:

S-00 setup
- schema-t0.json
- invocables-map.json
- stage-checks.json
- *(no reasoning artifacts — deterministic stage, no LLM calls)*

S-01 pre-probe
- hints.json
- vocab.json
- static-analysis.json
- stage-checks.json
- **probe-user-message-sample.txt** ← reasoning: proves hints reached probe user message (T-04)
- vocab-update-prompt.txt ← reasoning: prompt sent to extract vocab from hints text
- vocab-update-raw-response.json ← reasoning: raw LLM vocab extraction output
- id-format-extraction-trace.json ← reasoning: per regex match — which hint fragment, what pattern fired
- error-code-hint-parse.json ← reasoning: per hex code — which hint text, how classified

S-02 probe-loop
- probe-system-message.txt
- **probe-user-message-sample.txt** ← reasoning: T-04 (written at first function of run)
- **probe-log.json** (with `arg_sources` per entry) ← reasoning: T-05 — what was called + where each arg came from
- findings.json
- schema-post-enrichment.json
- stage-checks.json
- **probe-round-reasoning.json** ← reasoning: per function × per round — model's assistant text before tool calls ("I will try X because Y")
- **probe-stop-reasons.json** ← reasoning: per function — why probing stopped (natural / cap_hit / cancel / no_tool_call) + model's final summary sentence
- probe-vocab-snapshot.json ← reasoning: vocab state at the moment each function's probe session started
- probe-strategy-summary.json ← reasoning: per function — which probe phases ran (direct / sentinel / batch / cross-product) and which succeeded

S-03 post-probe
- reconcile-report.json
- sentinel-catalog.json
- stage-checks.json
- **sentinel-calibration-decisions.json** ← reasoning: per code — accepted/rejected, which hint text triggered it
- hypothesis-prompts.json ← reasoning: prompts sent for hypothesis generation per function
- hypothesis-raw-responses.json ← reasoning: raw LLM output per function — what the model inferred from raw probe numbers

S-04 synthesis
- **synthesis-input-snapshot.json** ← reasoning: findings + vocab dict at the exact moment _synthesize is called
- api-reference.md
- stage-checks.json
- synthesis-prompt.txt ← reasoning: full system + user message sent to synthesis LLM
- synthesis-raw-response.txt ← reasoning: raw LLM output before post-processing
- synthesis-coverage-check.json ← reasoning: which functions had findings vs. which appear in api-reference.md

S-05 backfill
- backfill-report.json
- schema-post-backfill.json
- stage-checks.json
- backfill-decision-log.json ← reasoning: per patch — original value, new value, evidence that justified it, LLM reasoning text
- param-rename-decisions.json ← reasoning: per renamed param — original name, new name, trigger

S-06 gap-resolution
- gap-input.json
- gap-resolution-log.json
- schema-post-gap.json
- stage-checks.json
- **mini-session-diagnostics.json** ← reasoning: per function — rounds used, tool calls, exit reason, timeout hit
- **expert-answer-interpretation.json** ← reasoning: per function — how expert answer was paraphrased into first probe instruction
- mini-session-round-reasoning.json ← reasoning: per function × per round — model text before tool calls
- gap-strategy-trace.json ← reasoning: per attempt — strategy type (pointer-encoding / buffer-size / XOR / cross-product), what triggered it

S-07 finalize
- harmonization-report.json
- final-vocab.json
- final-status.json
- stage-checks.json
- transition-evaluation-trace.json ← reasoning: per transition — evidence paths checked, comparison made, verdict computed (makes gate auditable)

Chat (agentic) — diagnostics/
- **chat-system-context-turn0.txt** ← reasoning: context injected at turn-0 (currently only on gap runs)
- **chat-tool-reasoning.json** ← reasoning: per tool call — model's text immediately before calling
- chat-error-interpretation.json ← reasoning: per return value — raw value, what model said, what vocab says (detects P1-2 failures)
- chat-round-summary.json ← reasoning: per turn — user message, tools called, return values, model's final summary

Note: items in **bold** are required for T-17..T-23 evaluation. Items without bold are valuable
but optional for the core gate. See §14 for the full reasoning artifact specification and
implementation tiers.

## 7. Pass/Fail Rules

### 7.1 Stage pass/fail

Stage passes only when:
- status == completed
- all required artifacts exist
- stage-checks.json has no required false checks

Stage fails when any of the above is false.

### 7.2 Transition pass/fail

Transition passes only when all three are true:
- producer evidence exists and indicates producer output is present
- consumer evidence exists and indicates output was consumed
- effect evidence exists and indicates downstream behavior changed

Transition warns when producer and consumer evidence exist but effect evidence is weak.
Transition fails when any required evidence is missing or contradictory.
Transition can be not_applicable only for gated flows (example: mini-session path).

## 8. Agent Parsing Rules

Agents must follow this order:

1. Read cohesion-report.json
2. Read transition-index.json for failed or warned transitions
3. Read stage-index.json for failed stages
4. Open only evidence paths referenced by failed transitions
5. Generate root-cause summary with exact transition IDs and artifact paths

Agents must not use transcript parsing as first-pass diagnosis when contract artifacts exist.

## 9. save-session Integration Contract

save-session should do three things:

1. Preserve all contract files in output unchanged.
2. Add derived files under human/ only (summary, markdown, dashboard rows).
3. Never overwrite machine-truth contract files.

This allows humans and agents to share one snapshot while keeping deterministic machine truth intact.

## 10. Compare Contract (cross-run)

A compare tool or script should compute:
- transition_pass_delta
- transition_fail_delta
- high_severity_fail_opened
- high_severity_fail_resolved
- schema_evolution_trend (from T-16 over N runs)

Output path:
- human/dashboard-row.json
- human/compare-summary.md

## 11. Versioning and Compatibility

Contract version field is required in all contract files.

Compatibility policy:
- minor version increments can add optional fields.
- major version increments may change required fields or transition IDs.
- agents must fail closed if required fields are missing for declared version.

## 12. Why This Improves Agent-Only Iteration

1. Deterministic diagnosis
- Agents stop guessing from long logs and read explicit verdicts.

2. Faster triage
- Failed transitions point directly to the smallest evidence set required.

3. Better regression control
- Changes in prompts, caps, refactors, or model deployments can be judged by causality outcomes, not cosmetic success rates.

4. Shared operational language
- Architect and agents refer to the same IDs (T-01..T-16, S-00..S-06), which prevents drift in discussions and fixes.

5. Safer autonomous loops
- Agents can enforce hard fail gates before promoting changes, reducing silent degradation.

## 13. Minimum Adoption Plan

Phase 1
- Emit stage-index.json and transition-index.json.
- Emit cohesion-report.json with hard_fail gate.

Phase 2
- Update save-session to preserve contract files and add derived human outputs.

Phase 3
- Update compare workflow to compute transition deltas and high-severity fail trends.

Phase 4
- Add CI gate: block release when cohesion-report.gates.hard_fail is true.

Phase 5 — Reasoning Artifact Layer
- Emit reasoning artifacts at each stage (see §14).
- Enable T-17..T-23 reasoning quality transitions.
- compare.ps1 computes reasoning quality deltas across runs (did context injection improve model argument choices?).

## 14. Reasoning Artifact Specification

> **Why this exists:** The data flow transitions (T-01..T-16) prove that artifacts reached the
> next stage. They do not prove the model *used* the context it received or *why* it made the
> specific decisions it made. For context engineering iteration this matters enormously — if you
> change a prompt and a transition moves from warn to pass, you still cannot tell *which part*
> of the prompt change caused the improvement without reasoning artifacts.
>
> This is the difference between "the pipeline ran correctly" and "the pipeline thought correctly."
> A product-grade context engineering pipeline needs both layers.

### 14.1 Reasoning Artifact Principles

1. **One reasoning artifact per LLM call, not per stage** — each call is a distinct decision point.
2. **Raw output first** — always persist the model's raw response before post-processing. Post-processing may normalize away the signal you need.
3. **Indexed by function** within S-02 and S-06 — model reasoning is per-function, not per-stage.
4. **Never a gate blocker** — reasoning artifacts are observability signals, not hard gates. Their absence produces a transition `warn`, not a `hard_fail`.
5. **Comparable across runs** — reasoning artifacts must use stable schemas so compare.ps1 can diff them (e.g. arg_source distribution changed from 60% fallback_string to 20% after a hint improvement).

### 14.2 Implementation Tiers

Tier 1 — Already fixed (this sprint):

| Artifact | Stage | Closes |
|---|---|---|
| `probe-user-message-sample.txt` | S-01/S-02 | T-04 |
| `probe-log.json` with `arg_sources` | S-02 | T-05 |
| `mini-session-diagnostics.json` | S-06 | FW-1 |

Tier 2 — Low effort, high diagnostic value (~1–2 hrs each):

| Artifact | Stage | What it unlocks |
|---|---|---|
| `probe-round-reasoning.json` | S-02 | T-17 — the model's text IS already in the conversation list in `_explore_one`; one write call per function after the loop |
| `probe-stop-reasons.json` | S-02 | T-23 — `_policy_stop_reason` already computed in code; just write it |
| `synthesis-input-snapshot.json` | S-04 | T-11/T-18 — snapshot findings+vocab before calling `_synthesize`; ~30 min |
| `chat-tool-reasoning.json` | Chat | T-22 — extract assistant text from existing executor_trace.json; ~1 hr |

Tier 3 — Medium effort, required for Q-series diagnosis:

| Artifact | Stage | What it unlocks |
|---|---|---|
| `sentinel-calibration-decisions.json` | S-03 | T-20 — audits whether hint codes reached calibration |
| `expert-answer-interpretation.json` | S-06 | T-19 — proves expert answer was used in strategy, not just injected |
| `mini-session-round-reasoning.json` | S-06 | T-19/T-22 — per-function per-round reasoning (same pattern as S-02) |
| `probe-vocab-snapshot.json` | S-02 | Q-series diagnosis — vocab at function-start (parallel runs may have stale snapshots) |

Tier 4 — Full product observability (longer term):

| Artifact | Stage | Value |
|---|---|---|
| `synthesis-prompt.txt` + `synthesis-raw-response.txt` | S-04 | Full LLM call audit for synthesis |
| `hypothesis-prompts.json` + `hypothesis-raw-responses.json` | S-03 | Hypothesis quality measurement |
| `transition-evaluation-trace.json` | S-07 | Makes gate logic auditable by agents |
| `vocab-update-prompt.txt` + `vocab-update-raw-response.json` | S-01 | Audits hint → vocab extraction |
| `chat-error-interpretation.json` + `chat-round-summary.json` | Chat | Full agentic loop observability |

### 14.3 The Reasoning Quality Signal in compare.ps1

Once Tier 2 artifacts are emitted, compare.ps1 should compute these additional deltas:

- `arg_source_distribution_delta`: fraction of probe args classified as `static_id` vs. `fallback_string` across runs. An increase in `static_id` rate after a hint change is direct evidence the hint worked.
- `probe_stop_reason_delta`: fraction of functions stopped by `natural` vs. `cap_hit`. An increase in `cap_hit` after a max_rounds reduction is expected; an increase after a prompt change is a signal.
- `reasoning_mention_rate_delta` (T-17): fraction of probe rounds where model text mentions the id_format it then used as an arg.
- `chat_vocab_reference_rate_delta` (T-22): fraction of tool call reasoning texts that cite vocab concepts.

These are the metrics that make context engineering iteration measurable rather than anecdotal.
