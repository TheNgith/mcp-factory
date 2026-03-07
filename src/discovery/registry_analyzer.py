r"""
registry_analyzer.py  –  §1.c Windows Registry inspection
=============================================================
Scans the Windows registry for hints about installed executables, COM classes,
and App Paths, then emits invocables that the MCP generator can consume.

Three scan passes:
  1. App Paths  –  HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths
                   maps short command names (e.g. "notepad.exe") to full paths.
  2. Installed software  –  HKLM\...\Uninstall (both 32- and 64-bit views)
                   extracts DisplayName + InstallLocation for every installed app.
  3. COM class shells  –  HKLM\SOFTWARE\Classes\CLSID
                   for every registered COM server that has an InprocServer32 or
                   LocalServer32 key, emit a launch/dispatch invocable.

Only runs on Windows (uses the built-in `winreg` module).
Returns an empty list on any other platform so the pipeline stays portable.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
import sys as _sys

logger = logging.getLogger(__name__)

# Always bind winreg so static checkers don't report 'possibly unbound'.
# On non-Windows the module is None and every function returns [] early.
if _sys.platform == "win32":
    import winreg  # type: ignore[import]
    _WINREG_AVAILABLE = True
else:
    winreg = None  # type: ignore[assignment]
    _WINREG_AVAILABLE = False


# ── Public API ─────────────────────────────────────────────────────────────

def analyze_registry(hints: str = "") -> list[dict[str, Any]]:
    """
    Scan relevant registry hives and return a list of invocable dicts.

    Args:
        hints: optional free-text filter (case-insensitive substring match on
               the invocable name / description).

    Returns:
        List of invocable dicts in the standard MCP discovery schema.
    """
    if not _WINREG_AVAILABLE:
        logger.info("registry_analyzer: winreg not available (non-Windows). Skipping.")
        return []

    invocables: list[dict[str, Any]] = []

    invocables.extend(_scan_app_paths())
    invocables.extend(_scan_uninstall())
    invocables.extend(_scan_com_classes())

    if hints:
        kw = hints.lower().split()
        invocables = [
            inv for inv in invocables
            if any(
                k in (inv.get("name") or "").lower()
                or k in (inv.get("description") or "").lower()
                for k in kw
            )
        ]

    logger.info(f"registry_analyzer: discovered {len(invocables)} invocables")
    return invocables


# ── Pass 1: App Paths ──────────────────────────────────────────────────────

_APP_PATHS_KEY = (
    r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"
)


def _scan_app_paths() -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for hive, hive_name in [
        (winreg.HKEY_LOCAL_MACHINE, "HKLM"),
        (winreg.HKEY_CURRENT_USER,  "HKCU"),
    ]:
        try:
            key = winreg.OpenKey(hive, _APP_PATHS_KEY)
        except OSError:
            continue

        idx = 0
        while True:
            try:
                sub_name = winreg.EnumKey(key, idx)
            except OSError:
                break
            idx += 1

            try:
                sub = winreg.OpenKey(key, sub_name)
                exe_path, _ = winreg.QueryValueEx(sub, "")
                winreg.CloseKey(sub)
            except OSError:
                continue

            cmd_name = Path(sub_name).stem.lower().replace(" ", "_")
            results.append(_make_invocable(
                name        = cmd_name,
                kind        = "cli",
                description = f"Registered application: {sub_name}  [{hive_name}\\App Paths]",
                exe_path    = str(exe_path).strip('"'),
                source      = f"{hive_name}\\App Paths\\{sub_name}",
                confidence  = "medium",
            ))

        winreg.CloseKey(key)

    return results


# ── Pass 2: Installed software (Uninstall keys) ────────────────────────────

_UNINSTALL_KEYS = [
    r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
    r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
]


def _scan_uninstall() -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    seen: set[str] = set()

    for subkey in _UNINSTALL_KEYS:
        try:
            root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, subkey)
        except OSError:
            continue

        idx = 0
        while True:
            try:
                app_key_name = winreg.EnumKey(root, idx)
            except OSError:
                break
            idx += 1

            try:
                app_key = winreg.OpenKey(root, app_key_name)
                display_name    = _qval(app_key, "DisplayName", "")
                install_loc     = _qval(app_key, "InstallLocation", "")
                display_icon    = _qval(app_key, "DisplayIcon", "")
                winreg.CloseKey(app_key)
            except OSError:
                continue

            if not display_name:
                continue

            # Deduplicate by display name
            key_norm = display_name.strip().lower()
            if key_norm in seen:
                continue
            seen.add(key_norm)

            # Try to derive a useful executable path
            exe_path = ""
            if install_loc:
                loc = Path(install_loc.strip('"'))
                # Look for an EXE that shares the app name
                stem = display_name.split()[0].lower()
                guesses = list(loc.glob(f"{stem}*.exe")) + list(loc.glob("*.exe"))
                if guesses:
                    exe_path = str(guesses[0])
            elif display_icon:
                # DisplayIcon often points to "path\to\app.exe,0"
                icon_part = display_icon.split(",")[0].strip('"')
                if icon_part.lower().endswith(".exe"):
                    exe_path = icon_part

            cmd_name = (
                display_name.lower()
                .split()[0]
                .replace("-", "_")
                .replace(".", "_")
            )
            results.append(_make_invocable(
                name        = cmd_name,
                kind        = "cli",
                description = f"Installed application: {display_name}",
                exe_path    = exe_path,
                source      = f"HKLM\\{subkey}\\{app_key_name}",
                confidence  = "low" if not exe_path else "medium",
            ))

        winreg.CloseKey(root)

    return results


# ── Pass 3: COM class registrations ───────────────────────────────────────

_CLSID_KEY    = r"SOFTWARE\Classes\CLSID"
_MAX_COM_SCAN = 2000   # guard against scanning 50 k+ entries in full pass


def _scan_com_classes() -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []

    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _CLSID_KEY)
    except OSError:
        return results

    idx = 0
    while idx < _MAX_COM_SCAN:
        try:
            clsid = winreg.EnumKey(root, idx)
        except OSError:
            break
        idx += 1

        try:
            clsid_key = winreg.OpenKey(root, clsid)
        except OSError:
            continue

        # Friendly name is the default value of the CLSID key
        friendly = _qval(clsid_key, "", "")

        server_path = ""
        for server_type in ("InprocServer32", "LocalServer32"):
            try:
                sv = winreg.OpenKey(clsid_key, server_type)
                server_path = _qval(sv, "", "")
                winreg.CloseKey(sv)
                if server_path:
                    break
            except OSError:
                pass

        winreg.CloseKey(clsid_key)

        if not server_path:
            continue  # no server path means not directly invocable

        # Skip system noise: entries without a readable name or pointing only
        # to system DLLs we already analyze via PE export scanning
        if not friendly:
            continue
        server_lower = server_path.lower()
        if any(skip in server_lower for skip in (
            "mscoree.dll", "msvbvm", "vbscript.dll",
        )):
            continue

        cmd_name = (
            friendly.lower()
            .replace(" ", "_")
            .replace(".", "_")
            .replace("-", "_")
            .replace("(", "")
            .replace(")", "")
        )[:64]

        results.append({
            "name":        cmd_name,
            "kind":        "com_dispatch",
            "confidence":  "medium",
            "tier":        2,
            "description": f"COM class: {friendly}  [{clsid}]",
            "return_type": "unknown",
            "parameters":  [],
            "signature":   f"{friendly} ({clsid})",
            "execution": {
                "method":        "com_dispatch",
                "clsid":         clsid,
                "server_path":   server_path,
                "friendly_name": friendly,
            },
            "metadata": {
                "source":    f"HKLM\\{_CLSID_KEY}\\{clsid}",
                "clsid":     clsid,
                "com_class": friendly,
            },
        })

    winreg.CloseKey(root)
    return results


# ── Helpers ────────────────────────────────────────────────────────────────

def _qval(key, name: str, default: str) -> str:
    """QueryValueEx with a safe fallback."""
    try:
        val, _ = winreg.QueryValueEx(key, name)
        return str(val) if val else default
    except OSError:
        return default


def _make_invocable(
    *,
    name: str,
    kind: str,
    description: str,
    exe_path: str,
    source: str,
    confidence: str,
) -> dict[str, Any]:
    return {
        "name":        name,
        "kind":        kind,
        "confidence":  confidence,
        "tier":        2 if confidence == "medium" else 3,
        "description": description,
        "return_type": "unknown",
        "parameters":  [],
        "signature":   name,
        "execution": {
            "method":          "cli",
            "executable_path": exe_path,
        },
        "metadata": {
            "source":   source,
            "exe_path": exe_path,
        },
    }
