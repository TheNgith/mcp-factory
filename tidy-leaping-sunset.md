# Richer Error Messages for Failed Tool Calls

## Context

When the LLM cannot fulfill a user's request through the MCP server — either the internal `/api/chat` wizard or a generated standalone `mcp_server.py` used by external clients like Claude Desktop — the failure currently degrades to bare strings like `"DLL call error: <exc>"`, `"Returned: 4294967295, sentinel: not found/invalid"`, or `"Bridge /execute error: ..."`. Key diagnostic data already exists in the system (probe matrix attempts, classified Windows error codes in `api/sentinel_codes.py`, `working_call` examples in stored findings) but never reaches the LLM or the UI, so the model cannot self-correct and users see opaque hex codes.

This change funnels that data into a structured error payload so:
- The internal chat LLM can call `explain_failure` (new synthetic tool) for diagnostics mid-loop, then retry or explain intelligently to the user.
- External MCP clients using the generated server receive human + machine-readable error content with classification, what was tried, known-good templates, and a suggested action.
- The UI renders failures with a classified badge and an actionable hint instead of a raw result string.

## Design

### 1. New shared module — `api/error_enrichment.py`

Single source of truth consumed by both `api/chat.py` and the generated server template. Pure, no I/O.

```python
def build_error_payload(
    function_name: str,
    raw_result: int | str | None,     # numeric return, or None if exception path
    trace: dict | None,               # the trace dict from executor.py
    exception: str | None,            # exception message if any
    findings_for_fn: list[dict],      # prior findings to mine for working_call
    extra_sentinels: dict | None,     # DLL-specific error_codes from vocab
) -> dict | None:                     # None => no error
```

Returned dict shape:
```
{
  "category": "sentinel" | "hresult" | "win32" | "ntstatus"
              | "bridge_unreachable" | "timeout" | "no_executable"
              | "unknown_tool" | "schema_mismatch" | "exception",
  "severity": "recoverable" | "blocking",
  "classified_name": "E_INVALIDARG" | None,
  "raw_code": "0xFFFFFFFF" | None,
  "what_tried": [ {"attempt": "...", "result": "..."} , ... ],
  "known_good": [ {"args": {...}, "confidence": "high"} ],
  "suggestion": "Retry with param_2 as a wide-string pointer, or call SetupContext first.",
  "human": "The call returned 0xFFFFFFFF (sentinel: not found/invalid). ..."
}
```

Reuses `api.sentinel_codes.classify_common_result_code` directly; do not reimplement.

### 2. `api/executor.py` — expand traced return

- Extend `_execute_tool_traced` (line 660) return contract from `{result_str, trace}` to `{result_str, trace, error}`. `error` is the dict above, or `None` on success.
- Expand `_probe_bridge` (lines 435–501) to return `(summary_string, tried_list)`. `tried_list` is `[{"encoding": "...", "size": N, "raw_result": "..."}, ...]`. The human summary stays as-is for backward compatibility.
- At each failure site (`_execute_dll` line 283–295, `_call_execute_bridge` line 595–603, `_execute_cli` line 304–366, `_execute_gui`), populate a minimal `error` dict (`category`, `raw_code`, `exception`) in the trace so `build_error_payload` can enrich it.
- `_execute_tool_traced` receives optional `findings_for_fn` passed by the caller and calls `build_error_payload`.

### 3. `api/chat.py` — surface errors + inject `explain_failure` tool

- **Inject synthetic tool** (mirror lines 46–98 pattern):
  ```python
  _EXPLAIN_FAILURE_TOOL = {
      "type": "function",
      "function": {
          "name": "explain_failure",
          "description": "Get structured diagnostics about a recent tool-call failure: "
                         "classified error code, probe-matrix attempts, known-good args "
                         "from stored findings, and a suggested next step. Call this "
                         "after a tool returns a sentinel or error when you need "
                         "guidance before retrying or explaining the problem to the user.",
          "parameters": {
              "type": "object",
              "properties": {
                  "function_name": {"type": "string"},
                  "recent_args": {"type": "object"}
              },
              "required": ["function_name"]
          }
      }
  }
  ```
  Add to the tools list wherever `_RECORD_FINDING_TOOL` / `_ENRICH_INVOCABLE_TOOL` are appended.

- **Dispatch** in `api/executor.py:_execute_tool` (line 606): recognise `name == "explain_failure"` and return a JSON-encoded `build_error_payload` result derived from the most recent `_tool_log` entry for that function (passed via `inv["_tool_log"]` the same way `_job_id` is threaded today).

- **Enrich `tool_result` SSE** at line 820:
  ```python
  yield _sse({
      "type": "tool_result",
      "name": fn_name,
      "result": tool_result,
      "error": error_payload,   # None on success
  })
  ```
  Extend the SSE event-type doc comment at lines 10–17.

- **Auto-hint after 3 consecutive sentinels** (extend lines 841–879): after the existing failure-count escalation, append a single `role: "system"` message summarising the latest `error_payload.human` + `suggestion`. Complements the `explain_failure` tool: LLM still chooses when to dig deeper, but the safety net guarantees it at least sees the classification.

- **Diagnostics artifact** (line 851–869): include the full `error_payload` next to the existing `result_excerpt`.

### 4. `api/generate.py` — bake findings into invocables

- Extend `_apply_findings_param_names` (lines 35–109) to attach a `findings_summary` field on each invocable: `{working_call: {...}, last_status: "success", confidence: "high"}`, drawn from the latest successful finding. The generated server has no `_JOB_INVOCABLE_MAPS` / blob access, so the summary must travel inside the baked invocable JSON.

### 5. `src/generation/section4_generate_server.py` — structured errors in generated server

- Vendor a copy of `api/error_enrichment.py` and `api/sentinel_codes.py` into the generated output (both are pure-Python with no runtime deps). Prefer copying files at generation time to textwrap-embedding so the template stays readable.
- Replace the four existing error-return strings:
  - line 293 `DLL call error: {exc}` → `_format_error("exception", exc, inv)`
  - line 303 `CLI error: no executable path…` → `_format_error("no_executable", None, inv)`
  - lines 326, 341 `CLI error: {exc}` → `_format_error("exception", exc, inv)`
  - GUI handlers (lines 506, 628, 712, 805, 814) → `_format_error(...)`
- `_format_error` returns a string of the form `"<human>\n\n```json\n<payload>\n```"` so MCP clients displaying raw content still see the explanation while structured-parsing clients can extract the JSON block.
- Also emit the `error` object on the `/invoke` JSON response (`{result, error}`) and inside the `/chat` SSE `tool_result` event, mirroring the internal chat surface.

### 6. `ui/main.py` — render the error

At line 1726 (the `tool_result` branch): if `evt.error` is populated, render a styled block (reuse the existing `.alert-error` CSS) showing:
- classified badge (`evt.error.classified_name` or `evt.error.category`)
- `evt.error.suggestion` on one line
- a `<details>` disclosure with `what_tried` + `known_good`

On success, behaviour is unchanged.

### 7. Tests

- `tests/test_error_enrichment.py` (new) — unit-test `build_error_payload` for every `category`; pin the `sentinel` + `hresult` + `win32` paths with fixed inputs.
- Extend `tests/test_chat.py` (or closest equivalent) to assert SSE `tool_result` emits `error=None` on success and a populated `error` dict on a simulated 0xFFFFFFFF return.
- `tests/test_generated_server_errors.py` (new) — generate a server against a fixture invocable, call `/invoke` with args that force the DLL exception path, assert the response body carries a structured error.

## Critical files

| File | Change |
|------|--------|
| `api/error_enrichment.py` | **NEW** — `build_error_payload` |
| `api/executor.py` | `_execute_tool_traced` returns `error`; `_probe_bridge` returns `tried_list`; `_execute_tool` handles `explain_failure` |
| `api/chat.py` | Inject `_EXPLAIN_FAILURE_TOOL`; `tool_result` SSE carries `error`; auto-hint after 3 sentinels; extend diagnostics artifact |
| `api/generate.py` | `_apply_findings_param_names` also attaches `findings_summary` |
| `src/generation/section4_generate_server.py` | Vendor enrichment modules; replace four error sites; emit `error` in `/invoke` + `/chat` |
| `ui/main.py` | Render `evt.error` with classification + suggestion |
| `tests/test_error_enrichment.py` | **NEW** unit tests |
| `tests/test_chat.py`, `tests/test_generated_server_errors.py` | Extend/new integration tests |

## Reused (do not reimplement)

- `api/sentinel_codes.py:classify_common_result_code` — full Windows code classification.
- `api/storage.py:_load_findings` — source of `working_call` templates.
- `api/chat.py` synthetic-tool pattern at lines 46–98 — mirror for `_EXPLAIN_FAILURE_TOOL`.
- `api/executor.py:_probe_bridge` — extend return shape; do not rewrite.
- `api/chat.py:841–879` sentinel-failure escalation — extend, not replace.

## Verification

1. **Unit**: `pytest tests/test_error_enrichment.py -v`
2. **Regression**: `pytest tests/ -v --tb=short --ignore=tests/test_mcp_stdio.py`
3. **Local pipeline**: `python mcp_factory.py --target <binary> --description "..."`; trigger a failing call; inspect SSE stream in browser DevTools — `tool_result` events should include an `error` object with `classified_name` and `suggestion`.
4. **Generated server**: `cd generated/<name> && python server.py`; `curl -X POST localhost:<port>/invoke -d '{"name":"<fn>","args":{"bad":"input"}}'`; response body contains a human paragraph plus a ```json code block with the structured payload.
5. **External MCP client**: wire the generated server to Claude Desktop via stdio (`tests/test_mcp_stdio.py` style); issue a failing tool call; confirm the LLM receives the human + JSON error content and can act on the suggestion.
6. **UI**: run the web wizard, trigger a 0xFFFFFFFF return, confirm the red banner shows classification + suggestion and the disclosure expands to show `what_tried` / `known_good`.
