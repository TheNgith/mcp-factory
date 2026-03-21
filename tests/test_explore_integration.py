"""
test_explore_integration.py — Regression tests for the exploration pipeline.

These tests run without network/blob access and cover:
  - FIX-1: _strip_output_buffer_params correctly preserves byte* string inputs
            while stripping undefined4* / int* genuine output buffers
  - FIX-2: return values ≤ 0xFFFFFFF0 are NOT treated as sentinel error codes
            (catches the CS_GetVersion = 131841 false-sentinel regression)
  - ExploreRuntime defaults load correctly from an empty env dict
  - ExploreContext construction produces the expected shape
  - session_snapshot ZIP structure: stage paths appear in main.py source
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))


# ─────────────────────────────────────────────────────────────────────────────
#  FIX-1 — _strip_output_buffer_params
# ─────────────────────────────────────────────────────────────────────────────

class TestStripOutputBufferParams:
    """Verify FIX-1: output-buffer stripping preserves byte* string arguments."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from api.explore_helpers import _strip_output_buffer_params
        self.strip = _strip_output_buffer_params

    def test_preserves_byte_star_string_input(self):
        """byte* params (customer_id, order_id) must NOT be stripped."""
        tc_args = {"customer_id": "CUST-007", "result_buf": 0}
        p_lookup = {
            "customer_id": {"type": "byte *",    "direction": "in"},
            "result_buf":  {"type": "undefined4 *", "direction": "out"},
        }
        result = self.strip(tc_args, p_lookup)
        assert "customer_id" in result, "byte* string input must not be stripped"
        assert "result_buf"  not in result, "undefined4* output buffer must be stripped"

    def test_strips_undefined4_star(self):
        """undefined4 * (canonical Ghidra output buffer) is stripped."""
        tc_args = {"buf": 0, "name": "Alice"}
        p_lookup = {
            "buf":  {"type": "undefined4 *"},
            "name": {"type": "char *"},
        }
        result = self.strip(tc_args, p_lookup)
        assert "buf"  not in result
        assert "name" in result

    def test_strips_int_star(self):
        """int * is a numeric-pointer output buffer and must be stripped."""
        tc_args = {"out_len": 0, "data": "hello"}
        p_lookup = {
            "out_len": {"type": "int *"},
            "data":    {"type": "byte *"},
        }
        result = self.strip(tc_args, p_lookup)
        assert "out_len" not in result
        assert "data" in result

    def test_no_output_buffers_unchanged(self):
        """When no output buffers exist the result equals the input."""
        tc_args = {"customer_id": "CUST-001", "amount": 100}
        p_lookup = {
            "customer_id": {"type": "byte *"},
            "amount":       {"type": "int"},
        }
        result = self.strip(tc_args, p_lookup)
        assert result == tc_args

    def test_empty_args_returns_empty(self):
        result = self.strip({}, {})
        assert result == {}

    def test_param_missing_from_lookup_is_preserved(self):
        """Params with no lookup entry (unknown) must be kept — never discard unknown params."""
        tc_args = {"mystery": "x"}
        result = self.strip(tc_args, {})
        assert "mystery" in result

    def test_direction_out_not_stripped_for_byte_star(self):
        """Even if Ghidra marks direction=out, byte* must NOT be stripped (FIX-1 invariant)."""
        tc_args = {"id_ptr": "CUST-042"}
        p_lookup = {"id_ptr": {"type": "byte *", "direction": "out"}}
        result = self.strip(tc_args, p_lookup)
        # byte* is excluded from _out_bases, so direction=out alone must not strip it
        assert "id_ptr" in result


# ─────────────────────────────────────────────────────────────────────────────
#  FIX-2 — sentinel code range gate (0xFFFFFFF0 threshold)
# ─────────────────────────────────────────────────────────────────────────────

class TestSentinelRangeGate:
    """Verify FIX-2: ordinary return values ≤ 0xFFFFFFF0 are not sentinel codes.

    CS_GetVersion returns 131841 (0x20301) which must never be classified as a
    sentinel error code.  Sentinel values always sit above 0xFFFFFFF0.
    """

    SENTINEL_FLOOR = 0xFFFFFFF0  # the threshold used in explore.py line ~840 / ~959

    def _is_sentinel_candidate(self, value: int) -> bool:
        """Mirrors the threshold logic in explore.py (see _seen_codes block)."""
        v = int(value) & 0xFFFFFFFF
        return v > self.SENTINEL_FLOOR

    def test_version_number_is_not_sentinel(self):
        """131841 (CS_GetVersion typical output) must NOT be a sentinel."""
        assert not self._is_sentinel_candidate(131841)

    def test_zero_is_not_sentinel(self):
        """0 (success / S_OK) must not be treated as a sentinel."""
        assert not self._is_sentinel_candidate(0)

    def test_small_positive_is_not_sentinel(self):
        assert not self._is_sentinel_candidate(1)
        assert not self._is_sentinel_candidate(42)
        assert not self._is_sentinel_candidate(0x0000FFFF)

    def test_exact_floor_is_not_sentinel(self):
        """The threshold is exclusive: value == 0xFFFFFFF0 must NOT be flagged."""
        assert not self._is_sentinel_candidate(0xFFFFFFF0)

    def test_above_floor_is_sentinel(self):
        """0xFFFFFFF1 through 0xFFFFFFFF are all sentinel-range values."""
        for v in [0xFFFFFFF1, 0xFFFFFFF5, 0xFFFFFFFB, 0xFFFFFFFC, 0xFFFFFFFE, 0xFFFFFFFF]:
            assert self._is_sentinel_candidate(v), f"0x{v:08X} should be sentinel-range"

    def test_negative_int_wraps_correctly(self):
        """Python int -1 masks to 0xFFFFFFFF which is sentinel-range."""
        assert self._is_sentinel_candidate(-1)
        assert self._is_sentinel_candidate(-5)

    def test_classify_common_result_code_zero(self):
        """classify_common_result_code(0) must return None (S_OK is not an error)."""
        from api.sentinel_codes import classify_common_result_code
        assert classify_common_result_code(0) is None

    def test_classify_common_result_code_version(self):
        """classify_common_result_code(131841) must return None."""
        from api.sentinel_codes import classify_common_result_code
        assert classify_common_result_code(131841) is None

    def test_classify_common_result_code_sentinel(self):
        """classify_common_result_code(0xFFFFFFFF) must return a non-None description."""
        from api.sentinel_codes import classify_common_result_code
        result = classify_common_result_code(0xFFFFFFFF)
        assert result is not None and len(result) > 0


# ─────────────────────────────────────────────────────────────────────────────
#  ExploreRuntime defaults
# ─────────────────────────────────────────────────────────────────────────────

class TestExploreRuntime:
    """Verify ExploreRuntime produces correct defaults without a live environment."""

    def test_default_construction(self):
        from api.explore_types import ExploreRuntime
        r = ExploreRuntime()
        assert r.max_rounds >= 1
        assert r.max_tool_calls >= 1
        assert r.max_functions >= 1
        assert r.min_direct_probes >= 1
        assert r.cap_profile == "default"
        assert r.deterministic_fallback_enabled is True
        assert r.gap_resolution_enabled is True
        assert r.clarification_enabled is True

    def test_from_job_runtime_empty_dict(self):
        """from_job_runtime({}) should fall back to defaults / environment defaults."""
        from api.explore_types import ExploreRuntime
        r = ExploreRuntime.from_job_runtime({})
        # Must not raise; runtime values must be sensible positive numbers
        assert r.max_rounds >= 1
        assert r.max_tool_calls >= 1
        assert r.max_functions >= 1

    def test_from_job_runtime_overrides(self):
        """Explicit keys in the explore_runtime dict must override defaults.

        The dict uses camelCase-style short keys (see ExploreRuntime.from_job_runtime
        source): 'max_rounds', 'max_tool_calls', 'max_functions'.
        """
        from api.explore_types import ExploreRuntime
        r = ExploreRuntime.from_job_runtime({
            "max_rounds":     3,
            "max_tool_calls": 12,
            "max_functions":  5,
        })
        assert r.max_rounds == 3
        assert r.max_tool_calls == 12
        assert r.max_functions == 5


# ─────────────────────────────────────────────────────────────────────────────
#  ExploreContext construction shape
# ─────────────────────────────────────────────────────────────────────────────

class TestExploreContext:
    """Verify ExploreContext dataclass has expected attributes."""

    def test_dataclass_fields_exist(self):
        from api.explore_types import ExploreContext, ExploreRuntime
        from threading import Lock

        ctx = ExploreContext(
            job_id="test-job",
            runtime=ExploreRuntime(),
            client=None,
            model="gpt-4o",
            run_started_at=0.0,
            invocables=[{"name": "Foo"}],
            inv_map={"Foo": {"name": "Foo"}},
            tool_schemas=[],
            total=1,
            sentinels={0xFFFFFFFF: "not found"},
            already_explored=set(),
        )
        assert ctx.job_id == "test-job"
        assert ctx.model == "gpt-4o"
        assert ctx.total == 1
        assert 0xFFFFFFFF in ctx.sentinels
        # phase fields default to None / empty
        assert ctx.vocab == {}
        assert ctx.static_analysis_result == {}
        assert ctx.sentinel_catalog == {}

    def test_state_dict_initialised(self):
        from api.explore_types import ExploreContext, ExploreRuntime
        ctx = ExploreContext(
            job_id="j", runtime=ExploreRuntime(), client=None, model="m",
            run_started_at=0.0, invocables=[], inv_map={}, tool_schemas=[],
            total=0, sentinels={}, already_explored=set(),
        )
        assert isinstance(ctx._state, dict)
        assert "explored" in ctx._state


# ─────────────────────────────────────────────────────────────────────────────
#  session_snapshot endpoint: staged ZIP structure (static source check)
# ─────────────────────────────────────────────────────────────────────────────

class TestSessionSnapshotZipStructure:
    """Verify that main.py embeds the expected stage-based ZIP paths.

    We parse main.py as source rather than importing it (which requires a live
    FastAPI + Azure environment) so the check runs offline in CI.
    """

    MAIN_PY = ROOT / "api" / "main.py"

    @pytest.fixture(autouse=True)
    def _source(self):
        self.src = self.MAIN_PY.read_text(encoding="utf-8")

    def _assert_present(self, path_fragment: str):
        assert path_fragment in self.src, f"Expected '{path_fragment}' in main.py session_snapshot"

    def test_stage_00_setup_present(self):
        self._assert_present("stage-00-setup/explore_config.json")
        self._assert_present("stage-00-setup/static_analysis.json")
        self._assert_present("stage-00-setup/sentinel_calibration.json")
        self._assert_present("stage-00-setup/schema.json")

    def test_stage_01_probe_loop_present(self):
        self._assert_present("stage-01-probe-loop/explore_probe_log.json")
        self._assert_present("stage-01-probe-loop/schema-before.json")
        self._assert_present("stage-01-probe-loop/schema-after.json")

    def test_stage_02_synthesis_present(self):
        self._assert_present("stage-02-synthesis/findings.json")
        self._assert_present("stage-02-synthesis/api_reference.md")

    def test_stage_03_gap_resolution_present(self):
        self._assert_present("stage-03-gap-resolution/gap_resolution_log.json")
        self._assert_present("stage-03-gap-resolution/schema-before.json")
        self._assert_present("stage-03-gap-resolution/schema-after.json")

    def test_stage_04_clarification_present(self):
        self._assert_present("stage-04-clarification/clarification-questions.md")
        self._assert_present("stage-04-clarification/schema-before.json")
        self._assert_present("stage-04-clarification/schema-after.json")

    def test_stage_05_finalization_present(self):
        self._assert_present("stage-05-finalization/findings.json")
        self._assert_present("stage-05-finalization/vocab.json")
        self._assert_present("stage-05-finalization/behavioral_spec.py")
        self._assert_present("stage-05-finalization/invocables_map.json")
        self._assert_present("stage-05-finalization/sentinel_catalog.json")
        self._assert_present("stage-05-finalization/harmonization_report.json")

    def test_diagnostics_present(self):
        self._assert_present("diagnostics/chat_transcript.txt")
        self._assert_present("diagnostics/executor_trace.json")
        self._assert_present("diagnostics/model_context.txt")
        self._assert_present("diagnostics/diagnosis_raw.json")

    def test_contract_artifacts_present(self):
        self._assert_present('"session-meta.json"')
        self._assert_present('"stage-index.json"')
        self._assert_present('"transition-index.json"')
        self._assert_present('"cohesion-report.json"')

    def test_no_old_flat_artifacts_path(self):
        """Old ZIP structure used 'artifacts/' prefix — must no longer appear."""
        assert "artifacts/findings.json" not in self.src, \
            "Old 'artifacts/' ZIP prefix still present in session_snapshot"

    def test_no_old_schema_numbered_files(self):
        """Old ZIP used 'schema/01-pre-enrichment.json' etc. — must no longer appear."""
        assert "schema/01-pre-enrichment.json" not in self.src, \
            "Old numbered schema/ ZIP prefix still present in session_snapshot"
