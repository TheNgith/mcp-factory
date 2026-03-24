"""One-time extraction: split session_snapshot + get_report out of main.py into routes_session.py."""
import pathlib, re

repo = pathlib.Path(__file__).parent.parent

src = repo / "api" / "main.py"
dest = repo / "api" / "routes_session.py"

lines = src.read_text(encoding="utf-8").splitlines(True)

# session_snapshot decorator starts at line 864 (1-indexed) = index 863 (0-indexed)
# Extract from the blank line just before the decorator through EOF
EXTRACT_START = 862  # index of the blank line before the @app.get decorator (0-indexed)
body_lines = lines[EXTRACT_START:]

# Replace @app.get/@app.post with @router.get/@router.post in the extracted content
body_text = "".join(body_lines)
body_text = re.sub(r"@app\.(get|post|put|delete|patch)\b", r"@router.\1", body_text)

HEADER = """\
\"\"\"api/routes_session.py – Session-snapshot and report endpoints.

Extracted from main.py so the main app router stays focused on job/explore
dispatch while the large session-artefact assembly logic lives here.

Registered in main.py via:
    from api.routes_session import router as session_router
    app.include_router(session_router)
\"\"\"
from __future__ import annotations

import json
import time
from typing import Any

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from api.config import ARTIFACT_CONTAINER
from api.storage import (
    _download_blob,
    _get_job_status,
    _upload_to_blob,
    _load_findings,
)
from api.pipeline.helpers import _infer_param_desc

import logging
logger = logging.getLogger("mcp_factory.api")

router = APIRouter()

"""

dest.write_text(HEADER + body_text, encoding="utf-8")
route_lines = len(dest.read_text(encoding="utf-8").splitlines())
print(f"Created routes_session.py: {route_lines} lines")

# Trim main.py: remove lines from EXTRACT_START onward and add include_router
remaining = lines[:EXTRACT_START]
remaining.append(
    "\n\n# Session-snapshot and report endpoints live in routes_session.py\n"
    "from api.routes_session import router as session_router  # noqa: E402\n"
    "app.include_router(session_router)\n"
)
src.write_text("".join(remaining), encoding="utf-8")
main_lines = len(src.read_text(encoding="utf-8").splitlines())
print(f"main.py trimmed to: {main_lines} lines")
