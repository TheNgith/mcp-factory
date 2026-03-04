#!/usr/bin/env python3
"""
main.py - CLI entry point and pipeline orchestration.

Parses arguments, runs the analysis pipeline, and writes outputs.
"""

import argparse
import logging
import sys
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

from classify import classify_file, FileType, extract_signature, get_architecture
from headers_scan import scan_headers, scan_docs_for_exports
from exports import demangle_with_undname, deduplicate_exports, resolve_forwarders
from pe_parse import read_pe_exports, get_exports_from_dumpbin, find_dumpbin
from schema import ExportedFunc, Invocable, MatchInfo, write_csv, write_json, write_markdown, write_tier_summary, write_invocables_json, exports_to_invocables
from utils import Spinner, format_verbose_header, format_verbose_result
from dotnet_analyzer import get_dotnet_methods, get_dotnet_metadata
from com_scan import scan_com_registry, com_objects_to_invocables
from import_analyzer import analyze_imports, format_capabilities_summary
from rpc_analyzer import analyze_rpc, rpc_to_invocables, format_rpc_summary
from cli_analyzer import analyze_cli
from gui_analyzer import analyze_gui, PYWINAUTO_AVAILABLE
from script_analyzer import analyze_script
from sql_analyzer import analyze_sql
from js_analyzer import analyze_js
from openapi_analyzer import analyze_openapi
from wsdl_analyzer import analyze_wsdl
from idl_analyzer import analyze_idl
from jndi_analyzer import analyze_jndi
from pdb_analyzer import analyze_pdb

# Plugin-based analyzer registry
ANALYZER_REGISTRY = {
    'pe_dll':   {'name': 'PE DLL Analyzer',        'enabled': True},
    'pe_exe':   {'name': 'PE EXE Analyzer',         'enabled': True},
    'dotnet':   {'name': '.NET Assembly Analyzer',  'enabled': True},
    'com':      {'name': 'COM Registry Analyzer',   'enabled': True},
    'cli':      {'name': 'CLI Tool Analyzer',        'enabled': True},
    'gui':      {'name': 'GUI App Analyzer (pywinauto)', 'enabled': True},
    'script':   {'name': 'Script Analyzer',          'enabled': True},
    'sql':      {'name': 'SQL Analyzer',             'enabled': True},
    'js':       {'name': 'JavaScript Analyzer',      'enabled': True},
    'openapi':  {'name': 'OpenAPI/JSON-RPC Analyzer','enabled': True},
    'wsdl':     {'name': 'SOAP/WSDL Analyzer',       'enabled': True},
    'idl':      {'name': 'CORBA IDL Analyzer',       'enabled': True},
    'jndi':     {'name': 'JNDI Config Analyzer',     'enabled': True},
    'pdb':      {'name': 'PDB Symbol Analyzer',      'enabled': True},
}


def score_confidence(export: Invocable, matches: dict, is_signed: bool, forwarded_resolved: bool, is_system_dll: bool = False) -> tuple:
    """Score confidence in export invocability.

    Factors are derived from actual extracted data first; the label is
    then derived from those factors. This keeps the label honest and
    consistent with what an LLM downstream will actually find usable.
    """
    reasons = ['exported from DLL']

    # Step 1: measure what we actually extracted for this export
    match = matches.get(export.name) if isinstance(matches, dict) else None
    has_signature   = bool(match and getattr(match, 'prototype', None))
    has_return_type = bool(match and getattr(match, 'return_type', None))
    _params = (getattr(match, 'parameters', '') or '').strip().lower() if match else ''
    has_parameters  = bool(_params and _params not in ('', 'void'))
    has_doc         = bool(getattr(export, 'doc_comment', None))

    # Step 2: label derived from factors (never set label before measuring data)
    if has_signature and has_parameters and has_return_type:
        confidence = 'guaranteed'
        reasons.append('complete signature from header file')
    elif has_signature and (has_parameters or has_return_type):
        confidence = 'high'
        reasons.append('partial signature from header file')
    elif hasattr(export, 'demangled') and export.demangled:
        confidence = 'medium'
        reasons.append('demangled C++ name available')
    elif is_signed and is_system_dll:
        confidence = 'medium'
        reasons.append('well-known signed system API (no header match)')
    elif is_signed:
        confidence = 'medium'
        reasons.append('digitally signed binary')
    elif is_system_dll:
        confidence = 'medium'
        reasons.append('well-known system API (no header match)')
    else:
        # Step 3: name-pattern heuristics as last resort
        confidence = 'low'
        name = export.name
        if '_' in name:
            prefix = name.split('_')[0]
            if len(prefix) >= 3 and (prefix.isupper() or prefix.islower()):
                confidence = 'medium'
                reasons.append(f'library prefix pattern ({prefix}_*)')
        elif any(name.startswith(p) for p in [
            'Create', 'Get', 'Set', 'Open', 'Close', 'Read', 'Write',
            'Initialize', 'Finalize', 'Register', 'Unregister',
            'Allocate', 'Free', 'Query', 'Release',
            'init', 'destroy', 'alloc', 'dealloc', 'malloc', 'calloc',
            'compress', 'decompress', 'encode', 'decode', 'encrypt', 'decrypt',
            'load', 'save', 'bind', 'connect', 'send', 'recv', 'shutdown'
        ]):
            confidence = 'medium'
            reasons.append('common API pattern')

    # Step 3: append any additional evidence (does not change tier)
    if forwarded_resolved:
        reasons.append('forwarded reference resolved')
    if has_doc:
        reasons.append('has documentation')

    return confidence, reasons


def generate_confidence_summary(
    exports: List[ExportedFunc],
    matches: dict,
    is_signed: bool,
    forwarding_chain: dict,
    base_name: str,
    out_dir: Path
) -> str:
    """Generate human-readable confidence summary and write to file."""
    
    # Check if this is a well-known system DLL
    is_system_dll = any(base_name.lower().endswith(name.replace('.dll', '')) for name in [
        'kernel32.dll', 'kernelbase.dll', 'user32.dll', 'gdi32.dll',
        'advapi32.dll', 'ntdll.dll', 'shell32.dll', 'ole32.dll',
        'oleaut32.dll', 'combase.dll', 'rpcrt4.dll', 'ws2_32.dll',
        'winhttp.dll', 'wininet.dll', 'bcrypt.dll', 'crypt32.dll',
        'msvcrt.dll', 'ucrtbase.dll'
    ])
    
    # Score all exports
    confidence_data = {'guaranteed': [], 'high': [], 'medium': [], 'low': []}
    reason_counts = {}
    
    for exp in exports:
        # Convert ExportedFunc to Invocable-like object for scoring
        forwarded_resolved = exp.name in forwarding_chain and forwarding_chain[exp.name] != exp.name
        score, reasons = score_confidence(
            type('Export', (), {
                'name': exp.name,
                'source_type': 'export',
                'doc_comment': getattr(exp, 'doc_comment', None),
                'demangled': getattr(exp, 'demangled', None)
            })(),
            matches,
            is_signed,
            forwarded_resolved,
            is_system_dll  # Pass system DLL flag
        )
        
        confidence_data[score].append({
            'name': exp.name,
            'reasons': reasons
        })
        
        # Track reason frequency
        for reason in reasons:
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
    
    # Calculate percentages
    total = len(exports)
    guar_pct = (len(confidence_data['guaranteed']) / total * 100) if total > 0 else 0
    high_pct = (len(confidence_data['high']) / total * 100) if total > 0 else 0
    med_pct = (len(confidence_data['medium']) / total * 100) if total > 0 else 0
    low_pct = (len(confidence_data['low']) / total * 100) if total > 0 else 0
    
    # ANSI color codes for terminal output
    RED = '\033[91m'      # Bright red
    YELLOW = '\033[93m'   # Bright yellow
    GREEN = '\033[92m'    # Bright green
    CYAN = '\033[96m'     # Bright cyan
    BLUE = '\033[94m'     # Bright blue
    RESET = '\033[0m'     # Reset to default
    
    # Generate summary text
    lines = [
        f"CONFIDENCE ANALYSIS SUMMARY",
        f"{'=' * 60}",
        f"",
        f"DLL: {base_name}",
        f"Total Exports: {total}",
        f"Analysis Date: {__import__('datetime').datetime.now().isoformat()}",
        f"",
        f"CONFIDENCE BREAKDOWN",
        f"{'-' * 60}",
        f"{RED}LOW     Confidence: {len(confidence_data['low']):3d} exports ({low_pct:5.1f}%){RESET}",
        f"{YELLOW}MEDIUM  Confidence: {len(confidence_data['medium']):3d} exports ({med_pct:5.1f}%){RESET}",
        f"{GREEN}HIGH    Confidence: {len(confidence_data['high']):3d} exports ({high_pct:5.1f}%){RESET}",
        f"{CYAN}GUARANT Confidence: {len(confidence_data['guaranteed']):3d} exports ({guar_pct:5.1f}%){RESET}",
        f"",
    ]
    
    # Add reason breakdown
    if reason_counts:
        lines.extend([
            f"CONFIDENCE FACTORS (by frequency)",
            f"{'-' * 60}",
        ])
        for reason, count in sorted(reason_counts.items(), key=lambda x: -x[1]):
            pct = (count / total * 100) if total > 0 else 0
            lines.append(f"  • {reason:<40s} {count:3d} ({pct:5.1f}%)")
        lines.append("")
    
    # Add improvement opportunities
    lines.extend([
        f"WAYS TO IMPROVE CONFIDENCE",
        f"{'-' * 60}",
    ])
    
    improvements = []
    if len(matches) < total:
        improvements.append(f"  • Provide header files (.h/.hpp): Would match function prototypes")
        improvements.append(f"    and increase HIGH confidence exports")
    if not is_signed:
        improvements.append(f"  • Obtain signed binaries: Digital signatures boost confidence")
    if 'forwarded reference resolved' not in reason_counts:
        improvements.append(f"  • Document forwarder chains: Track indirect function references")
    
    if improvements:
        for imp in improvements:
            lines.append(imp)
    else:
        lines.append("  • All major confidence factors are present!")
    
    lines.extend([
        "",
        f"EXPORT DETAILS BY CONFIDENCE LEVEL",
        f"{'-' * 60}",
    ])
    
    # Add sample exports from each tier (LOW, MEDIUM, HIGH, GUARANTEED)
    for level in ['low', 'medium', 'high', 'guaranteed']:
        data = confidence_data[level]
        
        # Select color based on confidence level
        if level == 'low':
            color = RED
            level_text = "LOW"
        elif level == 'medium':
            color = YELLOW
            level_text = "MEDIUM"
        elif level == 'high':
            color = GREEN
            level_text = "HIGH"
        else:
            color = CYAN
            level_text = "GUARANTEED"
        
        lines.append(f"\n{color}{level_text} CONFIDENCE ({len(data)} exports):{RESET}")
        
        if data:
            # Show first 5 and last 2 if more than 7
            shown = data[:5] if len(data) <= 7 else data[:5] + [{'name': '...', 'reasons': []}] + data[-2:]
            for item in shown:
                if item['name'] == '...':
                    lines.append(f"  ... ({len(data) - 7} more)")
                else:
                    reasons_str = ', '.join(item['reasons']) if item['reasons'] else 'no info'
                    lines.append(f"{BLUE}  * {item['name']}")
                    lines.append(f"      -> {reasons_str}{RESET}")
    
    lines.append("")
    
    # Write to file
    summary_text = '\n'.join(lines)
    summary_file = out_dir / f"{base_name}_confidence_summary.txt"
    summary_file.write_text(summary_text, encoding='utf-8')
    
    return summary_text


def get_default_output_dir() -> Path:
    """Get default output directory for analysis results."""
    return Path.cwd() / "mcp_dumpbin_out"


def analyze_dotnet_assembly(dll_path: Path, out_dir: Path, base_name: str, args) -> int:
    """.NET assembly analysis pipeline."""
    logger.info(f"Analyzing .NET assembly: {dll_path}")
    
    # Extract .NET methods
    invocables = get_dotnet_methods(dll_path)
    
    if not invocables:
        logger.warning("No public .NET methods found")
        # Try to get metadata at least
        metadata = get_dotnet_metadata(dll_path)
        if metadata:
            logger.info(f"Assembly: {metadata.get('AssemblyName')}, Version: {metadata.get('Version')}")
    else:
        logger.info(f"Found {len(invocables)} .NET methods")
    
    # Populate MCP-specific fields for .NET invocables
    for inv in invocables:
        inv.assembly_path = str(dll_path)
        if not inv.confidence:
            inv.confidence = "high"  # .NET reflection provides high confidence
    
    # Write outputs
    tier4_md = out_dir / f"{base_name}_dotnet_methods.md"
    with open(tier4_md, 'w', encoding='utf-8') as f:
        f.write(f"# .NET Assembly Analysis: {dll_path.name}\\n\\n")
        f.write(f"Total methods: {len(invocables)}\\n\\n")
        
        # Group by namespace
        from collections import defaultdict
        by_namespace = defaultdict(list)
        for inv in invocables:
            namespace = inv.name.split('.')[0] if '.' in inv.name else 'Global'
            by_namespace[namespace].append(inv)
        
        for namespace in sorted(by_namespace.keys()):
            f.write(f"## {namespace}\\n\\n")
            for inv in sorted(by_namespace[namespace], key=lambda x: x.name):
                f.write(f"- `{inv.signature}`\\n")
                if inv.doc_comment:
                    f.write(f"  - {inv.doc_comment}\\n")
            f.write("\\n")
    
    # Write MCP-compatible JSON output
    tier4_json_mcp = out_dir / f"{base_name}_dotnet_methods_mcp.json"
    write_invocables_json(
        tier4_json_mcp,
        invocables,
        dll_path=dll_path,
        tier=4,
        schema_version="2.0.0"
    )
    
    logger.info(f"Results written to {out_dir}")
    print(f"\\n.NET Analysis Complete")
    print(f"Methods found: {len(invocables)}")
    print(f"Markdown: {tier4_md}")
    print(f"MCP JSON: {tier4_json_mcp}")
    
    return 0


def analyze_com_object(dll_path: Path, out_dir: Path, base_name: str, args) -> int:
    """COM object analysis pipeline."""
    logger.info(f"Analyzing COM object: {dll_path}")
    
    # Scan registry for related COM objects
    com_objects = scan_com_registry(dll_path.name)
    
    # Even if no registry objects found, the DLL might contain a Type Library
    if not com_objects:
        logger.info(f"No COM objects found in registry for {dll_path.name}, checking for embedded Type Library...")
    else:
        logger.info(f"Found {len(com_objects)} COM objects in registry")
    
    # Convert COM objects / TLB to Invocables
    invocables = com_objects_to_invocables(com_objects, dll_path)
    
    if not invocables:
        logger.warning(f"No COM objects or Type Library found for {dll_path.name}")
        print(f"\nNo COM objects registered or embedded for {dll_path.name}")
        
        # Suppress empty report generation to reduce noise
        return 0
    
    # Populate MCP-specific fields
    for inv in invocables:
        inv.dll_path = str(dll_path)
        if not inv.confidence:
            inv.confidence = "medium"  # Default fallback
    
    # Write Markdown output
    tier4_md = out_dir / f"{base_name}_com_objects.md"
    with open(tier4_md, 'w', encoding='utf-8') as f:
        f.write(f"# COM Object Analysis: {dll_path.name}\n\n")
        f.write(f"Total COM objects: {len(invocables)}\n\n")
        
        for inv in invocables:
            f.write(f"## {inv.name}\n\n")
            if inv.clsid:
                f.write(f"**CLSID:** `{inv.clsid}`\n\n")
            if inv.signature:
                f.write(f"**ProgID:** {inv.signature}\n\n")
            f.write("\n")
    
    # Write MCP-compatible JSON output
    tier4_json = out_dir / f"{base_name}_com_objects_mcp.json"
    write_invocables_json(
        tier4_json,
        invocables,
        dll_path=dll_path,
        tier=4,
        schema_version="2.0.0"
    )
    
    logger.info(f"Results written to {out_dir}")
    print(f"\nCOM Analysis Complete")
    print(f"COM objects found: {len(invocables)}")
    print(f"Markdown: {tier4_md}")
    print(f"MCP JSON: {tier4_json}")
    
    return 0
    
    if not com_objects:
        logger.warning(f"No COM objects found registered for {dll_path.name}")
        logger.info("File imports ole32/oleaut32 but has no registry entries")
    else:
        logger.info(f"Found {len(com_objects)} COM objects")
    
    # Convert to invocables
    invocables = com_objects_to_invocables(com_objects)
    
    # Write outputs
    tier4_md = out_dir / f"{base_name}_com_objects.md"
    with open(tier4_md, 'w', encoding='utf-8') as f:
        f.write(f"# COM Object Analysis: {dll_path.name}\\n\\n")
        f.write(f"Total COM objects: {len(com_objects)}\\n\\n")
        
        if com_objects:
            f.write("## Registered COM Objects\\n\\n")
            for obj in com_objects:
                f.write(f"### {obj.get('name', 'Unknown')}\\n\\n")
                f.write(f"- **CLSID:** `{obj['clsid']}`\\n")
                if obj.get('progid'):
                    f.write(f"- **ProgID:** `{obj['progid']}`\\n")
                f.write(f"- **Server:** `{obj['server_path']}`\\n")
                f.write(f"- **Type:** {'In-process' if obj['inproc'] else 'Out-of-process'}\\n\\n")
        else:
            f.write("_No COM objects registered for this DLL._\\n\\n")
            f.write("Note: This file was detected as COM due to ole32/oleaut32 imports.\\n")
    
    # Write JSON output
    tier4_json = out_dir / f"{base_name}_com_objects.json"
    import json
    with open(tier4_json, 'w', encoding='utf-8') as f:
        json.dump(com_objects, f, indent=2)
    
    logger.info(f"Results written to {out_dir}")
    print(f"\\nCOM Analysis Complete")
    print(f"COM objects found: {len(com_objects)}")
    print(f"Output: {tier4_md}")
    
    return 0


# -------------------------------------------------------------------------
# JIT / scripting language pipeline  (Python, PS1, Batch, VBS, Shell,
#                                     SQL, JavaScript, TypeScript)
# -------------------------------------------------------------------------

# Map FileType → (human label, analyzer function, source_type tag)
_SCRIPT_DISPATCH = {
    FileType.PYTHON_SCRIPT:      ('Python script',       analyze_script, 'python_function'),
    FileType.POWERSHELL_SCRIPT:  ('PowerShell script',   analyze_script, 'powershell_function'),
    FileType.BATCH_SCRIPT:       ('Batch script',        analyze_script, 'batch_label'),
    FileType.VBSCRIPT:           ('VBScript',            analyze_script, 'vbscript_function'),
    FileType.SHELL_SCRIPT:       ('Shell script',        analyze_script, 'shell_function'),
    FileType.JAVASCRIPT:         ('JavaScript module',   analyze_js,     'js_function'),
    FileType.TYPESCRIPT:         ('TypeScript module',   analyze_js,     'ts_method'),
    FileType.RUBY_SCRIPT:        ('Ruby script',         analyze_script, 'ruby_function'),
    FileType.PHP_SCRIPT:         ('PHP script',          analyze_script, 'php_function'),
    FileType.SQL_FILE:           ('SQL source file',     analyze_sql,    'sql_procedure'),
    FileType.OPENAPI_SPEC:       ('OpenAPI spec',        analyze_openapi,'openapi_operation'),
    FileType.JSONRPC_SPEC:       ('JSON-RPC descriptor', analyze_openapi,'jsonrpc_method'),
    FileType.WSDL_FILE:          ('WSDL service desc.',  analyze_wsdl,   'soap_operation'),
    FileType.CORBA_IDL:          ('CORBA IDL file',      analyze_idl,    'corba_method'),
    FileType.JNDI_CONFIG:        ('JNDI config file',    analyze_jndi,   'jndi_lookup'),
    FileType.PDB_FILE:           ('PDB symbols file',    analyze_pdb,    'pdb_symbol'),
}

# ── Directory scan ────────────────────────────────────────────────────────────
# Extensions for which classify_file() returns something other than UNKNOWN.
# Used to filter rglob results when the --target is a directory.
RECOGNIZED_EXTENSIONS: frozenset = frozenset({
    # PE binaries / type libraries
    '.dll', '.exe', '.tlb', '.olb',
    # JIT / scripting languages
    '.py', '.ps1', '.bat', '.cmd', '.vbs',
    '.sh', '.bash', '.zsh',
    '.js', '.mjs', '.cjs', '.ts', '.tsx', '.mts',
    '.rb', '.php', '.sql',
    # Protocol / spec descriptors
    '.yaml', '.yml', '.wsdl', '.idl', '.jndi', '.properties', '.pdb',
    # Content-sniffed types — classify_file() peeks inside these
    '.json', '.xml',
})


def analyze_directory(dir_path: Path, out_dir: Path, args) -> int:
    """Walk *dir_path*, analyze every recognized file, and write an aggregate
    ``<dir>_scan_mcp.json`` that merges all discovered invocables.

    Each file is analyzed in its own sub-directory under *out_dir* so
    individual artifacts are preserved.  The combined output lets
    ``select_invocables.py`` work on the entire directory at once.

    §2.a compliance — "Users must be able to provide the system a copy of
    the target file **or an installed instance**."  This function handles
    the installed-instance (directory) case.
    """
    import json as _json
    from datetime import datetime as _dt

    logger.info("Directory scan: %s", dir_path)
    print(f"\n{'='*60}")
    print(f"  Directory Scan: {dir_path}")
    print(f"{'='*60}\n")

    # ── Collect recognized files ──────────────────────────────────────────────
    candidates: list = []
    for f in sorted(dir_path.rglob('*')):
        if not f.is_file():
            continue
        if f.suffix.lower() not in RECOGNIZED_EXTENSIONS:
            continue
        ft = classify_file(f)
        if ft == FileType.UNKNOWN:
            continue
        candidates.append((f, ft))

    if not candidates:
        print(f"  No recognized files found in {dir_path}")
        return 0

    print(f"  Found {len(candidates)} recognized file(s):\n")
    for fp, ft in candidates:
        print(f"    {fp.name}  [{ft.value}]")
    print()

    dir_base = dir_path.name   # e.g. "scripts" or "AppD"
    all_invocables: list = []
    source_files: list = []

    # ── Per-file sub-analysis ─────────────────────────────────────────────────
    for file_path, file_type in candidates:
        # Unique sub-dir per file; use stem to keep names readable
        sub_out = out_dir / file_path.stem
        sub_out.mkdir(parents=True, exist_ok=True)

        try:
            rel = file_path.relative_to(dir_path)
        except ValueError:
            rel = Path(file_path.name)
        print(f"  \u27a4 {rel}  ({file_type.value})")

        # Re-invoke main() with sys.argv targeting this individual file
        saved_argv = sys.argv[:]
        try:
            sys.argv = [
                "main.py",
                "--target", str(file_path),
                "--out",    str(sub_out),
            ]
            main()
        except SystemExit:
            pass
        except Exception as exc:
            logger.warning("Analysis failed for %s: %s", file_path.name, exc)
            print(f"    [WARN] {exc}")
        finally:
            sys.argv = saved_argv

        # Harvest all *_mcp.json written in sub_out
        file_invocables: list = []
        for mcp_json in sorted(sub_out.glob('*_mcp.json')):
            try:
                with open(mcp_json, encoding='utf-8') as fh:
                    data = _json.load(fh)
                file_invocables.extend(data.get('invocables', []))
            except Exception:
                pass

        all_invocables.extend(file_invocables)
        source_files.append({
            'file':          str(file_path.name),
            'relative_path': str(rel),
            'type':          file_type.value,
            'count':         len(file_invocables),
        })

    # ── Write aggregate MCP JSON ──────────────────────────────────────────────
    agg_path = out_dir / f"{dir_base}_scan_mcp.json"
    agg_payload = {
        "metadata": {
            "target_name":   dir_path.name,
            "target_path":   str(dir_path),
            "analysis_type": "directory_scan",
            "generated_at":  _dt.utcnow().isoformat() + "Z",
            "file_count":    len(candidates),
            "source_files":  source_files,
        },
        "invocables": all_invocables,
        "summary": {
            "total_invocables":      len(all_invocables),
            "files_analyzed":        len(candidates),
            "files_with_invocables": sum(1 for s in source_files if s['count'] > 0),
            "confidence_breakdown":  {
                level: sum(
                    1 for inv in all_invocables
                    if inv.get('confidence') == level
                )
                for level in ('guaranteed', 'high', 'medium', 'low')
            },
        },
    }
    with open(agg_path, 'w', encoding='utf-8') as fh:
        _json.dump(agg_payload, fh, indent=2, ensure_ascii=False)

    print(f"\n  Directory Scan Complete")
    print(f"  Files analysed:      {len(candidates)}")
    print(f"  Total invocables:    {len(all_invocables)}")
    print(f"  Aggregate MCP JSON:  {agg_path}")
    return 0


def analyze_scripting_language(target: Path, out_dir: Path, base_name: str,
                               file_type: FileType) -> int:
    """Shared pipeline for all JIT / scripting / query file types.

    Calls the appropriate sub-analyzer, writes Markdown + MCP JSON output,
    and prints a summary.  Returns 0 on success, 1 on failure.
    """
    label, analyzer_fn, _ = _SCRIPT_DISPATCH[file_type]
    logger.info("Analyzing %s: %s", label, target.name)

    with Spinner(f"Analyzing {label} capabilities"):
        invocables = analyzer_fn(target)

    if not invocables:
        logger.warning("No invocables detected in %s", target.name)
        print(f"\nNo invocables found in {target.name}")
        return 0

    # --- Markdown report ---
    md_path = out_dir / f"{base_name}_{file_type.value.lower()}.md"
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write(f"# {label} Analysis: {target.name}\n\n")
        f.write(f"Total invocables detected: {len(invocables)}\n\n")
        # Confidence breakdown
        by_conf: dict = {}
        for inv in invocables:
            by_conf.setdefault(inv.confidence, []).append(inv)
        for lvl in ('guaranteed', 'high', 'medium', 'low'):
            if lvl in by_conf:
                f.write(f"| Confidence | Count |\n|---|---|\n")
                break
        for lvl in ('guaranteed', 'high', 'medium', 'low'):
            if lvl in by_conf:
                f.write(f"| {lvl.capitalize()} | {len(by_conf[lvl])} |\n")
        f.write("\n")

        # Invocable detail
        for inv in invocables:
            f.write(f"## {inv.name}\n\n")
            f.write(f"**Signature:** `{inv.signature}`\n\n")
            if inv.doc_comment:
                f.write(f"**Description:** {inv.doc_comment}\n\n")
            if inv.parameters:
                f.write(f"**Parameters:** `{inv.parameters}`\n\n")
            if inv.return_type:
                f.write(f"**Returns:** `{inv.return_type}`\n\n")
            f.write(f"**Confidence:** {inv.confidence}\n\n")

    # --- MCP JSON ---
    mcp_path = out_dir / f"{base_name}_{file_type.value.lower()}_mcp.json"
    write_invocables_json(
        mcp_path,
        invocables,
        dll_path=target,
        tier=4,
        schema_version="2.0.0",
    )

    logger.info("Results written to %s", out_dir)
    print(f"\n{label} Analysis Complete")
    print(f"Invocables found: {len(invocables)}")
    print(f"Markdown: {md_path}")
    print(f"MCP JSON: {mcp_path}")
    return 0


def analyze_cli_tool(exe_path: Path, out_dir: Path, base_name: str, args) -> int:
    """CLI + GUI tool analysis pipeline.

    Runs CLI help-flag scraping first, then (when pywinauto is available)
    launches the EXE to walk its menu bar and collect GUI invocables.
    Both result sets are written to separate MCP JSON artifacts and a
    combined Markdown summary.
    """
    # ── Phase 1: CLI help output ───────────────────────────────────────────
    with Spinner("Analyzing CLI capabilities"):
        logger.info(f"Analyzing CLI tool: {exe_path}")
        cli_invocables = analyze_cli(exe_path)

    if not cli_invocables:
        logger.warning(f"No CLI capabilities detected for {exe_path.name}")
        print(f"\nNo CLI help output detected (likely a GUI-only application)")
    else:
        logger.info(f"Found {len(cli_invocables)} CLI interactions")

    # ── Phase 2: GUI menu walk (pywinauto) ─────────────────────────────────
    gui_invocables = []
    if PYWINAUTO_AVAILABLE:
        with Spinner("Analyzing GUI capabilities (pywinauto)"):
            gui_invocables = analyze_gui(exe_path)
        if gui_invocables:
            logger.info(f"Found {len(gui_invocables)} GUI actions in {exe_path.name}")
            # Write dedicated GUI MCP JSON
            gui_json = out_dir / f"{base_name}_gui_mcp.json"
            write_invocables_json(
                gui_json,
                gui_invocables,
                dll_path=exe_path,
                tier=4,
                schema_version="2.0.0",
            )
            print(f"GUI MCP JSON: {gui_json}")
        else:
            logger.info("No GUI actions found (non-Win32 or UWP app?)")
    else:
        print("\n[gui_analyzer] pywinauto not installed — skipping GUI analysis.")
        print("  Install with:  pip install pywinauto")

    # ── Combined Markdown output ───────────────────────────────────────────
    all_invocables = cli_invocables + gui_invocables
    tier4_md = out_dir / f"{base_name}_cli_help.md"
    with open(tier4_md, 'w', encoding='utf-8') as f:
        f.write(f"# CLI/GUI Analysis: {exe_path.name}\n\n")
        f.write(f"CLI commands detected: {len(cli_invocables)}\n")
        f.write(f"GUI actions detected:  {len(gui_invocables)}\n\n")

        if cli_invocables:
            f.write("## CLI Capabilities\n\n")
            for inv in cli_invocables:
                f.write(f"### {inv.name}\n\n")
                f.write(f"**Usage:** `{inv.signature}`\n\n")
                if inv.doc_comment:
                    f.write(f"**Description:**\n\n{inv.doc_comment}\n\n")
                if inv.parameters:
                    f.write(f"**Parameters detected:**\n\n{inv.parameters}\n\n")

        if gui_invocables:
            f.write("## GUI Capabilities\n\n")
            for inv in gui_invocables:
                f.write(f"### {inv.name}\n\n")
                f.write(f"**Action:** `{inv.signature}`\n\n")
                if inv.doc_comment:
                    f.write(f"**Description:** {inv.doc_comment}\n\n")

    # ── CLI MCP JSON (CLI invocables only, keeps original naming convention) ─
    tier4_json = out_dir / f"{base_name}_cli_mcp.json"
    write_invocables_json(
        tier4_json,
        cli_invocables,
        dll_path=exe_path,
        tier=4,
        schema_version="2.0.0",
    )

    logger.info(f"Results written to {out_dir}")
    print(f"\nCLI/GUI Analysis Complete")
    print(f"CLI commands: {len(cli_invocables)}  |  GUI actions: {len(gui_invocables)}")
    print(f"Markdown:     {tier4_md}")
    print(f"CLI MCP JSON: {tier4_json}")

    return 0


def main():
    """Main entry point."""
    logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')
    parser = argparse.ArgumentParser(
        description=(
            "MCP Factory — Binary/Executable/Script Analyzer. "
            "Accepts DLLs, EXEs, .NET assemblies, COM objects, SQL files, "
            "Python, PowerShell, JavaScript, TypeScript, Batch, VBScript, "
            "Shell scripts, and more."
        )
    )

    # Required arguments (one of)
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument(
        "--dll", "--target",
        dest="dll",
        type=Path,
        metavar="TARGET",
        help="Path to any target file to analyze (DLL, EXE, .py, .ps1, .sql, .js, .ts, etc.)",
    )
    input_group.add_argument(
        "--exports-raw",
        type=Path,
        help="Path to existing dumpbin /exports output (skip dumpbin run)",
    )

    # Optional arguments
    parser.add_argument(
        "--headers",
        type=Path,
        help="Root directory to search for header files (.h, .hpp, etc.)",
    )
    parser.add_argument(
        "--docs", type=Path, help="Root directory to search for documentation files"
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help=f"Output directory (default: {get_default_output_dir()})",
    )
    parser.add_argument(
        "--tag", type=str, default="", help="Tag to append to output filenames"
    )
    parser.add_argument(
        "--dumpbin",
        type=str,
        default="dumpbin",
        help="Path to dumpbin.exe or name on PATH",
    )
    parser.add_argument(
        "--undname",
        type=str,
        default="undname",
        help="Path to undname.exe or name on PATH",
    )
    parser.add_argument(
        "--no-demangle",
        action="store_true",
        help="Skip C++ name demangling step",
    )
    parser.add_argument(
        "--max-doc-hits",
        type=int,
        default=2,
        help="Maximum documentation file hits per export",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show detailed pipeline steps with spinner animation",
    )

    args = parser.parse_args()

    # Configure verbose mode for Spinner
    Spinner.enabled = args.verbose
    
    if args.verbose:
        print("\n" + "=" * 60)
        print("MCP FACTORY - ADVANCED BINARY ANALYZER")
        print("=" * 60 + "\n")

    # Resolve output directory
    out_dir = args.out or get_default_output_dir()
    out_dir.mkdir(parents=True, exist_ok=True)

    # Build base filename from DLL or use generic name
    if args.dll:
        base_name = args.dll.stem
        dll_path = args.dll
    else:
        base_name = "analysis"
        dll_path = None

    if args.tag:
        base_name = f"{base_name}_{args.tag}"

    # ── §2.a: Accept installed directory (c:\Program Files\AppD\) ────────────
    if dll_path and dll_path.is_dir():
        return analyze_directory(dll_path, out_dir, args)
    # ─────────────────────────────────────────────────────────────────────────

    # Detect file type and route to appropriate analyzer
    file_type = None
    if dll_path and dll_path.exists():
        file_type = classify_file(dll_path)
        logger.info(f"Detected file type: {file_type.value}")
        
        # Route based on file type
        if file_type == FileType.DOTNET_ASSEMBLY:
            logger.info("Analyzing .NET assembly...")
            return analyze_dotnet_assembly(dll_path, out_dir, base_name, args)
        elif file_type == FileType.COM_OBJECT:
            logger.info("Analyzing COM object...")
            # Run COM analysis but don't return - fall through to native analysis
            # because many COM DLLs (shell32, oleaut32) also have standard exports
            analyze_com_object(dll_path, out_dir, base_name, args)
        elif file_type == FileType.PE_EXE:
            logger.info("Analyzing PE executable (try CLI)...")
            analyze_cli_tool(dll_path, out_dir, base_name, args)
        elif file_type in _SCRIPT_DISPATCH:
            # JIT / scripting / query file — route to language-specific analyzer
            logger.info("Routing to scripting language analyzer: %s", file_type.value)
            return analyze_scripting_language(dll_path, out_dir, base_name, file_type)

        # For PE files (DLL or EXE) that weren't classified as COM_OBJECT,
        # still check if they are registered as COM servers (InProc or LocalServer).
        if file_type in [FileType.PE_DLL, FileType.PE_EXE]:
             # analyze_com_object is safe to call, it checks registry
             logger.info("Checking for associated COM objects (Registry)...")
             analyze_com_object(dll_path, out_dir, base_name, args)

        # Otherwise fall through to native DLL analysis

    # Phase 1: Get raw exports
    exports: List[ExportedFunc] = []
    
    # If it's a pure EXE or unknown, dumpbin exports might be empty.
    # We should be graceful.
    allow_no_exports = (file_type == FileType.PE_EXE) or (file_type == FileType.PE_DLL)

    if args.exports_raw:
        # Read from existing raw file
        try:
            raw_text = args.exports_raw.read_text(encoding="utf-8")
            from pe_parse import parse_dumpbin_exports
            exports = parse_dumpbin_exports(raw_text)
        except Exception as e:
            logger.error(f"Error reading exports-raw file: {e}")
            return 1
    elif args.dll:
        # Use pefile to extract exports (Primary Method)
        logger.info(f"Extracting exports from {args.dll} (using pefile)...")
        exports, success = read_pe_exports(args.dll)
        
        # Log result
        if exports:
            logger.info(f"Found {len(exports)} exports")
        else:
            if not allow_no_exports:
                logger.warning(f"No exports found in {args.dll} (and file is classified as PE_DLL)")
                # We can try legacy dumpbin if pefile yields 0 but that probably means 0 exports.
            else:
                logger.info("No exports found (expected for EXE/Internal DLL)")



    if not exports:
        logger.warning("No exports found")
        if not allow_no_exports:
            return 1

    # Phase 2: Deduplicate and demangle, resolve forwarders
    exports = deduplicate_exports(exports)

    if not args.no_demangle:
        demangle_with_undname(exports, args.undname)
    
    # Extract digital signature
    is_signed, publisher = False, None
    if dll_path:
        is_signed, publisher = extract_signature(dll_path)
    
    # Resolve forwarder chains
    forwarding_chain = resolve_forwarders(exports)

    # Phase 3: Match to headers and documentation
    matches: dict = {}
    doc_hits: dict = {}

    if args.headers:
        matches = scan_headers(args.headers, exports)

    if args.docs:
        doc_hits = scan_docs_for_exports(args.docs, exports, args.max_doc_hits)

    # Phase 4: Write tiered outputs
    tier_entries = []

    # Tier 1: Exports + headers + docs
    if args.headers and args.docs and matches:
        tier1_csv = out_dir / f"{base_name}_tier1_api.csv"
        tier1_md = out_dir / f"{base_name}_tier1_api.md"
        write_csv(tier1_csv, exports, matches, doc_hits, is_signed, publisher)
        write_markdown(tier1_md, dll_path or args.dll or Path(base_name), exports, matches, doc_hits)
        tier_entries.append("Tier 1: Exports + headers + docs")

    # Tier 2: Exports + headers
    if args.headers and matches:
        tier2_csv = out_dir / f"{base_name}_tier2_api.csv"
        tier2_md = out_dir / f"{base_name}_tier2_api.md"
        write_csv(tier2_csv, exports, matches, {}, is_signed, publisher)
        write_markdown(tier2_md, dll_path or args.dll or Path(base_name), exports, matches, {})
        tier_entries.append("Tier 2: Exports + headers")

    # Tier 3: Exports + demangle
    if any(e.demangled for e in exports):
        tier3_csv = out_dir / f"{base_name}_tier3_api.csv"
        tier3_md = out_dir / f"{base_name}_tier3_api.md"
        write_csv(tier3_csv, exports, {}, {}, is_signed, publisher)
        write_markdown(tier3_md, dll_path or args.dll or Path(base_name), exports, {}, {})
        tier_entries.append("Tier 3: Exports + demangled names")

    # Tier 4: Exports only (always generated)
    tier4_csv = out_dir / f"{base_name}_tier4_api.csv"
    tier4_md = out_dir / f"{base_name}_tier4_api.md"
    write_csv(tier4_csv, exports, {}, {}, is_signed, publisher)
    write_markdown(tier4_md, dll_path or args.dll or Path(base_name), exports, {}, {})
    tier_entries.append("Tier 4: Exports only")
    
    # Import Analysis (detect capabilities: RPC, COM, networking, etc.)
    logger.info("Analyzing import table for capabilities...")
    import_analysis = analyze_imports(dll_path or args.dll or Path(base_name), args.dumpbin)
    
    # RPC Analysis (if RPC capabilities detected)
    rpc_analysis = None
    rpc_invocables = []
    if import_analysis['summary'].get('has_rpc'):
        logger.info("RPC capabilities detected - analyzing RPC interfaces...")
        rpc_analysis = analyze_rpc(
            dll_path or args.dll or Path(base_name),
            import_analysis['imports']
        )
        rpc_invocables = rpc_to_invocables(rpc_analysis, dll_path or args.dll or Path(base_name))
    
    # Convert exports to Invocables and write MCP JSON
    invocables = exports_to_invocables(
        exports,
        dll_path or args.dll or Path(base_name),
        matches,
        is_signed,
        publisher
    )
    
    # Merge RPC invocables if found
    if rpc_invocables:
        logger.info(f"Adding {len(rpc_invocables)} RPC invocables")
        invocables.extend(rpc_invocables)
    
    if invocables:
        mcp_json_path = out_dir / f"{base_name}_exports_mcp.json"
        write_invocables_json(
            mcp_json_path,
            invocables,
            dll_path=dll_path or args.dll or Path(base_name),
            tier=4,
            schema_version="2.0.0"
        )
    else:
        logger.info(f"No invocables found (exports or RPC), skipping {base_name}_exports_mcp.json")
    
    # Write capabilities summary
    capabilities_md = out_dir / f"{base_name}_capabilities.md"
    with open(capabilities_md, 'w', encoding='utf-8') as f:
        f.write(f"# DLL Capabilities Analysis: {base_name}\n\n")
        f.write("## Summary\n\n")
        f.write(f"Total DLLs imported: {import_analysis['summary']['total_dlls']}\n")
        f.write(f"Total functions imported: {import_analysis['summary']['total_functions']}\n\n")
        
        f.write("## Detected Capabilities\n\n")
        caps = import_analysis['capabilities']
        if caps:
            for cap, info in sorted(caps.items()):
                f.write(f"### {cap.upper()}\n")
                f.write(f"- **DLLs**: {', '.join(info['dlls'])}\n")
                if 'functions' in info and info['functions']:
                    f.write(f"- **Functions detected**: {len(info['functions'])}\n")
                    f.write("  ```\n")
                    for func in info['functions'][:10]:
                        f.write(f"  {func}\n")
                    if len(info['functions']) > 10:
                        f.write(f"  ... and {len(info['functions']) - 10} more\n")
                    f.write("  ```\n")
                if cap == 'networking' and 'protocols' in info:
                    f.write(f"- **Protocols**: {', '.join(info['protocols'])}\n")
                f.write("\n")
        else:
            f.write("No special capabilities detected (standard Win32 API usage)\n\n")
        
        # RPC section
        if rpc_analysis and rpc_analysis.get('has_rpc'):
            f.write("## RPC Interfaces\n\n")
            if rpc_analysis['summary'].get('is_server'):
                f.write("**Mode**: RPC Server\n\n")
            if rpc_analysis['summary'].get('is_client'):
                f.write("**Mode**: RPC Client\n\n")
            
            interfaces = rpc_analysis.get('interfaces', [])
            if interfaces:
                f.write(f"**Interfaces found**: {len(interfaces)}\n\n")
                for iface in interfaces[:5]:
                    f.write(f"- `{iface.uuid}` (v{iface.version})\n")
                if len(interfaces) > 5:
                    f.write(f"- ... and {len(interfaces) - 5} more\n")
                f.write("\n")
            
            pipes = rpc_analysis.get('named_pipes', [])
            if pipes:
                f.write(f"**Named Pipes**: {len(pipes)}\n\n")
                for pipe in pipes[:10]:
                    f.write(f"- `{pipe}`\n")
                f.write("\n")

    # Tier 5: Metadata
    tier5_md = out_dir / f"{base_name}_tier5_metadata.md"
    with open(tier5_md, 'w', encoding='utf-8') as f:
        f.write(f"# DLL Metadata: {base_name}\n\n")
        f.write(f"Total exports: {len(exports)}\n")
        if dll_path and dll_path.exists():
            f.write(f"File size: {dll_path.stat().st_size} bytes\n")
        demangled_count = sum(1 for e in exports if e.demangled)
        f.write(f"Demangled: {demangled_count}\n")
        matched_count = sum(1 for e in exports if e.name in matches)
        f.write(f"Header matched: {matched_count}\n")
        f.write(f"\nDate generated: {__import__('datetime').datetime.now().isoformat()}\n")
    tier_entries.append("Tier 5: Metadata only")

    # Tier summary
    summary_md = out_dir / f"{base_name}_tiers.md"
    write_tier_summary(summary_md, tier_entries)

    # Phase 5: Generate confidence summary
    confidence_summary = generate_confidence_summary(
        exports, matches, is_signed, forwarding_chain, base_name, out_dir
    )

    logger.info(f"Analysis complete. Results in: {out_dir}")
    logger.info(f"Exports found: {len(exports)}")
    logger.info(f"Matched to headers: {sum(1 for e in exports if e.name in matches)}")
    logger.info(f"Demangled: {sum(1 for e in exports if e.demangled)}")
    
    # Print confidence summary to console
    print("\n" + "=" * 60)
    print(confidence_summary)
    print("=" * 60)

    return 0


if __name__ == "__main__":
    sys.exit(main())
