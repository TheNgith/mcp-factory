"""S-07: Behavioral Spec + Harmonization + Contract Artifacts

CONTEXT IN:
    ctx.findings        — final findings after all resolution phases
    ctx.vocab           — final vocabulary
    ctx.invocables      — final enriched schemas
    api_reference.md    — synthesis document

CONTEXT OUT:
    behavioral_spec.py  — typed Python behavioral specification
    harmonization_report — deterministic reconciliation results
    Contract artifacts  — session-meta.json, transition-index.json, etc.
    Final job status    — explore_phase = "done" or "awaiting_clarification"

SHARED INFRA USED:
    storage     — blob upload, job status persistence
    telemetry   — OpenAI client for behavioral spec generation
    cohesion    — emit_contract_artifacts

MODEL CONTEXT:
    Behavioral spec: LLM receives full findings + api-reference.md
    Harmonization: No LLM (deterministic log scan)
    Finalize: LLM generates 1-sentence DLL description from vocab

CHECKPOINT:
    s07_checkpoint.json — behavioral_spec.py present, harmonization complete
    This is the terminal checkpoint — pipeline is fully complete.

SESSION DATA:
    {job_id}/stages/s07_finalize/behavioral_spec.py
    {job_id}/stages/s07_finalize/harmonization_report.json
    {job_id}/stages/s07_finalize/contract_artifacts/
"""

from api.pipeline.s07_finalize.behavioral_spec import _run_phase_9_behavioral_spec  # noqa: F401
from api.pipeline.s07_finalize.harmonize import (  # noqa: F401
    _run_phase_10_harmonize,
    _run_finalize,
)
