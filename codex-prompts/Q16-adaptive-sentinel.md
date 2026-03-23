# Codex Prompt — Q16: Adaptive Sentinel Calibration (`api/explore_phases.py`)

## What This Implements

Q16 ("Adaptive Sentinel Calibration") in `docs/architecture/QUESTIONS.md`.

Currently, sentinel calibration runs once (Phase 0.5) before any exploration begins.
If a sentinel code is missed — because it only appears at runtime, not on empty-arg
calls — it remains unknown for the entire session. Unknown sentinels cause the write-
unlock gate to fail permanently, blocking all write-path functions.

This task adds:
1. **Q-1 fix**: pre-seed the sentinel table from `vocab["error_codes"]` (already
   parsed in `_parse_hint_error_codes`) so hint-derived codes are known from the start.
2. **`write_unlock_outcome` emission**: record whether `_probe_write_unlock` succeeded,
   failed, or was not attempted, and write it to session-meta.
3. **Sentinel state emission**: after each stage boundary, write the sentinel table
   delta to session storage so save-session can include it in the compact manifest.

## Context: What Already Exists in `api/explore_phases.py`

### `_parse_hint_error_codes(hints: str) -> dict[int, str]`
Already implemented (lines ~57-80). Parses `0xHEX = meaning` patterns from the hints
string. Returns a `dict[int, str]` mapping integer code values to their meaning strings.
**This function exists but its output is not currently used to seed the sentinel table.**

### `_calibrate_sentinels(invocables, client, model, job_id) -> dict[int, str]`
Already implemented (lines ~81-160). Does an empty-arg sweep of all functions,
clusters non-zero high-bit return values, and submits candidates to an LLM call that
assigns meanings. Returns `dict[int, str]` (int code → meaning string).

The sentinel table is built as `candidates: dict[int, str]` inside this function.
After the LLM call produces names, the result dict is returned and used as the session's
sentinel table for all subsequent phases.

**The Q-1 fix location is clear**: before the LLM call on candidates, merge the
output of `_parse_hint_error_codes(hints)` into `candidates`. This pre-populates codes
whose meanings are already known from the hints text.

### Write-unlock probe
Somewhere in `explore_phases.py` (or `explore.py`), a function
`_probe_write_unlock` or equivalent runs the write-unlock check. It returns a
bool or status string indicating whether write functions are accessible.

## Task 1: Q-1 Fix — Pre-seed Sentinel Table from Hints

In `_calibrate_sentinels()`, after the empty-arg sweep builds the `candidates` dict
and before the LLM call:

```python
# Q-1 fix: pre-seed from hint-derived error codes
if hints:
    hint_codes = _parse_hint_error_codes(hints)
    for code_int, meaning in hint_codes.items():
        if code_int not in candidates:
            candidates[code_int] = meaning  # known from hints, no LLM needed
```

`hints` must be passed as a new parameter to `_calibrate_sentinels`. Find where
`_calibrate_sentinels` is called in `explore.py` (in `_run_phase_05_calibrate`) and
pass `ctx.hints` (or equivalent field from `ExploreContext`).

## Task 2: Emit Write-Unlock Outcome to Session-Meta

Find the write-unlock probe call in `explore.py` (in `_run_phase_1_write_unlock` or
equivalent). After the probe completes, write three fields to the job's session-meta:

```python
write_unlock_data = {
    "write_unlock_outcome": "resolved" | "blocked" | "not_attempted",
    "write_unlock_sentinel": "0xFFFFFFFB" | null,  # the code that was blocking, if known
}
```

Values:
- `"resolved"` — write-unlock probe succeeded; write-path functions are accessible
- `"blocked"` — probe returned a sentinel/error code; write functions remain gated
- `"not_attempted"` — write-unlock phase was skipped (e.g. no write functions in DLL)
- `write_unlock_sentinel` — the hex string of the blocking sentinel code, or null

Write these to job status via `_persist_job_status` (same mechanism used in
`api/worker.py` for other status updates). The fields must end up in `session-meta.json`
so `collect_session.py` can copy them to `human/dashboard-row.json`.

## Task 3: Emit Sentinel New-Code Count to Session-Meta

After `_calibrate_sentinels` completes each time it is called (initial Phase 0.5,
and any future re-calibration calls), record how many new codes were resolved compared
to the table before the call:

```python
sentinel_new_codes_this_run = len(new_sentinel_table) - len(previous_sentinel_table)
```

For the initial Phase 0.5 call, `previous_sentinel_table` is the empty/default table.
Write `sentinel_new_codes_this_run` (cumulative total for the session) to session-meta.

## Task 4: Stage-Boundary Re-calibration (Incremental)

After the probe loop phase completes (after `_run_phase_3_probe_loop`), scan all
probe log entries for that session for any new high-bit return values not in the
current sentinel table:

```python
new_candidates = {}
for entry in probe_log_entries:
    ret = entry.get("return_value")
    if ret is not None and isinstance(ret, int) and ret > 0x80000000:
        if ret not in current_sentinel_table:
            new_candidates[ret] = "unknown"

if new_candidates:
    # Submit only new_candidates to LLM sentinel-naming call
    resolved = _name_sentinel_candidates(new_candidates, client, model, job_id)
    current_sentinel_table.update(resolved)
    # Update sentinel_new_codes_this_run
```

This re-calibration is called once: after the probe loop, before synthesis begins.
Do not re-run the full empty-arg sweep. Only submit newly observed codes.

## Files to Modify

- `api/explore_phases.py`:
  - `_calibrate_sentinels()` — add `hints: str = ""` parameter; add Q-1 pre-seed block
  - Add `_name_sentinel_candidates()` helper (extract the LLM call portion of
    `_calibrate_sentinels` into a reusable helper that takes a `candidates` dict)
- `api/explore.py`:
  - `_run_phase_05_calibrate` — pass `ctx.hints` to `_calibrate_sentinels`
  - `_run_phase_1_write_unlock` — emit `write_unlock_outcome` and `write_unlock_sentinel`
    to session-meta after probe
  - After `_run_phase_3_probe_loop` — call stage-boundary re-calibration (Task 4)
  - After full session completes — emit `sentinel_new_codes_this_run` to session-meta

## Files to Modify (collect_session.py)

- `scripts/collect_session.py` — add three new fields to `_build_dashboard_row()`:
  - `write_unlock_outcome` (read from `session-meta.json`, default null)
  - `write_unlock_sentinel` (read from `session-meta.json`, default null)
  - `sentinel_new_codes_this_run` (read from `session-meta.json`, default 0)

## Files to NOT Modify

- `scripts/run_set_orchestrator.py`, `scripts/union_merger.py`
- Any session folder contents

## Acceptance Criteria

1. Run the pipeline on contoso_cs.dll with hints that include `0xFFFFFFFB = ...`.
   Verify that `session-meta.json` contains `write_unlock_outcome` and
   `write_unlock_sentinel`.
2. Parse the hints-derived code: if `vocab["error_codes"]` contains `0xFFFFFFFB`,
   that code must appear in the sentinel table after `_calibrate_sentinels` even if
   the empty-arg sweep did not return it.
3. After the probe loop, any new high-bit codes observed in the probe log that were
   not in the initial sentinel table should appear resolved in the updated table
   (if the LLM can name them).
4. `human/dashboard-row.json` must contain `write_unlock_outcome`, `write_unlock_sentinel`,
   `sentinel_new_codes_this_run` after `collect_session.py` processes the session.
5. No syntax errors in modified files (`python -m py_compile api/explore_phases.py api/explore.py`).
6. All existing probe-loop behavior is unchanged. The Q-1 fix is additive only.
