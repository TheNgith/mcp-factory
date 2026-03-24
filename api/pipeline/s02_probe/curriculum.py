"""api.pipeline.s02_probe.curriculum – Placeholder for Phase 2 curriculum ordering.

The actual implementation lives in the orchestrator (Phase 2 inline logic).
This module provides a callable entry point for the stage package.
"""

from __future__ import annotations


def _run_phase_2_curriculum_order(ctx) -> None:
    """Phase 2: sort invocables by uncertainty score for curriculum-style probing.

    This is currently inline in the orchestrator; will be extracted
    in a future pass.
    """
    raise NotImplementedError("Phase 2 is currently inline in orchestrator")
