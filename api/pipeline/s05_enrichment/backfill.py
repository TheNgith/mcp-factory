"""api.pipeline.s05_enrichment.backfill – Phase 7 schema backfill.

Re-exports from the orchestrator module until full extraction.
"""
from api.pipeline.orchestrator import _run_phase_7_backfill  # noqa: F401
