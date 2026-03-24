"""DEPRECATED: This module has moved to api.pipeline.s02_probe.probe_loop.

This shim exists only for backward compatibility during the transition.
Import from api.pipeline.s02_probe.probe_loop instead.
"""
from api.pipeline.s02_probe.probe_loop import (  # noqa: F401
    _explore_one, _run_phase_3_probe_loop,
)
