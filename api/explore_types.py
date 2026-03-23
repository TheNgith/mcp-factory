"""api/explore_types.py – Shared dataclasses for the exploration pipeline.

ExploreRuntime: immutable configuration loaded from job_runtime at worker start.
ExploreContext:  mutable pipeline state threaded through all phase functions.

Using explicit types instead of ~20 closure-captured locals gives three wins:
  1. Data flow is visible: each field is annotated with the phase that writes it.
  2. Phase functions are testable in isolation — construct an ExploreContext, run
     the phase, assert on the fields it wrote.
  3. Parallel _explore_one workers share state through clearly-labelled locks,
     not implicit closure references.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any


# ── Constants imported at module level to avoid circular deps ─────────────────
# (imported inline inside ExploreRuntime.from_job_runtime to keep this file
#  importable even when the full api/ package is not available in unit tests)


@dataclass
class ExploreRuntime:
    """Immutable pipeline configuration — set once at worker start.

    All fields default to the same values as the module-level constants so
    unit tests can construct an ``ExploreRuntime()`` without a live job.
    """

    max_rounds: int = 8
    max_tool_calls: int = 24
    max_functions: int = 50
    min_direct_probes: int = 1
    cap_profile: str = "default"
    skip_documented: bool = True
    deterministic_fallback_enabled: bool = True
    gap_resolution_enabled: bool = True
    clarification_enabled: bool = True
    model_override: str = ""
    concurrency: int = 1

    @classmethod
    def from_job_runtime(cls, job_runtime: dict) -> "ExploreRuntime":
        """Construct from the ``explore_runtime`` sub-dict in job status."""
        import os as _os
        from api.explore_phases import (
            _MAX_EXPLORE_ROUNDS_PER_FUNCTION,
            _MAX_TOOL_CALLS_PER_FUNCTION,
            _MAX_FUNCTIONS_PER_SESSION,
            _CAP_PROFILE,
        )
        from api.explore_helpers import _GAP_RESOLUTION_ENABLED

        return cls(
            max_rounds=int(job_runtime.get("max_rounds") or _MAX_EXPLORE_ROUNDS_PER_FUNCTION),
            max_tool_calls=int(job_runtime.get("max_tool_calls") or _MAX_TOOL_CALLS_PER_FUNCTION),
            max_functions=int(job_runtime.get("max_functions") or _MAX_FUNCTIONS_PER_SESSION),
            min_direct_probes=max(1, int(job_runtime.get("min_direct_probes_per_function") or 1)),
            cap_profile=str(job_runtime.get("cap_profile") or _CAP_PROFILE),
            skip_documented=bool(job_runtime.get("skip_documented", True)),
            deterministic_fallback_enabled=bool(
                job_runtime.get("deterministic_fallback_enabled", True)
            ),
            gap_resolution_enabled=bool(
                job_runtime.get("gap_resolution_enabled", _GAP_RESOLUTION_ENABLED)
            ),
            clarification_enabled=bool(
                job_runtime.get("clarification_questions_enabled", True)
            ),
            model_override=str(job_runtime.get("model") or "").strip(),
            concurrency=int(_os.getenv("EXPLORE_CONCURRENCY", "1")),
        )


@dataclass
class ExploreContext:
    """Mutable pipeline state — threaded through each phase function.

    Fields are grouped by the phase that produces them.  A phase function
    must only *write* to its own group; it may *read* from any earlier group.
    Forward reads (reading a field before its producing phase has run) are bugs.

    Thread safety
    -------------
    ``vocab``, ``sentinel_catalog``, ``already_explored``, and ``_state``
    are shared across parallel ``_explore_one`` workers.  Always acquire
    ``_lock`` before mutating them.
    """

    # ── Identity (set at construction, never mutated) ─────────────────────────
    job_id: str
    runtime: ExploreRuntime
    client: Any          # openai.AzureOpenAI | openai.OpenAI
    model: str
    run_started_at: float = field(default_factory=time.time)

    # ── Built from invocables at construction ─────────────────────────────────
    invocables: list[dict] = field(default_factory=list)
    inv_map: dict[str, dict] = field(default_factory=dict)
    tool_schemas: list[dict] = field(default_factory=list)
    # Total functions to explore (= len(invocables) after phase-2 capping by max_functions)
    total: int = 0

    # ── PRODUCED BY _run_phase_05_calibrate ───────────────────────────────────
    # DLL-specific sentinel map: {unsigned_int_code: "meaning_string"}
    # Always contains at least _SENTINEL_DEFAULTS keys.
    sentinels: dict = field(default_factory=dict)

    # ── PRODUCED BY _run_phase_0_vocab_seed ───────────────────────────────────
    # Cross-function vocabulary table.  Grows throughout the session.
    vocab: dict = field(default_factory=dict)
    # Raw use-cases text from job metadata (injected verbatim into LLM prompts).
    use_cases_text: str = ""

    # ── PRODUCED BY _run_phase_0_static ───────────────────────────────────────
    # Text block appended to every per-function LLM prompt for binary grounding.
    static_hints_block: str = ""
    # Binary-string evidence: {"ids": [...], "emails": [...], "all": [...]}
    dll_strings: dict = field(default_factory=dict)
    # Full run_static_analysis() result — also uploaded as static_analysis.json.
    static_analysis_result: dict = field(default_factory=dict)

    # ── PRODUCED BY _run_phase_1_write_unlock ────────────────────────────────
    # Outcome of the write-unlock probe sequence.
    unlock_result: dict = field(default_factory=lambda: {
        "unlocked": False, "sequence": [], "notes": "not attempted"
    })
    # Text injected into write-function prompts when unlock succeeded.
    write_unlock_block: str = ""

    # ── Q16: Cumulative sentinel new-code count (written to session-meta) ─────
    sentinel_new_codes_this_run: int = 0

    # ── PRODUCED BY _run_phase_2_curriculum_order ────────────────────────────
    # Init/startup/login functions — probed before all others (Q-5).
    init_invocables: list[dict] = field(default_factory=list)

    # ── ACCUMULATED DURING _run_phase_3_probe_loop (thread-safe) ─────────────
    # Cross-function error-code evidence: {hex_str: {evidence_count, functions, ...}}
    sentinel_catalog: dict = field(default_factory=dict)
    # Function names completed in this or prior sessions (for skip-documented gate).
    already_explored: set = field(default_factory=set)
    # Shared progress counter: {"explored": int}
    _state: dict = field(default_factory=lambda: {"explored": 0})
    # Mutex protecting vocab, sentinel_catalog, already_explored, and _state.
    _lock: threading.Lock = field(default_factory=threading.Lock)
