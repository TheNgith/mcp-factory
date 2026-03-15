"""
ghidra_analyzer.py - Recover function signatures from stripped binaries via Ghidra headless.

This analyzer is the "last resort" fallback for .dll and .exe files where
every other analyzer (TLB, CLI, RPC, .NET exports) yields zero invocables.
It invokes Ghidra's analyzeHeadless CLI, lets Ghidra auto-analyze the binary,
then runs ExtractFunctions.py (Jython) to dump all recovered functions to JSON.

Requirements (on the Windows VM):
  - GHIDRA_HOME env var pointing to the Ghidra installation directory
    (set by _vm_install_ghidra.ps1, e.g.  C:\\ghidra)
  - Java 17+ on PATH (installed by the same script)

Timeout:
  Default 180 s — enough for most DLLs under 10 MB.  Large binaries (> 50 MB)
  may need more time; callers can pass timeout_s explicitly.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Path to our Jython extraction script, co-located in ghidra_scripts/
_SCRIPT_DIR  = Path(__file__).parent / "ghidra_scripts"
_SCRIPT_NAME = "ExtractFunctions.py"

# Calling-convention strings Ghidra emits → ctypes convention names
_CC_MAP: dict[str, str] = {
    "__cdecl":    "cdecl",
    "__stdcall":  "stdcall",
    "__fastcall": "fastcall",
    "__thiscall": "cdecl",     # closest Python ctypes approximation
    "unknown":    "cdecl",     # safe default for x86
    "default":    "cdecl",
}


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def analyze_with_ghidra(
    binary_path: Path,
    timeout_s: int = 180,
    exported_only: bool = False,
) -> list[dict[str, Any]]:
    """Run Ghidra headless on *binary_path* and return a list of invocable dicts.

    Args:
        binary_path:   Absolute path to the .dll or .exe to analyze.
        timeout_s:     Maximum seconds to wait for analyzeHeadless.
        exported_only: If True, only return functions that appear in the PE
                       export table.  If False (default), return all recovered
                       functions — useful for showing internal helpers Ghidra
                       found that are NOT in the export table.

    Returns:
        List of invocable dicts in the standard gui_bridge format.
        Empty list if Ghidra is not installed or analysis fails.
    """
    headless = _find_headless()
    if headless is None:
        logger.warning(
            "Ghidra not found — set GHIDRA_HOME or install via scripts/_vm_install_ghidra.ps1"
        )
        return []

    binary_path = Path(binary_path).resolve()
    if not binary_path.exists():
        logger.error("ghidra_analyzer: binary not found: %s", binary_path)
        return []

    # Resolve proj_dir to its canonical long-path form.  On some Windows builds
    # tempfile.mkdtemp() returns an 8.3 short path (e.g. C:\Users\AZUREU~1\...) while
    # Ghidra's JVM expands it to the full long path before writing files — so the
    # output JSON lands at a path that Python's os.path.exists() never finds.
    proj_dir  = Path(tempfile.mkdtemp(prefix="ghidra_proj_")).resolve()
    proj_name = "analysis"

    # Write output JSON to a sibling temp file OUTSIDE the Ghidra project dir.
    # This avoids any risk of -deleteProject wiping the file before we read it,
    # and eliminates path-canonicalization races on 8.3 short-name accounts.
    _out_fd, _out_json_str = tempfile.mkstemp(suffix=".json", prefix="ghidra_out_")
    os.close(_out_fd)
    out_json = Path(_out_json_str)

    try:
        _run_headless(headless, binary_path, proj_dir, proj_name, out_json, timeout_s)
        return _parse_output(out_json, binary_path, exported_only)
    finally:
        try:
            shutil.rmtree(proj_dir, ignore_errors=True)
        except Exception:
            pass
        try:
            out_json.unlink(missing_ok=True)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _find_headless() -> Path | None:
    """Locate analyzeHeadless.bat from GHIDRA_HOME or common install paths."""
    candidates: list[Path] = []

    ghidra_home = os.environ.get("GHIDRA_HOME", "")
    if ghidra_home:
        candidates.append(Path(ghidra_home) / "support" / "analyzeHeadless.bat")

    # Common default install locations
    for base in [Path("C:/ghidra"), Path("C:/Program Files/Ghidra")]:
        candidates.append(base / "support" / "analyzeHeadless.bat")

    # Also search C:\ for any ghidra_*_PUBLIC directory
    try:
        for d in Path("C:/").iterdir():
            if d.is_dir() and d.name.lower().startswith("ghidra_"):
                candidates.append(d / "support" / "analyzeHeadless.bat")
    except Exception:
        pass

    for c in candidates:
        if c.exists():
            logger.info("ghidra_analyzer: found analyzeHeadless at %s", c)
            return c

    return None


def _run_headless(
    headless: Path,
    binary: Path,
    proj_dir: Path,
    proj_name: str,
    out_json: Path,
    timeout_s: int,
) -> None:
    """Invoke analyzeHeadless and block until it finishes or times out."""
    script_path = str(_SCRIPT_DIR)
    cmd = [
        str(headless),
        str(proj_dir),
        proj_name,
        "-import",   str(binary),
        "-postScript", _SCRIPT_NAME, str(out_json),
        "-scriptPath", script_path,
        "-deleteProject",
        "-log",      str(proj_dir / "ghidra.log"),
    ]

    logger.info("ghidra_analyzer: running analyzeHeadless on %s (timeout=%ds)",
                binary.name, timeout_s)
    logger.debug("ghidra_analyzer: cmd = %s", " ".join(cmd))

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            cwd=str(proj_dir),
        )
        if result.returncode != 0:
            logger.warning(
                "ghidra_analyzer: analyzeHeadless exited %d\nstdout: %s\nstderr: %s",
                result.returncode,
                result.stdout[-2000:] if result.stdout else "",
                result.stderr[-2000:] if result.stderr else "",
            )
        else:
            logger.info("ghidra_analyzer: analyzeHeadless completed for %s", binary.name)
    except subprocess.TimeoutExpired:
        logger.error(
            "ghidra_analyzer: analyzeHeadless timed out after %ds for %s",
            timeout_s, binary.name,
        )


def _parse_output(
    out_json: Path,
    binary_path: Path,
    exported_only: bool,
) -> list[dict[str, Any]]:
    """Parse the JSON written by ExtractFunctions.py into invocable dicts."""
    if not out_json.exists():
        logger.error("ghidra_analyzer: output JSON not found: %s", out_json)
        return []

    try:
        with open(out_json, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception as exc:
        logger.error("ghidra_analyzer: failed to parse output JSON: %s", exc)
        return []

    functions = data.get("functions", [])
    invocables: list[dict[str, Any]] = []

    for fn in functions:
        name = fn.get("name", "")
        if not name or name.startswith("FUN_"):
            # FUN_xxxxxxxx = Ghidra placeholder for unnamed function — skip unless exported
            if not fn.get("is_exported"):
                continue

        if exported_only and not fn.get("is_exported"):
            continue

        # Map Ghidra CC string to ctypes convention
        cc_ghidra = fn.get("calling_convention", "unknown")
        cc = _CC_MAP.get(cc_ghidra, "cdecl")

        # Build parameter list in standard schema format
        parameters: list[dict] = []
        for p in fn.get("parameters", []):
            ptype    = p.get("type", "int")
            ptype_lc = ptype.lower()
            # Non-const pointer parameters are output buffers / out-params in Win32 C APIs.
            # Tagging them lets the executor allocate the correct ctypes object automatically.
            is_out = (
                "*" in ptype_lc
                and "const " not in ptype_lc
                and not ptype_lc.strip().startswith("const")
            )
            parameters.append({
                "name":        p.get("name", f"param_{p.get('ordinal', 0)}"),
                "type":        ptype,
                "description": f"Parameter recovered by Ghidra decompiler (type: {ptype})",
                "direction":   "out" if is_out else "in",
            })

        ret_type = fn.get("return_type", "int")

        # Confidence: exported functions are high, internal ones are medium
        confidence = "high" if fn.get("is_exported") else "medium"

        # Source tag so the UI can show "recovered by Ghidra"
        source_tag = "ghidra_export" if fn.get("is_exported") else "ghidra_internal"

        invocables.append({
            "name":        name,
            "source_type": source_tag,
            "signature":   fn.get("signature", name),
            "confidence":  confidence,
            "dll_path":    str(binary_path),
            "doc_comment": (
                f"Recovered by Ghidra static analysis. "
                f"Address: {fn.get('address', '?')}. "
                f"Calling convention: {cc_ghidra}."
            ),
            "parameters":  parameters,
            "return_type": ret_type,
            "execution": {
                "method":             "dll_import",
                "dll_path":           str(binary_path),
                "function_name":      name,
                "calling_convention": cc,
                "arg_types":          [p.get("type", "int") for p in fn.get("parameters", [])],
                "return_type":        ret_type,
            },
        })

    exported = sum(1 for i in invocables if "export" in i["source_type"])
    internal = len(invocables) - exported
    logger.info(
        "ghidra_analyzer: %d invocables from %s (%d exported, %d internal)",
        len(invocables), binary_path.name, exported, internal,
    )
    return invocables
