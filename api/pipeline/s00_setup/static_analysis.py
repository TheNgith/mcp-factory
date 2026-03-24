"""api.pipeline.s00_setup.static_analysis – Placeholder for Phase 0b static analysis.

The actual implementation lives in the orchestrator (Phase 0 inline logic).
This module provides a callable entry point for the stage package.
"""

from __future__ import annotations


def _run_phase_0_static(ctx) -> None:
    """Phase 0b: run binary static analysis (strings, IAT, PE metadata).

    This is currently inline in the orchestrator; will be extracted
    in a future pass.
    """
    raise NotImplementedError("Phase 0b is currently inline in orchestrator")
