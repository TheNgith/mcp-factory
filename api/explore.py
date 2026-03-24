"""DEPRECATED: This module has moved to api.pipeline.orchestrator.

This shim exists only for backward compatibility during the transition.
Import from api.pipeline.orchestrator instead.
"""
from api.pipeline.orchestrator import _explore_worker  # noqa: F401
from api.pipeline.s06_gaps.gap_resolution import _run_gap_answer_mini_sessions  # noqa: F401
