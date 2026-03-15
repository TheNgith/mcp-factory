"""api/config.py – Shared configuration constants and Azure credential helper.

Extracted from api/main.py to break circular-import chains: sub-modules
(storage, worker, discovery, executor, chat) all import from here; main.py
imports from those sub-modules, so nothing here can import from main.py.
"""

from __future__ import annotations

import os
import platform
import sys
import tempfile
from pathlib import Path

IS_WINDOWS = platform.system() == "Windows"

# ── Config from environment ────────────────────────────────────────────────
STORAGE_ACCOUNT   = os.getenv("AZURE_STORAGE_ACCOUNT", "mcpfactorystore")
OPENAI_ENDPOINT   = os.getenv("AZURE_OPENAI_ENDPOINT", "")
OPENAI_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")
# High-reasoning model used when schema quality is low (generic param names) or after 3+ failures.
# Falls back to OPENAI_DEPLOYMENT when unset so existing deployments are unaffected.
OPENAI_REASONING_DEPLOYMENT = os.getenv("AZURE_OPENAI_REASONING_DEPLOYMENT", OPENAI_DEPLOYMENT)
MANAGED_CLIENT_ID = os.getenv("AZURE_CLIENT_ID", "")   # Managed Identity clientId

# Maximum number of tool definitions sent in a single OpenAI API call.
# When total tools <= OPENAI_MAX_TOOLS the full set is sent with no filtering.
# Default is 60: enough to cover calc (38), notepad (~20), and most DLLs
# without hitting OpenAI's 128-tool hard cap or blowing the context budget.
# Raise via env var for large DLLs (shell32 has 35 TLB methods + registry).
OPENAI_MAX_TOOLS  = int(os.getenv("OPENAI_MAX_TOOLS", "60"))

# ── Windows GUI bridge (optional) ─────────────────────────────────────────
# Set GUI_BRIDGE_URL to the Windows runner VM's bridge address, e.g.:
#   http://<vm-public-ip>:8090
# Set GUI_BRIDGE_SECRET to the same BRIDGE_SECRET configured on the VM.
# If either is absent the pipeline works normally (Linux-only analysis).
GUI_BRIDGE_URL    = os.getenv("GUI_BRIDGE_URL", "").rstrip("/")
GUI_BRIDGE_SECRET = os.getenv("GUI_BRIDGE_SECRET", "")

# ── Pipeline API key guard (optional) ────────────────────────────────────
# Set PIPELINE_API_KEY on the container to require a shared key on every
# request.  Leave unset for open access during local development.
# The UI container forwards X-Pipeline-Key from its own UI_API_KEY secret.
PIPELINE_API_KEY = os.getenv("PIPELINE_API_KEY", "")

# ── App Insights connection string ─────────────────────────────────────────
APPINSIGHTS_CONN = os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING", "")

# ── Blob / queue container names ──────────────────────────────────────────
UPLOAD_CONTAINER   = "uploads"
ARTIFACT_CONTAINER = "artifacts"
ANALYSIS_QUEUE     = "analysis-jobs"

# ── Discovery source directory ─────────────────────────────────────────────
SRC_DISCOVERY_DIR = Path(__file__).parent.parent / "src" / "discovery"

# ── Generation module (P1: MCP SDK server emit) ───────────────────────────
_GEN_DIR = Path(__file__).parent.parent / "src" / "generation"
if str(_GEN_DIR) not in sys.path:
    sys.path.insert(0, str(_GEN_DIR))

# ── Allowed base paths for /api/analyze-path ─────────────────────────────
# Restrict server-side path analysis to safe upload/temp directories so a
# caller cannot read arbitrary container filesystem paths (e.g. /proc/self/environ).
_SAFE_PATH_PREFIXES: tuple[Path, ...] = (
    Path(tempfile.gettempdir()),
    Path("/app"),          # container working directory
    Path("C:/"),           # Windows local runs
    Path("D:/"),
)

# ── Azure credential (Managed Identity in ACA, DefaultAzureCredential locally) ──
from azure.identity import ManagedIdentityCredential, DefaultAzureCredential


def _get_credential():
    if MANAGED_CLIENT_ID:
        return ManagedIdentityCredential(client_id=MANAGED_CLIENT_ID)
    return DefaultAzureCredential()
