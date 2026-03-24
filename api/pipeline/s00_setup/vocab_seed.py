"""api.pipeline.s00_setup.vocab_seed – Placeholder for Phase 0a vocab seeding.

The actual implementation lives in the orchestrator (Phase 0 inline logic).
This module provides a callable entry point for the stage package.
"""

from __future__ import annotations


def _run_phase_0_vocab_seed(ctx) -> None:
    """Phase 0a: seed vocabulary table from hints, prior vocab, and sentinels.

    This is currently inline in the orchestrator; will be extracted
    in a future pass.
    """
    raise NotImplementedError("Phase 0a is currently inline in orchestrator")
