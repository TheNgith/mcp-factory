## Causality Artifact Layer — Revised Plan (2026-03-21)

> **Drafted 2026-03-21** from Codex full-codebase review.  
> This section supersedes the informal transition table from the session discussion.  
> It corrects three factual errors in the original plan and classifies every  
> transition by whether it is measurable today or requires new instrumentation.

---

### Why We Are Doing This

We can read logs to *infer* what happened. We cannot currently *prove* it,
run-to-run, in a machine-checkable way.

The history of disconnects (D-1 through D-12, GR-1, GR-2 above) shows a
recurring pattern: a stage produces output that the next stage silently
ignores or overwrites. The bug is invisible because:

1. **Both stages succeed** — no exception is raised.
2. **Outputs look plausible** — the schema changes, the findings file grows,
   the synthesis report is non-empty.
3. **The causal link is broken** — stage B's output was not influenced by
   stage A's output at all.

Examples of this pattern in the codebase's own history:

- D-3: Explore worker never called `_register_invocables` → `enrich_invocable`
  appeared to run but patched nothing because the registry was empty.
- D-9: Backfill read a stale `invocables` snapshot instead of the freshly
  enriched one → overwrote the very param names it was supposed to preserve.
- D-11: Ground-truth override patched `status=success` but left the `finding`
  text reading "all probes returned sentinel codes" → synthesis saw the error
  text and generated no backfill patches.

In every case the run *completed* and produced artifacts. The score was the
only signal that something was wrong. We need earlier, cheaper, automatic
signal.

The goal of this layer is to move from:

> "We inspected the session ZIP and inferred the flow was probably working."

to:

> "Each stage writes a contract attesting what it produced. A post-run
> evaluator verifies each contract against the next stage's actual input.
> Any broken link is a named, numbered failure — not an inference."

---

### What the Codex Review Corrected

Before implementing anything, three errors from the first-draft plan must be
fixed. These are based on 400k-context analysis of the actual API source.

#### Correction 1 — T-11 (synthesis input is not schema)

**Original claim:** "T-11: Probe schema influenced synthesis input."

**What the code actually does:** `_synthesize()` in `explore_prompts.py:316`
receives `findings: list[dict]`, `vocab: dict`, `sentinels: dict`. It never
reads `mcp_schema.json`. The probe schema is not passed to synthesis at all.
The synthesis prompt is built entirely from findings JSON + vocab block +
sentinel codes (`explore_prompts.py:379`).

**Corrected check:** T-11 should verify that the findings written during the
probe loop (by `record_finding`) actually appear in the `_syn_findings` list
loaded at the start of `_run_phase_6_synthesize` (`explore.py:1314`). The
schema is irrelevant to synthesis; the question is whether probe findings
reached the synthesis LLM's input.

#### Correction 2 — T-04 (static hints in user message, not system message)

**Original claim:** "T-04: static hints block appears verbatim in probe system message."

**What the code actually does:** `ctx.static_hints_block` is built in phase 0b
(`explore.py:348-349`) and appended to the **user message content** of each
probe conversation (`explore.py:492`). It is not part of the system message at
all. The system message is built by `_build_explore_system_message()` which
does not receive `static_hints_block`.

**Corrected check:** T-04 should verify that the probe conversation's first
user message content contains the static hints block text (or that
`static_hints_block_length > 0` in `static_analysis.json`). A system-message
assertion will always fail.

#### Correction 3 — T-12 / T-13 (mini-sessions are answer-gaps-triggered, not phase 8)

**Original claim:** "Mini-session checks are part of phase-8 execution."

**What the code actually does:** `_run_phase_8_gap_resolution` calls
`_attempt_gap_resolution` (automated retry) and optionally emits clarification
questions. It does **not** call `_run_gap_answer_mini_sessions`. Mini-sessions
are spawned exclusively from the `POST /api/jobs/{job_id}/answer-gaps` endpoint
handler (`main.py:791-804`), in a separate background thread.

**Corrected check:** T-12 and T-13 are conditional on the answer-gaps endpoint
being called by the user. In a normal explore-only run that never hits
answer-gaps, these transitions do not fire and should be marked `N/A`, not
`fail`.

#### Correction 4 — T-01 (stale id_formats on incremental runs)

**Code evidence:** `vocab["id_formats"]` is only seeded when the key is absent:

```python
if _hint_ids and "id_formats" not in ctx.vocab:
    ctx.vocab["id_formats"] = _hint_ids
```

If a prior session already set `id_formats`, updated hints with new patterns
will not propagate. The transition check must account for the possibility that
`id_formats` is present but stale — matching the old hints text, not the
current one.

---

### Transition Table — Revised and Classified

Each transition has an **implementability tier** and a **severity** (used to derive `hard_fail`):

Implementability tiers:
- `NOW` — measurable by reading existing blobs/logs with no API changes
- `PARTIAL` — inferrable from existing artifacts; proxy-passing evidence produces `warn` status, not `pass`
- `INSTRUMENT` — requires new code instrumentation to measure

Severity levels:
- `high` — broken link is invisible to the downstream stage, silently corrupts data, or causes complete output failure; any `high`-severity `fail` sets `hard_fail: true` in `cohesion_report.json`
- `medium` — broken link degrades quality or coverage but the pipeline completes and produces usable output
- `low` — cosmetic or informational degradation only; no score impact

| ID | From → To | Corrected assertion | Tier | Severity |
|---|---|---|---|---|
| T-01 | Hints text → `vocab["id_formats"]` | `id_formats` contains at least one pattern matching the current hints text. Fails if key absent OR if hints changed but key was not updated. | `PARTIAL` — guarded setdefault means stale values pass | medium |
| T-02 | User hints error codes → `ctx.sentinels` | Every hex code (`0xFFFFFF..`) found in hints text is present as a key in `sentinel_calibration.json`. | `NOW` — can regex hints.txt and check sentinel_calibration.json | **high** |
| T-03 | Vocab id_formats → Probe system message | Probe system message (saved in `stage-01-explore-*` context blob) contains at least one value from `vocab["id_formats"]`. | `PARTIAL` — context blob exists; grep is viable if format is stable | medium |
| T-04 | Static hints block → Probe user message | `static_analysis.json` field `static_hints_block_length > 0` AND at least one probe conversation's first user message contains the static hints text. *(NOT system message — static hints are appended to user message content at `explore.py:492`.)* | `PARTIAL` — length flag is NOW; message content grep needs new logging | medium |
| T-05 | Static string IDs → Fallback probe args | `explore_probe_log.json` fallback entries for customer-param functions have args matching a pattern from `dll_strings.ids`, not a generic string. | `PARTIAL` — log exists; assertion logic needs evaluator code | low |
| T-06 | Probe findings → Synthesis LLM input | `api_reference.md` mentions function names that appear in `findings.json`. Synthesis input is `findings.json` directly (`explore.py:1314`), so any finding present in the file was available to synthesis. | `NOW` — cross-reference findings.json function names vs api_reference.md | **high** |
| T-07 | Synthesis doc → Backfill patches | Number of schema patches applied by `_run_phase_7_backfill`. **No blob currently records this count.** Must add instrumentation to `_run_phase_7_backfill` to write `backfill_result.json`. | `INSTRUMENT` — not currently measurable | medium |
| T-08 | `enrich_invocable` call → Schema param names | For each function where `explore_probe_log.json` shows an `enrich` event, `invocables_map.json` has at least one param with a non-`param_N` name. | `NOW` — cross-reference probe log enrich events vs invocables_map | **high** |
| T-09 | D-12 auto-enrich → Schema descriptions | Functions that appear in probe log with `phase=d12_autoenrich` have non-Ghidra-boilerplate descriptions in `invocables_map.json`. | `NOW` — probe log flag + invocables_map grep | medium |
| T-10 | Vocab semantic knowledge → Param names | `invocables_map.json` has zero functions where all params are still `param_N` after probe loop completed. | `NOW` — count `param_N` names in invocables_map.json | medium |
| T-11 | Probe findings → Synthesis content | *(Replaces "probe schema → synthesis input" — schema is not synthesis input.)* Functions documented in `findings.json` also appear in `api_reference.md`. Missing function = finding never reached synthesis. | `NOW` — set intersection: findings functions vs api_reference mentions | **high** |
| T-12 | Gap answers → Mini-session context | *(Conditional on answer-gaps endpoint being called.)* Mini-session transcript contains function names from the answer-gaps POST body. | `NOW` (when applicable) — but N/A on explore-only runs | medium |
| T-13 | Mini-session enrich → Schema evolution | *(Conditional on answer-gaps endpoint being called.)* Schema snapshot after mini-session differs from schema snapshot entering mini-session. Guards against D-1 regression. | `NOW` (when applicable) — schema stage snapshot diff | **high** |
| T-14 | Findings → Chat system message | Chat system message at turn 0 (`chat_transcript.json` first system entry) contains text output from `_load_findings`, i.e. at least one function name from `findings.json`. | `NOW` — transcript blob + findings.json cross-reference | **high** |
| T-15 | Vocab → Chat system message | Chat system message contains at least one of: `## ID FORMATS`, `## ERROR CODES`, `## VALUE SEMANTICS`. | `NOW` — grep first system message in chat transcript | medium |
| T-16 | Pipeline → Schema evolution | At least one `schema_evolution.json` entry has `"changed": true`. A run where every checkpoint is byte-identical = complete pipeline freeze. | `NOW` — parse schema_evolution.json | **high** |

**Summary:** 8 transitions are `NOW`-tier (T-02, T-06, T-08, T-09, T-10, T-11, T-14, T-15, T-16). 6 are `PARTIAL`/inferential (T-01, T-03, T-04, T-05, T-12, T-13). 1 requires new instrumentation (T-07). Of the 16 transitions, 7 are `high` severity (T-02, T-06, T-08, T-11, T-13, T-14, T-16), 8 are `medium` (T-01, T-03, T-04, T-07, T-09, T-10, T-12, T-15), and 1 is `low` (T-05). Any `high`-severity `fail` sets `hard_fail: true`.

---

### Stage Flag Groups

Each group corresponds to one JSON blob written by the pipeline. The evaluator
reads these blobs to populate `cohesion_report.json`.

#### SETUP flags — sourceable from existing blobs without instrumentation
| Flag | Source | Assertion |
|---|---|---|
| `schema_generated` | `mcp_schema.json` exists | blob present and non-empty |
| `invocables_map_generated` | `invocables_map.json` exists | blob present |
| `function_count` | `invocables_map.json` | count of top-level keys |
| `has_params` | `invocables_map.json` | at least one function has `parameters` list |
| `schema_t0_saved` | `mcp_schema_t0.json` exists | pre-explore T0 snapshot present |
| `hints_present` | `hints.txt` in session ZIP | non-empty hints text |
| `use_cases_present` | `hints.txt` use_cases section | non-empty |

#### PRE-PROBE flags — sourceable from existing blobs
| Flag | Source | Assertion |
|---|---|---|
| `sentinel_calibration_ran` | `sentinel_calibration.json` exists | blob present |
| `sentinel_count` | `sentinel_calibration.json` | key count |
| `hint_error_codes_merged` | `sentinel_calibration.json` vs `hints.txt` | all hint hex codes appear as keys |
| `id_formats_in_vocab` | `vocab.json` | `id_formats` key is non-empty list |
| `use_cases_reached_vocab` | `vocab.json` | `user_context` key is non-empty |
| `static_analysis_ran` | `static_analysis.json` exists | blob present |
| `static_ids_found` | `static_analysis.json` | `binary_strings.ids_found` count > 0 |
| `static_hints_block_built` | `static_analysis.json` | `static_hints_block_length > 0` |
| `write_unlock_succeeded` | `explore_probe_log.json` | entry with `phase=write_unlock` and `result` not matching sentinel pattern |

#### PROBE flags — sourceable from existing blobs
| Flag | Source | Assertion |
|---|---|---|
| `probe_loop_ran` | `explore_probe_log.json` exists | blob present |
| `functions_attempted` | `explore_probe_log.json` | distinct `function` values |
| `functions_llm_all_failed` | `explore_probe_log.json` | functions where every entry has `phase=llm_error` |
| `llm_429_error_count` | `explore_probe_log.json` | entries where `result_excerpt` contains `status=429` |
| `enrich_invocable_called_count` | `explore_probe_log.json` | entries with `phase=enrich` |
| `d12_autoenrich_triggered_count` | `explore_probe_log.json` | entries with `phase=d12_autoenrich` |
| `findings_written_count` | `findings.json` | total entry count |
| `functions_with_success_finding` | `findings.json` | distinct functions with `status=success` |
| `functions_skipped_dependency_missing` | `explore_probe_log.json` | entries with `phase=policy_stop` and result containing `dependency_missing` |
| `schema_evolved_probe_vs_t0` | `mcp_schema_t0.json` vs `mcp_schema_probe_after.json` | SHA-256 differ |
| `param_names_semantic` | `invocables_map.json` | at least one function with zero `param_N` keys |

#### SYNTHESIS flags — sourceable from existing blobs
| Flag | Source | Assertion |
|---|---|---|
| `synthesis_ran` | `api_reference.md` exists | blob present and non-empty |
| `synthesis_covers_all_functions` | `api_reference.md` vs `findings.json` | all finding function names mentioned in markdown |
| `backfill_ran` | `mcp_schema_probe_after.json` vs `mcp_schema.json` after phase 7 | blobs differ (proxy — not exact) |
| `backfill_patches_count` | **NOT YET AVAILABLE** — requires new `backfill_result.json` blob | N/A until instrumented |

#### GAP RESOLUTION flags — conditional on gap_resolution_enabled
| Flag | Source | Assertion |
|---|---|---|
| `gap_resolution_enabled` | `explore_config.json` | `gap_resolution_enabled: true` |
| `gap_resolution_log_written` | `gap_resolution_log.json` exists | blob present |
| `gap_functions_improved` | `gap_resolution_log.json` | entries with `status_after=success` and `status_before=error` |
| `mini_session_invocables_registered` | mini-session transcript | no "not found in job" errors — D-1 regression guard |
| `mini_session_schema_evolved` | schema stage snapshots | post-mini-session snapshot ≠ pre-mini-session snapshot |

#### FINALIZE flags — sourceable from existing blobs
| Flag | Source | Assertion |
|---|---|---|
| `harmonization_ran` | `harmonization_report.json` exists | blob present |
| `final_phase_value` | `status.json` | `explore_phase` ∈ {done, awaiting_clarification, canceled, error} |
| `vocab_description_set` | `vocab.json` | `description` key non-empty |
| `behavioral_spec_written` | `behavioral_spec.py` exists | blob present |

---

### cohesion_report.json Output Format

```json
{
  "job_id": "abc123",
  "run_timestamp": "2026-03-21T00:00:00Z",
  "codex_review_applied": true,
  "hard_fail": true,
  "summary": {
    "now_pass": 10,
    "now_fail": 2,
    "now_warn": 2,
    "partial": 3,
    "instrument_needed": 1,
    "not_applicable": 2,
    "critical_failures": ["T-16", "T-08"],
    "pipeline_verdict": "DEGRADED | OK | FROZEN"
  },
  "transitions": {
    "T-02": { "tier": "NOW",        "severity": "high",   "status": "pass",           "detail": "4/4 hint codes found in sentinel_calibration.json" },
    "T-04": { "tier": "PARTIAL",    "severity": "medium", "status": "warn",           "detail": "static_hints_block_length=1240 (non-zero) — proxy pass. Message content check not yet instrumented." },
    "T-07": { "tier": "INSTRUMENT", "severity": "medium", "status": "partial",        "detail": "backfill_result.json not yet written by pipeline." },
    "T-11": { "tier": "NOW",        "severity": "high",   "status": "fail",           "detail": "3 functions in findings.json not mentioned in api_reference.md: CS_UnlockAccount, CS_ProcessRefund, entry" },
    "T-13": { "tier": "NOW",        "severity": "high",   "status": "not_applicable", "detail": "answer-gaps endpoint not called in this run — mini-session transitions skipped." }
  },
  "stage_flags": {
    "setup":     { "schema_generated": true, "function_count": 13, "schema_t0_saved": true },
    "pre_probe": { "sentinel_calibration_ran": true, "hint_error_codes_merged": true, "write_unlock_succeeded": false },
    "probe":     { "functions_attempted": 13, "llm_429_error_count": 39, "enrich_invocable_called_count": 0, "d12_autoenrich_triggered_count": 13 },
    "synthesis": { "synthesis_ran": true, "synthesis_covers_all_functions": false },
    "gap":       { "gap_resolution_enabled": true, "gap_functions_improved": 4 },
    "finalize":  { "final_phase_value": "done", "vocab_description_set": true }
  }
}
```

**`pipeline_verdict` rules:**
- `FROZEN` — T-16 fails (schema never moved at all)
- `DEGRADED` — any `NOW`-tier transition fails; or `llm_429_error_count > 0`
- `OK` — all `NOW`-tier transitions pass; `PARTIAL` and `INSTRUMENT` tiers may still be unresolved

**`hard_fail` rule:**
- `hard_fail = true` when any transition has `severity = high` AND `status = fail`
- Automation gates (CI, release scripts, compare.ps1) must check `hard_fail: true` before promoting a run's artifacts
- A `DEGRADED` verdict without `hard_fail` indicates medium/low-severity issues only — acceptable for development; blocked for production promotion
- `warn` status (PARTIAL tier with proxy-passing evidence) does **not** set `hard_fail`; it signals reduced confidence, not a confirmed broken link

**Valid `status` values per transition:**
- `pass` — assertion verified from artifacts
- `fail` — assertion verified as false from artifacts
- `warn` — PARTIAL tier: proxy evidence suggests pass but full verification not possible
- `partial` — INSTRUMENT tier: instrumentation not yet written; cannot evaluate
- `not_applicable` — transition does not apply to this run (e.g. T-12/T-13 when answer-gaps not called)

---

### Implementation Steps (ordered by value/effort)

| Step | File(s) | What to add | Value |
|---|---|---|---|
| **1** | `scripts/evaluate_cohesion.py` *(new)* | Post-hoc evaluator: reads all session artifacts, evaluates all `NOW`-tier transitions, writes `cohesion_report.json` | Immediately unlocks 9 NOW-tier checks with zero API changes |
| **2** | `scripts/save-session.ps1` | Call `evaluate_cohesion.py` at end of save-session; include `cohesion_report.json` in ZIP | Makes every session save automatically produce pass/fail verdict |
| **3** | `api/explore.py` `_run_phase_7_backfill` | Write `backfill_result.json` blob with patch count, function list, and before/after schema sizes | Unlocks T-07 (the only `INSTRUMENT`-tier item) |
| **4** | `api/explore_helpers.py` `_record_cohesion_flags` | New helper: appends flags dict to `cohesion/{stage}.json` blob; called at exit of each phase | Enables inline real-time flags for pre-probe, probe, synthesis, finalize |
| **5** | `api/explore.py` (all phase functions) | Call `_record_cohesion_flags` at exit with relevant flags from that phase's context slice | Wires up the inline layer |
| **6** | `api/explore_gap.py` | Add T-13 enrollment: write schema stage snapshot and note invocable registration status at mini-session entry/exit | Hardens D-1 regression guard |
| **7** | `sessions/compare.ps1` | Pull `cohesion_report.json` from each session ZIP; add `verdict`, `now_fail`, `critical_failures` columns to `DASHBOARD.md` | Cross-session cohesion regression tracking |

**Start with Step 1** — the evaluator script has zero risk (read-only) and immediately makes the 9 NOW-tier transitions machine-checkable against all existing session ZIPs, including historical ones.

---

### Contract Authority And Binding

To avoid schema drift across autonomous coding/review agents, this document is
normative for **transition semantics** (T-01..T-16), corrected interpretations,
severity intent, and evaluator logic.

The canonical JSON contract and folder layout are defined only in:
- `docs/AI-FIRST-SNAPSHOT-CONTRACT.md`

Implementation binding:
1. `scripts/evaluate_cohesion.py` must read transition logic from this document and emit artifacts that conform to `docs/AI-FIRST-SNAPSHOT-CONTRACT.md`.
2. `scripts/save-session.ps1` and `sessions/compare.ps1` must treat `docs/AI-FIRST-SNAPSHOT-CONTRACT.md` as the source of truth for artifact paths and schema fields.
3. Any schema/layout changes must be made in `docs/AI-FIRST-SNAPSHOT-CONTRACT.md` first; this file should then reference the new version and only update transition rationale if needed.

---

## Architecture Rework Status (Runtime-First Phase)

This section records what was implemented in the runtime/API contract phase,
what was intentionally deferred, and what blocks save-session integration.

### What Was Done

1. Runtime contract artifact emission was added in API pipeline code.
Artifacts now emitted by runtime:
- `session-meta.json`
- `stage-index.json`
- `transition-index.json`
- `cohesion-report.json`

2. Transition evaluator was implemented for `T-01..T-16` with statuses:
- `pass`
- `fail`
- `warn`
- `partial`
- `not_applicable`

3. Severity mapping was implemented and gate semantics are deterministic.
- `hard_fail = true` iff any `high` severity transition has `status=fail`

4. Corrected edge-case semantics were implemented.
- `T-04`: user prompt path (not system prompt-only check)
- `T-11`: findings + vocab semantics into synthesis context
- `T-12/T-13`: `not_applicable` when answer-gaps flow not triggered

5. Minimal new instrumentation was added for measurability.
- `backfill_result.json` emission in backfill phase for `T-07`

6. Session snapshot packaging now includes contract artifacts when present.

7. Lightweight validation tests were added for:
- contract artifact creation
- transition index shape
- hard-fail semantics
- `T-12/T-13` conditional `not_applicable` behavior

### What Was Intentionally Not Done (In This Phase)

1. No changes to `scripts/save-session.ps1`.
2. No changes to compare scripts.
3. No broad unrelated refactors.
4. No full migration to canonical `evidence/` contract directory layout in this phase.
5. No CI/release gate wiring based on `cohesion-report.json` in this phase.

---

## Pre-Save-Session Blocker Plan (4 Blockers)

These blockers must be resolved before integrating save-session into strict
contract-first operation.

### Blocker 1: Real-Run Contract Validation

Problem:
- Need proof from one real end-to-end run that all four contract artifacts are
  consistently emitted and parseable.

Plan:
1. Execute one clean discovery run and one answer-gaps-triggered run.
2. Verify presence and JSON validity of:
- `session-meta.json`
- `stage-index.json`
- `transition-index.json`
- `cohesion-report.json`
3. Verify `transition-index.json` contains `T-01..T-16` with `status` and `severity`.
4. Verify `cohesion-report.json.gates.hard_fail` is deterministic across repeat run with unchanged inputs.

Acceptance:
- Both runs produce all four artifacts with valid JSON and deterministic gate behavior.

### Blocker 2: Evidence Completeness for Prompt-Path Transitions (T-04/T-14/T-15)

Problem:
- Some transitions still rely on proxy evidence when explicit prompt snapshots
  are missing or incomplete.

Plan:
1. Persist probe user message sample artifact for T-04.
2. Persist explicit chat system-context artifact for turn-0 prompt inspection.
3. Ensure transition evidence arrays point only to concrete existing files.
4. Downgrade ambiguous checks to `warn`/`partial` only when hard evidence is absent.

Acceptance:
- T-04, T-14, and T-15 evaluate from explicit evidence paths (not inferred-only).

### Blocker 3: Contract Layout Parity (Snapshot Path Normalization)

Problem:
- Runtime emits contract files, but snapshot stage evidence paths are not yet
  fully normalized to canonical `evidence/stage-*` contract layout.

Plan:
1. Introduce path mapping layer from legacy stage paths to canonical `evidence/` layout.
2. Keep backward-compatible aliases during transition window.
3. Update stage-index artifact references to canonical paths.
4. Add regression test that rejects missing canonical references in `stage-index.json`.

Acceptance:
- Snapshot contains canonical `evidence/` paths and stage-index references resolve.

### Blocker 4: Downstream Consumer Readiness (Before save-session switch)

Problem:
- save-session and compare were intentionally untouched; downstream strict mode
  cannot be enabled until compatibility and version handling are defined.

Plan:
1. Define compatibility behavior for runs missing any required contract file.
2. Add schema-version support map and fail-closed behavior.
3. Define strict-gate mode based on `cohesion-report.json.gates.hard_fail`.
4. Document a two-run rollout:
- run A: compatibility on, strict gate off
- run B: compatibility on, strict gate on

Acceptance:
- save-session integration can be enabled without mutating machine-truth artifacts,
  and with deterministic contract-first gating semantics.
