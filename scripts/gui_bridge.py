"""
gui_bridge.py — Windows-only analysis bridge
=============================================
Runs on the self-hosted Windows runner VM (or any Windows machine with
pywinauto + pywin32 installed).  Exposes a small FastAPI HTTP server that
the Linux ACA pipeline can call to perform analysers that require Windows:

  • GUI  (pywinauto UIA tree walk)
  • COM / Type Library  (pythoncom / pywin32)
  • CLI  (run Windows EXEs for --help output)
  • Registry scan  (winreg HKLM App Paths / Uninstall / COM CLSIDs)

Authentication: every request must carry  X-Bridge-Key: <BRIDGE_SECRET>
(set the BRIDGE_SECRET env var before starting this server).

Usage (on the Windows VM):
    set BRIDGE_SECRET=<a long random string>
    python scripts/gui_bridge.py          # listens on 0.0.0.0:8090

The ACA pipeline reads GUI_BRIDGE_URL (e.g. http://<vm-ip>:8090) and
GUI_BRIDGE_SECRET from its environment / Key Vault secrets and calls
POST /analyze with:
    {
      "path":   "C:\\Windows\\System32\\calc.exe",   # or uploaded temp path
      "hints":  "calculator",
      "types":  ["gui", "com", "cli", "registry"]    # optional filter
    }

Returns standard discovery JSON  { "invocables": [...] }.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import sys
import tempfile
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# ── ensure the discovery package is importable ───────────────────────────────
_ROOT = Path(__file__).parent.parent
_DISCOVERY = _ROOT / "src" / "discovery"
for _p in [str(_ROOT), str(_DISCOVERY)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("gui_bridge")

# ── Auth ─────────────────────────────────────────────────────────────────────
BRIDGE_SECRET = os.getenv("BRIDGE_SECRET", "")
if not BRIDGE_SECRET:
    logger.warning(
        "BRIDGE_SECRET env var is not set — "
        "the bridge will reject ALL requests.  "
        "Set it before starting the server."
    )

app = FastAPI(title="MCP Factory GUI Bridge", version="1.0.0")


def _check_auth(x_bridge_key: str) -> None:
    """Constant-time secret comparison — raises 401 on mismatch."""
    if not BRIDGE_SECRET or not secrets.compare_digest(x_bridge_key, BRIDGE_SECRET):
        raise HTTPException(status_code=401, detail="Invalid or missing X-Bridge-Key")


# ── Request / response models ─────────────────────────────────────────────────
class AnalyzeRequest(BaseModel):
    path: str
    hints: str = ""
    types: list[str] = ["gui", "com", "cli", "registry"]


# ── Lazy analyzer imports (all Windows-only) ─────────────────────────────────
def _import_gui():
    from gui_analyzer import analyze_gui  # type: ignore
    return analyze_gui

def _import_com():
    from com_scan import scan_com_registry, com_objects_to_invocables  # type: ignore
    return scan_com_registry, com_objects_to_invocables

def _import_tlb():
    from tlb_analyzer import scan_type_library  # type: ignore
    return scan_type_library

def _import_cli():
    from cli_analyzer import analyze_cli  # type: ignore
    return analyze_cli

def _import_registry():
    from registry_analyzer import analyze_registry  # type: ignore
    return analyze_registry


def _inv_to_dict(inv: Any) -> dict:
    """Convert an Invocable dataclass (or dict) to a plain dict."""
    if isinstance(inv, dict):
        return inv
    return {k: v for k, v in vars(inv).items() if not k.startswith("_")}


# ── Main endpoint ─────────────────────────────────────────────────────────────
@app.post("/analyze")
async def analyze(
    body: AnalyzeRequest,
    x_bridge_key: str = Header(default=""),
):
    _check_auth(x_bridge_key)

    target = Path(body.path)
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"Path not found: {body.path}")

    requested = set(body.types)
    invocables: list[dict] = []
    errors: dict[str, str] = {}

    # ── GUI ──────────────────────────────────────────────────────────────────
    if "gui" in requested and target.suffix.lower() == ".exe":
        try:
            analyze_gui = _import_gui()
            results = analyze_gui(target)
            invocables.extend(_inv_to_dict(i) for i in results)
            logger.info("GUI: %d invocables from %s", len(results), target.name)
        except Exception as exc:
            logger.warning("GUI analysis failed: %s", exc)
            errors["gui"] = str(exc)

    # ── COM / Type Library ───────────────────────────────────────────────────
    if "com" in requested:
        try:
            scan_type_library = _import_tlb()
            tlb_results = scan_type_library(target)
            # tlb_analyzer returns raw dicts — convert to Invocable-style dicts
            for entry in tlb_results:
                for method in entry.get("methods", []):
                    invocables.append({
                        "name":        method.get("name", "unknown"),
                        "source_type": "com",
                        "signature":   method.get("signature", method.get("name", "")),
                        "confidence":  "high",
                        "dll_path":    str(target),
                        "doc_comment": method.get("doc", ""),
                        "parameters":  method.get("parameters", []),
                        "return_type": method.get("return_type", ""),
                        "execution": {
                            "method":   "com_invoke",
                            "dll_path": str(target),
                            "interface": entry.get("name", ""),
                            "member":   method.get("name", ""),
                        },
                    })
            logger.info("COM/TLB: %d methods from %s", len(tlb_results), target.name)
        except Exception as exc:
            logger.warning("COM/TLB analysis failed: %s", exc)
            errors["com"] = str(exc)

    # ── CLI ──────────────────────────────────────────────────────────────────
    if "cli" in requested and target.suffix.lower() == ".exe":
        try:
            analyze_cli = _import_cli()
            results = analyze_cli(target)
            invocables.extend(_inv_to_dict(i) for i in results)
            logger.info("CLI: %d invocables from %s", len(results), target.name)
        except Exception as exc:
            logger.warning("CLI analysis failed: %s", exc)
            errors["cli"] = str(exc)

    # ── Registry ─────────────────────────────────────────────────────────────
    if "registry" in requested:
        try:
            analyze_registry = _import_registry()
            results = analyze_registry(target)
            invocables.extend(_inv_to_dict(i) for i in results)
            logger.info("Registry: %d invocables", len(results))
        except Exception as exc:
            logger.warning("Registry analysis failed: %s", exc)
            errors["registry"] = str(exc)

    # De-duplicate by name (same function may appear in multiple analyzers)
    seen: set[str] = set()
    unique: list[dict] = []
    for inv in invocables:
        name = inv.get("name", "")
        if name and name not in seen:
            seen.add(name)
            unique.append(inv)

    return JSONResponse({
        "invocables": unique,
        "count":      len(unique),
        "errors":     errors,
        "source":     str(target),
    })


@app.get("/health")
async def health():
    return {"status": "ok", "platform": "windows"}


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.getenv("BRIDGE_PORT", "8090"))
    logger.info("Starting GUI bridge on 0.0.0.0:%d", port)
    uvicorn.run(app, host="0.0.0.0", port=port)
