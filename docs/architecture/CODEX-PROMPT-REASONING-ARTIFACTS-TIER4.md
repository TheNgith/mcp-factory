# Codex Prompt — Reasoning Artifacts Tier 4

> Paste everything below the horizontal rule into a new Codex/agent window.
> After finishing, run the verification commands and report all output.
>
> **Prerequisite:** Tier 2 and Tier 3 artifacts should be deployed first, but
> Tier 4 changes are logically independent — they can be applied on top of the
> current codebase without Tier 2/3 being present.

---

## Implement Tier 4 Reasoning Artifacts

You are adding five reasoning observability artifacts. No new LLM calls and no
schema changes — only persist data that is already computed.

**Files touched: `api/explore.py`, `api/explore_gap.py`, `api/explore_vocab.py`, `api/chat.py`**

---

### Change 1 — `backfill-decision-log.json` in `_backfill_schema_from_synthesis` (`api/explore_vocab.py`)

**What it is:** The raw list of patches the synthesis LLM proposed for schema
backfill, alongside what was actually applied vs. skipped (including RC-2
quality guard). Proves whether the backfill LLM was proposing meaningful
enrichments or generic overwrites.

**Find the return statement** at the end of the success path in
`_backfill_schema_from_synthesis` (around line 347):

```python
        logger.info("[%s] backfill_schema: applied patches to %d/%d functions", job_id, patched, len(patches))
        return {
            "patches_requested": len(patches),
            "patches_applied": patched,
            "patched_functions": patched_functions,
        }
```

**Add this block immediately before that `return` statement:**

```python
        # Reasoning artifact: persist the full backfill decision log.
        # Records what the LLM proposed vs. what was actually applied.
        # Writes to evidence/stage-06-backfill/backfill-decision-log.json.
        try:
            from api.storage import _upload_to_blob as _ul_bf
            from api.config import ARTIFACT_CONTAINER as _AC_bf
            _backfill_log = {
                "patches_proposed": len(patches),
                "patches_applied": patched,
                "patched_functions": patched_functions,
                "patches": patches,
            }
            _ul_bf(
                _AC_bf,
                f"{job_id}/evidence/stage-06-backfill/backfill-decision-log.json",
                json.dumps(_backfill_log, indent=2).encode(),
            )
        except Exception as _bdl_e:
            logger.debug("[%s] backfill-decision-log write failed: %s", job_id, _bdl_e)
```

`json` is already imported at the top of `explore_vocab.py`. Use inline imports
for `_upload_to_blob` and `ARTIFACT_CONTAINER` as shown (they are not at the
module top-level in this file).

---

### Change 2 — `param-rename-decisions.json` in `_explore_one` (`api/explore.py`)

**What it is:** When the pipeline forces an `enrich_invocable` call (because the
LLM skipped it), record what param renames the model proposed vs. the original
Ghidra names. Critical for diagnosing whether enrichment is doing real semantic
work or just echoing type annotations.

**Find the forced enrich logger line** in `_explore_one` (around line 1008):

```python
                _execute_tool(ctx.inv_map["enrich_invocable"], _eargs)
                logger.info("[%s] explore_worker: forced enrich_invocable for %s",
                            ctx.job_id, fn_name)
        except Exception as _ee:
            logger.debug("[%s] forced enrich failed for %s: %s", ctx.job_id, fn_name, _ee)
```

**Add this block immediately after the `logger.info` line (before the outer
`except Exception as _ee:`):**

```python
                # Reasoning artifact: record param rename decisions from forced enrich.
                # Maps original Ghidra param names to what the model proposed.
                # Writes to evidence/stage-02-probe-loop/param-rename-decisions.json.
                try:
                    _original_params = {
                        p.get("name", ""): p.get("type", "")
                        for p in (inv.get("parameters") or [])
                        if isinstance(p, dict)
                    }
                    _rename_entry = {
                        "function": fn_name,
                        "original_params": _original_params,
                        "proposed_description": _eargs.get("description", ""),
                        "proposed_enrich_args": _eargs,
                    }
                    _rename_blob = (
                        f"{ctx.job_id}/evidence/stage-02-probe-loop/param-rename-decisions.json"
                    )
                    try:
                        _existing_renames = json.loads(
                            _download_blob(ARTIFACT_CONTAINER, _rename_blob)
                        )
                    except Exception:
                        _existing_renames = []
                    _existing_renames.append(_rename_entry)
                    _upload_to_blob(
                        ARTIFACT_CONTAINER, _rename_blob,
                        json.dumps(_existing_renames, indent=2).encode(),
                    )
                except Exception as _rde:
                    logger.debug("[%s] param-rename-decisions write failed for %s: %s",
                                 ctx.job_id, fn_name, _rde)
```

`_download_blob`, `_upload_to_blob`, and `ARTIFACT_CONTAINER` are already
imported at the top of `explore.py`. `json` is also already imported.

---

### Change 3 — `expert-answer-interpretation.json` in `_run_gap_answer_mini_sessions` (`api/explore_gap.py`)

**What it is:** Records the domain expert answer received for each function,
alongside the technical question asked and the prior finding — the full input
context that drive the mini-session, written _before_ probing begins. Proves
what context the model had and lets you diff "what expert said" vs "what model
did."

**Find the `conversation` list construction** in the per-function loop inside
`_run_gap_answer_mini_sessions`. Find this line:

```python
            _p_lookup = {p.get("name", ""): p for p in (inv.get("parameters") or [])}
            _observed_successes: list[dict] = []
            _mini_tool_log: list[dict] = []
```

**Add this block immediately before the `_p_lookup` line:**

```python
            # Reasoning artifact: record expert answer interpretation inputs before the mini-session.
            # Writes to evidence/stage-05-gap-resolution/expert-answer-interpretation.json.
            try:
                _expert_entry = {
                    "function": fn_name,
                    "answer_text": answer_text,
                    "technical_question": technical_q,
                    "prev_finding": prev_finding.get("finding") if prev_finding else None,
                    "prev_status": prev_finding.get("status") if prev_finding else None,
                }
                _expert_blob = (
                    f"{job_id}/evidence/stage-05-gap-resolution/expert-answer-interpretation.json"
                )
                try:
                    _existing_exp = json.loads(
                        _download_blob(ARTIFACT_CONTAINER, _expert_blob)
                    )
                except Exception:
                    _existing_exp = []
                _existing_exp.append(_expert_entry)
                _upload_to_blob(
                    ARTIFACT_CONTAINER, _expert_blob,
                    json.dumps(_existing_exp, indent=2).encode(),
                )
            except Exception as _eae:
                logger.debug(
                    "[%s] gap_mini_sessions: expert-answer-interpretation write failed for %s: %s",
                    job_id, fn_name, _eae,
                )
```

`_download_blob`, `_upload_to_blob`, and `ARTIFACT_CONTAINER` are already
imported at the top of `explore_gap.py`. `json` is also already imported.

---

### Change 4 — `chat-system-context.txt` in `stream_chat` (`api/chat.py`)

**What it is:** The full system message content at the start of each chat
session — vocab block, findings block, ID format rules, etc. One-time write per
session. Proves exactly what rules and knowledge the chat agent had loaded when
it handled this request. Required for diagnosing "why did the agent ignore vocab
term X?"

**Find the conversation initialization block** in `stream_chat` (around line
444):

```python
    if not sys_msgs:
        sys_msgs = [_build_system_message(invocables, job_id)]
    conversation = sys_msgs + user_msgs[-_CONTEXT_WINDOW_TURNS:]
```

**Add this block immediately after `conversation = sys_msgs + user_msgs[...]`:**

```python
    # Reasoning artifact: persist the full system message for this chat session.
    # One-time write per session — proves what rules and vocab the agent had loaded.
    # Writes to diagnostics/chat-system-context.txt.
    if job_id and sys_msgs:
        try:
            from api.storage import _upload_to_blob as _ul_sys
            from api.config import ARTIFACT_CONTAINER as _AC_sys
            _sys_content = (sys_msgs[0].get("content") or "").encode("utf-8")
            _ul_sys(_AC_sys, f"{job_id}/diagnostics/chat-system-context.txt", _sys_content)
        except Exception:
            pass  # never fail a chat session over an artifact write
```

---

### Change 5 — `chat-error-interpretation.json` in `stream_chat` (`api/chat.py`)

**What it is:** Per-sentinel-hit log — each time a tool returns 4294967295 or
-1, record the tool name, args, round number, and which model was active. Proves
whether sentinel hits happen early (suggesting argument format errors) or late
(suggesting genuine permission/dependency failures).

**Find the sentinel tracking block** in `stream_chat` (around line 800):

```python
                # Track error sentinels; escalate to reasoning model after 3 failures
                # if a more capable model is configured.
                if "4294967295" in tool_result or tool_result.strip() == "-1":
                    _failure_count += 1
                    if (_failure_count >= 3
                            and _active_model != _reasoning_model
                            and _base_model != _reasoning_model):
                        _active_model = _reasoning_model
                        logger.info(
                            "[stream_chat/%d] Escalating to reasoning model %s after %d failures",
                            _round, _active_model, _failure_count,
                        )
```

**Replace the `if "4294967295" ...` block with this expanded version** (the
escalation logic is preserved unchanged — only the artifact write is added):

```python
                # Track error sentinels; escalate to reasoning model after 3 failures
                # if a more capable model is configured.
                if "4294967295" in tool_result or tool_result.strip() == "-1":
                    _failure_count += 1
                    # Reasoning artifact: log each sentinel hit with context.
                    # Writes to diagnostics/chat-error-interpretation.json.
                    if job_id:
                        try:
                            from api.storage import _download_blob as _dlb_err
                            from api.storage import _upload_to_blob as _ulb_err
                            from api.config import ARTIFACT_CONTAINER as _AC_err
                            import json as _jerr
                            _err_entry = {
                                "round": _round,
                                "tool": fn_name,
                                "args": fn_args,
                                "result_excerpt": tool_result[:200],
                                "model_at_failure": _active_model,
                                "failure_count": _failure_count,
                                "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                            }
                            _err_blob = f"{job_id}/diagnostics/chat-error-interpretation.json"
                            try:
                                _err_existing = _jerr.loads(_dlb_err(_AC_err, _err_blob))
                            except Exception:
                                _err_existing = []
                            _err_existing.append(_err_entry)
                            _ulb_err(
                                _AC_err, _err_blob,
                                _jerr.dumps(_err_existing, indent=2).encode(),
                            )
                        except Exception:
                            pass  # never fail a chat session over an artifact write
                    if (_failure_count >= 3
                            and _active_model != _reasoning_model
                            and _base_model != _reasoning_model):
                        _active_model = _reasoning_model
                        logger.info(
                            "[stream_chat/%d] Escalating to reasoning model %s after %d failures",
                            _round, _active_model, _failure_count,
                        )
```

`time` is already imported at the top of `chat.py`.

---

### Verification

Run from the repo root with venv activated:

```powershell
# 1. Syntax check all four touched files
.\.venv\Scripts\python.exe -c "
import ast, sys
for path in ['api/explore.py', 'api/explore_gap.py', 'api/explore_vocab.py', 'api/chat.py']:
    try:
        ast.parse(open(path).read())
        print(f'SYNTAX OK: {path}')
    except SyntaxError as e:
        print(f'SYNTAX ERROR in {path}: {e}')
        sys.exit(1)
print('All files parse cleanly.')
"

# 2. backfill-decision-log
Select-String -Path api/explore_vocab.py -Pattern 'backfill-decision-log'

# 3. param-rename-decisions
Select-String -Path api/explore.py -Pattern 'param-rename-decisions'

# 4. expert-answer-interpretation
Select-String -Path api/explore_gap.py -Pattern 'expert-answer-interpretation'

# 5. chat-system-context
Select-String -Path api/chat.py -Pattern 'chat-system-context'

# 6. chat-error-interpretation
Select-String -Path api/chat.py -Pattern 'chat-error-interpretation'
```

**Expected results:**
- All four files: `SYNTAX OK`
- Commands 2–6: at least one match each (the blob path string)

Report the full output of all six commands.

---

### What this enables after deployment

With all four tiers deployed:

| Blob path | Transition unlocked |
|---|---|
| `evidence/stage-06-backfill/backfill-decision-log.json` | T-23: pass when backfill proposed ≥1 semantic rename per function |
| `evidence/stage-02-probe-loop/param-rename-decisions.json` | T-19: pass when model proposed non-generic param names |
| `evidence/stage-05-gap-resolution/expert-answer-interpretation.json` | T-21: cross-ref with mini-session-round-reasoning to verify model cited answer |
| `diagnostics/chat-system-context.txt` | Baseline for debugging chat vocab compliance |
| `diagnostics/chat-error-interpretation.json` | T-22: pass when sentinel hit on round 1 is followed by different args on round 2 |

The full 23-transition reasoning quality framework (T-01..T-23) becomes
implementable once all four tiers are deployed.
