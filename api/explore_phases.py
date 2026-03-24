"""DEPRECATED: This module has been split across api.pipeline.

  Constants -> api.pipeline.helpers
  Calibration -> api.pipeline.s00_setup.calibration
  Write-unlock -> api.pipeline.s01_unlock.write_unlock
  _infer_param_desc -> api.pipeline.helpers

This shim exists only for backward compatibility during the transition.
"""
from api.pipeline.helpers import (  # noqa: F401
    _CAP_PROFILE, _CAP_PROFILES,
    _MAX_EXPLORE_ROUNDS_PER_FUNCTION, _MAX_TOOL_CALLS_PER_FUNCTION,
    _MAX_FUNCTIONS_PER_SESSION, _SENTINEL_DEFAULTS, _infer_param_desc,
)
from api.pipeline.s00_setup.calibration import (  # noqa: F401
    _calibrate_sentinels, _name_sentinel_candidates, _parse_hint_error_codes,
)
from api.pipeline.s01_unlock.write_unlock import (  # noqa: F401
    _probe_write_unlock, _generate_xor_codes,
)
