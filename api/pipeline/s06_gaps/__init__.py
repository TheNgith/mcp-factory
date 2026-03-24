"""S-06: Gap Resolution + MC-6 Final Comprehensive Unlock

CONTEXT IN:
    All accumulated context from S-00 through S-05:
        ctx.findings, ctx.vocab, ctx.sentinels, ctx.invocables
        ctx.unlock_result, ctx.write_unlock_block
        verification_results from S-05
        MC-3/4/5 decisions

CONTEXT OUT:
    Gap resolution outcomes (retried functions, clarification questions)
    MC-6 decision — final comprehensive unlock attempt
    winning_init_sequence.json (updated if MC-6 finds new sequence)
    ctx.write_unlock_block — updated if MC-6 cracks write-unlock

SHARED INFRA USED:
    storage     — findings, blob upload, transcript
    executor    — _execute_tool for gap retries and MC-6 unlock attempts
    telemetry   — OpenAI client for gap resolution LLM calls
    cohesion    — emit_contract_artifacts after gap resolution

MODEL CONTEXT:
    Gap resolution: per-function LLM agent with full accumulated knowledge.
    MC-6: comprehensive code reasoning on decompiled C for ALL remaining
    failures, with all prior MC decisions as additional context.

CHECKPOINT:
    s06_checkpoint.json — gap resolution outcomes, MC-6 decisions
    Per-function: resolved functions are checkpointed.

SESSION DATA:
    {job_id}/stages/s06_gaps/gap_resolution_log.json
    {job_id}/stages/s06_gaps/mc6_decision.json
    {job_id}/stages/s06_gaps/winning_init_sequence.json
    {job_id}/stages/s06_gaps/model_context_s06.txt
"""

from api.pipeline.s06_gaps.gap_resolution import (  # noqa: F401
    _attempt_gap_resolution,
    _run_gap_answer_mini_sessions,
)
from api.pipeline.s06_gaps.mc6_final_unlock import _mc6_post_gap_resolution  # noqa: F401
