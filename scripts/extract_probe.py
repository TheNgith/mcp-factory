"""One-time extraction script: splits explore_probe.py out of explore.py."""
import pathlib, sys

repo = pathlib.Path(__file__).parent.parent

src = repo / "api" / "explore.py"
dest = repo / "api" / "explore_probe.py"

lines = src.read_text(encoding="utf-8").splitlines(True)

# Lines 487-1536 (1-indexed) = indices 486-1535 (0-indexed)
# _classify_arg_source starts at line 487, extraction ends just before the
# Phase 4 ════ section header at line 1537.
EXTRACT_START = 486   # inclusive, 0-indexed
EXTRACT_END   = 1536  # exclusive, 0-indexed

body_lines = lines[EXTRACT_START:EXTRACT_END]
print(f"Extracting {len(body_lines)} lines ({EXTRACT_START+1}..{EXTRACT_END}) from explore.py")

HEADER = """\
\"\"\"api/explore_probe.py – Per-function LLM probe loop.

Contains _classify_arg_source, _explore_one, and _run_phase_3_probe_loop.
Extracted from explore.py so the ~1000-line per-function agent loop lives in
its own module, making explore.py focus on the pipeline phases and orchestrator.

Import surface for callers:
    from api.pipeline.s02_probe.probe_loop import _explore_one, _run_phase_3_probe_loop
\"\"\"
from __future__ import annotations

import json
import logging
import re as _re
import threading as _threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor as _TPE
from typing import Any

from api.config import ARTIFACT_CONTAINER
from api.executor import _execute_tool, _execute_tool_traced
from api.storage import (
    _persist_job_status, _get_job_status, _patch_invocable,
    _save_finding, _patch_finding, _upload_to_blob, _download_blob,
    _append_transcript, _append_executor_trace, _append_explore_probe_log,
    _register_invocables, _merge_invocables, _get_current_invocables,
)
from api.telemetry import _openai_client
from api.pipeline.helpers import (
    _SENTINEL_DEFAULTS, _CAP_PROFILE,
    _MAX_EXPLORE_ROUNDS_PER_FUNCTION, _MAX_TOOL_CALLS_PER_FUNCTION, _MAX_FUNCTIONS_PER_SESSION,
    _calibrate_sentinels, _probe_write_unlock, _infer_param_desc,
    _name_sentinel_candidates,
)
from api.pipeline.vocab import (
    _update_vocabulary, _generate_hypothesis, _backfill_schema_from_synthesis,
    _vocab_block, _uncertainty_score,
)
from api.pipeline.prompts import (
    _build_explore_system_message, _generate_behavioral_spec,
    _synthesize, _generate_confidence_gaps,
)
from api.pipeline.helpers import (
    _GAP_RESOLUTION_ENABLED,
    _INIT_RE,
    _VERSION_FN_RE,
    _WRITE_FN_RE,
    _WRITE_RETRY_BUDGET_BY_CLASS,
    _build_ranked_fallback_probe_args,
    _build_tool_schemas,
    _cancel_requested,
    _classify_result_text,
    _save_stage_context,
    _sentinel_class_from_classification,
    _set_explore_status,
    _snapshot_schema_stage,
    _strip_output_buffer_params,
    _write_policy_precheck,
)
from api.pipeline.s06_gaps.gap_resolution import _attempt_gap_resolution, _run_gap_answer_mini_sessions
from api.pipeline.types import ExploreContext, ExploreRuntime

logger = logging.getLogger("mcp_factory.api")

"""

dest.write_text(HEADER + "".join(body_lines), encoding="utf-8")
probe_lines = len(dest.read_text(encoding="utf-8").splitlines())
print(f"Created explore_probe.py: {probe_lines} lines")
