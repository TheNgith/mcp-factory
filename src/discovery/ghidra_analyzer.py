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
import re
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

# Ghidra / C type strings → JSON Schema type strings.
# "undefined*" and pointer types are treated as strings so the LLM can pass
# numeric ids, hex addresses, or string values without the schema rejecting them.
_GHIDRA_TYPE_TO_JSON: dict[str, str] = {
    "int":        "integer",
    "uint":       "integer",
    "long":       "integer",
    "ulong":      "integer",
    "longlong":   "integer",
    "ulonglong":  "integer",
    "short":      "integer",
    "ushort":     "integer",
    "char":       "integer",
    "uchar":      "integer",
    "bool":       "boolean",
    "float":      "number",
    "double":     "number",
    "void":       "string",
}


def _ghidra_type_to_json_schema(ghidra_type: str) -> str:
    """Convert a Ghidra/C type string to a JSON Schema primitive type.

    Falls back to "string" for pointers, undefined*, and anything unknown so
    the LLM can always pass a serialised value the executor can handle.
    """
    t = ghidra_type.lower().strip()
    # Strip pointer stars and const so we check the base type
    base = t.replace("*", "").replace("const", "").strip()
    return _GHIDRA_TYPE_TO_JSON.get(base, "string")


def _params_from_signature(sig: str) -> list[dict]:
    """Parse parameter types/names from a C-style Ghidra prototype string.

    Handles signatures like:
        int __stdcall CS_Foo(undefined8 param1, float * param2, int param3)

    Returns a list of {"name", "type", "ordinal"} dicts, or [] if unparseable.
    This is used as a last-resort fallback when neither DecompInterface nor
    getParameters() returned any parameters.
    """
    m = re.search(r'\(([^)]*)\)', sig)
    if not m:
        return []
    params_str = m.group(1).strip()
    if not params_str or params_str.lower() == "void":
        return []
    result = []
    for i, part in enumerate(params_str.split(",")):
        part = part.strip()
        if not part:
            continue
        tokens = part.split()
        if len(tokens) >= 2:
            # Last token is the parameter name; preceding tokens are the type
            raw_name = tokens[-1]
            ptype    = " ".join(tokens[:-1])
            # If the name starts with '*', it's a pointer — move stars to type
            stars    = len(raw_name) - len(raw_name.lstrip("*"))
            name     = raw_name.lstrip("*") or f"param_{i}"
            if stars:
                ptype += " " + ("*" * stars)
        else:
            name  = f"param_{i}"
            ptype = tokens[0] if tokens else "undefined"
        result.append({"name": name, "type": ptype, "ordinal": i})
    return result


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
    # IMPORTANT: .resolve() converts 8.3 short names (e.g. AZUREU~1) to their
    # full long-path equivalents.  Without it, Python creates the file at the
    # short path, passes that string to Ghidra's JVM, the JVM canonicalises
    # the path internally and writes the JSON to the long-path location, then
    # Python reads back the original short-path file which is still empty —
    # producing the misleading "Expecting value: line 1 column 1 (char 0)" error.
    _out_fd, _out_json_str = tempfile.mkstemp(suffix=".json", prefix="ghidra_out_")
    os.close(_out_fd)
    out_json = Path(_out_json_str).resolve()

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
        # Always log at least the tail of stdout — ExtractFunctions.py's
        # print() calls land here and are the only way to see Jython errors
        # when analyzeHeadless exits 0 but the postScript crashed silently.
        _stdout_tail = (result.stdout or "").strip()[-3000:]
        _stderr_tail = (result.stderr or "").strip()[-1000:]
        if result.returncode != 0:
            logger.warning(
                "ghidra_analyzer: analyzeHeadless exited %d\nstdout: %s\nstderr: %s",
                result.returncode, _stdout_tail, _stderr_tail,
            )
        else:
            logger.info("ghidra_analyzer: analyzeHeadless completed for %s", binary.name)
            if _stdout_tail:
                logger.info("ghidra_analyzer: headless stdout:\n%s", _stdout_tail)
            if _stderr_tail:
                logger.warning("ghidra_analyzer: headless stderr:\n%s", _stderr_tail)
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
        content = out_json.read_text(encoding="utf-8").strip()
        if not content:
            # File exists but is empty — most likely the JVM resolved the temp path
            # to a different long-path location and wrote the JSON there while Python
            # is reading the original short-path file (still 0 bytes from mkstemp).
            # This is caught upstream by the .resolve() fix; this guard is a safety net.
            logger.error(
                "ghidra_analyzer: output JSON is empty — ExtractFunctions.py postScript "
                "may not have executed. Verify -scriptPath is correct and Java can write "
                "to the temp directory. Path used: %s",
                out_json,
            )
            return []
        data = json.loads(content)
    except Exception as exc:
        logger.error("ghidra_analyzer: failed to parse output JSON: %s", exc)
        return []

    # Surface any Jython-level error the script caught and embedded in the JSON.
    if "error" in data:
        logger.error("ghidra_analyzer: ExtractFunctions.py reported error: %s", data["error"])

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

        raw_params = fn.get("parameters", [])

        # Last-resort fallback: if DecompInterface and getParameters() both
        # returned nothing, try to parse the prototype string Ghidra built
        # (getPrototypeString).  This typically looks like:
        #   int __stdcall CS_Foo(undefined8 param1, undefined8 param2)
        # and at least tells us how many parameters the function takes.
        if not raw_params:
            sig = fn.get("signature", "")
            if sig:
                parsed = _params_from_signature(sig)
                if parsed:
                    logger.debug(
                        "ghidra_analyzer: parsed %d params from signature for %s: %s",
                        len(parsed), name, sig,
                    )
                    raw_params = parsed

        for p in raw_params:
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
                "json_type":   _ghidra_type_to_json_schema(ptype),
                "description": f"Parameter recovered by Ghidra decompiler (type: {ptype})",
                "direction":   "out" if is_out else "in",
            })

        ret_type = fn.get("return_type", "int")

        # Confidence: exported functions are high, internal ones are medium
        confidence = "high" if fn.get("is_exported") else "medium"

        # Source tag so the UI can show "recovered by Ghidra"
        source_tag = "ghidra_export" if fn.get("is_exported") else "ghidra_internal"

        # G-5: include decompiled C body in doc so explore agent sees parameter
        # semantics, error code conditions, and local variable names without probing.
        _decompiled_c = fn.get("decompiled_c", "")
        _base_doc = (
            f"Recovered by Ghidra static analysis. "
            f"Address: {fn.get('address', '?')}. "
            f"Calling convention: {cc_ghidra}."
        )
        _doc = (_base_doc + "\n\nDecompiled C:\n" + _decompiled_c) if _decompiled_c else _base_doc

        invocables.append({
            "name":        name,
            "source_type": source_tag,
            "signature":   fn.get("signature", name),
            "confidence":  confidence,
            "dll_path":    str(binary_path),
            "doc_comment": _doc,
            "doc":         _doc,
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
