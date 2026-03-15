# -*- coding: utf-8 -*-
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

# NOTE: do NOT add any top-level Ghidra imports here (e.g.
# "from ghidra.program.model.symbol import SymbolType").
# Such imports run before run() is defined.  If they fail in the installed
# Ghidra version the entire module dies, run() is never called, and the
# output JSON stays empty — producing the misleading
# "Expecting value: line 1 column 1" error on the Python side.
import json
import os


def _decompile_params(func, decompiler, monitor):
    """Use Ghidra's DecompInterface to recover parameter info for *func*.

    getParameters() on a stripped export-only DLL returns [] because Ghidra
    has no internal call sites to infer argument types from.  The decompiler,
    however, analyses the stack frame and register usage *inside* the function
    body and usually recovers at least the count and size of each parameter.

    Returns a list of {"name", "type", "ordinal"} dicts, or [] on failure.
    """
    try:
        results = decompiler.decompileFunction(func, 30, monitor)
        if not results or not results.decompileCompleted():
            return []
        high_func = results.getHighFunction()
        if not high_func:
            return []
        proto = high_func.getFunctionPrototype()
        params = []
        for i in range(proto.getNumParams()):
            p = proto.getParam(i)
            params.append({
                "name":    p.getName(),
                "type":    str(p.getDataType()),
                "ordinal": i,
            })
        return params
    except Exception as exc:
        print("WARNING: decompiler params for " + func.getName() + ": " + str(exc))
        return []


def run():
    args = getScriptArgs()  # noqa: F821  (injected by Ghidra GhidraScript context)
    if not args:
        print("ERROR: ExtractFunctions.py requires one argument: <output_json_path>")
        return

    out_path = args[0]
    result   = {"binary": "", "function_count": 0, "functions": []}

    try:
        prog    = getCurrentProgram()  # noqa: F821
        fm      = prog.getFunctionManager()
        sym_tbl = prog.getSymbolTable()
        result["binary"] = prog.getName()

        # ── Initialise DecompInterface for richer parameter recovery ──────────
        # getParameters() on a stripped, export-only DLL returns [] because
        # Ghidra has no internal call sites to infer argument types from.
        # DecompInterface analyses the stack frame and register usage INSIDE
        # the function body, so it recovers param count and types even when
        # the export table carries no type information.
        # All imports are done here (inside run()) to avoid the top-level
        # Jython import restriction documented above.
        decompiler = None
        decompiler_monitor = None
        try:
            from ghidra.app.decompiler import DecompInterface  # noqa: F821
            from ghidra.util.task import ConsoleTaskMonitor    # noqa: F821
            decompiler = DecompInterface()
            decompiler.openProgram(prog)
            decompiler_monitor = ConsoleTaskMonitor()
            print("INFO: DecompInterface initialised — will use decompiler params for exported functions")
        except Exception as _dcomp_exc:
            print("WARNING: DecompInterface unavailable, falling back to getParameters(): " + str(_dcomp_exc))

        # ── Collect PE export entry-point addresses ───────────────────────────
        # analyzeHeadless marks exported symbols as external entry points.
        # Wrapped in try/except because the method name varies across Ghidra
        # versions and may not exist on non-PE targets.
        export_addrs = set()
        try:
            ep_iter = sym_tbl.getExternalEntryPointIterator()
            while ep_iter.hasNext():
                export_addrs.add(str(ep_iter.next()))
        except Exception as _ep_exc:
            print("WARNING: getExternalEntryPointIterator failed: " + str(_ep_exc))

        # ── Walk all defined functions ────────────────────────────────────────
        functions = []
        func_iter = fm.getFunctions(True)  # True = forward order
        while func_iter.hasNext():
            func = func_iter.next()

            # Skip thunks (stubs that immediately jump elsewhere) and imports
            if func.isThunk() or func.isExternal():
                continue

            addr_str    = str(func.getEntryPoint())
            is_exported = addr_str in export_addrs

            # -- Parameters ---------------------------------------------------
            # Strategy:
            #   1. For exported functions, try DecompInterface first — it uses
            #      stack-frame / register analysis and recovers param types even
            #      when there are no internal call sites (common for DLL exports).
            #   2. Fall back to getParameters() for internal functions or when
            #      the decompiler is not available / returns nothing.
            params = []
            if is_exported and decompiler is not None:
                params = _decompile_params(func, decompiler, decompiler_monitor)
                if params:
                    print("INFO: decompiler recovered " + str(len(params)) +
                          " params for exported " + func.getName())

            if not params:
                try:
                    for p in func.getParameters():
                        params.append({
                            "name":    p.getName(),
                            "type":    str(p.getDataType()),
                            "ordinal": p.getOrdinal(),
                        })
                except Exception as _p_exc:
                    print("WARNING: params for " + func.getName() + ": " + str(_p_exc))

            # -- Calling convention -------------------------------------------
            cc_raw = func.getCallingConventionName()
            cc = str(cc_raw) if cc_raw else "unknown"

            # -- Human-readable signature -------------------------------------
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

        result["functions"]      = functions
        result["function_count"] = len(functions)

    except Exception as _outer_exc:
        # Catch-all: record the error inside the JSON so _parse_output can
        # log it.  Without this, any Jython exception leaves out_path empty
        # and the Python side just sees "Expecting value: line 1 column 1".
        print("ERROR in ExtractFunctions.py: " + str(_outer_exc))
        result["error"] = str(_outer_exc)

    # ── Write JSON ────────────────────────────────────────────────────────────
    # Always write, even on error, so the Python side gets valid JSON and can
    # surface the error message rather than failing with an empty-file parse error.
    out_dir = os.path.dirname(out_path)
    if out_dir and not os.path.exists(out_dir):
        os.makedirs(out_dir)

    with open(out_path, "w") as fh:
        json.dump(result, fh, indent=2)

    exported_count = sum(1 for f in result["functions"] if f.get("is_exported"))
    print("ExtractFunctions: wrote {} functions ({} exported) to {}".format(
        len(result["functions"]), exported_count, out_path))


run()
