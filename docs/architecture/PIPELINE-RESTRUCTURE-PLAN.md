# Pipeline Restructure Plan — `api/pipeline/` Package

> Created 2026-03-23. Execute AFTER MVP (12/12 on contoso_cs.dll).
> This plan converts the flat `api/explore*.py` file structure into a
> stage-based package that mirrors the pipeline's data flow.

---

## Why

The current `api/` directory has 8 `explore_*.py` files totaling ~7,500 lines.
Functions from different stages sit in the same file (e.g., `explore.py` has
phases 0-2, 4-10, and all 4 micro coordinators). The flat structure makes it
hard to:

1. See what data flows between stages
2. Know which variables belong to which stage
3. Understand what context each stage receives and produces
4. Navigate the pipeline as a newcomer (human or AI)

The restructure makes the file system mirror the pipeline flow — reading the
directory listing left-to-right IS the pipeline.

---

## Shared Infrastructure (stays in `api/`)

These files are used by every stage and do not belong to any single stage:

| File | Purpose |
|------|---------|
| `storage.py` | Blob/job persistence, findings, invocable registry |
| `executor.py` | DLL execution bridge (`_execute_tool`, `_execute_tool_traced`) |
| `config.py` | Environment variables and settings |
| `telemetry.py` | OpenAI client setup |
| `sentinel_codes.py` | Static sentinel classification |
| `main.py` | FastAPI app + HTTP endpoints |
| `routes_session.py` | Session snapshot ZIP endpoint |
| `chat.py` | Streaming chat endpoint |
| `cohesion.py` | Contract artifacts (T-01 through T-21) |
| `binary_analysis.py` | Ghidra binary discovery (renamed from `discovery.py`) |
| `generate.py` | Generate endpoint |
| `search.py` | Search endpoint |
| `worker.py` | Background worker |
| `transition_readiness.py` | AB readiness evaluation |

---

## Proposed Directory Structure

```
api/
  pipeline/
    __init__.py              # Re-exports public API (_explore_worker, ExploreContext)
    orchestrator.py          # _explore_worker loop, _build_explore_context (~200 lines)
    types.py                 # ExploreContext, ExploreRuntime (from explore_types.py)
    helpers.py               # Write policy, shared utils (from explore_helpers.py)
    vocab.py                 # Cross-stage vocabulary mgmt (from explore_vocab.py)
    prompts.py               # LLM prompt builders (from explore_prompts.py)

    s00_setup/
      __init__.py            # Context-in/out docs for stage 0
      calibration.py         # _run_phase_05_calibrate, _calibrate_sentinels
      vocab_seed.py          # _run_phase_0_vocab_seed, circular feedback loader
      static_analysis.py     # _run_phase_0_static (wrapper around api/static_analysis.py)

    s01_unlock/
      __init__.py            # Context-in/out docs for stage 1
      write_unlock.py        # _run_phase_1_write_unlock, _probe_write_unlock
                             # Also: winning init sequence replay for warm/hot starts

    s02_probe/
      __init__.py            # Context-in/out docs for stage 2
      curriculum.py          # _run_phase_2_curriculum_order
      probe_loop.py          # _run_phase_3_probe_loop, _explore_one (from explore_probe.py)

    s03_reconcile/
      __init__.py            # Context-in/out docs for stage 3
      reconcile.py           # _run_phase_4_reconcile
      mc3_write_analysis.py  # _mc3_post_reconcile (MC-3 coordinator)
      sentinel_catalog.py    # _run_phase_5_sentinel_catalog

    s04_synthesis/
      __init__.py            # Context-in/out docs for stage 4
      synthesize.py          # _run_phase_6_synthesize
      mc4_init_clues.py      # _mc4_post_synthesis (MC-4 coordinator)

    s05_enrichment/
      __init__.py            # Context-in/out docs for stage 5
      backfill.py            # _run_phase_7_backfill
      verify.py              # _run_phase_7b_verify_enrichment
      mc5_chain_reads.py     # _mc5_post_verification (MC-5 coordinator)

    s06_gaps/
      __init__.py            # Context-in/out docs for stage 6
      gap_resolution.py      # _run_phase_8_gap_resolution (from explore_gap.py)
      mc6_final_unlock.py    # _mc6_post_gap_resolution (MC-6 coordinator)

    s07_finalize/
      __init__.py            # Context-in/out docs for stage 7
      behavioral_spec.py     # _run_phase_9_behavioral_spec
      harmonize.py           # _run_phase_10_harmonize
```

---

## Context-In / Context-Out Documentation

Each stage `__init__.py` documents what the stage receives and produces.
This is the most valuable part of the restructure — it makes the data flow
explicit and machine-readable.

### Example: `s03_reconcile/__init__.py`

```python
"""S-03: Post-Probe Reconciliation + MC-3 Write Analysis

CONTEXT IN:
    ctx.probe_log       — full probe log from S-02 probe loop
    ctx.findings        — accumulated findings (per-function outcomes)
    ctx.sentinels       — sentinel table (from S-00 calibration)
    ctx.unlock_result   — write-unlock probe result (from S-01)
    ctx.vocab           — vocabulary accumulated during probing
    ctx.invocables      — function registry with enriched schemas

CONTEXT OUT:
    ctx.findings        — reconciled (probe log vs findings alignment verified)
    ctx.sentinel_catalog — promoted sentinel evidence from probe results
    ctx.write_unlock_block — updated if MC-3 cracks write-unlock
    Blob: mc-decisions/mc3-post-reconcile.json (MC reasoning artifact)
    Blob: sentinel_catalog.json (promoted codes)

SHARED INFRA USED:
    storage     — _load_findings, _upload_to_blob, _persist_job_status
    executor    — _execute_tool (for MC-3 targeted unlock attempts)

MODEL CONTEXT:
    No LLM calls in reconciliation itself.
    MC-3 uses heuristic analysis only (pattern classification of sentinel codes).
    The write_unlock_block string IS model context — it's injected into subsequent
    LLM prompts for write function probing.

TRANSITIONS:
    T-19 (mc_coordinator_decisions) — MC-3 decision artifact presence
"""
from .reconcile import _run_phase_4_reconcile
from .mc3_write_analysis import _mc3_post_reconcile
from .sentinel_catalog import _run_phase_5_sentinel_catalog
```

### Context docs for all stages:

| Stage | Context In | Context Out | Model Context |
|-------|-----------|-------------|---------------|
| S-00 | DLL binary, user hints, prior_job_id | ctx.sentinels, ctx.vocab, ctx.dll_strings, ctx.already_explored | Sentinel names as foundation for all subsequent prompts |
| S-01 | ctx.invocables, ctx.sentinels, ctx.vocab, winning_init_sequence (if warm start) | ctx.unlock_result, ctx.write_unlock_block | write_unlock_block injected into every write-function probe prompt |
| S-02 | Full ctx (sentinels, vocab, unlock_result, invocables) | ctx.findings, probe_log, enriched schemas | Per-function system message + user message with static analysis hints |
| S-03 | ctx.probe_log, ctx.findings, ctx.sentinels | Reconciled findings, sentinel_catalog, MC-3 decision | MC-3 updates write_unlock_block based on failure classification |
| S-04 | ctx.findings, ctx.vocab, ctx.sentinels | api_reference.md, MC-4 decision | Synthesis prompt includes ALL findings and vocab; MC-4 parses output for init clues |
| S-05 | api_reference.md, ctx.findings, ctx.invocables | Backfilled schemas, verification results, MC-5 decision | MC-5 chains verified read outputs as concrete write function args |
| S-06 | All accumulated context, verification results | Gap resolution log, MC-6 decision, winning_init_sequence.json | MC-6 does final comprehensive unlock with all accumulated knowledge |
| S-07 | Final findings, vocab, schemas | behavioral_spec.py, harmonization_report, contract artifacts | Behavioral spec generation uses full findings + api-reference |

---

## Migration Steps

### Step 1: Create package structure
```
mkdir api/pipeline
mkdir api/pipeline/s00_setup api/pipeline/s01_unlock api/pipeline/s02_probe
mkdir api/pipeline/s03_reconcile api/pipeline/s04_synthesis api/pipeline/s05_enrichment
mkdir api/pipeline/s06_gaps api/pipeline/s07_finalize
```
Create all `__init__.py` files with context documentation.

### Step 2: Move shared explore utilities (no splitting required)
```
api/explore_types.py   -> api/pipeline/types.py
api/explore_helpers.py -> api/pipeline/helpers.py
api/explore_vocab.py   -> api/pipeline/vocab.py
api/explore_prompts.py -> api/pipeline/prompts.py
```

### Step 3: Split explore_phases.py (435 lines)
- `_calibrate_sentinels`, `_parse_hint_error_codes`, `_name_sentinel_candidates`
  -> `api/pipeline/s00_setup/calibration.py`
- `_probe_write_unlock` -> `api/pipeline/s01_unlock/write_unlock.py`
- Constants (`_MAX_*`, `_CAP_PROFILE`, `_SENTINEL_DEFAULTS`)
  -> `api/pipeline/helpers.py` (merge with existing helpers)
- `_infer_param_desc` -> `api/pipeline/helpers.py`

### Step 4: Move explore_probe.py (1,160 lines)
- `_explore_one`, `_run_phase_3_probe_loop`
  -> `api/pipeline/s02_probe/probe_loop.py`

### Step 5: Split explore.py (2,255 lines) — the big one

| Source function(s) | Destination |
|-------------------|-------------|
| `_explore_worker`, `_build_explore_context`, `_load_prior_session_artifacts` | `orchestrator.py` |
| `_run_phase_05_calibrate` | `s00_setup/calibration.py` |
| `_run_phase_0_vocab_seed` | `s00_setup/vocab_seed.py` |
| `_run_phase_0_static` | `s00_setup/static_analysis.py` |
| `_run_phase_1_write_unlock` | `s01_unlock/write_unlock.py` |
| `_run_phase_2_curriculum_order` | `s02_probe/curriculum.py` |
| `_run_phase_4_reconcile` | `s03_reconcile/reconcile.py` |
| `_mc3_post_reconcile` + helpers | `s03_reconcile/mc3_write_analysis.py` |
| `_run_phase_5_sentinel_catalog` | `s03_reconcile/sentinel_catalog.py` |
| `_run_phase_6_synthesize` | `s04_synthesis/synthesize.py` |
| `_mc4_post_synthesis` | `s04_synthesis/mc4_init_clues.py` |
| `_run_phase_7_backfill` | `s05_enrichment/backfill.py` |
| `_run_phase_7b_verify_enrichment` | `s05_enrichment/verify.py` |
| `_mc5_post_verification` | `s05_enrichment/mc5_chain_reads.py` |
| `_run_phase_8_gap_resolution` | `s06_gaps/gap_resolution.py` |
| `_mc6_post_gap_resolution` + helpers | `s06_gaps/mc6_final_unlock.py` |
| `_run_phase_9_behavioral_spec` | `s07_finalize/behavioral_spec.py` |
| `_run_phase_10_harmonize` | `s07_finalize/harmonize.py` |
| `_mark_unlock_resolved`, `_reprobe_write_functions_after_unlock`, `_targeted_unlock_attempt`, `_build_write_args_from_vocab`, `_persist_mc_decision` | `api/pipeline/helpers.py` (MC shared utilities) |

### Step 6: Move explore_gap.py (599 lines)
- `_attempt_gap_resolution` -> `s06_gaps/gap_resolution.py`
- `_run_gap_answer_mini_sessions` -> `s06_gaps/gap_resolution.py`

### Step 7: Add compatibility shims (temporary, 1-2 commits)

Create thin wrapper files at old locations that re-export from new locations:

```python
# api/explore.py (compatibility shim — remove after all consumers updated)
"""DEPRECATED: Pipeline code has moved to api/pipeline/. Update your imports."""
from api.pipeline import _explore_worker  # noqa: F401
from api.pipeline.vocab import _vocab_block  # noqa: F401
```

### Step 8: Update all imports (~60 statements)

Key import changes:
```python
# Before:
from api.explore_types import ExploreContext, ExploreRuntime
from api.explore_helpers import _write_policy_precheck
from api.explore_probe import _explore_one

# After:
from api.pipeline.types import ExploreContext, ExploreRuntime
from api.pipeline.helpers import _write_policy_precheck
from api.pipeline.s02_probe.probe_loop import _explore_one
```

### Step 9: Rename discovery.py
```
api/discovery.py -> api/binary_analysis.py
```
Update the single import in `api/worker.py`.

### Step 10: Update tests, scripts, .cursorrules, CI
- `tests/test_explore_integration.py` — update all imports
- `scripts/extract_probe.py` — update imports
- `scripts/extract_routes_session.py` — update imports
- `.cursorrules` — update file layout section
- `Dockerfile` — no change (copies entire `api/` directory)

---

## Risk Mitigation

1. **Compatibility shims**: Old import paths continue to work during migration
2. **Git branch**: Do this on a feature branch, not main
3. **Run tests after each step**: `pytest tests/ -v --tb=short --ignore=tests/test_mcp_stdio.py`
4. **Deploy and test**: Run a full pipeline session after restructure to verify nothing broke

---

## Estimated Effort

- Step 1 (create structure): 15 minutes
- Steps 2-6 (move/split code): 1-2 hours
- Steps 7-8 (imports + shims): 30 minutes
- Steps 9-10 (cleanup): 30 minutes
- **Total: 2-3 hours focused work**

---

## What This Enables

After restructure:
- Reading `api/pipeline/s03_reconcile/__init__.py` tells you EXACTLY what stage 3
  receives, produces, and how it uses shared infrastructure
- MC coordinator logic lives next to the stage it belongs to
- Session snapshot evidence paths (`evidence/stage-03-post-probe/`) mirror code
  paths (`api/pipeline/s03_reconcile/`)
- Any AI assistant can navigate the codebase by stage, not by guessing which
  2,255-line file contains the function it needs
- Variable scope is naturally limited to the stage module
