"""S-03: Post-Probe Reconciliation + MC-3 Write Analysis + Sentinel Catalog

CONTEXT IN:
    ctx.findings        — accumulated findings from S-02
    ctx.sentinels       — sentinel table from S-00
    ctx.unlock_result   — write-unlock result from S-01
    ctx.vocab           — vocabulary accumulated during probing
    ctx.invocables      — function registry with enriched schemas
    Blob: explore_probe_log.json from S-02

CONTEXT OUT:
    ctx.findings        — reconciled (probe log vs findings alignment verified)
    ctx.sentinel_catalog — promoted sentinel evidence from probe results
    ctx.write_unlock_block — updated if MC-3 cracks write-unlock
    Blob: mc-decisions/mc3-post-reconcile.json
    Blob: sentinel_catalog.json

SHARED INFRA USED:
    storage     — _load_findings, blob upload
    executor    — _execute_tool (for MC-3 targeted unlock attempts)

MODEL CONTEXT:
    No LLM calls in reconciliation itself (deterministic log scan).
    MC-3 uses heuristic analysis only (pattern classification of sentinel codes).
    The write_unlock_block IS model context — injected into subsequent LLM prompts.

CHECKPOINT:
    s03_checkpoint.json — reconciled findings, sentinel_catalog, MC-3 decision
    Requires all S-02 function checkpoints to be present.

SESSION DATA:
    {job_id}/stages/s03_reconcile/reconciliation_report.json
    {job_id}/stages/s03_reconcile/mc3_decision.json
    {job_id}/stages/s03_reconcile/sentinel_catalog.json
    {job_id}/stages/s03_reconcile/model_context_s03.txt

TRANSITIONS:
    T-19 (mc_coordinator_decisions) — MC-3 decision artifact
"""

from api.pipeline.s03_reconcile.reconcile import _run_phase_4_reconcile  # noqa: F401
from api.pipeline.s03_reconcile.mc3_write_analysis import _mc3_post_reconcile  # noqa: F401
from api.pipeline.s03_reconcile.sentinel_catalog import _run_phase_5_sentinel_catalog  # noqa: F401
