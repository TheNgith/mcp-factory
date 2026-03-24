"""S-00: Setup — Sentinel Calibration + Vocab Seed + Static Analysis

CONTEXT IN:
    DLL binary (uploaded via /api/analyze)
    User hints (free text from job creation)
    prior_job_id (optional — for circular feedback)
    Checkpoint data (optional — sentinels, vocab from prior verified run)

CONTEXT OUT:
    ctx.sentinels       — DLL-specific sentinel error code table
    ctx.vocab           — cross-function vocabulary (id_formats, value_semantics)
    ctx.dll_strings     — embedded strings from binary analysis
    ctx.static_hints_block — text block injected into every LLM prompt
    ctx.already_explored — function names from prior session findings

SHARED INFRA USED:
    storage     — blob upload/download for sentinel_calibration.json, vocab.json
    executor    — _execute_tool for calibration probes
    telemetry   — OpenAI client for sentinel naming LLM call

MODEL CONTEXT:
    Phase 0.5: LLM names sentinel error codes (short prompt, ~60 tokens each)
    Phase 0a:  No LLM call (vocab seeding is deterministic)
    Phase 0b:  No LLM call (static analysis is binary tooling)

CHECKPOINT:
    s00_checkpoint.json — sentinels, vocab, dll_strings, static_analysis_result
    Loading this checkpoint skips recalibration and static analysis entirely.

SESSION DATA:
    {job_id}/stages/s00_setup/sentinel_calibration.json
    {job_id}/stages/s00_setup/vocab.json
    {job_id}/stages/s00_setup/static_analysis.json
    {job_id}/stages/s00_setup/dll_strings.json
    {job_id}/stages/s00_setup/model_context_s00.txt

TRANSITIONS:
    T-17 (sentinel_calibration_outcome)
"""

from api.pipeline.s00_setup.calibration import (  # noqa: F401
    _calibrate_sentinels,
    _name_sentinel_candidates,
    _parse_hint_error_codes,
)
from api.pipeline.s00_setup.vocab_seed import _run_phase_0_vocab_seed  # noqa: F401
from api.pipeline.s00_setup.static_analysis import _run_phase_0_static  # noqa: F401
