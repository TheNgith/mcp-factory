# Temporary Findings — Q14/Q15/Q16/Q17 Coherence Review

Generated: 2026-03-23  
Reviewer: Opus (Cursor)  
Scope: All code implemented by Codex across commits 954d472, bd3ad25, 6a766ec, a2fc8c5

## Overall Assessment

The four implementations (Q14 union merger, Q15 ablation tagging + orchestrator,
Q16 adaptive sentinel calibration, Q17 coordinator agent) are architecturally
coherent — they form a working chain. However, there are specific coherence
issues that range from "latent bug" to "will silently produce wrong data."

---

## Working Correctly

### T-17 (sentinel_calibration_outcome) chain
- `_calibrate_sentinels` in `explore_phases.py` accepts `hints` param and applies Q-1 pre-seeding
- `_run_phase_05_calibrate` in `explore.py` passes hints correctly
- Stage-boundary re-calibration after phase 3 scans probe log for new high-bit codes
- `sentinel_new_codes_this_run` accumulates through `ctx` and is written to job status at finalize
- `cohesion.py:650` reads `sentinel_new_codes_this_run` from status correctly
- T-17 logic properly distinguishes default vs DLL-specific sentinel codes

### T-18 (write_unlock_probe_outcome) chain
- `_run_phase_1_write_unlock` emits `write_unlock_outcome` + `write_unlock_sentinel` to job status
- `write_unlock_probe.json` artifact is uploaded to blob
- `cohesion.py:666-683` reads the fields and maps to pass/warn/partial/not_applicable
- Both T-17 and T-18 are included in `transition-index.json` via `emit_contract_artifacts`

### Ablation tag pass-through (Q15)
- `/api/analyze` accepts all 7 ablation fields as Form params and persists them to job status
- `cohesion.py:759-766` copies them into `session-meta.json`
- `collect_session.py:139-155` copies them from `session-meta.json` to `dashboard-row.json`
- Orchestrator additionally patches dashboard-row.json client-side as a safety net

### Union merger (Q14)
- Handles both direct and `leg-a/` session layouts
- Correct highest-confidence selection for successes, richest-failure-evidence for errors
- Supplemental evidence merging works
- `merge-summary.json` computes `union_gain_vs_best_single` correctly

### Coordinator (Q17)
- State serialization/deserialization works
- Step A (baseline confirmation) and Step B (variable sweep) logic is correct
- Cycle reports are well-structured
- Stopping conditions cover the specified cases

---

## Issues Found

### Issue 1: `functions_success` missing from session-meta and dashboard-row — RESOLVED

**Severity: Medium-High** | **Status: Fixed in this cleanup**

`cohesion.py` never wrote `functions_success` to `session-meta.json`.
`collect_session.py` never wrote it to `dashboard-row.json`.

All three downstream consumers (merger, orchestrator, coordinator) expect this field:
- `union_merger.py` reads `dashboard.get("functions_success")`
- `run_set_orchestrator.py` reads `dashboard.get("functions_success")`
- `run_coordinator.py` reads `row.get("functions_success")`

**Fix**: Added `functions_total`, `functions_success`, `functions_error` to both
`cohesion.py` (`session_meta` dict) and `collect_session.py` (`_build_dashboard_row`).

### Issue 2: `/api/jobs/{job_id}/explore` does not propagate ablation tags — KNOWN

**Severity: Low (works in practice)** | **Status: Open, low priority**

The orchestrator sends `prompt_profile_id`, `run_set_id`, etc. in the explore
request body. The explore endpoint only reads `explore_settings` and `invocables`.
Tags survive because they were already set during `/api/analyze` and preserved
via `**(_get_job_status(job_id) or {})`.

### Issue 3: Coordinator plateau detection threshold — KNOWN

**Severity: Low** | **Status: Open, low priority**

Q17 spec says "three consecutive cycles with no improvement." Code checks
`state["cycles_completed"][-3:]` for 2+ non-promoted before appending current
cycle. May trigger after 2 + current = 3, which could be correct depending on
interpretation.

### Issue 4: CI tests broken after Q16 + refactor — RESOLVED

**Severity: Blocking (CI fails)** | **Status: Fixed in this cleanup**

Two separate failures:
1. `test_contract_artifacts.py:124` asserted `len(transitions) == 16` but T-17
   and T-18 were added by Q16. Updated to expect 18 and added T-17/T-18 assertions.
2. `TestSessionSnapshotZipStructure` (8 tests) parsed `main.py` for ZIP paths,
   but the a2fc8c5 refactor moved `session_snapshot` to `routes_session.py`.
   Updated to read the correct file.

---

## Naming Coherency Audit (Full)

### 1. Invocable / function registry

| Name | Location | Role |
|------|----------|------|
| `invocables` | everywhere | list of invocable dicts (canonical) |
| `inv_map` | explore.py, ExploreContext | dict {name: invocable} (canonical) |
| `_JOB_INVOCABLE_MAPS` | storage.py | module-level registry (canonical) |
| ~~`_job_inv_maps`~~ | ~~main.py (import alias)~~ | **FIXED: removed alias** |
| ~~`_jimap`~~ | ~~chat.py (import alias)~~ | **FIXED: removed alias** |
| `selected` | generate.py API body | same as invocables (divergent key name) |
| `tools` | some Ghidra scripts | same concept, different name |

### 2. Vocabulary / semantics accumulator

| Name | Location | Role |
|------|----------|------|
| `vocab` / `ctx.vocab` | everywhere | canonical |
| `_vocab_snap` | explore_probe.py | snapshot for concurrency safety (OK) |
| `_vocab_for_gaps` | routes_session.py | subset loaded for gap answers (OK) |

### 3. Probe findings / results

| Name | Location | Role |
|------|----------|------|
| `findings` | storage.py, explore.py | list of finding dicts (canonical) |
| `function` | finding dict key | function name in finding (canonical) |
| `function_name` | executor.py tool args | same concept, **divergent** |
| `_syn_findings`, `_gap_findings`, etc. | explore.py | phase-specific locals (OK, scoped) |

### 4. Sentinel / error-code tables

| Name | Location | Role |
|------|----------|------|
| `ctx.sentinels` | ExploreContext | int → meaning dict (canonical runtime) |
| `SENTINEL_DEFAULTS` | sentinel_codes.py | static defaults (canonical) |
| `sentinel_calibration.json` | blob | hex-string keys (canonical persist format) |
| `vocab["error_codes"]` | vocab table | hex-string keys (promotional copy) |
| `sentinel_catalog` | ExploreContext | cross-probe evidence (distinct concept) |

**Key divergence**: int keys in runtime vs hex-string keys in JSON/vocab.
Intentional (JSON can't have int keys) but confusing at boundaries.

### 5. Session metadata / dashboard row

| Name | Location | Role |
|------|----------|------|
| `session-meta.json` | cohesion.py | contract artifact (canonical) |
| `dashboard-row.json` | collect_session.py | compact manifest for humans (canonical) |
| `session-save-meta.json` | collect_session.py | save-time metadata |
| `functions_success` | **ADDED** to both | was missing from both, all consumers needed it |

### 6. Job status locals

**Before cleanup**: `current`, `_cur`, `_init`, `_job_meta`, `_gap_current`,
`_wucurrent` — all the same job status dict.

**After cleanup**: `current_status` in explore.py phase 1 (was `_wucurrent`).
Other locals are scoped and less confusing.

## Changes Made in This Cleanup

### CI Test Fixes
- `test_contract_artifacts.py`: transition count 16 → 18, added T-17/T-18 assertions
- `test_explore_integration.py`: `TestSessionSnapshotZipStructure` now reads
  `routes_session.py` instead of `main.py` (endpoint was moved in a2fc8c5 refactor)

### Import Alias Removal
- `main.py`: removed `_JOB_INVOCABLE_MAPS as _job_inv_maps`
- `chat.py`: removed two `_JOB_INVOCABLE_MAPS as _jimap` aliases, plus
  `_download_blob as _dl_fb`, `ARTIFACT_CONTAINER as _AC_fb`, `json as _json_fb`
- `explore_phases.py`: removed `_download_blob as _dl_blob`, `ARTIFACT_CONTAINER as _AC`

### Cryptic Abbreviation Expansion
- `explore.py` phase 1: `_wuo` → `unlock_outcome`, `_wus` → `blocking_sentinel`,
  `_wucurrent` → `current_status`, `_wc` → `candidate_code`
- `explore.py` stage-boundary recal: `_ret_m2` → `return_match`, `_pv` → `return_val`,
  `_pe` → `entry`, `_new_cands` → `new_sentinel_candidates`,
  `_boundary_resolved` → `boundary_resolved`
- `cohesion.py` T-17/T-18: `_wuo` → `write_unlock_outcome`,
  `_wus_label` → `sentinel_label`, `_sentinel_default_hexes` → `sentinel_default_hexes`,
  `_non_default` → `non_default_codes`, `_sentinel_new` → `sentinel_new_count`
- `explore_phases.py`: `_hc`/`_hm` → `hint_code`/`hint_meaning`

### Missing Data Field
- `cohesion.py` `session_meta`: added `functions_total`, `functions_success`, `functions_error`
- `collect_session.py` `_build_dashboard_row`: added same three fields, read from session-meta
- These were expected by union_merger.py, run_set_orchestrator.py, and run_coordinator.py
  but were never written. All three had fallback paths (counting findings.json) that masked
  the gap.

### Remaining Divergences (not addressed — lower priority)
- `function_name` vs `function` at executor/storage boundary
- `selected` vs `invocables` in generate API
- int vs hex-string sentinel key types across runtime/JSON boundary
- `_ret_m` pattern in probe loops (consistent usage, low confusion risk)
- Hyphen vs underscore in evidence zip paths vs blob names (intentional design)

---

## Data Collection Scripts Structure Review

### Current State

The `scripts/` folder has 40+ files in a flat structure (except `vm-ops/`).
Scripts serve four distinct purposes:

**Core data collection chain** (these form a pipeline):
1. `save-session.ps1` → downloads session ZIP from API, calls `collect_session.py`
2. `collect_session.py` → validates contract artifacts, builds `dashboard-row.json`
3. `morning_report.py` → generates summary report from session folders
4. `_cohesion_history.py` → tracks cohesion trends
5. `_cmp_scores.py` → compares scores across runs
6. `_check_invocables.py` → validates invocable maps
7. `_audit_context.py` → audits context/vocab tables

**Experiment orchestration** (Q14-Q17):
1. `run_set_orchestrator.py` → Q15 ablation dispatch
2. `run_coordinator.py` → Q17 autonomous coordinator
3. `union_merger.py` → Q14 session merging
4. `run_ab_parallel.py` → A/B parallel runs
5. `run_batch_parallel.py` → batch parallel dispatch
6. `run_transition_isolation_matrix.py` → isolation matrix experiments
7. `run_model_comparison.py` → model comparison experiments

**Debug/utility**:
- `debug_dll.py`, `debug_chat.py`, `run_local.py`, `extract_probe.py`, etc.

**Infrastructure/deployment**:
- `install-github-runner.ps1`, `wire-bridge-to-aca.ps1`, `finalize_azure.ps1`, etc.

### Recommendation

Reorganization into subdirectories would help, but would break:
- CI/CD workflow references to `scripts/save-session.ps1`
- The coordinator's subprocess call to `scripts/run_set_orchestrator.py`
- The orchestrator's subprocess call to `scripts/save-session.ps1`

**Recommended approach**: Don't restructure until after Q15/Q16 are verified
end-to-end. Instead, add a `scripts/README.md` that documents the purpose
of each script, and consider reorganization as a follow-up when the
cross-script references can be updated together.
