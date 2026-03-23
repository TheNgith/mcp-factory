# Codex Prompt — Q15: Run-Set Orchestrator + Ablation Tagging

## What This Implements

Q15 ("Context Ablation") in `docs/architecture/QUESTIONS.md` — specifically:
- **Phase 2**: pass-through ablation tagging fields through the job API into `session-meta.json`
- **Phase 2**: a run-set orchestrator (`scripts/run_set_orchestrator.py`) that dispatches
  N control runs + M ablation runs in parallel, waits for all, invokes the UNION merger

Q15 Phase 1 (running identical control runs manually) is already possible with the
existing `scripts/run_batch_parallel.py`. This task builds the structured layer on top.

## Context: What Already Exists

### How jobs are dispatched today (`scripts/run_ab_parallel.py`)

The existing `run_ab_parallel.py` (and `run_batch_parallel.py`) calls:
```python
import api.chat as ab  # or equivalent
# dispatches a job via POST /api/jobs with a RunConfig payload
# waits for completion, downloads session snapshot, saves to sessions/_runs/
```

Key pattern to reuse: `run_batch_parallel.py` already dispatches N identical legs,
waits with polling, and writes per-leg session folders. The run-set orchestrator
extends this to support named profiles and ablation variables.

### What `session-meta.json` currently contains

Written by `api/worker.py` via `_persist_job_status`. Current fields include:
`status`, `progress`, `message`, `result`, `error`, `updated_at`, `component_name`,
`hints`, `use_cases`, `created_at`.

The job request payload goes through `api/worker.py` → `_analyze_worker()`.
The job request is submitted via the REST API.

### Profile system concept

A "prompt profile" is just a named set of overrides for one variable. For MVP,
profiles are defined in `scripts/prompt_profiles.json` (new file). The orchestrator
reads this file, picks profiles for the M ablation runs, and passes the profile name
as a job parameter.

## Task 1: Add Ablation Tagging Fields to the Job API

### 1a. New optional fields in the job request body

The job POST endpoint (`api/main.py` or equivalent) must accept these optional fields
and pass them through to `session-meta.json`. If absent, they default to null:

```json
{
  "prompt_profile_id": "baseline",
  "layer": 1,
  "ablation_variable": null,
  "ablation_value": null,
  "run_set_id": "runset-2026-03-22-abc123",
  "coordinator_cycle": null,
  "playbook_step": null
}
```

### 1b. Persist them in `api/worker.py`

In `_analyze_worker()`, add these fields to the final status payload that is written
to Blob storage. They are pure pass-through — the worker does not act on them, just
stores them so `save-session.ps1` / `collect_session.py` can later copy them into
`human/dashboard-row.json`.

Locate the final status write block in `api/worker.py` (around lines 72–103) and
add the new fields alongside the existing ones.

### 1c. Copy them into `human/dashboard-row.json` via `scripts/collect_session.py`

In `collect_session.py`, `_build_dashboard_row()` currently reads from `contract.parsed`
(which is sourced from `session-meta.json`). Add a read of the six new fields from
`session-meta.json` and include them in the dashboard row output. If absent, emit null.

Fields to add to `human/dashboard-row.json`:
```json
{
  "prompt_profile_id": "baseline",
  "layer": 1,
  "ablation_variable": null,
  "ablation_value": null,
  "run_set_id": "runset-2026-03-22-abc123",
  "coordinator_cycle": null,
  "playbook_step": null
}
```

## Task 2: Create `scripts/prompt_profiles.json`

Define the four ablation variable families. Each profile is a named variant that
changes exactly one thing from the baseline. This file is the authoritative list of
available ablation variants.

```json
{
  "baseline": {
    "description": "Control profile — no changes from default explore settings",
    "ablation_variable": null,
    "ablation_value": null,
    "overrides": {}
  },
  "framing-systematic": {
    "description": "Prompt framing: systematic exploration instruction",
    "ablation_variable": "prompt_framing",
    "ablation_value": "systematic",
    "overrides": {
      "instruction_fragment": "Explore this function systematically. Try all argument type combinations before concluding."
    }
  },
  "framing-explore": {
    "description": "Prompt framing: open-ended exploration instruction",
    "ablation_variable": "prompt_framing",
    "ablation_value": "explore",
    "overrides": {
      "instruction_fragment": "Treat this function as unknown territory. Discover what it does by probing broadly."
    }
  },
  "framing-find-combos": {
    "description": "Prompt framing: argument combination search instruction",
    "ablation_variable": "prompt_framing",
    "ablation_value": "find-combos",
    "overrides": {
      "instruction_fragment": "Your goal is to find valid argument combinations. Try every plausible input type."
    }
  },
  "vocab-ids-first": {
    "description": "Vocab ordering: static IDs listed before other context",
    "ablation_variable": "vocab_ordering",
    "ablation_value": "ids_first",
    "overrides": {
      "vocab_order": "ids_first"
    }
  },
  "vocab-ids-last": {
    "description": "Vocab ordering: static IDs listed after other context",
    "ablation_variable": "vocab_ordering",
    "ablation_value": "ids_last",
    "overrides": {
      "vocab_order": "ids_last"
    }
  },
  "vocab-ids-omit": {
    "description": "Vocab ordering: static IDs omitted entirely",
    "ablation_variable": "vocab_ordering",
    "ablation_value": "ids_omit",
    "overrides": {
      "vocab_order": "ids_omit"
    }
  },
  "density-full": {
    "description": "Context density: full prior findings injected (current default)",
    "ablation_variable": "context_density",
    "ablation_value": "full",
    "overrides": {
      "context_density": "full"
    }
  },
  "density-minimal": {
    "description": "Context density: only current-stage facts, no prior run findings",
    "ablation_variable": "context_density",
    "ablation_value": "minimal",
    "overrides": {
      "context_density": "minimal"
    }
  },
  "density-none": {
    "description": "Context density: no prior context at all (cold start per function)",
    "ablation_variable": "context_density",
    "ablation_value": "none",
    "overrides": {
      "context_density": "none"
    }
  },
  "budget-8": {
    "description": "Tool budget: max 8 tool calls per function",
    "ablation_variable": "tool_budget",
    "ablation_value": "8",
    "overrides": {
      "max_tool_calls": 8
    }
  },
  "budget-16": {
    "description": "Tool budget: max 16 tool calls (current default)",
    "ablation_variable": "tool_budget",
    "ablation_value": "16",
    "overrides": {
      "max_tool_calls": 16
    }
  },
  "budget-24": {
    "description": "Tool budget: max 24 tool calls",
    "ablation_variable": "tool_budget",
    "ablation_value": "24",
    "overrides": {
      "max_tool_calls": 24
    }
  }
}
```

## Task 3: Create `scripts/run_set_orchestrator.py`

A new script that:
1. Reads a **run-set definition** JSON (passed via `--run-set-def`) that specifies:
   - `run_set_id` (string UUID — caller provides or orchestrator generates)
   - `component` (e.g. `"contoso_cs"`)
   - `api_url`, `api_key`
   - `base_config` (the shared job config: `mode`, `model`, `max_rounds`, `hints_sha256`, etc.)
   - `n_control` (int, default 3) — number of baseline control runs
   - `m_ablation` (int, default 3) — number of ablation variant runs
   - `ablation_profiles` (list of 3 profile IDs from `prompt_profiles.json`,
     e.g. `["framing-systematic", "framing-explore", "framing-find-combos"]`)
   - `output_dir` (where to save session folders and merged output)
   - `coordinator_cycle` (int or null)
   - `playbook_step` (string or null)

2. Dispatches N control runs (all using `prompt_profile_id = "baseline"`, `layer = 1`)
   and M ablation runs (each using one profile from `ablation_profiles`, `layer = 2`),
   all in parallel.

3. Each dispatched run gets tagged with:
   - `run_set_id`
   - `layer` (1 or 2)
   - `prompt_profile_id` (from profiles list)
   - `ablation_variable` + `ablation_value` (from profile definition)
   - `coordinator_cycle`, `playbook_step` (from run-set def, may be null)

4. Waits for all N+M runs to complete (polls job status, timeout configurable).

5. For each completed run, triggers `save-session` (calls `scripts/collect_session.py`
   on the already-downloaded session folder, or downloads snapshot first).

6. After all runs complete and sessions are saved, invokes:
   ```bash
   python scripts/union_merger.py \
     --sessions <all N+M session dirs> \
     --output <output_dir>/merged-schema \
     --run-set-id <run_set_id>
   ```

7. Writes `<output_dir>/run-set-summary.json`:
   ```json
   {
     "run_set_id": "runset-2026-03-22-abc123",
     "completed_at": "2026-03-22T...",
     "n_control": 3,
     "m_ablation": 3,
     "sessions": [
       {"folder": "...", "layer": 1, "profile": "baseline", "functions_success": 4, "hard_fail": false},
       ...
     ],
     "control_median_success": 4,
     "best_ablation_success": 4,
     "union_success": 4,
     "union_gain_vs_best_single": 0,
     "merge_id": "<from merge-summary.json>"
   }
   ```

## CLI Interface

```bash
python scripts/run_set_orchestrator.py \
  --run-set-def codex-prompts/example-run-set-def.json \
  --api-key YOUR_KEY
```

## Files to Create

- `scripts/run_set_orchestrator.py` — new file, ~250 lines
- `scripts/prompt_profiles.json` — new file, profile definitions

## Files to Modify

- `api/worker.py` — add 7 new optional pass-through fields to final status write
- `api/main.py` (or wherever job POST endpoint is defined) — accept the 7 new fields
- `scripts/collect_session.py` — add 7 fields to `_build_dashboard_row()`

## Files to NOT Modify

- `api/explore_prompts.py` — prompt profile *application* is a separate task
- `api/explore_phases.py`
- Any existing session folder contents

## Acceptance Criteria

1. Dispatch a run-set of 2 control + 1 ablation run. Verify:
   - All 3 runs complete and session folders are created.
   - Each `session-meta.json` contains `prompt_profile_id`, `layer`, `run_set_id`.
   - `human/dashboard-row.json` in each session contains the same fields.
   - Control runs have `layer: 1`, `ablation_variable: null`.
   - Ablation run has `layer: 2`, `ablation_variable: "prompt_framing"`.
2. `run-set-summary.json` is written and readable as valid JSON.
3. `merged-schema/accumulated-findings.json` is written (by union_merger invocation).
4. No syntax errors in any modified Python file (`python -m py_compile`).
