"""S-02: Curriculum Ordering + Per-Function LLM Probe Loop

CONTEXT IN:
    Full ctx from S-00 and S-01:
        ctx.sentinels, ctx.vocab, ctx.unlock_result, ctx.write_unlock_block
        ctx.invocables (with decompiled C in doc_comment)
        ctx.static_hints_block, ctx.dll_strings
        ctx.use_cases_text, ctx.already_explored

CONTEXT OUT:
    ctx.findings        — per-function discovery results (status, working_call, etc.)
    ctx.sentinel_catalog — cross-function error code evidence
    ctx.already_explored — updated with newly probed functions
    ctx._state["explored"] — progress counter
    Blob: explore_probe_log.json (per-function probe trace)

SHARED INFRA USED:
    storage     — findings, probe log, invocable updates
    executor    — _execute_tool_traced for DLL calls during probing
    telemetry   — OpenAI client for per-function LLM agent loop

MODEL CONTEXT:
    Each function gets a dedicated LLM agent with:
    - System message: invocable registry, prior findings, sentinel table,
      vocabulary, static analysis hints, write_unlock_block, user hints
    - Per-function: signature, decompiled C code, param descriptions,
      criticality, dependencies, deterministic fallback args
    The model does NOT see other functions' decompiled code (only target).
    The model does NOT see bridge/executor implementation details.

CHECKPOINT:
    s02_checkpoint.json — per-function: {fn_name: {status, working_call, ...}}
    Functions with status="success" and verified working_call are skippable.
    focus_functions parameter limits probing to specific functions only.

SESSION DATA:
    {job_id}/stages/s02_probe/explore_probe_log.json
    {job_id}/stages/s02_probe/findings_snapshot_post_probe.json
    {job_id}/stages/s02_probe/model_context_s02.txt

TRANSITIONS:
    T-01 through T-16 (per-function discovery transitions)
"""

from api.pipeline.s02_probe.probe_loop import (  # noqa: F401
    _explore_one,
    _run_phase_3_probe_loop,
)
from api.pipeline.s02_probe.curriculum import (  # noqa: F401
    _run_phase_2_curriculum_order,
)
