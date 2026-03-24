"""S-05: Schema Backfill + Verification + MC-5 Read-to-Write Chaining

CONTEXT IN:
    api_reference.md    — synthesis document from S-04
    ctx.findings        — current findings
    ctx.invocables      — function registry

CONTEXT OUT:
    Enriched invocable schemas (backfilled descriptions from synthesis)
    verification_results — per-function execution verification
    MC-5 decision       — chained read outputs as write function args
    ctx.write_unlock_block — updated if MC-5 cracks write-unlock

SHARED INFRA USED:
    storage     — invocable patching, blob upload
    executor    — _execute_tool for verification probes

MODEL CONTEXT:
    No LLM calls in backfill (regex parsing of synthesis doc).
    MC-5 uses heuristic chaining (match read outputs to write params).

CHECKPOINT:
    s05_checkpoint.json — verification results per function
    Per-function like S-02: verified functions are checkpointed individually.

SESSION DATA:
    {job_id}/stages/s05_enrichment/backfill_changes.json
    {job_id}/stages/s05_enrichment/verification_results.json
    {job_id}/stages/s05_enrichment/mc5_decision.json
    {job_id}/stages/s05_enrichment/model_context_s05.txt
"""

from api.pipeline.s05_enrichment.backfill import _run_phase_7_backfill  # noqa: F401
from api.pipeline.s05_enrichment.verify import _run_phase_7b_verify_enrichment  # noqa: F401
from api.pipeline.s05_enrichment.mc5_chain_reads import _mc5_post_verification  # noqa: F401
