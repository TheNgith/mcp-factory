"""S-04: API Reference Synthesis + MC-4 Init Sequence Clues

CONTEXT IN:
    ctx.findings    — reconciled findings from S-03
    ctx.vocab       — vocabulary with sentinel meanings
    ctx.sentinels   — full sentinel table
    ctx.invocables  — enriched function registry

CONTEXT OUT:
    api_reference.md   — LLM-generated API documentation
    MC-4 decision      — init sequence clues parsed from synthesis
    ctx (unchanged)    — synthesis is read-only on ctx

SHARED INFRA USED:
    storage     — blob upload for api_reference.md
    telemetry   — OpenAI client for synthesis LLM call

MODEL CONTEXT:
    Synthesis prompt includes ALL findings and vocabulary.
    MC-4 parses the synthesis output for init sequence mentions.

CHECKPOINT:
    s04_checkpoint.json — api_reference.md hash, synthesis_complete flag
    Only invalidated if findings changed since checkpoint.

SESSION DATA:
    {job_id}/stages/s04_synthesis/api_reference.md
    {job_id}/stages/s04_synthesis/mc4_decision.json
    {job_id}/stages/s04_synthesis/synthesis_prompt.txt
    {job_id}/stages/s04_synthesis/model_context_s04.txt
"""

from api.pipeline.s04_synthesis.synthesize import _run_phase_6_synthesize  # noqa: F401
from api.pipeline.s04_synthesis.mc4_init_clues import _mc4_post_synthesis  # noqa: F401
