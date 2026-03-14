"""
com_scan.py - COM object discovery and type library analysis.

Implements:
- Registry scanning (CLSID, AppID, ProgID)
- Type library (TLB) parsing (via tlb_analyzer)
"""

import logging
from pathlib import Path
from typing import List, Dict, Optional

from schema import Invocable
from tlb_analyzer import scan_type_library, format_tlb_signature

logger = logging.getLogger(__name__)


def scan_com_registry(target_name: str) -> List[Dict]:
    """Scan registry for COM objects registered by this DLL.
    
    Args:
        target_name: Name of target DLL/executable (e.g., "ole32.dll")
        
    Returns:
        List of COM objects found with CLSID, ProgID, and path info
    """
    try:
        import winreg
    except ImportError:
        logger.warning("winreg not available (Windows only)")
        return []
    
    results = []
    target_lower = target_name.lower()
    
    try:
        # Search HKEY_CLASSES_ROOT\CLSID
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, "CLSID") as clsid_key:
            num_subkeys = winreg.QueryInfoKey(clsid_key)[0]
            
            for i in range(min(num_subkeys, 10000)):  # Increased limit for better coverage
                try:
                    clsid = winreg.EnumKey(clsid_key, i)
                    
                    found_match = False
                    server_path = None
                    server_type = None

                    # Check InprocServer32 (DLLs)
                    try:
                        with winreg.OpenKey(clsid_key, f"{clsid}\\InprocServer32") as server_key:
                            server_path = winreg.QueryValueEx(server_key, "")[0]
                            server_type = "InprocServer32"
                    except WindowsError:
                        pass

                    # Check LocalServer32 (EXEs)
                    if not server_path:
                        try:
                            with winreg.OpenKey(clsid_key, f"{clsid}\\LocalServer32") as server_key:
                                server_path = winreg.QueryValueEx(server_key, "")[0]
                                server_type = "LocalServer32"
                        except WindowsError:
                            pass

                    if server_path:
                        # Clean up path (remove quotes, args)
                        clean_path = Path(server_path.replace('"', '').split(' -')[0].split(' /')[0])
                        server_filename = clean_path.name.lower()
                        
                        if target_lower == server_filename:
                            # Found a match - try to get friendly name
                            friendly_name = None
                            try:
                                with winreg.OpenKey(clsid_key, clsid) as name_key:
                                    friendly_name = winreg.QueryValueEx(name_key, "")[0]
                            except WindowsError:
                                pass
                            
                            # Try to get ProgID
                            progid = None
                            try:
                                with winreg.OpenKey(clsid_key, f"{clsid}\\ProgID") as progid_key:
                                    progid = winreg.QueryValueEx(progid_key, "")[0]
                            except WindowsError:
                                pass
                            
                            results.append({
                                'clsid': clsid,
                                'name': friendly_name or clsid,
                                'progid': progid,
                                'server_path': server_path,
                                'inproc': (server_type == "InprocServer32"),
                                'server_type': server_type
                            })

                except WindowsError:
                    continue
        
        logger.info(f"Found {len(results)} COM objects for {target_name}")
        return results
    
    except Exception as e:
        logger.error(f"Error scanning COM registry: {e}")
        return []


def com_objects_to_invocables(com_objects: List[Dict], dll_path: Optional[Path] = None) -> List[Invocable]:
    """Convert COM object registry entries AND TLB entries to Invocable records."""
    invocables = []
    
    # 1. Process Registry-discovered Objects (CoClasses)
    for obj in com_objects:
        # COM objects from registry are GUARANTEED - they're officially registered with CLSID
        inv = Invocable(
            name=obj.get('progid') or obj['clsid'],
            source_type='com',
            signature=f"CLSID: {obj['clsid']}",
            doc_comment=obj.get('name'),
            parameters=None,   # Registry entries have no enumerable parameter list
            dll_path=str(dll_path) if dll_path else obj.get('server_path'),
            clsid=obj['clsid'],
            confidence='high', # Registry exists, but methods unknown
        )
        invocables.append(inv)

    # 2. Process Type Library (TLB) embedded in the DLL (if provided)
    if dll_path and dll_path.exists():
        tlb_results = scan_type_library(dll_path)
        
        for item in tlb_results:
            # We are interested in Interfaces (methods) and CoClasses
            item_name = item['name']
            
            # For interfaces, create invocables for each method
            if item['kind'] in ('interface', 'dispatch'):
                for method in item.get('methods', []):
                    # Signature formatting — params may be dicts or plain strings
                    _param_names = [
                        p["name"] if isinstance(p, dict) else p
                        for p in method["parameters"]
                    ]
                    sig_str = format_tlb_signature(method['name'], _param_names)

                    # Build a C-style parameter string for Invocable (which parses
                    # it back into name+type dicts via _parse_parameters_to_list).
                    param_str = ", ".join([f"VARIANT {n}" for n in _param_names])
                    
                    inv = Invocable(
                        name=f"{item_name}::{method['name']}", # Namespaced name
                        source_type='com',
                        clsid=item['guid'], # Use Interface IID as CLSID ref
                        confidence='guaranteed', # Extracted from TLB
                        signature=sig_str,
                        parameters=param_str,
                        return_type="HRESULT",
                        doc_comment=item.get('description', f"Method of interface {item_name}"),
                        dll_path=str(dll_path)
                    )
                    invocables.append(inv)
    
    return invocables


def parse_type_library(tlb_path: Path) -> List[dict]:
    """Parse type library using PowerShell and Get-TypeLib (if available).
    
    Args:
        tlb_path: Path to .tlb file or DLL with embedded TLB
        
    Returns:
        List of interface/method definitions
    """
    if not tlb_path.exists():
        logger.warning(f"Type library not found: {tlb_path}")
        return []
    
    try:
        # Use PowerShell to load and inspect type library via COM
        ps_script = f"""
        try {{
            # Try to load type library
            $typeLib = [System.Runtime.InteropServices.Marshal]::BindToMoniker("{tlb_path}")
            if ($typeLib) {{
                Write-Output "SUCCESS: Type library loaded"
            }} else {{
                Write-Output "FAILED: Could not load type library"
            }}
        }} catch {{
            Write-Output "ERROR: $($_.Exception.Message)"
        }}
        """
        
        import subprocess
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if "SUCCESS" in result.stdout:
            logger.info(f"Type library loaded successfully: {tlb_path.name}")
            # Note: Full parsing would require extensive COM interop
            # For now, just confirm it's loadable
            return [{
                'path': str(tlb_path),
                'size': tlb_path.stat().st_size,
                'loadable': True,
                'note': 'Full TLB method enumeration requires COM interop - returning basic info'
            }]
        else:
            logger.warning(f"Could not load type library: {result.stdout}")
            return []
            
    except Exception as e:
        logger.warning(f"Error parsing type library: {e}")
        return []


def enumerate_idispatch_safe(clsid: str) -> List[dict]:
    """Attempt safe IDispatch enumeration via PowerShell.
    
    Args:
        clsid: CLSID of COM object to enumerate
        
    Returns:
        List of method names (if available)
    """
    try:
        # Use PowerShell to safely instantiate and enumerate
        ps_script = f"""
        try {{
            $obj = [System.Activator]::CreateInstance([System.Type]::GetTypeFromCLSID("{clsid}"))
            if ($obj) {{
                $methods = $obj.GetType().GetMethods() | Select-Object -ExpandProperty Name
                $methods | ForEach-Object {{ Write-Output "METHOD:$_" }}
            }}
        }} catch {{
            Write-Output "ERROR:$($_.Exception.Message)"
        }}
        """
        
        import subprocess
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        methods = []
        for line in result.stdout.split('\n'):
            if line.startswith('METHOD:'):
                method_name = line.replace('METHOD:', '').strip()
                if method_name:
                    methods.append({'name': method_name, 'type': 'IDispatch'})
        
        if methods:
            logger.info(f"Enumerated {len(methods)} methods for {clsid}")
        
        return methods
        
    except Exception as e:
        logger.warning(f"Could not enumerate IDispatch for {clsid}: {e}")
        return []
