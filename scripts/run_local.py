#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_local.py — Local end-to-end DLL chat+debug runner.

Accepts either:
  - A Ghidra JSON with top-level "tools" key (OpenAI tool format)
  - An invocables_map.json with top-level "invocables" key

Injects the DLL path, converts to invocables, calls ctypes via
gui_bridge._execute_dll_bridge, and runs an agentic loop — all locally with
no Azure, no bridge server, no containers.

Usage:
    python scripts/run_local.py --dll "C:/path/contoso_cs.dll" --json "C:/path/ghidra.json"
    python scripts/run_local.py --dll "C:/path/contoso_cs.dll" --json "C:/path/ghidra.json" --prompt "get the version"
    python scripts/run_local.py --dll "C:/path/contoso_cs.dll" --json "C:/path/ghidra.json" --discover
    python scripts/run_local.py --dll "C:/path/contoso_cs.dll" --json "C:/path/ghidra.json" --model gpt-4o
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "scripts"))

# Suppress the BRIDGE_SECRET warning — we don't need the HTTP server, only the ctypes helper
os.environ.setdefault("BRIDGE_SECRET", "local-debug")

from gui_bridge import _execute_dll_bridge  # noqa: E402

# Ensure stdout can handle any Unicode the model returns (Windows cp1252 default can't)
import io as _io
import sys as _sys
if hasattr(_sys.stdout, "reconfigure"):
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
elif not isinstance(_sys.stdout, _io.TextIOWrapper):
    _sys.stdout = _io.TextIOWrapper(_sys.stdout.buffer, encoding="utf-8", errors="replace")


# ── Environment ────────────────────────────────────────────────────────────────

def _load_env() -> None:
    """Load .env from project root if present (does not overwrite existing env vars)."""
    env_file = _ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                v = v.strip()
                if v:  # skip blank values — empty string breaks openai base_url
                    os.environ.setdefault(k.strip(), v)


def _openai_client():
    api_key        = os.getenv("OPENAI_API_KEY")
    azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT") or os.getenv("OPENAI_ENDPOINT")
    deployment     = (
        os.getenv("OPENAI_CHAT_MODEL")
        or os.getenv("OPENAI_DEPLOYMENT")
        or "gpt-4o-mini"
    )
    if not api_key:
        print("[!] OPENAI_API_KEY not set — add it to .env or export it in your shell.")
        sys.exit(1)
    if azure_endpoint:
        from openai import AzureOpenAI
        client = AzureOpenAI(
            azure_endpoint=azure_endpoint,
            api_key=api_key,
            api_version=os.getenv("OPENAI_API_VERSION", "2024-10-21"),
        )
    else:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
    return client, deployment


# ── JSON format adapters ───────────────────────────────────────────────────────

def _parse_c_type_from_desc(desc: str) -> str:
    """Extract C type from Ghidra description like '...type: uint)'."""
    m = re.search(r"\(type:\s*([^)]+)\)", desc)
    return m.group(1).strip() if m else "int"


def _parse_return_type(prototype: str, func_name: str) -> str:
    """Parse return type from a C prototype like 'undefined8 CS_Foo(...)'."""
    s = re.sub(r'\b(__stdcall|__cdecl|__fastcall|WINAPI)\b\s*', '', prototype).strip()
    m = re.match(rf'^(.+?)\s+{re.escape(func_name)}\s*\(', s)
    return m.group(1).strip().lower() if m else "int"


# C base types that are exclusively used as output pointer slots (never input strings)
_OUT_SCALAR_BASES = frozenset({
    "undefined", "undefined2", "undefined4", "undefined8",
    "uint", "uint32_t", "int", "int32_t", "dword", "ulong",
    "uint4", "uint8", "long", "ulong32",
})


def _infer_direction(c_type: str) -> str:
    """Return 'out' for pointer types that are output slots, 'in' otherwise."""
    lc = c_type.lower().replace("const ", "").strip()
    if "*" not in lc:
        return "in"
    base = lc.rstrip(" *").strip()
    return "out" if base in _OUT_SCALAR_BASES else "in"


def _ghidra_tools_to_invocables(tools: list[dict], dll_path: str) -> list[dict]:
    """Convert Ghidra OpenAI-format tool list to internal invocables."""
    invocables = []
    for tool in tools:
        fn    = tool.get("function", {})
        name  = fn["name"]
        desc  = fn.get("description", name)
        props = fn.get("parameters", {}).get("properties", {})
        req   = fn.get("parameters", {}).get("required", [])

        return_type = _parse_return_type(desc, name)

        parameters = [
            {
                "name":      pname,
                "type":      _parse_c_type_from_desc(pschema.get("description", "")),
                "json_type": pschema.get("type", "string"),
                "direction": _infer_direction(_parse_c_type_from_desc(pschema.get("description", ""))),
            }
            for pname, pschema in props.items()
        ]

        invocables.append({
            "name":        name,
            "description": desc,
            "return_type": return_type,
            "parameters":  parameters,
            "signature":   desc,
            "execution": {
                "method":        "dll_import",
                "dll_path":      dll_path,
                "function_name": name,
            },
            "_tool_schema": {
                "type": "function",
                "function": {
                    "name":        name,
                    "description": desc[:200],
                    "parameters": {
                        "type":       "object",
                        "properties": props,
                        "required":   req,
                    },
                },
            },
        })
    return invocables


def _load_input(json_path: Path, dll_path: str) -> list[dict]:
    """Load and normalise to invocables regardless of source format."""
    data = json.loads(json_path.read_text(encoding="utf-8"))

    if "invocables" in data:
        # Standard invocables_map.json from the pipeline — inject/override dll_path
        invs = data["invocables"]
        for inv in invs:
            exec_ = inv.get("execution") or (inv.get("mcp") or {}).get("execution") or {}
            exec_["dll_path"] = dll_path
            inv["execution"]  = exec_
            if "_tool_schema" not in inv:
                # Build tool schema from the invocable's mcp field if present
                mcp    = inv.get("mcp", {})
                schema = mcp.get("input_schema", {"type": "object", "properties": {}, "required": []})
                doc    = inv.get("documentation", {})
                d      = (doc.get("summary") or doc.get("description") or inv.get("name", ""))[:200]
                inv["_tool_schema"] = {
                    "type": "function",
                    "function": {"name": inv["name"], "description": d, "parameters": schema},
                }
        return invs

    if "tools" in data:
        # Ghidra OpenAI tool format
        return _ghidra_tools_to_invocables(data["tools"], dll_path)

    print("[!] Unrecognised JSON — expected top-level 'invocables' or 'tools' key.")
    sys.exit(1)

import base64
import subprocess

_WORKER = Path(__file__).resolve().parent / "_dll_call_worker.py"
_PYTHON = sys.executable


def _execute_local(inv: dict, args: dict) -> str:
    """Execute a DLL function in a subprocess so an access violation crash
    kills only the child process, not the whole debug session."""
    execution = inv.get("execution", {})
    if execution.get("method") != "dll_import":
        return f"[local] method '{execution.get('method')}' not supported — only dll_import"
    payload = base64.b64encode(
        json.dumps({"inv": inv, "args": args}).encode("utf-8")
    ).decode("ascii")
    try:
        proc = subprocess.run(
            [_PYTHON, str(_WORKER), payload],
            capture_output=True,
            timeout=15,
        )
        if proc.returncode == 0 or proc.stdout:
            return proc.stdout.decode("utf-8", errors="replace").strip()
        # Non-zero exit with no stdout — likely a native crash (0xC0000005)
        return f"DLL call crashed (exit {proc.returncode:#010x}) — access violation or stack corruption"
    except subprocess.TimeoutExpired:
        return "DLL call timed out after 15s"
    except Exception as exc:
        return f"Subprocess error: {exc}"


# ── Chat loop ──────────────────────────────────────────────────────────────────

def _chat_loop(client, deployment: str, invocables: list[dict], prompt: str, max_rounds: int = 8) -> None:
    tools   = [inv["_tool_schema"] for inv in invocables if "_tool_schema" in inv]
    inv_map = {inv["name"]: inv for inv in invocables}

    _INIT = ("initialize", "init", "startup", "start", "setup", "open", "login", "logon")
    init_fns = [n for n in inv_map if any(n.lower() == s or n.lower().endswith(s) or f"_{s}" in n.lower() for s in _INIT)]
    init_hint = f"\nCall these silently FIRST before any other function: {', '.join(init_fns)}." if init_fns else ""

    system_msg = (
        "You are an AI agent with direct access to a Windows DLL via ctypes.\n"
        "Call tools immediately when asked. Batch multiple calls in one response where possible.\n"
        "If a call returns 4294967295 or -1 it is an error sentinel — try integer then string encoding for pointer params.\n"
        "After all tool calls, write one sentence summarising the result."
        + init_hint
    )

    conversation = [
        {"role": "system", "content": system_msg},
        {"role": "user",   "content": prompt},
    ]
    print(f"\n[chat] model={deployment}  tools={len(tools)}  prompt={prompt!r}\n")

    for _round in range(max_rounds):
        resp = client.chat.completions.create(
            model=deployment,
            messages=conversation,
            tools=tools,
            tool_choice="auto",
            temperature=0,
        )
        msg = resp.choices[0].message

        if not msg.tool_calls:
            print(f"[assistant] {msg.content or '(no text)'}")
            break

        conversation.append({
            "role":       "assistant",
            "content":    msg.content or "",
            "tool_calls": [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                for tc in msg.tool_calls
            ],
        })

        for tc in msg.tool_calls:
            try:
                fn_args = json.loads(tc.function.arguments or "{}")
            except Exception:
                fn_args = {}
            inv    = inv_map.get(tc.function.name)
            result = _execute_local(inv, fn_args) if inv else f"[error] '{tc.function.name}' not in inv_map"
            print(f"  -> {tc.function.name}({fn_args})\n     {result}")
            conversation.append({"role": "tool", "tool_call_id": tc.id, "content": result})

    print("\n[done]")


# ── Discover loop ──────────────────────────────────────────────────────────────

_DISCOVER_SYSTEM = """\
You are a reverse-engineering agent systematically documenting an undocumented Windows DLL.

PROTOCOL FOR EACH FUNCTION:
1. If any init function exists (CS_Initialize, *Initialize*, *Init*, *Open*), call it FIRST silently.
2. Call the target function with safe probe values.  For each parameter:
   - integer params (uint, int, ushort): try 0, then 1, then 64, then 256
   - input string/pointer params (byte*, char*): try "" (empty string), then "test", then integer 0
   - output pointer params (undefined4*, undefined8*, uint*, int*): omit from the call — the bridge
     auto-allocates an output buffer for params NOT included in the call.  You will see the written
     value reported as "param_N=<value>" appended to the return string.
   - bare undefined* params: also omit — treated as a byte buffer by the bridge.
   - Batch multiple probe variants in ONE round if possible.
3. Classify the return value:
   - 0 = success for action functions (Initialize, Process*, Unlock*, Redeem*)
   - 4294967295 (0xFFFFFFFF) = error sentinel ("not found", "invalid input")
   - 4294967294 (0xFFFFFFFE) = secondary error code (e.g. "null argument")
   - IMPORTANT: for version/build/revision functions (GetVersion, GetBuild, GetRevision):
     the return value IS the version number (a UINT) — any non-zero integer is a VALID result,
     NOT an error. Mark status "success" and document the return as "version number as UINT".
   - "access violation" or crash = wrong pointer arg, try different encoding
4. Status classification rules — the PRIMARY indicator is always the INTEGER RETURN VALUE:
   - Output buffer values like `param_N=<value>` appended to the result are secondary;
     they only matter when the return code indicates success.
   - "success"  = return value is 0 OR makes semantic sense as a direct result
                  (e.g. a version UINT for GetVersion, a handle for Open-style calls).
                  NOT "success" if the return is 0xFFFFFFFF or 0xFFFFFFFE.
   - "error"    = function is reachable but every call returned 0xFFFFFFFF / 0xFFFFFFFE,
                  meaning the probed inputs were invalid (e.g. account not found, null arg).
                  This is the expected result for data-dependent functions without real data.
   - "crash"    = function caused an access violation / process crash
   - "unknown"  = function not found in the DLL export table
5. After gathering evidence, output ONLY a JSON block in this EXACT format:

ENRICHMENT:
{
  "function": "<exact function name>",
  "status": "success|error|crash|unknown",
  "description": "<one clear sentence: what this function does>",
  "return_value": "<what the return value means, e.g. '0 = success, 0xFFFFFFFF = not found'>",
  "params": {
    "param_1": {"semantic_name": "<name>", "description": "<what it is>", "type_hint": "<input|output|size>"},
    "param_2": {"semantic_name": "<name>", "description": "<what it is>", "type_hint": "<input|output|size>"}
  },
  "working_call": <exact args dict that produced non-error result, or null>,
  "notes": "<anything unusual: output buffers written values, required init, known failure modes>"
}

RULES:
- ALWAYS output the ENRICHMENT JSON block even if every call failed.
- For output params (undefined4*, undefined8*, uint*) DO NOT include them in working_call — they are
  auto-allocated by the bridge.  Document them in "params" with type_hint="output".
- If a function has no params, set "params": {}.
- If you cannot determine semantics, use generic names but still output valid JSON.
- Do not add any text outside the ENRICHMENT JSON block.
"""

def _parse_enrichment(text: str) -> dict | None:
    """Extract the JSON block from after 'ENRICHMENT:' in the model's response."""
    if not text:
        return None
    # Find JSON block — may or may not have a label
    idx = text.find("ENRICHMENT:")
    raw = text[idx + len("ENRICHMENT:"):].strip() if idx != -1 else text.strip()
    # Strip markdown code fences
    if raw.startswith("```"):
        raw = "\n".join(raw.split("\n")[1:])
        raw = raw.rstrip("`").strip()
    # Find outermost { }
    start = raw.find("{")
    if start == -1:
        return None
    depth, end = 0, -1
    for i, ch in enumerate(raw[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    if end == -1:
        return None
    try:
        return json.loads(raw[start : end + 1])
    except Exception:
        return None


def _discover_loop(client, deployment: str, invocables: list[dict],
                   findings_path: Path | None = None) -> list[dict]:
    """Probe every function, produce structured enrichment JSON, persist findings.

    Returns the list of enrichment dicts (one per function).
    If findings_path is given, load prior findings (skip already-enriched functions)
    and save after each function so a crash doesn't lose progress.
    """
    tools   = [inv["_tool_schema"] for inv in invocables if "_tool_schema" in inv]
    inv_map = {inv["name"]: inv for inv in invocables}

    # Load prior findings so we can skip already-done functions
    prior: dict[str, dict] = {}
    if findings_path and findings_path.exists():
        try:
            prior = {e["function"]: e for e in json.loads(findings_path.read_text(encoding="utf-8"))}
            print(f"[discover] Loaded {len(prior)} prior findings from {findings_path.name}")
        except Exception as e:
            print(f"[discover] Could not load prior findings: {e}")

    enrichments: list[dict] = list(prior.values())
    total = len(invocables)
    print(f"\n[discover] {total} functions  model={deployment}\n")

    # Build a short rolling context from prior findings to seed each call
    def _context_block() -> str:
        if not enrichments:
            return "  (none yet)"
        lines = []
        for e in enrichments[-6:]:
            wc = f" working_call={e['working_call']}" if e.get("working_call") else ""
            lines.append(f"  - {e['function']} ({e.get('status','?')}): {e.get('description','')}{wc}")
        return "\n".join(lines)

    for idx, inv in enumerate(invocables):
        name = inv["name"]

        if name in prior:
            print(f"[{idx+1}/{total}] {name}  (skipped — already in findings)")
            continue

        desc  = inv.get("description", name)
        params_info = ", ".join(
            f"{p['name']}:{p.get('type','?')}" + (" [out]" if p.get("direction") == "out" else "")
            for p in inv.get("parameters", [])
        )

        print(f"[{idx+1}/{total}] {name}({params_info})")

        conversation = [
            {"role": "system", "content": _DISCOVER_SYSTEM},
            {
                "role": "user",
                "content": (
                    f"Document this function:\n"
                    f"  Name: {name}\n"
                    f"  Prototype: {desc[:400]}\n"
                    f"  Parameters: {params_info or '(none)'}\n\n"
                    f"Previously learned about this DLL:\n{_context_block()}\n\n"
                    "Probe it and output the ENRICHMENT JSON block."
                ),
            },
        ]

        enrichment: dict | None = None
        for _round in range(4):  # up to 4 rounds: init + probe + probe + final
            resp = client.chat.completions.create(
                model=deployment,
                messages=conversation,
                tools=tools,
                tool_choice="auto",
                temperature=0,
            )
            msg = resp.choices[0].message

            if not msg.tool_calls:
                # Model gave a text response — try to parse enrichment JSON
                text = msg.content or ""
                enrichment = _parse_enrichment(text)
                if enrichment:
                    status = enrichment.get("status", "?")
                    desc_short = enrichment.get("description", "")[:80]
                    print(f"   [{status}] {desc_short}")
                    wc = enrichment.get("working_call")
                    if wc:
                        print(f"   working_call: {wc}")
                    rnames = {k: v.get("semantic_name", k) for k, v in enrichment.get("params", {}).items()}
                    if rnames:
                        print(f"   param renames: {rnames}")
                else:
                    # Model gave prose without JSON — store as-is
                    enrichment = {"function": name, "status": "unknown", "description": text[:200],
                                  "params": {}, "working_call": None, "notes": "no structured output from model"}
                    print(f"   (no JSON block — stored prose)")
                print()
                break

            # Execute tool calls
            conversation.append({
                "role": "assistant", "content": msg.content or "",
                "tool_calls": [
                    {"id": tc.id, "type": "function",
                     "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                    for tc in msg.tool_calls
                ],
            })
            for tc in msg.tool_calls:
                try:
                    fn_args = json.loads(tc.function.arguments or "{}")
                except Exception:
                    fn_args = {}
                fn_inv = inv_map.get(tc.function.name)
                result = _execute_local(fn_inv, fn_args) if fn_inv else "[error] not found"
                print(f"   call: {tc.function.name}({fn_args}) -> {result}")
                conversation.append({"role": "tool", "tool_call_id": tc.id, "content": result})
        else:
            # Exhausted rounds without a text response — force a final answer
            conversation.append({
                "role": "user",
                "content": "You have run out of probe rounds. Output the ENRICHMENT JSON block now based on what you observed."
            })
            resp = client.chat.completions.create(model=deployment, messages=conversation, temperature=0)
            text = resp.choices[0].message.content or ""
            enrichment = _parse_enrichment(text) or {
                "function": name, "status": "unknown",
                "description": f"Could not determine in {4} rounds",
                "params": {}, "working_call": None, "notes": text[:300],
            }

        if enrichment:
            enrichment.setdefault("function", name)
            enrichments.append(enrichment)
            # Persist after each function so a crash doesn't lose progress
            if findings_path:
                findings_path.write_text(
                    json.dumps(enrichments, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )

    # Print final summary
    print("\n" + "=" * 72)
    print("DISCOVERY SUMMARY")
    print("=" * 72)
    for e in enrichments:
        status = e.get("status", "?").upper()
        fn     = e.get("function", "?")
        d      = e.get("description", "")[:70]
        wc     = f"  working={e['working_call']}" if e.get("working_call") else ""
        print(f"  [{status:8}] {fn}: {d}{wc}")
    print("=" * 72)

    if findings_path:
        print(f"\n[saved] {findings_path}")

    return enrichments



# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    _load_env()

    ap = argparse.ArgumentParser(description="Local DLL debug runner (Ghidra JSON or invocables_map)")
    ap.add_argument("--dll",      default=None,         help="Path to the .dll file (required for chat/discover)")
    ap.add_argument("--json",     default=None,         help="Ghidra JSON or invocables_map.json path (required for chat/discover)")
    ap.add_argument("--prompt",   default=None,         help="Chat prompt (interactive if omitted)")
    ap.add_argument("--discover", action="store_true",  help="Autonomous probe+document loop across all functions")
    ap.add_argument("--save",     default=None,         help="Save findings to this JSON file (auto-resumes if exists)")
    ap.add_argument("--report",   default=None,         help="Print a previously saved findings JSON (no LLM call)")
    ap.add_argument("--model",    default=None,         help="Override model (e.g. gpt-4o)")
    ap.add_argument("--rounds",   type=int, default=8,  help="Max tool-call rounds per chat (default 8)")
    ns = ap.parse_args()

    # --report: just pretty-print a saved findings file
    if ns.report:
        rp = Path(ns.report)
        if not rp.exists():
            print(f"[!] Not found: {rp}")
            sys.exit(1)
        findings = json.loads(rp.read_text(encoding="utf-8"))
        print(f"\nFindings from {rp.name} ({len(findings)} functions)\n")
        print("=" * 72)
        for e in findings:
            status = e.get("status", "?").upper()
            fn     = e.get("function", "?")
            d      = e.get("description", "")
            print(f"\n[{status}] {fn}")
            print(f"  Description : {d}")
            rv = e.get("return_value", "")
            if rv:
                print(f"  Return value: {rv}")
            wc = e.get("working_call")
            if wc:
                print(f"  Working call: {wc}")
            for pname, pinfo in (e.get("params") or {}).items():
                sname = pinfo.get("semantic_name", pname)
                pdesc = pinfo.get("description", "")
                hint  = pinfo.get("type_hint", "")
                print(f"  {pname} -> {sname} ({hint}): {pdesc}")
            notes = e.get("notes", "")
            if notes:
                print(f"  Notes: {notes}")
        print("\n" + "=" * 72)
        return

    if not ns.dll or not ns.json:
        print("[!] --dll and --json are required for chat and discover modes.")
        ap.print_help()
        sys.exit(1)

    dll_path  = str(Path(ns.dll).resolve())
    json_path = Path(ns.json)
    if not json_path.is_absolute():
        json_path = _ROOT / json_path

    if not Path(dll_path).exists():
        print(f"[!] DLL not found: {dll_path}")
        sys.exit(1)
    if not json_path.exists():
        print(f"[!] JSON not found: {json_path}")
        sys.exit(1)

    invocables = _load_input(json_path, dll_path)
    print(f"[info] {len(invocables)} functions loaded")
    print(f"[info] DLL:  {dll_path}")

    client, deployment = _openai_client()
    if ns.model:
        deployment = ns.model
    print(f"[info] Model: {deployment}")

    if ns.discover:
        findings_path = Path(ns.save) if ns.save else None
        _discover_loop(client, deployment, invocables, findings_path=findings_path)
        return

    prompt = ns.prompt
    if not prompt:
        print("\nEnter a prompt (Enter = 'initialize the library and get the version'):")
        prompt = input("  > ").strip() or "initialize the library and get the version"

    _chat_loop(client, deployment, invocables, prompt, max_rounds=ns.rounds)


if __name__ == "__main__":
    main()
