# Codex Prompt — Reasoning Artifacts Tier 3

> Paste everything below the horizontal rule into a new Codex/agent window.
> After finishing, run the verification commands and report all output.

---

## Implement Tier 3 Reasoning Artifacts

You are adding five reasoning observability artifacts to the MCP Factory pipeline.
As with Tier 2, no new LLM calls and no schema changes. Only persist data that
is already computed.

**Files touched: `api/explore.py`, `api/explore_gap.py`**

---

### Change 1 — `probe-vocab-snapshot.json` in `_explore_one` (`api/explore.py`)

**What it is:** A snapshot of the vocab dict at the start of each function's
probe sequence. Proves what domain knowledge (id_formats, known IDs, error
codes, value semantics) the model had access to _before_ probing each function.
Critical for diagnosing "why did the model not use vocab term X when probing Y?"

**Where to add it:** In `_explore_one`, immediately after the T-04 block
(which writes `probe-user-message-sample.txt`). Insert between the existing
`except Exception as _t04_err:` block and the `# Per-function local state`
comment:

```python
    except Exception as _t04_err:
        logger.debug("[%s] T-04: probe user message sample write failed: %s",
                     ctx.job_id, _t04_err)
```

**Add this block immediately after that except block:**

```python
    # Reasoning artifact: snapshot the vocab state at this function's probe start.
    # Captures what domain knowledge the model had available before probing began.
    # Writes to evidence/stage-01-pre-probe/probe-vocab-snapshot.json (appended per function).
    try:
        _vocab_snap_entry = {
            "function": fn_name,
            "vocab_keys": list(_vocab_snap.keys()),
            "vocab": _vocab_snap,
        }
        _vocab_snap_blob = (
            f"{ctx.job_id}/evidence/stage-01-pre-probe/probe-vocab-snapshot.json"
        )
        try:
            _existing_vsnaps = json.loads(
                _download_blob(ARTIFACT_CONTAINER, _vocab_snap_blob)
            )
        except Exception:
            _existing_vsnaps = []
        _existing_vsnaps.append(_vocab_snap_entry)
        _upload_to_blob(
            ARTIFACT_CONTAINER, _vocab_snap_blob,
            json.dumps(_existing_vsnaps, indent=2).encode(),
        )
    except Exception as _vsne:
        logger.debug("[%s] probe-vocab-snapshot write failed for %s: %s",
                     ctx.job_id, fn_name, _vsne)
```

The `_vocab_snap` variable is already computed (line ~470:
`_vocab_snap = dict(ctx.vocab)`) and in scope at this location.
`_download_blob` and `_upload_to_blob` are already imported in this file — use
the same names already in use.

---

### Change 2 — `probe-strategy-summary.json` in `_explore_one` (`api/explore.py`)

**What it is:** A per-function record of exactly which probe strategies fired
(LLM rounds, deterministic fallback, cross-validation, hypothesis, write-mode
write-policy). Lets you answer "did the model even try deterministic fallback
before giving up?" per function.

**Where to add it:** In `_explore_one`, at the very end — immediately before
the existing probe log flush block (the same location as Tier 2 Changes 1 and
2). The existing flush block looks like this:

```python
    # ── Flush probe log for this function ────────────────────────────────────

    if _fn_probe_log:
        try:
            _append_explore_probe_log(ctx.job_id, _fn_probe_log)
```

**Add this block immediately before that flush comment (after the Tier 2
probe-stop-reasons block if it is already present):**

```python
    # Reasoning artifact: probe strategy summary — which strategies fired per function.
    # Writes to evidence/stage-02-probe-loop/probe-strategy-summary.json (appended per function).
    try:
        _phases_used = sorted({e.get("phase", "unknown") for e in _fn_probe_log})
        _strategy_entry = {
            "function": fn_name,
            "phases_used": _phases_used,
            "rounds_used": sum(1 for m in conversation if m.get("role") == "assistant"),
            "tool_calls_used": _fn_tool_call_count,
            "direct_target_calls": _direct_target_tool_calls,
            "no_tool_call_rounds": _no_tool_call_rounds,
            "deterministic_fallback_fired": any(
                e.get("phase") == "deterministic_fallback" for e in _fn_probe_log
            ),
            "cross_validation_ran": any(
                e.get("phase") == "cross_validate" for e in _fn_probe_log
            ),
            "hypothesis_ran": bool(_best_raw_result),
            "is_write_fn": _is_write_fn,
            "enrich_called": _enrich_called,
            "finding_recorded": _finding_recorded,
            "stop_reason": _policy_stop_reason or (
                "cap_hit_tool_calls"
                if _fn_tool_call_count >= ctx.runtime.max_tool_calls
                else "natural"
            ),
        }
        _strategy_blob = (
            f"{ctx.job_id}/evidence/stage-02-probe-loop/probe-strategy-summary.json"
        )
        try:
            _existing_strats = json.loads(
                _download_blob(ARTIFACT_CONTAINER, _strategy_blob)
            )
        except Exception:
            _existing_strats = []
        _existing_strats.append(_strategy_entry)
        _upload_to_blob(
            ARTIFACT_CONTAINER, _strategy_blob,
            json.dumps(_existing_strats, indent=2).encode(),
        )
    except Exception as _psume:
        logger.debug("[%s] probe-strategy-summary write failed for %s: %s",
                     ctx.job_id, fn_name, _psume)
```

All variables referenced (`_fn_probe_log`, `conversation`, `_fn_tool_call_count`,
`_direct_target_tool_calls`, `_no_tool_call_rounds`, `_best_raw_result`,
`_is_write_fn`, `_enrich_called`, `_finding_recorded`, `_policy_stop_reason`,
`ctx.runtime.max_tool_calls`) are all in scope at this location.

---

### Change 3 — `sentinel-calibration-decisions.json` in `_run_phase_05_calibrate` (`api/explore.py`)

**What it is:** A richer version of `sentinel_calibration.json` (which already
exists). Adds: how many functions were probed, whether defaults were used, and
the count of resolved vs default entries. Closes T-17.

**Find the existing sentinel upload block** in `_run_phase_05_calibrate`
(around line 177):

```python
        try:
            _upload_to_blob(
                ARTIFACT_CONTAINER,
                f"{ctx.job_id}/sentinel_calibration.json",
                json.dumps(
                    {f"0x{k:08X}": v for k, v in ctx.sentinels.items()}, indent=2
                ).encode(),
            )
        except Exception as _sce:
            logger.debug("[%s] phase0.5: sentinel artifact upload failed: %s",
                         ctx.job_id, _sce)
```

**Add this block immediately after that existing except block:**

```python
        # Reasoning artifact: sentinel calibration decisions with coverage metadata.
        # Writes to evidence/stage-00-calibrate/sentinel-calibration-decisions.json.
        try:
            _calib_decision = {
                "functions_calibrated": len(ctx.invocables),
                "sentinel_count": len(ctx.sentinels),
                "used_defaults": ctx.sentinels == _SENTINEL_DEFAULTS,
                "sentinels_resolved": {
                    f"0x{k:08X}": v for k, v in ctx.sentinels.items()
                },
            }
            _upload_to_blob(
                ARTIFACT_CONTAINER,
                f"{ctx.job_id}/evidence/stage-00-calibrate/sentinel-calibration-decisions.json",
                json.dumps(_calib_decision, indent=2).encode(),
            )
        except Exception as _cde:
            logger.debug("[%s] phase0.5: sentinel-calibration-decisions write failed: %s",
                         ctx.job_id, _cde)
```

`_SENTINEL_DEFAULTS` is already imported in this file (`from api.explore_phases
import ... _SENTINEL_DEFAULTS`).

---

### Change 4 — `synthesis-coverage-check.json` in `_run_phase_6_synthesize` (`api/explore.py`)

**What it is:** After writing `api_reference.md`, check which function names
from `_syn_findings` actually appear in the synthesis text. Coverage < 100%
means some functions were silently dropped by the synthesis LLM — a critical
failure mode that no other artifact currently catches.

**Find this block** in `_run_phase_6_synthesize` (around line 1447):

```python
        _upload_to_blob(
            ARTIFACT_CONTAINER,
            f"{ctx.job_id}/api_reference.md",
            _report.encode("utf-8"),
        )
        logger.info("[%s] phase6: api_reference.md saved to blob", ctx.job_id)
```

**Add this block immediately after the `logger.info` line (before "Refresh
invocables"):**

```python
        # Reasoning artifact: check which functions appear in the synthesis text.
        # Coverage < 100% means the synthesis LLM silently dropped functions.
        # Writes to evidence/stage-04-synthesis/synthesis-coverage-check.json.
        try:
            _fn_names_found = [f.get("function", "") for f in _syn_findings if f.get("function")]
            _coverage_entries = [
                {"function": _fn_c, "in_api_reference": bool(_fn_c and _fn_c in _report)}
                for _fn_c in _fn_names_found
            ]
            _covered_count = sum(1 for e in _coverage_entries if e["in_api_reference"])
            _cov_check = {
                "total_functions": len(_fn_names_found),
                "covered_in_report": _covered_count,
                "coverage_pct": (
                    round(100.0 * _covered_count / len(_fn_names_found), 1)
                    if _fn_names_found else 0.0
                ),
                "functions": _coverage_entries,
            }
            _upload_to_blob(
                ARTIFACT_CONTAINER,
                f"{ctx.job_id}/evidence/stage-04-synthesis/synthesis-coverage-check.json",
                json.dumps(_cov_check, indent=2).encode(),
            )
        except Exception as _cc_e:
            logger.debug("[%s] phase6: synthesis-coverage-check write failed: %s",
                         ctx.job_id, _cc_e)
```

---

### Change 5 — `mini-session-round-reasoning.json` in `_run_gap_answer_mini_sessions` (`api/explore_gap.py`)

**What it is:** Per-tool-call reasoning text from the mini-session round loop,
extracted from `_mini_tool_log`. Proves whether the model cited the expert
answer when choosing its probe strategy. The data is already in `_mini_tool_log`
(each entry has a `"reasoning"` field set to the assistant text at round start).

**Where to add it:** In `_run_gap_answer_mini_sessions`, for each function's
inner round loop, after the round loop ends and immediately before the existing
`try: _append_transcript(...)` block. Find this pattern:

```python
            try:
                _mini_user_msg = (
                    f"[GAP MINI-SESSION: {fn_name}]\n"
                    f"Domain expert answer: {answer_text!r}\n"
                    f"{technical_ctx}"
                    f"{prev_ctx}"
                )
                _mini_final = "(mini-session complete)"
                for _turn in reversed(conversation):
                    if _turn.get("role") == "assistant" and _turn.get("content"):
                        _mini_final = _turn["content"]
                        break
                _append_transcript(job_id, _mini_user_msg, _mini_final, _mini_tool_log,
                                   transcript_blob="mini_session_transcript.txt")
```

**Add this block immediately before that try block:**

```python
            # Reasoning artifact: extract per-round reasoning from mini-session tool log.
            # Writes to evidence/stage-05-gap-resolution/mini-session-round-reasoning.json.
            try:
                _mini_reasoning_entries = [
                    {
                        "function": fn_name,
                        "call": e["call"],
                        "args": e.get("args", {}),
                        "reasoning": e.get("reasoning") or "",
                        "result_excerpt": str(e.get("result", ""))[:200],
                    }
                    for e in _mini_tool_log
                    if e.get("reasoning")
                ]
                if _mini_reasoning_entries:
                    _mini_reasoning_blob = (
                        f"{job_id}/evidence/stage-05-gap-resolution/"
                        "mini-session-round-reasoning.json"
                    )
                    try:
                        _existing_mr = json.loads(
                            _download_blob(ARTIFACT_CONTAINER, _mini_reasoning_blob)
                        )
                    except Exception:
                        _existing_mr = []
                    _existing_mr.extend(_mini_reasoning_entries)
                    _upload_to_blob(
                        ARTIFACT_CONTAINER, _mini_reasoning_blob,
                        json.dumps(_existing_mr, indent=2).encode(),
                    )
            except Exception as _mrre:
                logger.debug(
                    "[%s] gap_mini_sessions: mini-session-round-reasoning write failed for %s: %s",
                    job_id, fn_name, _mrre,
                )
```

`_download_blob` and `_upload_to_blob` are already imported in `explore_gap.py`
(check top of file — they come from `api.storage`). `ARTIFACT_CONTAINER` is
also already imported from `api.config`.

---

### Verification

Run from the repo root with venv activated:

```powershell
# 1. Syntax check both touched files
.\.venv\Scripts\python.exe -c "
import ast, sys
for path in ['api/explore.py', 'api/explore_gap.py']:
    try:
        ast.parse(open(path).read())
        print(f'SYNTAX OK: {path}')
    except SyntaxError as e:
        print(f'SYNTAX ERROR in {path}: {e}')
        sys.exit(1)
print('All files parse cleanly.')
"

# 2. probe-vocab-snapshot
Select-String -Path api/explore.py -Pattern 'probe-vocab-snapshot'

# 3. probe-strategy-summary
Select-String -Path api/explore.py -Pattern 'probe-strategy-summary'

# 4. sentinel-calibration-decisions
Select-String -Path api/explore.py -Pattern 'sentinel-calibration-decisions'

# 5. synthesis-coverage-check
Select-String -Path api/explore.py -Pattern 'synthesis-coverage-check'

# 6. mini-session-round-reasoning
Select-String -Path api/explore_gap.py -Pattern 'mini-session-round-reasoning'
```

**Expected results:**
- Both files: `SYNTAX OK`
- Commands 2–6: at least one match each (the blob path string)

Report the full output of all six commands.

---

### What this enables after deployment

- `evidence/stage-01-pre-probe/probe-vocab-snapshot.json` — per function: what vocab the model had before probing (unlocks "did the model use id_formats?" diagnosis)
- `evidence/stage-02-probe-loop/probe-strategy-summary.json` — per function: which strategies ran and why they stopped (unlocks T-18, T-19 transitions)
- `evidence/stage-00-calibrate/sentinel-calibration-decisions.json` — full calibration context (unlocks T-17 transition)
- `evidence/stage-04-synthesis/synthesis-coverage-check.json` — synthesis completeness (unlocks T-20 transition: warn if coverage < 100%)
- `evidence/stage-05-gap-resolution/mini-session-round-reasoning.json` — expert-answer-driven session reasoning (unlocks T-21 transition: did the model cite the expert answer?)
