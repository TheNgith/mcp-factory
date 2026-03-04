"""
schema.py - Data models and output writers for invocable records.

Defines the core data structures for representing exported functions and
provides writers for CSV, JSON, and Markdown output formats.
"""

import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class ExportedFunc:
    """Represents an exported function from a DLL."""
    name: str
    ordinal: Optional[int] = None
    hint: Optional[str] = None
    rva: Optional[str] = None
    forwarded_to: Optional[str] = None
    demangled: Optional[str] = None


@dataclass
class Invocable:
    """Unified record for any callable (export, COM object, CLI command, etc)."""
    name: str
    source_type: str  # "export" | "com" | "cli" | "dotnet" | "rpc"
    ordinal: Optional[int] = None
    hint: Optional[str] = None
    rva: Optional[str] = None
    return_type: Optional[str] = None
    parameters: Optional[str] = None
    signature: Optional[str] = None
    doc_comment: Optional[str] = None
    header_file: Optional[str] = None
    header_line: Optional[int] = None
    demangled: Optional[str] = None
    doc_files: Optional[str] = None  # semicolon-separated
    confidence: str = "medium"  # "low", "medium", "high"
    confidence_reasons: Optional[List[str]] = None
    is_forwarded: bool = False
    publisher: Optional[str] = None
    is_signed: bool = False
    
    # MCP-specific fields
    dll_path: Optional[str] = None
    assembly_path: Optional[str] = None
    type_name: Optional[str] = None
    is_static: Optional[bool] = None
    clsid: Optional[str] = None

    # GUI-specific fields (pywinauto)
    gui_menu_path: Optional[List[str]] = None   # e.g. ["File", "Save As"]
    gui_action_type: Optional[str] = None       # "menu_click" | "keyboard_shortcut" | "button_click" | "type_text" | "save_as" | "get_text" | "close_app"
    gui_backend: Optional[str] = None           # "win32" (classic) | "uia" (WinUI3/MSIX) — detected at discovery time
    gui_kb_shortcut: Optional[str] = None       # e.g. "^n" — keyboard fallback for WinUI3 apps that lack Win32 menus
    gui_button_name: Optional[str] = None       # e.g. "7", "÷" — UIA button label for button_click actions
    
    def to_dict(self) -> dict:
        """Convert Invocable to flat MCP-compatible JSON dict."""
        return {
            "name": self.name,
            "kind": self.source_type,
            "confidence": self.confidence,
            "description": self.doc_comment or "",
            "return_type": self.return_type or "unknown",
            "parameters": self._parse_parameters_to_list(),
            "execution": self._get_execution_metadata(),
        }
    
    def _generate_tool_id(self) -> str:
        """Generate unique MCP tool identifier."""
        import re
        
        # Clean name for use in ID
        clean_name = re.sub(r'[^a-zA-Z0-9_]', '_', self.name)
        
        # Add source type prefix
        prefix_map = {
            "export": "dll",
            "dotnet": "net",
            "com": "com",
            "cli": "cli",
            "rpc": "rpc"
        }
        prefix = prefix_map.get(self.source_type, "unk")
        
        # Add ordinal if available
        suffix = f"_{self.ordinal}" if self.ordinal else ""
        
        return f"{prefix}_{clean_name}{suffix}"
    
    def _build_prototype(self) -> str:
        """Build function prototype string."""
        ret = self.return_type or "void"
        params = self.parameters or ""
        return f"{ret} {self.name}({params})"
    
    def _parse_parameters_to_schema(self) -> dict:
        """Convert parameter string to JSON Schema for MCP."""
        if not self.parameters or self.parameters.strip().lower() in ('void', ''):
            return {
                "type": "object",
                "properties": {},
                "required": []
            }
        
        properties = {}
        required = []
        
        # Parse parameter string (e.g., "int x, char* y, DWORD flags")
        import re
        
        # Split by comma, handling nested types
        params = self._split_parameters(self.parameters)
        
        for i, param in enumerate(params):
            param = param.strip()
            if not param:
                continue
            
            # Extract type and name
            param_info = self._parse_single_parameter(param, i)
            if param_info:
                properties[param_info["name"]] = {
                    "type": param_info["json_type"],
                    "description": f"{param_info['c_type']}"
                }
                required.append(param_info["name"])
        
        return {
            "type": "object",
            "properties": properties,
            "required": required
        }

    def _parse_parameters_to_list(self) -> list:
        """Convert parameter string to a flat list of parameter dicts for MCP."""
        if not self.parameters or self.parameters.strip().lower() in ('void', ''):
            return []

        result = []
        params = self._split_parameters(self.parameters)

        for i, param in enumerate(params):
            param = param.strip()
            if not param:
                continue
            param_info = self._parse_single_parameter(param, i)
            if param_info:
                result.append({
                    "name": param_info["name"],
                    "type": param_info["json_type"],
                    "required": True,
                    "description": param_info["c_type"],
                })

        return result

    def _split_parameters(self, params: str) -> List[str]:
        """Split parameter string by commas, respecting nested types."""
        result = []
        current = ""
        depth = 0
        
        for char in params:
            if char in "<([":
                depth += 1
            elif char in ">)]":
                depth -= 1
            elif char == ',' and depth == 0:
                result.append(current)
                current = ""
                continue
            current += char
        
        if current:
            result.append(current)
        
        return result
    
    def _parse_single_parameter(self, param: str, index: int) -> Optional[dict]:
        """Parse a single parameter into name and type.

        Handles three calling conventions:
          - C/C++:   ``type name``  or  ``type* name``  (last token = name)
          - Python:  ``name: type`` or  ``name: type = default``
          - SQL:     ``@name TYPE`` or  ``name TYPE(n)``  (first token = name)
        """
        import re

        param = param.strip()
        if not param:
            return None

        # Remove const, volatile, etc.
        param = re.sub(r'\b(const|volatile|static|extern)\s+', '', param).strip()

        # Strip default value for name/type extraction only
        base = param.split('=')[0].strip()

        # ── Python-style: "name: type" ────────────────────────────────────
        colon_m = re.match(r'^(\w+)\s*:\s*(.+)$', base)
        if colon_m:
            name = colon_m.group(1).strip()
            c_type = colon_m.group(2).strip()
            return {"name": name, "c_type": c_type, "json_type": self._c_type_to_json_type(c_type)}

        tokens = base.split()
        if not tokens:
            return None
        if len(tokens) == 1:
            return {"name": f"arg{index}", "c_type": tokens[0],
                    "json_type": self._c_type_to_json_type(tokens[0])}

        # ── SQL @-prefixed: "@name TYPE" ──────────────────────────────────
        if tokens[0].startswith('@'):
            name = tokens[0].lstrip('@')
            c_type = ' '.join(tokens[1:])
            return {"name": name, "c_type": c_type, "json_type": self._c_type_to_json_type(c_type)}

        # ── SQL size-constraint: "name TYPE(n)" — last token has parens ───
        if '(' in tokens[-1]:
            name = tokens[0].strip('*&')
            c_type = ' '.join(tokens[1:])
            return {"name": name, "c_type": c_type, "json_type": self._c_type_to_json_type(c_type)}

        # ── Default: C-style "type name" ──────────────────────────────────
        name = tokens[-1].strip('*&') or f"arg{index}"
        c_type = ' '.join(tokens[:-1])
        return {"name": name, "c_type": c_type, "json_type": self._c_type_to_json_type(c_type)}
    
    def _c_type_to_json_type(self, c_type: str) -> str:
        """Map C/C++ type to JSON Schema type."""
        c_type_lower = c_type.lower().strip()
        
        # 1. Pointers
        # void* or LPVOID -> integer (address) or string? 
        # Most FFI tooling (ctypes etc) can handle integers as pointers. 
        # But LLMs are better at "0x1234" strings often.
        # Let's check for specific string pointers first.
        
        # String pointers (const char*, LPWSTR, etc)
        if any(x in c_type_lower for x in ['char*', 'wchar*', 'str*', 'lpcstr', 'lpwstr', 'lpcwstr', 'bstr']):
            return "string"

        # Other pointers -> integer (addresses usually 64-bit ints) or string?
        # Current logic said string. Let's keep strict pointers as string for safety if they are opaque handles.
        if '*' in c_type or 'ptr' in c_type_lower or 'handle' in c_type_lower:
            return "string"
        
        # 2. Integers
        # Broad matching for integer types
        int_types = [
            'int', 'long', 'short', 'byte', 'dword', 'word', 'uint', 
            'size_t', 'hresult', 'bool', 'boolean', 'unsigned', 'ulong',
            'hwnd', 'int32', 'int64', 'uptr'
        ]
        
        # Split tokens to avoid matching "pointer" in "intptr" falsely if not careful, 
        # but logic above handles * and ptr.
        if any(t in c_type_lower for t in int_types):
            return "integer"
        
        # 3. Floating point
        if any(t in c_type_lower for t in ['float', 'double', 'decimal']):
            return "number"
        
        # 4. Characters
        if c_type_lower in ['char', 'wchar_t']:
             return "string" # Treat single char as string usually easier for LLMs

        # 5. String types (explicit)
        if any(t in c_type_lower for t in ['string', 'str', 'wchar']):
            return "string"
        
        # Default to string for unknown structures/unions to be safe
        return "string"
    
    def _get_execution_metadata(self) -> dict:
        """Generate execution metadata for MCP server."""
        if self.source_type == "export":
            return {
                "method": "dll_import",
                "dll_path": self.dll_path,
                "function_name": self.name,
                "calling_convention": "stdcall",  # Default, could be enhanced
                "charset": "unicode" if 'W' in self.name else "ansi"
            }
        
        elif self.source_type == "dotnet":
            return {
                "method": "dotnet_reflection",
                "assembly_path": self.assembly_path,
                "type_name": self.type_name,
                "function_name": self.name,
                "is_static": self.is_static or False,
            }

        elif self.source_type == "com":
            return {
                "method": "com_dispatch",
                "clsid": self.clsid,
                "interface": "IDispatch",
                "function_name": self.name,
            }

        elif self.source_type == "cli":
            return {
                "method": "subprocess",
                "executable_path": self.dll_path,  # dll_path holds exe path for CLI
                "arg_style": "flag",
            }

        elif self.source_type == "rpc":
            return {
                "method": "rpc_call",
                "endpoint": self.parameters,
                "interface_uuid": self.signature.split(": ")[-1] if "UUID:" in (self.signature or "") else None,
                "dll_path": self.dll_path,
            }

        elif self.source_type == "python_function":
            return {
                "method": "python_subprocess",
                "module_path": self.dll_path,
                "function_name": self.name,
                "example": (
                    f"python -c \""
                    f"import importlib.util; spec=importlib.util.spec_from_file_location('m',r'{self.dll_path}'); "
                    f"m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); "
                    f"print(m.{self.name}(...))\""
                ),
            }

        elif self.source_type == "powershell_function":
            return {
                "method": "powershell",
                "script_path": self.dll_path,
                "function_name": self.name,
                "example": f"powershell -NoProfile -File \"{self.dll_path}\" # then call {self.name}",
            }

        elif self.source_type == "batch_label":
            return {
                "method": "cmd_call",
                "script_path": self.dll_path,
                "label": self.name,
                "example": f"cmd /c call \"{self.dll_path}\" :{self.name}",
            }

        elif self.source_type == "shell_function":
            return {
                "method": "bash",
                "script_path": self.dll_path,
                "function_name": self.name,
                "example": f"bash -c 'source \"{self.dll_path}\"; {self.name} ...'",
            }

        elif self.source_type == "ruby_method":
            return {
                "method": "ruby",
                "script_path": self.dll_path,
                "method_name": self.name,
                "example": f"ruby -r '{self.dll_path}' -e 'puts {self.name}(...)'",
            }

        elif self.source_type == "php_function":
            return {
                "method": "php",
                "script_path": self.dll_path,
                "function_name": self.name,
                "example": f"php -r \"require '{self.dll_path}'; echo {self.name}(...);\"",
            }

        elif self.source_type in ("js_function", "js_arrow", "js_module", "cjs_export"):
            return {
                "method": "node",
                "script_path": self.dll_path,
                "function_name": self.name,
                "example": f"node -e \"const m=require('{self.dll_path}'); console.log(m.{self.name}(...))\"",
            }

        elif self.source_type == "js_cli":
            return {
                "method": "node",
                "script_path": self.dll_path,
                "example": f"node \"{self.dll_path}\" --help",
            }

        elif self.source_type == "ts_method":
            return {
                "method": "ts-node",
                "script_path": self.dll_path,
                "function_name": self.name,
                "example": f"ts-node -e \"const m=require('{self.dll_path}'); console.log(m.{self.name}(...))\"",
            }

        elif self.source_type in ("vbscript_function", "vbscript_sub"):
            return {
                "method": "cscript",
                "script_path": self.dll_path,
                "function_name": self.name,
                "example": f"cscript //nologo \"{self.dll_path}\"",
            }

        elif self.source_type == "shell_script":
            return {
                "method": "bash",
                "script_path": self.dll_path,
                "example": f"bash \"{self.dll_path}\"",
            }

        elif self.source_type == "batch_script":
            return {
                "method": "cmd",
                "script_path": self.dll_path,
                "example": f"cmd /c \"{self.dll_path}\"",
            }

        elif self.source_type == "gui_action":
            meta: dict = {
                "method": "gui_action",
                "exe_path": self.dll_path or "",
                "action_type": self.gui_action_type or "menu_click",
                "menu_path": self.gui_menu_path or [],
                "gui_backend": self.gui_backend or "win32",
            }
            if self.gui_kb_shortcut:
                meta["kb_shortcut"] = self.gui_kb_shortcut
            if self.gui_action_type == "keyboard_shortcut" and self.gui_kb_shortcut:
                meta["keys"] = self.gui_kb_shortcut
            if self.gui_button_name is not None:
                meta["button_name"] = self.gui_button_name
            return meta

        elif self.source_type in ("sql_procedure", "sql_function", "sql_view",
                                   "sql_table", "sql_trigger"):
            sql_kind = self.source_type.replace("sql_", "")
            if sql_kind == "procedure":
                stmt = self.signature or f"EXEC {self.name}"
            elif sql_kind == "function":
                stmt = self.signature or f"SELECT {self.name}(...)"
            else:
                stmt = f"SELECT * FROM {self.name}"
            return {
                "method": "sql_exec",
                "connection_required": True,
                "object_type": sql_kind,
                "source_file": self.dll_path,
                "statement": stmt,
            }

        elif self.source_type == "openapi_operation":
            # Extract HTTP method + path from signature like "GET /customers({...})"
            sig = self.signature or ""
            import re as _re
            m = _re.match(r'^(?P<http>[A-Z]+)\s+(?P<path>[^(]+)', sig)
            http_method = m.group("http").lower() if m else "get"
            path = m.group("path").strip() if m else "/"
            return {
                "method": "http_request",
                "http_method": http_method,
                "path": path,
                "content_type": "application/json",
                "notes": "Call via HTTP against the OpenAPI server base URL",
            }

        elif self.source_type == "jsonrpc_method":
            return {
                "method": "jsonrpc",
                "rpc_version": "2.0",
                "method_name": self.name,
                "transport": "http",
                "content_type": "application/json",
                "notes": 'POST {"jsonrpc":"2.0","method":"%s","params":[...],"id":1}' % self.name,
            }

        elif self.source_type == "soap_operation":
            interface = self.type_name or ""
            return {
                "method": "soap",
                "transport": "http",
                "action": self.name,
                "interface": interface,
                "content_type": "text/xml; charset=utf-8",
                "notes": f"SOAP 1.1 document/literal — SOAPAction: {self.name}",
            }

        elif self.source_type == "corba_method":
            iface = self.type_name or self.name
            return {
                "method": "corba_iiop",
                "interface": iface,
                "operation": self.name,
                "transport": "iiop",
                "notes": "Invoke via CORBA ORB (e.g. omniORB, JacORB) over IIOP",
            }

        elif self.source_type.startswith("jndi"):
            return {
                "method": "jndi_lookup",
                "lookup_name": (self.parameters or self.name),
                "initial_context_factory": "javax.naming.InitialContext",
                "notes": "Requires a running JNDI provider (LDAP/RMI/IIOP)",
            }

        elif self.source_type == "pdb_symbol":
            return {
                "method": "dll_import",
                "dll_path": self.dll_path,
                "function_name": self.name,
                "source": "pdb_symbols",
                "notes": "Function resolved from PDB debug symbols",
            }

        else:
            return {
                "method": "unknown",
                "notes": f"Execution method not defined for source_type: {self.source_type}",
            }



@dataclass
class MatchInfo:
    """Information extracted from header file about a function."""
    function: str
    return_type: str
    parameters: str
    doc_comment: str
    header_file: str
    line: int
    prototype: str


def exports_to_invocables(
    exports: List[ExportedFunc],
    dll_path: Path,
    matches: Dict[str, MatchInfo] = None,
    is_signed: bool = False,
    publisher: str = None
) -> List[Invocable]:
    """Convert ExportedFunc list to Invocable list for native DLL exports.
    
    Args:
        exports: List of ExportedFunc objects from dumpbin
        dll_path: Path to the DLL file
        matches: Optional header file matches
        is_signed: Whether the DLL is digitally signed (extracted from Security Directory[4])
        publisher: Publisher name from certificate
        
    Returns:
        List of Invocable objects
    """
    matches = matches or {}
    invocables = []
    
    # Extract signature if not provided
    if is_signed is False and publisher is None:
        from signature import get_signature_info
        is_signed, publisher = get_signature_info(dll_path)
    
    # Check if this is a well-known system DLL (high confidence by default)
    dll_name = dll_path.name.lower() if hasattr(dll_path, 'name') else str(dll_path).lower()
    is_system_dll = any(dll_name.endswith(name) for name in [
        'kernel32.dll', 'kernelbase.dll', 'user32.dll', 'gdi32.dll',
        'advapi32.dll', 'ntdll.dll', 'shell32.dll', 'ole32.dll',
        'oleaut32.dll', 'combase.dll', 'rpcrt4.dll', 'ws2_32.dll',
        'winhttp.dll', 'wininet.dll', 'bcrypt.dll', 'crypt32.dll',
        'msvcrt.dll', 'ucrtbase.dll'
    ])
    
    for export in exports:
        match = matches.get(export.name)

        # ----------------------------------------------------------------
        # CONFIDENCE SCORING: compute factors from actual extracted data
        # first, then derive the label. The label must never contradict
        # the factors — that makes the JSON self-consistent and honest
        # for downstream LLM consumers.
        # ----------------------------------------------------------------

        # Step 1: measure what we actually have from the match
        has_signature   = bool(match and match.prototype)
        has_return_type = bool(match and match.return_type)
        _params = (match.parameters or "").strip().lower() if match else ""
        has_parameters  = bool(match and _params and _params not in ('', 'void'))
        has_documentation = bool(match and match.doc_comment)

        # Step 2: derive confidence from factors (factors → label, not label → factors)
        confidence_reasons = ["exported from DLL"]

        if has_signature and has_parameters and has_return_type:
            # Full header match: we know exactly how to call this
            confidence = "guaranteed"
            confidence_reasons.append("complete signature from header file")
        elif has_signature and (has_parameters or has_return_type):
            # Partial header match: structure present but incomplete
            confidence = "high"
            confidence_reasons.append("partial signature from header file")
        elif export.demangled:
            # C++ mangled name decoded: we know types but not all params
            confidence = "medium"
            confidence_reasons.append("demangled C++ name available")
        elif is_signed and is_system_dll:
            # Trusted source but no extracted data — medium, not high
            confidence = "medium"
            confidence_reasons.append("well-known signed system API (no header match)")
            if publisher and "Microsoft" in publisher:
                confidence_reasons.append("Microsoft signed")
        elif is_signed:
            confidence = "medium"
            confidence_reasons.append("digitally signed binary")
        elif is_system_dll:
            # Known DLL but unsigned and no header — still only medium
            confidence = "medium"
            confidence_reasons.append("well-known system API (no header match)")
        else:
            # Step 3: name-pattern heuristics as last resort
            confidence = "low"
            name = export.name
            if '_' in name:
                prefix = name.split('_')[0]
                if len(prefix) >= 3 and (prefix.isupper() or prefix.islower()):
                    confidence = "medium"
                    confidence_reasons.append(f"library prefix pattern ({prefix}_*)")
            elif any(name.startswith(p) for p in [
                'Create', 'Get', 'Set', 'Open', 'Close', 'Read', 'Write',
                'Initialize', 'Finalize', 'Register', 'Unregister',
                'Allocate', 'Free', 'Query', 'Release',
                'init', 'destroy', 'alloc', 'dealloc', 'malloc', 'calloc',
                'compress', 'decompress', 'encode', 'decode', 'encrypt', 'decrypt',
                'load', 'save', 'bind', 'connect', 'send', 'recv', 'shutdown'
            ]):
                confidence = "medium"
                confidence_reasons.append("common API pattern")

        if has_documentation:
            confidence_reasons.append("has documentation")

        # Create Invocable
        invocable = Invocable(
            name=export.name,
            source_type="export",
            ordinal=export.ordinal,
            hint=export.hint,
            rva=export.rva,
            return_type=match.return_type if match else None,
            parameters=match.parameters if match else "",
            signature=match.prototype if match else None,
            doc_comment=match.doc_comment if match else None,
            header_file=match.header_file if match else None,
            header_line=match.line if match else None,
            demangled=export.demangled,
            confidence=confidence,
            confidence_reasons=confidence_reasons,
            is_forwarded=bool(export.forwarded_to),
            publisher=publisher,
            is_signed=is_signed,
            dll_path=str(dll_path)
        )
        
        invocables.append(invocable)
    
    return invocables


def write_csv(
    path: Path,
    exports: List[ExportedFunc],
    matches: Dict[str, MatchInfo],
    doc_hits: Dict[str, List[str]],
    is_signed: bool = False,
    publisher: str = None
) -> None:
    """Write exports to CSV file with matched headers and documentation."""
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow([
            'Function', 'Ordinal', 'Hint', 'RVA', 'ForwardedTo',
            'ReturnType', 'Parameters', 'Signature', 'DocComment',
            'HeaderFile', 'Line', 'Demangled', 'DocFiles', 'IsSigned', 'Publisher'
        ])

        for exp in exports:
            mi = matches.get(exp.name)
            docs = doc_hits.get(exp.name, [])

            writer.writerow([
                exp.name,
                exp.ordinal or '',
                exp.hint or '',
                exp.rva or '',
                exp.forwarded_to or '',
                mi.return_type if mi else '',
                mi.parameters if mi else '',
                mi.prototype if mi else '',
                mi.doc_comment if mi else '',
                mi.header_file if mi else '',
                mi.line if mi else '',
                exp.demangled or '',
                ';'.join(docs) if docs else '',
                'Yes' if is_signed else 'No',
                publisher or ''
            ])


def write_json(
    path: Path,
    exports: List[ExportedFunc],
    matches: Dict[str, MatchInfo],
    doc_hits: Dict[str, List[str]]
) -> None:
    """Write exports to JSON file (for MCP schema generation)."""
    import json

    path.parent.mkdir(parents=True, exist_ok=True)

    records = []
    for exp in exports:
        mi = matches.get(exp.name)
        docs = doc_hits.get(exp.name, [])

        record = {
            'name': exp.name,
            'ordinal': exp.ordinal,
            'hint': exp.hint,
            'rva': exp.rva,
            'forwarded_to': exp.forwarded_to,
            'demangled': exp.demangled,
        }

        if mi:
            record.update({
                'return_type': mi.return_type,
                'parameters': mi.parameters,
                'signature': mi.prototype,
                'doc_comment': mi.doc_comment,
                'header_file': str(mi.header_file),
                'header_line': mi.line,
            })

        if docs:
            record['doc_files'] = docs

        records.append(record)

    with open(path, 'w', encoding='utf-8') as f:
        json.dump(records, f, indent=2)


def write_markdown(
    path: Path,
    dll_path: Path,
    exports: List[ExportedFunc],
    matches: Dict[str, MatchInfo],
    doc_hits: Dict[str, List[str]]
) -> None:
    """Write exports to Markdown report."""
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, 'w', encoding='utf-8') as f:
        f.write(f"# API Report: {dll_path.name}\n\n")
        f.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        f.write(f"Total Exports: {len(exports)}\n\n")

        for exp in exports:
            mi = matches.get(exp.name)
            docs = doc_hits.get(exp.name, [])

            f.write(f"## {exp.name}\n\n")

            if exp.ordinal:
                f.write(f"**Ordinal:** {exp.ordinal}\n\n")

            if mi:
                f.write(f"**Signature:** `{mi.prototype}`\n\n")
                f.write(f"**Header:** `{mi.header_file}:{mi.line}`\n\n")

                if mi.doc_comment:
                    f.write(f"**Documentation:**\n\n{mi.doc_comment}\n\n")

                if docs:
                    f.write(f"**Referenced in:**\n\n")
                    for doc in docs:
                        f.write(f"- `{doc}`\n")
                    f.write("\n")

            if exp.demangled and exp.demangled != exp.name:
                f.write(f"**Demangled:** `{exp.demangled}`\n\n")

            f.write("---\n\n")


def write_tier_summary(path: Path, entries: List[str]) -> None:
    """Write summary of all tiers generated."""
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, 'w', encoding='utf-8') as f:
        f.write("# Tier Summary\n\n")
        f.write("Tiers successfully generated:\n\n")
        for entry in entries:
            f.write(f"- {entry}\n")
        f.write("\n")
        f.write("See individual tier files for detailed analysis.\n")


def write_invocables_json(
    path: Path,
    invocables: List[Invocable],
    dll_path: Optional[Path] = None,
    tier: int = 2,
    schema_version: str = "2.0.0"
) -> None:
    """Write Invocable objects to MCP-compatible JSON.
    
    Args:
        path: Output JSON file path
        invocables: List of Invocable objects
        dll_path: Source DLL/assembly path
        tier: Analysis tier level
        schema_version: JSON schema version
    """
    import json
    import os
    
    path.parent.mkdir(parents=True, exist_ok=True)
    
    # Build metadata (do not emit schema_version at top-level)
    metadata = {
        "tier": tier,
        "analysis_timestamp": datetime.now().isoformat(),
        "pipeline_version": "1.0.0"
    }
    
    if dll_path:
        metadata.update({
            "target_path": str(dll_path),
            "target_name": dll_path.name,
            "target_type": dll_path.suffix.lstrip('.'),
            "file_size_bytes": os.path.getsize(dll_path) if dll_path.exists() else 0
        })
    
    # Convert invocables to dicts
    invocable_dicts = [inv.to_dict() for inv in invocables]
    
    # Build final structure (omit top-level schema_version to match demo outputs)
    output = {
        "metadata": metadata,
        "invocables": invocable_dicts,
        "summary": {
            "total_invocables": len(invocables),
            "by_source_type": _count_by_source_type(invocables),
            "by_confidence": _count_by_confidence(invocables)
        }
    }
    
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)


def _count_by_source_type(invocables: List[Invocable]) -> dict:
    """Count invocables by source type."""
    counts = {}
    for inv in invocables:
        counts[inv.source_type] = counts.get(inv.source_type, 0) + 1
    return counts


def _count_by_confidence(invocables: List[Invocable]) -> dict:
    """Count invocables by confidence level."""
    counts = {}
    for inv in invocables:
        counts[inv.confidence] = counts.get(inv.confidence, 0) + 1
    return counts
