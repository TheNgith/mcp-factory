"""DEPRECATED: This module has moved to api.pipeline.types.

This shim exists only for backward compatibility during the transition.
Import from api.pipeline.types instead.
"""
from api.pipeline.types import ExploreContext, ExploreRuntime  # noqa: F401

__all__ = ["ExploreContext", "ExploreRuntime"]
