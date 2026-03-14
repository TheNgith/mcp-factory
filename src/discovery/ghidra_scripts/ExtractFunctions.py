# ExtractFunctions.py
# Ghidra headless post-analysis script (Jython / GhidraScript API)
#
# Extracts all functions from the analyzed binary and writes a JSON file
# containing names, signatures, parameter types, calling conventions, and
# whether each function is in the PE export table.
#
# Called by ghidra_analyzer.py via:
#   analyzeHeadless.bat <proj> <name> -import <dll>
#       -postScript ExtractFunctions.py <output_json_path>
#       -scriptPath <this_directory>
#       -deleteProject
#
# The single script argument is the absolute path where JSON should be written.

import json
import os

from ghidra.program.model.symbol import SymbolType  # noqa: F401 (Ghidra Jython env)


def run():
    args = getScriptArgs()  # noqa: F821  (injected by Ghidra GhidraScript context)
    if not args:
        print("ERROR: ExtractFunctions.py requires one argument: <output_json_path>")
        return

    out_path = args[0]

    prog    = getCurrentProgram()  # noqa: F821
    fm      = prog.getFunctionManager()
    sym_tbl = prog.getSymbolTable()

    # ── Collect PE export entry-point addresses ───────────────────────────────
    # analyzeHeadless marks exported symbols as external entry points.
    export_addrs = set()
    ep_iter = sym_tbl.getExternalEntryPointIterator()
    while ep_iter.hasNext():
        export_addrs.add(str(ep_iter.next()))

    # ── Walk all defined functions ────────────────────────────────────────────
    functions = []
    func_iter = fm.getFunctions(True)  # True = forward order
    while func_iter.hasNext():
        func = func_iter.next()

        # Skip thunks (stubs that immediately jump elsewhere) and imports
        if func.isThunk() or func.isExternal():
            continue

        addr_str    = str(func.getEntryPoint())
        is_exported = addr_str in export_addrs

        # -- Parameters -------------------------------------------------------
        params = []
        for p in func.getParameters():
            params.append({
                "name": p.getName(),
                "type": str(p.getDataType()),
                "ordinal": p.getOrdinal(),
            })

        # -- Calling convention -----------------------------------------------
        cc_raw = func.getCallingConventionName()
        # Ghidra names: "__cdecl", "__stdcall", "__fastcall", "unknown", etc.
        cc = str(cc_raw) if cc_raw else "unknown"

        # -- Human-readable signature -----------------------------------------
        # getPrototypeString(bool includeCallingConvention, bool includeReturn)
        try:
            sig = func.getPrototypeString(True, False)
        except Exception:
            sig = func.getName()

        functions.append({
            "name":               func.getName(),
            "address":            addr_str,
            "signature":          sig,
            "calling_convention": cc,
            "return_type":        str(func.getReturnType()),
            "parameters":         params,
            "is_exported":        is_exported,
        })

    # ── Write JSON ────────────────────────────────────────────────────────────
    result = {
        "binary":         prog.getName(),
        "function_count": len(functions),
        "functions":      functions,
    }

    # Ensure the output directory exists (Ghidra runs with its own cwd)
    out_dir = os.path.dirname(out_path)
    if out_dir and not os.path.exists(out_dir):
        os.makedirs(out_dir)

    with open(out_path, "w") as fh:
        json.dump(result, fh, indent=2)

    exported_count = sum(1 for f in functions if f["is_exported"])
    print("ExtractFunctions: wrote {} functions ({} exported) to {}".format(
        len(functions), exported_count, out_path))


run()
