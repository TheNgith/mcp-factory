"""api/static_analysis.py — Pre-probe static enrichment of uploaded DLL binaries.

Implements:
  G-4  IAT capability injection: what Windows APIs does the DLL import? → read/write classification
  G-7  Binary strings promoted to first-class vocab with confidence:"high"
  G-8  PE Version Info: domain context from the binary's resource section
  G-9  Capstone sentinel harvesting: scan exported function bodies for high-bit constants

Called during Phase 0 of _explore_worker BEFORE any probe calls are made.
Returns a dict that maps directly onto vocab fields and is persisted as
{job_id}/static_analysis.json in blob storage for session-snapshot inclusion.
"""

from __future__ import annotations

import json
import logging
import re as _re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("mcp_factory.api")

# ---------------------------------------------------------------------------
# G-8 — PE Version Info extraction
# ---------------------------------------------------------------------------

def _extract_pe_version_info(pe_obj: Any) -> dict:
    """Parse VS_VERSION_INFO resource block from a loaded pefile.PE object."""
    info: dict[str, str] = {}
    try:
        if not hasattr(pe_obj, "VS_VERSIONINFO"):
            return info
        for vi in pe_obj.VS_VERSIONINFO:
            if not hasattr(vi, "StringFileInfo"):
                continue
            for sfi in vi.StringFileInfo:
                if not hasattr(sfi, "StringTable"):
                    continue
                for st in sfi.StringTable:
                    for k, v in st.entries.items():
                        try:
                            key = k.decode("utf-8", errors="replace").strip()
                            val = v.decode("utf-8", errors="replace").strip()
                            if key and val:
                                info[key] = val
                        except Exception:
                            pass
    except Exception as exc:
        logger.debug("pe_version_info parse error: %s", exc)
    return info


# ---------------------------------------------------------------------------
# G-4 — IAT capability profiling
# ---------------------------------------------------------------------------

# Lightweight capability map — covers the common cases without importing
# the full import_analyzer module (which has heavy logging side effects).
_IAT_CAPABILITY_DLLS: dict[str, str] = {
    "ws2_32.dll":    "networking",
    "wsock32.dll":   "networking",
    "winhttp.dll":   "networking",
    "wininet.dll":   "networking",
    "iphlpapi.dll":  "networking",
    "netapi32.dll":  "networking",
    "rpcrt4.dll":    "rpc",
    "ole32.dll":     "com",
    "oleaut32.dll":  "com",
    "combase.dll":   "com",
    "bcrypt.dll":    "crypto",
    "crypt32.dll":   "crypto",
    "ncrypt.dll":    "crypto",
    "advapi32.dll":  "registry",      # also includes crypto/security
    "secur32.dll":   "security",
    "sspicli.dll":   "security",
    "odbc32.dll":    "database",
    "oledb32.dll":   "database",
    "kernel32.dll":  "filesystem",
    "ntdll.dll":     "syscalls",
}

# Specific function names that sharpen the profile beyond DLL-level hints
_IAT_FUNCTION_CAPS: dict[str, str] = {
    # registry
    "RegOpenKeyExA":   "registry", "RegOpenKeyExW":   "registry",
    "RegQueryValueExA":"registry", "RegQueryValueExW":"registry",
    "RegSetValueExA":  "registry", "RegSetValueExW":  "registry",
    # crypto
    "CryptEncrypt":    "crypto",   "CryptDecrypt":    "crypto",
    "BCryptEncrypt":   "crypto",   "BCryptDecrypt":   "crypto",
    # network
    "WSAStartup":      "networking","HttpSendRequestA":"networking",
    "WinHttpConnect":  "networking",
    # filesystem
    "ReadFile":        "filesystem","WriteFile":       "filesystem",
    "CreateFileA":     "filesystem","CreateFileW":     "filesystem",
    "DeleteFileA":     "filesystem","DeleteFileW":     "filesystem",
    # database
    "SQLConnect":      "database",  "SQLExecDirectA":  "database",
    "SQLExecDirectW":  "database",
}


def _extract_iat_capabilities(pe_obj: Any) -> dict:
    """Return IAT capability profile from a loaded pefile.PE object."""
    caps: dict[str, list[str]] = {}
    raw_dlls: list[str] = []

    try:
        if not hasattr(pe_obj, "DIRECTORY_ENTRY_IMPORT"):
            return {"raw_imports": raw_dlls, "categories": caps}

        for entry in pe_obj.DIRECTORY_ENTRY_IMPORT:
            try:
                dll_name = (entry.dll or b"").decode("utf-8", errors="replace").lower()
            except Exception:
                dll_name = str(entry.dll).lower()

            raw_dlls.append(dll_name)

            # DLL-level capability
            cat = _IAT_CAPABILITY_DLLS.get(dll_name)
            if cat:
                if cat not in caps:
                    caps[cat] = []

            # Function-level sharpening
            try:
                for imp in entry.imports:
                    if imp.name:
                        try:
                            fn = imp.name.decode("utf-8", errors="replace")
                        except Exception:
                            fn = str(imp.name)
                        fn_cat = _IAT_FUNCTION_CAPS.get(fn)
                        if fn_cat:
                            if fn_cat not in caps:
                                caps[fn_cat] = []
                            if fn not in caps[fn_cat]:
                                caps[fn_cat].append(fn)
            except Exception:
                pass

    except Exception as exc:
        logger.debug("iat_extract error: %s", exc)

    return {"raw_imports": raw_dlls, "categories": caps}


# ---------------------------------------------------------------------------
# G-7 — Binary string extraction (promoted to first-class vocab)
# ---------------------------------------------------------------------------

_ID_PATTERN    = _re.compile(r"^[A-Z]{2,6}-[\w-]+$")
_EMAIL_PATTERN = _re.compile(r"^[\w.+-]+@[\w.-]+\.[a-z]{2,}$", _re.I)
# A real printf format string: must contain %[flags?][width?]specifier
# where specifier is one of: d i u f e g s c p x X o n l
_FMT_PATTERN   = _re.compile(r"%[-+0# ]*\d*(?:\.\d+)?[diufegscpxXoln]")
_STATUS_WORDS  = {
    "active", "inactive", "pending", "shipped", "delivered",
    "cancelled", "canceled", "suspended", "complete", "unknown",
    "locked", "unlocked", "enabled", "disabled", "open", "closed",
}


def _extract_binary_strings(data: bytes) -> dict:
    """ASCII-scan the binary and classify strings into vocab-relevant buckets."""
    text  = data.decode("ascii", errors="ignore")
    raw   = sorted(set(
        m.group(0).strip()
        for m in _re.finditer(r"[ -~]{6,}", text)
        if m.group(0).strip()
    ))
    ids     = [s for s in raw if _ID_PATTERN.match(s) and len(s) < 40]
    emails  = [s for s in raw if _EMAIL_PATTERN.match(s)]
    status  = [s for s in raw
               if s.isupper() and 4 <= len(s) <= 16 and s.isalpha()
               and s.lower() in _STATUS_WORDS]
    # Only keep strings that contain a real printf format specifier AND
    # have enough surrounding human-readable text (at least one word of 3+
    # alphabetic chars that isn't just a specifier letter, and minimum length)
    fmts    = [s for s in raw
               if _FMT_PATTERN.search(s)
               and len(s) >= 10
               and len(s) < 120
               and _re.search(r"[a-zA-Z]{3,}", s)]
    return {
        "ids_found":      ids[:30],
        "emails_found":   emails[:10],
        "status_tokens":  status[:20],
        "format_strings": fmts[:10],
    }


# ---------------------------------------------------------------------------
# G-9 — Capstone sentinel harvesting
# ---------------------------------------------------------------------------

def _harvest_sentinels_capstone(data: bytes, pe_obj: Any) -> dict:
    """Disassemble exported function bodies using Capstone and collect
    32-bit immediate values in the 0xFFFFF000-0xFFFFFFFF range that appear
    in MOV EAX/RAX, CMP EAX/RAX, or TEST instructions.

    Returns a dict: {hex_str: {"function": name, "instruction": asm_text}}.
    Only looks at instructions inside exported function RVAs to avoid
    noise from compiler helpers and thunks.
    """
    harvested: dict[str, dict] = {}

    try:
        from capstone import Cs, CS_ARCH_X86, CS_MODE_32, CS_MODE_64  # type: ignore
        from capstone.x86_const import X86_OP_IMM  # type: ignore
    except ImportError:
        logger.warning("capstone not installed — G-9 sentinel harvest skipped")
        return harvested

    try:
        # Detect 32 vs 64 bit
        is_64bit = False
        try:
            if hasattr(pe_obj, "OPTIONAL_HEADER"):
                is_64bit = (pe_obj.OPTIONAL_HEADER.Magic == 0x20B)
        except Exception:
            pass

        mode = CS_MODE_64 if is_64bit else CS_MODE_32
        md = Cs(CS_ARCH_X86, mode)
        md.detail = True

        # Collect exported functions: (name, VA, size_estimate)
        exported_funcs: list[tuple[str, int, int]] = []
        try:
            if hasattr(pe_obj, "DIRECTORY_ENTRY_EXPORT"):
                exports = pe_obj.DIRECTORY_ENTRY_EXPORT.symbols
                addrs = sorted(exp.address for exp in exports if exp.address)
                for idx, exp in enumerate(exports):
                    if not exp.address:
                        continue
                    name = (exp.name or b"").decode("utf-8", errors="replace") or f"#ord{exp.ordinal}"
                    va = exp.address
                    # Estimate size as distance to next export (cap at 64KB)
                    next_addrs = [a for a in addrs if a > va]
                    size = min(next_addrs[0] - va, 0x10000) if next_addrs else 0x1000
                    exported_funcs.append((name, va, size))
        except Exception as exc:
            logger.debug("capstone: export enumeration failed: %s", exc)

        if not exported_funcs:
            logger.debug("capstone: no exports found — skipping sentinel harvest")
            return harvested

        image_base = pe_obj.OPTIONAL_HEADER.ImageBase

        for fn_name, va, size in exported_funcs:
            try:
                offset = pe_obj.get_offset_from_rva(va)
                fn_bytes = data[offset : offset + size]
            except Exception:
                continue

            try:
                for insn in md.disasm(fn_bytes, image_base + va):
                    mnemonic = insn.mnemonic.lower()

                    # Only care about: mov eax/rax,imm | cmp eax/rax,imm | test eax/rax,imm
                    if mnemonic not in ("mov", "cmp", "test"):
                        continue

                    # Check operands for sentinel-range immediates
                    for op in insn.operands:
                        if op.type != X86_OP_IMM:
                            continue
                        # Use unsigned interpretation
                        val = op.imm & 0xFFFFFFFF
                        if 0xFFFFF000 <= val <= 0xFFFFFFFF:
                            hex_str = f"0x{val:08X}"
                            if hex_str not in harvested:
                                insn_text = f"{insn.mnemonic} {insn.op_str}"
                                harvested[hex_str] = {
                                    "function":    fn_name,
                                    "instruction": insn_text,
                                    "value":       val,
                                }
            except Exception:
                continue

    except Exception as exc:
        logger.debug("capstone sentinel harvest failed: %s", exc)

    return harvested


# ---------------------------------------------------------------------------
# Default sentinel meanings (fallback if no existing vocab entry)
# ---------------------------------------------------------------------------

_SENTINEL_DEFAULT_MEANINGS: dict[str, str] = {
    "0xFFFFFFFF": "generic failure / invalid handle",
    "0xFFFFFFFE": "already active or not found",
    "0xFFFFFFFD": "permission denied",
    "0xFFFFFFFC": "account not found / no-op",
    "0xFFFFFFFB": "write denied — account locked or value out of range",
}


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_static_analysis(data: bytes, dll_name: str = "unknown.dll") -> dict:
    """Run all static analysis stages on the DLL bytes.

    Args:
        data:     Raw bytes of the uploaded DLL/EXE.
        dll_name: Original filename (used for metadata only).

    Returns a dict with keys:
        pe_version_info, iat_capabilities, binary_strings,
        sentinel_constants, generated_at, dll_name
    """
    result: dict = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dll_name":     dll_name,
        "pe_version_info":  {},
        "iat_capabilities": {"raw_imports": [], "categories": {}},
        "binary_strings":   {"ids_found": [], "emails_found": [],
                              "status_tokens": [], "format_strings": []},
        "sentinel_constants": {"source": "none", "harvested": {},
                                "calibration_fallback_used": True},
        "vocab_seeded": {},
        "injected_into_prompt": False,
        "static_hints_block_length": 0,
    }

    # ── Load PE ──────────────────────────────────────────────────────────────
    pe_obj = None
    try:
        import pefile  # type: ignore
        pe_obj = pefile.PE(data=data)
        logger.info("[static_analysis] pefile loaded — %d bytes", len(data))
    except ImportError:
        logger.warning("[static_analysis] pefile not installed — PE analysis skipped")
    except Exception as exc:
        logger.debug("[static_analysis] pefile load failed: %s", exc)

    # ── G-8: PE Version Info ─────────────────────────────────────────────────
    if pe_obj is not None:
        try:
            result["pe_version_info"] = _extract_pe_version_info(pe_obj)
            if result["pe_version_info"]:
                logger.info("[static_analysis] G-8 version_info: %s",
                            {k: v for k, v in result["pe_version_info"].items()
                             if k in ("FileDescription", "ProductName", "OriginalFilename")})
        except Exception as exc:
            logger.debug("[static_analysis] G-8 version_info failed: %s", exc)

    # ── G-4: IAT capabilities ────────────────────────────────────────────────
    if pe_obj is not None:
        try:
            result["iat_capabilities"] = _extract_iat_capabilities(pe_obj)
            cats = list(result["iat_capabilities"]["categories"].keys())
            logger.info("[static_analysis] G-4 IAT capabilities: %s", cats)
        except Exception as exc:
            logger.debug("[static_analysis] G-4 IAT failed: %s", exc)

    # ── G-7: Binary string extraction ────────────────────────────────────────
    try:
        result["binary_strings"] = _extract_binary_strings(data)
        bs = result["binary_strings"]
        logger.info("[static_analysis] G-7 strings: %d IDs, %d emails, %d status, %d fmts",
                    len(bs["ids_found"]), len(bs["emails_found"]),
                    len(bs["status_tokens"]), len(bs["format_strings"]))
    except Exception as exc:
        logger.debug("[static_analysis] G-7 string extraction failed: %s", exc)

    # ── G-9: Capstone sentinel harvest ───────────────────────────────────────
    if pe_obj is not None:
        try:
            harvested = _harvest_sentinels_capstone(data, pe_obj)
            if harvested:
                result["sentinel_constants"] = {
                    "source":                  "capstone",
                    "harvested":               harvested,
                    "calibration_fallback_used": False,
                }
                logger.info("[static_analysis] G-9 capstone sentinels: %s", list(harvested.keys()))
            else:
                result["sentinel_constants"]["source"] = "capstone_empty"
                result["sentinel_constants"]["calibration_fallback_used"] = True
        except Exception as exc:
            logger.debug("[static_analysis] G-9 capstone failed: %s", exc)

    return result


def build_vocab_seeds(static: dict, existing_vocab: dict) -> dict:
    """Derive vocab fields that should be seeded from static analysis results.

    Uses setdefault semantics: binary evidence only fills a vocab key if the
    user hasn't already provided it.  Binary strings are ground truth
    (source: "static_extraction", confidence: "high") and therefore *do*
    win over a missing/empty key, but do NOT override an explicitly
    user-provided value.

    Returns a dict of vocab fields to apply (caller decides how to merge).
    """
    seeds: dict = {}
    bs = static.get("binary_strings", {})
    sentinels = static.get("sentinel_constants", {})

    # id_formats — from binary IDs
    if bs.get("ids_found") and "id_formats" not in existing_vocab:
        seeds["id_formats"] = bs["ids_found"][:20]

    # error_codes — from Capstone harvest (fills blanks only)
    harvested = sentinels.get("harvested", {})
    if harvested:
        existing_codes = existing_vocab.get("error_codes") or {}
        new_codes = dict(existing_codes)
        for hex_str, info in harvested.items():
            if hex_str not in new_codes:
                # Use a default meaning if we know it, otherwise note the source
                meaning = _SENTINEL_DEFAULT_MEANINGS.get(
                    hex_str,
                    f"sentinel constant (found in {info.get('function', 'unknown')})"
                )
                new_codes[hex_str] = meaning
        if new_codes != existing_codes:
            seeds["error_codes"] = new_codes

    # value_semantics — status tokens
    if bs.get("status_tokens") and "value_semantics" not in existing_vocab:
        seeds["value_semantics"] = {"status_values": bs["status_tokens"]}

    # description — from PE version info (if user didn't supply one)
    vi = static.get("pe_version_info", {})
    if vi and "description" not in existing_vocab:
        desc_parts = []
        if vi.get("FileDescription"):
            desc_parts.append(vi["FileDescription"])
        if vi.get("ProductName") and vi.get("ProductName") != vi.get("FileDescription"):
            desc_parts.append(f"({vi['ProductName']})")
        if vi.get("CompanyName"):
            desc_parts.append(f"by {vi['CompanyName']}")
        if vi.get("ProductVersion"):
            desc_parts.append(f"v{vi['ProductVersion']}")
        if desc_parts:
            seeds["description"] = " ".join(desc_parts)

    return seeds


def build_static_hints_block(static: dict) -> str:
    """Build the text block appended to the LLM prompt (secondary role after vocab seeding)."""
    parts: list[str] = []
    bs = static.get("binary_strings", {})
    vi = static.get("pe_version_info", {})
    iat = static.get("iat_capabilities", {})
    sent = static.get("sentinel_constants", {})

    # Version info banner
    if vi.get("FileDescription") or vi.get("ProductName"):
        banner = vi.get("FileDescription") or vi.get("ProductName") or ""
        if vi.get("CompanyName"):
            banner += f" — {vi['CompanyName']}"
        if vi.get("ProductVersion"):
            banner += f" v{vi['ProductVersion']}"
        parts.append(f"BINARY IDENTITY: {banner}")

    # IAT capability profile
    cats = list((iat.get("categories") or {}).keys())
    if cats:
        parts.append(f"IAT CAPABILITIES (imports): {', '.join(cats)}")
        no_net = "network" not in cats and "networking" not in cats
        if no_net:
            parts.append("  → No network imports: all failures are local (arg errors, state, locks)")

    # Binary string hits
    if bs.get("ids_found"):
        parts.append("Known IDs/codes: " + ", ".join(bs["ids_found"][:20]))
    if bs.get("emails_found"):
        parts.append("Known emails: " + ", ".join(bs["emails_found"][:10]))
    if bs.get("status_tokens"):
        parts.append("Known status values: " + ", ".join(bs["status_tokens"][:15]))
    if bs.get("format_strings"):
        parts.append("Output format strings: " + " | ".join(bs["format_strings"][:5]))

    # Harvested sentinels (confirmation that they're in the binary)
    harvested = sent.get("harvested", {})
    if harvested:
        sentinel_lines = []
        for hex_str, info in list(harvested.items())[:8]:
            sentinel_lines.append(f"  {hex_str} in {info['function']} ({info['instruction']})")
        parts.append("SENTINEL CONSTANTS (from binary disassembly):\n" + "\n".join(sentinel_lines))

    if not parts:
        return ""

    return (
        "\nSTATIC ANALYSIS HINTS (extracted from DLL binary before any probing):\n"
        + "\n".join(parts)
        + "\nUse these as probe values for string params before trying generic ones.\n"
    )
