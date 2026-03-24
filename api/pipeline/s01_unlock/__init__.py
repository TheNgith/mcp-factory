"""S-01: Write-Unlock Probe

CONTEXT IN:
    ctx.invocables      — function registry with param types and decompiled C
    ctx.sentinels       — sentinel table from S-00
    ctx.vocab           — vocabulary from S-00
    ctx.dll_strings     — embedded strings from S-00
    winning_init_sequence.json (if warm/hot start from prior run)

CONTEXT OUT:
    ctx.unlock_result   — {unlocked: bool, sequence: [...], notes: str}
    ctx.write_unlock_block — text injected into every write-function prompt
    Blob: write_unlock_probe.json
    Blob: winning_init_sequence.json (if discovered)
    Blob: code_reasoning_analysis.json (LLM analysis of decompiled C)

SHARED INFRA USED:
    executor    — _execute_tool for init sequence + write function probes
    storage     — blob upload for probe artifacts

MODEL CONTEXT:
    Phase 1 code reasoning: LLM receives decompiled C code from doc_comment
    for each write function, asked to identify unlock mechanisms (XOR checksums,
    magic values, required sequences). This is the primary mechanism for
    autonomous discovery of unlock codes.

CHECKPOINT:
    s01_checkpoint.json — unlock_result, write_unlock_block, winning_init_sequence
    Loading this checkpoint replays the winning init sequence instead of
    re-discovering it.

SESSION DATA:
    {job_id}/stages/s01_unlock/write_unlock_probe.json
    {job_id}/stages/s01_unlock/winning_init_sequence.json
    {job_id}/stages/s01_unlock/code_reasoning_analysis.json
    {job_id}/stages/s01_unlock/model_context_s01.txt

TRANSITIONS:
    T-18 (write_unlock_probe_outcome)
"""

from api.pipeline.s01_unlock.write_unlock import (  # noqa: F401
    _probe_write_unlock,
    _generate_xor_codes,
)
