# Codex Prompt — Reasoning Artifacts Tier 2

> Paste everything below the horizontal rule into a new Codex/agent window.
> After finishing, run the verification commands and report all output.

---

## Implement Tier 2 Reasoning Artifacts

You are adding four reasoning observability artifacts to the MCP Factory pipeline.
These are low-effort, high-value captures — the data already exists in memory
during execution and just needs to be written to blob storage.

**All changes are in two files: `api/explore.py` and `api/chat.py`.**

No new logic. No new LLM calls. No schema changes. Only persist data that is
already computed.

---

### Change 1 — `probe-round-reasoning.json` in `_explore_one` (`api/explore.py`)

**What it is:** The model's assistant text before each round of tool calls — the
"I will try X because Y" reasoning. This text is already in the `conversation`
list as assistant messages. It just needs to be extracted and written once per
function after the probe loop finishes.

**Where to add it:** In `_explore_one`, immediately before the existing probe
log flush block (around line 1220):

```python
    # ── Flush probe log for this function ────────────────────────────────────

    if _fn_probe_log:
        try:
            _append_explore_probe_log(ctx.job_id, _fn_probe_log)
```

**Add this block immediately before that existing block:**

```python
    # Reasoning artifact: persist model's per-round reasoning text for this function.
    # Extracts all assistant messages from the conversation that preceded tool calls.
    # Writes to evidence/stage-02-probe-loop/probe-round-reasoning.json (appended per function).
    try:
        _round_reasoning_entries = []
        _conv_round = 0
        for _cm in conversation:
            if _cm.get("role") == "assistant":
                _reasoning_text = (_cm.get("content") or "").strip()
                if _reasoning_text:
                    _round_reasoning_entries.append({
                        "function": fn_name,
                        "round": _conv_round,
                        "reasoning": _reasoning_text,
                    })
                _conv_round += 1
        if _round_reasoning_entries:
            _reasoning_blob = (
                f"{ctx.job_id}/evidence/stage-02-probe-loop/probe-round-reasoning.json"
            )
            try:
                _existing_raw = _download_blob(ARTIFACT_CONTAINER, _reasoning_blob)
                _existing = json.loads(_existing_raw)
            except Exception:
                _existing = []
            _existing.extend(_round_reasoning_entries)
            _upload_to_blob(
                ARTIFACT_CONTAINER, _reasoning_blob,
                json.dumps(_existing, indent=2).encode(), "application/json",
            )
    except Exception as _rre:
        logger.debug("[%s] probe-round-reasoning write failed for %s: %s",
                     ctx.job_id, fn_name, _rre)
```

Search `api/explore.py` for `_download_blob` and `_upload_to_blob` to confirm
the exact import names already used in this file. Use those names — do not add
new imports.

---

### Change 2 — `probe-stop-reasons.json` in `_explore_one` (`api/explore.py`)

**What it is:** Why probing stopped for each function — `natural` (model
finished), `cap_hit` (tool call or round cap hit), `cancel`, or
`policy_exhausted` (write policy blocked). The variable `_policy_stop_reason`
is already computed in the function body.

**Add this block in the same location as Change 1** (immediately before the
probe log flush, after the reasoning block):

```python
    # Reasoning artifact: persist probe stop reason for this function.
    # Writes to evidence/stage-02-probe-loop/probe-stop-reasons.json (appended per function).
    try:
        # Determine stop reason from local state
        if _policy_stop_reason:
            _computed_stop_reason = _policy_stop_reason
        elif _fn_tool_call_count >= ctx.runtime.max_tool_calls:
            _computed_stop_reason = "cap_hit_tool_calls"
        elif _cancel_requested(ctx.job_id):
            _computed_stop_reason = "cancel"
        else:
            _computed_stop_reason = "natural"

        # Get model's final summary sentence (last assistant message with no tool calls after it)
        _final_summary = ""
        for _cm in reversed(conversation):
            if _cm.get("role") == "assistant" and (_cm.get("content") or "").strip():
                _final_summary = (_cm.get("content") or "").strip()
                break

        _stop_entry = {
            "function": fn_name,
            "stop_reason": _computed_stop_reason,
            "rounds_used": sum(1 for _cm in conversation if _cm.get("role") == "assistant"),
            "tool_calls_used": _fn_tool_call_count,
            "direct_target_calls": _direct_target_tool_calls,
            "final_summary": _final_summary[:300] if _final_summary else None,
        }
        _stop_blob = (
            f"{ctx.job_id}/evidence/stage-02-probe-loop/probe-stop-reasons.json"
        )
        try:
            _existing_stops_raw = _download_blob(ARTIFACT_CONTAINER, _stop_blob)
            _existing_stops = json.loads(_existing_stops_raw)
        except Exception:
            _existing_stops = []
        _existing_stops.append(_stop_entry)
        _upload_to_blob(
            ARTIFACT_CONTAINER, _stop_blob,
            json.dumps(_existing_stops, indent=2).encode(), "application/json",
        )
    except Exception as _sre:
        logger.debug("[%s] probe-stop-reasons write failed for %s: %s",
                     ctx.job_id, fn_name, _sre)
```

---

### Change 3 — `synthesis-input-snapshot.json` in `_run_phase_6_synthesize` (`api/explore.py`)

**What it is:** A snapshot of the exact `findings` list and `vocab` dict that
are passed to `_synthesize`. Captures what the synthesis LLM actually received,
not what was in memory at some other point.

**Find this block** in `_run_phase_6_synthesize` (around line 1437):

```python
        _report = _synthesize(
            ctx.client, ctx.model, _syn_findings,
            vocab=ctx.vocab, sentinels=ctx.sentinels,
        )
```

**Add this block immediately before it:**

```python
        # Reasoning artifact: snapshot synthesis inputs before the LLM call.
        # Writes to evidence/stage-04-synthesis/synthesis-input-snapshot.json
        try:
            _syn_snapshot = {
                "function_count": len(ctx.invocables),
                "findings_count": len(_syn_findings),
                "findings": _syn_findings,
                "vocab": ctx.vocab,
            }
            _upload_to_blob(
                ARTIFACT_CONTAINER,
                f"{ctx.job_id}/evidence/stage-04-synthesis/synthesis-input-snapshot.json",
                json.dumps(_syn_snapshot, indent=2).encode(), "application/json",
            )
        except Exception as _snap_e:
            logger.debug("[%s] synthesis-input-snapshot write failed: %s",
                         ctx.job_id, _snap_e)
```

---

### Change 4 — `chat-tool-reasoning.json` in `stream_chat` (`api/chat.py`)

**What it is:** Per tool call in the chat agent loop, the model's assistant text
immediately before that call. Proves (or disproves) that the model cited vocab
concepts when choosing to call a function. This closes T-22.

**Find the existing executor trace persistence block** in `stream_chat` (around
line 669). It looks like this:

```python
                        _trace_entries = [e["trace"] for e in _tl_snap if e.get("trace")]
                        if _trace_entries:
                            loop.run_in_executor(
                                None,
                                lambda te=_trace_entries: _append_executor_trace(job_id, te),
                            )
```

**Add this block immediately after it** (still inside the same `if job_id and _last_user_message and _final_text:` block):

```python
                        # Reasoning artifact: persist per-tool-call reasoning text.
                        # Each entry records what the model said before calling each tool.
                        _tool_reasoning_entries = [
                            {
                                "call": e["call"],
                                "args": e.get("args", {}),
                                "reasoning_before_call": e.get("reasoning", ""),
                                "result_excerpt": str(e.get("result", ""))[:200],
                            }
                            for e in _tl_snap
                            if e.get("call")
                        ]
                        if _tool_reasoning_entries:
                            try:
                                from api.storage import _download_blob as _dlb
                                from api.config import ARTIFACT_CONTAINER as _artc
                                import json as _jtr
                                _tr_blob = f"{job_id}/diagnostics/chat-tool-reasoning.json"
                                try:
                                    _tr_existing = _jtr.loads(_dlb(_artc, _tr_blob))
                                except Exception:
                                    _tr_existing = []
                                _tr_existing.extend(_tool_reasoning_entries)
                                from api.storage import _upload_blob as _ulb
                                _ulb(_artc, _tr_blob,
                                     _jtr.dumps(_tr_existing, indent=2).encode(),
                                     "application/json")
                            except Exception as _tre:
                                logger.debug("[stream_chat] chat-tool-reasoning write failed: %s", _tre)
```

**Important:** Check what `_tool_log` entries look like in `stream_chat`.
Search for where entries are appended to `_tool_log` and confirm the dict
structure has `call`, `args`, `result` fields. The `reasoning` field may need
to be added at the point where tool log entries are built — search for
`_tool_log.append` to find that location. If `reasoning` is not yet in the
tool log entry, add it as the assistant's `msg.content` at the moment the
entry is created.

---

### Verification

Run from the repo root with venv activated:

```powershell
# 1. Syntax check both touched files
.\.venv\Scripts\python.exe -c "
import ast, sys
for path in ['api/explore.py', 'api/chat.py']:
    try:
        ast.parse(open(path).read())
        print(f'SYNTAX OK: {path}')
    except SyntaxError as e:
        print(f'SYNTAX ERROR in {path}: {e}')
        sys.exit(1)
print('All files parse cleanly.')
"

# 2. Confirm Change 1 — probe-round-reasoning
Select-String -Path api/explore.py -Pattern 'probe-round-reasoning'

# 3. Confirm Change 2 — probe-stop-reasons
Select-String -Path api/explore.py -Pattern 'probe-stop-reasons'

# 4. Confirm Change 3 — synthesis-input-snapshot
Select-String -Path api/explore.py -Pattern 'synthesis-input-snapshot'

# 5. Confirm Change 4 — chat-tool-reasoning
Select-String -Path api/chat.py -Pattern 'chat-tool-reasoning'
```

**Expected results:**
- Both files: `SYNTAX OK`
- Command 2: at least one match — the `_reasoning_blob` assignment line
- Command 3: at least one match — the `_stop_blob` assignment line
- Command 4: at least one match — the `_syn_snapshot` upload line
- Command 5: at least one match — the `_tr_blob` assignment line

Report the full output of all five commands.

---

### What this enables after deployment

Once these artifacts are emitted, each run will produce:
- `evidence/stage-02-probe-loop/probe-round-reasoning.json` — indexed by function + round, shows what the model said before every tool call
- `evidence/stage-02-probe-loop/probe-stop-reasons.json` — indexed by function, shows why probing stopped (natural vs cap_hit is critical for context engineering diagnosis)
- `evidence/stage-04-synthesis/synthesis-input-snapshot.json` — proves exactly what findings and vocab reached synthesis
- `diagnostics/chat-tool-reasoning.json` — proves whether the chat agent cited vocab before tool calls

These four artifacts unlock T-17, T-22, T-23 reasoning quality transitions and
the `arg_source_distribution_delta` and `probe_stop_reason_delta` metrics in
compare.ps1.
