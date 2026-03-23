# Codex Prompt — Q14: UNION Merger (`scripts/union_merger.py`)

## What This Implements

Q14 ("Tournament Strategy") in `docs/architecture/QUESTIONS.md`.

The UNION merger takes the output of N+M parallel pipeline runs and produces one
authoritative composite schema. Unlike a simple "pick the best run," the UNION approach
means **every valid finding from every run contributes**, regardless of which run it came
from. A run that only resolved one extra function is still a valuable contributor.

## Context: What Already Exists

- Each pipeline run produces a session folder under `sessions/_runs/`.
- Inside each session folder, `findings.json` (usually at
  `evidence/stage-07-finalize/findings.json` or `artifacts/findings.json`) contains
  a list of per-function probe results.
- Each finding object has at minimum:
  ```json
  {
    "function_name": "CS_GetVersion",
    "status": "success",
    "confidence": 0.9,
    "return_value": "...",
    "args_used": [...],
    "error_code": null,
    "probe_count": 4
  }
  ```
- `human/dashboard-row.json` exists in each session folder and contains
  `functions_success` (int count) and `functions_error` (int count).
- `scripts/collect_session.py` already writes `human/dashboard-row.json` and
  `human/session-save-meta.json`. Do not modify it in this task.

## Task: Create `scripts/union_merger.py`

A standalone Python script that:

1. Accepts a list of session directory paths (as CLI args or a manifest JSON file).
2. Finds `findings.json` in each session (search in order:
   `{session}/evidence/stage-07-finalize/findings.json`,
   `{session}/artifacts/findings.json`,
   `{session}/findings.json`).
3. For each unique `function_name` across all sessions:
   - **If any session has `status=success`:**
     - Collect all success findings for this function.
     - Select the one with the highest `confidence` as the primary.
     - From lower-confidence successes, copy any fields that the primary is missing
       or has null (e.g. `return_value`, `boundary_conditions`, extra `args_used`
       variants). These are "supplemental evidence."
     - Set `selection_reason` to `"highest_confidence"` on the primary.
   - **If no session has `status=success`:**
     - Select the finding with the most unique `error_code` values observed across
       its probe attempts, or the highest `probe_count` as tiebreaker.
     - Set `selection_reason` to `"richest_failure_evidence"`.
   - Always record `source_run_id` (the session folder name) on every merged function.
4. Writes one output file: `merged-schema/accumulated-findings.json` inside a
   specified output directory.
5. Also writes `merged-schema/merge-summary.json` with:
   ```json
   {
     "merge_id": "<uuid4>",
     "merged_at": "<iso8601>",
     "input_sessions": ["session-folder-name-1", ...],
     "run_set_id": "<passed via --run-set-id or null>",
     "functions_total": 13,
     "functions_success": 5,
     "functions_error_only": 8,
     "sessions_contributed_new_success": ["session-name-2"],
     "union_gain_vs_best_single": 1
   }
   ```
   `union_gain_vs_best_single` = (union success count) − (max single-run success count).
   This is the key signal: if it is > 0, the UNION approach found functions that no
   single run found on its own.

## CLI Interface

```bash
python scripts/union_merger.py \
  --sessions sessions/_runs/runset-2026-03-22-abc123/N1 \
             sessions/_runs/runset-2026-03-22-abc123/N2 \
             sessions/_runs/runset-2026-03-22-abc123/N3 \
             sessions/_runs/runset-2026-03-22-abc123/M1 \
             sessions/_runs/runset-2026-03-22-abc123/M2 \
             sessions/_runs/runset-2026-03-22-abc123/M3 \
  --output sessions/_runs/runset-2026-03-22-abc123/merged-schema \
  --run-set-id runset-2026-03-22-abc123
```

Also accept `--sessions-manifest path/to/manifest.json` as an alternative to listing
individual paths, where the manifest is a JSON array of path strings.

## Output Schema: `accumulated-findings.json`

```json
[
  {
    "function_name": "CS_GetVersion",
    "status": "success",
    "confidence": 0.95,
    "return_value": "3.1.0",
    "args_used": [],
    "error_code": null,
    "probe_count": 4,
    "source_run_id": "2026-03-22-abc123-contoso-N2",
    "selection_reason": "highest_confidence",
    "supplemental_evidence": [
      {
        "source_run_id": "2026-03-22-abc123-contoso-M1",
        "field": "boundary_condition",
        "value": "returns empty string on first call before init"
      }
    ]
  },
  {
    "function_name": "CS_GetAccountBalance",
    "status": "error",
    "confidence": 0.0,
    "return_value": null,
    "args_used": [0, 1, 64],
    "error_code": "0xFFFFFFFF",
    "probe_count": 12,
    "source_run_id": "2026-03-22-abc123-contoso-N1",
    "selection_reason": "richest_failure_evidence",
    "supplemental_evidence": []
  }
]
```

## Files to Create

- `scripts/union_merger.py` — new file, ~150 lines

## Files to NOT Modify

- `scripts/collect_session.py`
- `api/*.py`
- Any existing session folder contents

## Acceptance Criteria

1. Running with 6 session dirs from `sessions/_runs/baseline/` (which have 4/13
   success each) produces `accumulated-findings.json` with:
   - `functions_success >= 4` (at minimum matches any single run)
   - `union_gain_vs_best_single >= 0` (may be 0 if all runs found the same 4)
   - All 13 functions present in output (success + error)
   - Every entry has `source_run_id` and `selection_reason`
2. Running with two session dirs where one has a unique success produces:
   - `union_gain_vs_best_single = 1`
   - `sessions_contributed_new_success` lists the contributing session
3. Script exits 0 on success, 1 on missing/unreadable findings.json in any session.
4. No modifications to input session folders.
