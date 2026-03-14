"""
tlb_analyzer.py - Extract COM interface definitions from Type Libraries.

Uses pywin32 (pythoncom) to parse embedded Type Libraries (.tlb) 
within DLLs/EXEs to extract Interface and Method definitions.
"""
import logging
from pathlib import Path
from typing import List, Dict, Optional, Any

try:
    import pythoncom
except ImportError:
    pythoncom = None

logger = logging.getLogger(__name__)

def scan_type_library(dll_path: Path) -> List[Dict[str, Any]]:
    """
    Load and parse the Type Library embedded in the given file.
    Returns a list of parsed interfaces/coclasses with their methods.
    """
    results = []
    
    if pythoncom is None:
        logger.warning("pythoncom (pywin32) not installed, skipping Type Library scan")
        return []

    try:
        # Load the Type Library
        # This will raise pythoncom.com_error if no TLB is present
        tlb = pythoncom.LoadTypeLib(str(dll_path))
        count = tlb.GetTypeInfoCount()
        
        logger.info(f"Common Type Library found: {count} type infos")
        
        # First pass: collect all CoClass CLSIDs from this TLB.
        # TKIND_DISPATCH interfaces (kind=4) do not express inheritance via
        # cImplTypes in the type library — e.g. IShellDispatch6 does NOT
        # declare IShellDispatch as a base in the TLB, even though the live
        # COM object exposes all accumulated methods via IDispatch.
        # Strategy: attach every TLB CoClass CLSID to every interface entry
        # so _execute_com_bridge can try each one with win32com.client.Dispatch.
        all_coclass_clsids: List[str] = []
        for i in range(count):
            try:
                ti   = tlb.GetTypeInfo(i)
                attr = ti.GetTypeAttr()
                if attr.typekind == 5:  # TKIND_COCLASS
                    all_coclass_clsids.append(str(attr.iid))
            except Exception:
                pass

        # Second pass: extract interface/dispatch methods and attach the CoClass CLSID.
        for i in range(count):
            try:
                # Get basic info
                type_info = tlb.GetTypeInfo(i)
                # GetDocumentation returns (name, docString, helpContext, helpFile)
                doc_tuple = tlb.GetDocumentation(i)
                type_name = doc_tuple[0]
                type_doc = doc_tuple[1]
                
                # Get Type Attributes
                attr = type_info.GetTypeAttr()
                # TypeKind enum:
                # TKIND_ENUM=0, TKIND_RECORD=1, TKIND_MODULE=2, TKIND_INTERFACE=3, 
                # TKIND_DISPATCH=4, TKIND_COCLASS=5, TKIND_ALIAS=6, TKIND_UNION=7
                kind = attr.typekind
                guid = str(attr.iid)
                
                if kind in (3, 4): # Interface or Dispatch
                    methods = []
                    # Iterate functions
                    for j in range(attr.cFuncs):
                        try:
                            # GetFuncDesc returns keys like memid, scodeArray, etc.
                            func_desc = type_info.GetFuncDesc(j)
                            
                            # GetNames returns list of [funcName, paramName1, paramName2...]
                            # This is the most reliable way to get readable signatures
                            names = type_info.GetNames(func_desc.memid)
                            func_name = names[0]
                            param_names = names[1:]

                            # Emit params as dicts so downstream consumers
                            # (generate.py, gui_bridge.py) get name/type/description
                            # without needing a re-parse step.
                            params = [
                                {
                                    "name": n,
                                    "type": "variant",
                                    "description": f"COM VARIANT parameter {n}",
                                }
                                for n in param_names
                            ]
                            
                            methods.append({
                                'name': func_name,
                                'parameters': params,
                                'memid': func_desc.memid,
                                'invkind': func_desc.invkind # 1=Func, 2=PropGet, 4=PropPut
                            })
                        except Exception as e:
                            # Some functions might fail to resolve names
                            pass
                    
                    if methods:
                        entry: Dict[str, Any] = {
                            'name': type_name,
                            'guid': guid,
                            'kind': 'interface' if kind == 3 else 'dispatch',
                            'description': type_doc,
                            'methods': methods,
                            'confidence': 'guaranteed',  # TLB info is authoritative
                            # All CoClass CLSIDs in this TLB — executor tries each
                            # one until it finds the method via IDispatch.
                            'coclass_candidates': list(all_coclass_clsids),
                        }
                        results.append(entry)
                        
                elif kind == 5: # CoClass (The actual object class)
                    # CoClasses don't usually have methods directly, they implement interfaces
                    results.append({
                        'name': type_name,
                        'guid': guid,
                        'kind': 'coclass',
                        'description': type_doc,
                        'methods': [],
                        'confidence': 'guaranteed'
                    })
                    
            except Exception as e:
                logger.warning(f"Error inspecting TypeInfo {i} in {dll_path.name}: {e}")
                continue
                
    except pythoncom.com_error:
        # Expected for many DLLs that aren't COM servers
        pass
    except Exception as e:
        logger.error(f"Error scanning TypeLib for {dll_path}: {e}")
         
    return results

def format_tlb_signature(method_name: str, params: List) -> str:
    """Format a display string for a TLB method.

    params may be a list of strings (legacy) or a list of dicts with a
    'name' key (current format from scan_type_library).
    """
    names = [p["name"] if isinstance(p, dict) else p for p in params]
    param_str = ", ".join(names)
    return f"HRESULT {method_name}({param_str})"
