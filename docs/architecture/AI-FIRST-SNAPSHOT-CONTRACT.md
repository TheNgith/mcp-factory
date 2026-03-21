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

The following transition IDs are required:

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

S-01 pre-probe
- hints.json
- vocab.json
- static-analysis.json
- stage-checks.json

S-02 probe-loop
- probe-system-message.txt
- probe-user-message-sample.txt
- probe-log.json
- findings.json
- schema-post-enrichment.json
- stage-checks.json

S-03 post-probe
- reconcile-report.json
- sentinel-catalog.json
- stage-checks.json

S-04 synthesis
- synthesis-input.json
- api-reference.md
- stage-checks.json

S-05 backfill
- backfill-report.json
- schema-post-backfill.json
- stage-checks.json

S-06 gap-resolution
- gap-input.json
- gap-resolution-log.json
- mini-session-transcript.txt (if applicable)
- schema-post-gap.json
- stage-checks.json

S-07 finalize
- harmonization-report.json
- final-vocab.json
- final-status.json
- stage-checks.json

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
