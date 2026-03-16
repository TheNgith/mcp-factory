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
                "direction": "in",
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

def _discover_loop(client, deployment: str, invocables: list[dict]) -> None:
    """Probe every function, journal findings, print a summary."""
    tools   = [inv["_tool_schema"] for inv in invocables if "_tool_schema" in inv]
    inv_map = {inv["name"]: inv for inv in invocables}
    findings: list[str] = []

    print(f"\n[discover] {len(invocables)} functions  model={deployment}\n")

    for idx, inv in enumerate(invocables):
        name = inv["name"]
        desc = inv.get("description", name)
        print(f"[{idx+1}/{len(invocables)}] {name}")

        context = "\n".join(f"  - {f}" for f in findings[-8:]) or "  (none yet)"
        system_msg = (
            "You are a reverse-engineering agent probing an undocumented Windows DLL.\n"
            "Probe the function with safe values (small integers: 0/1/64/256, empty strings).\n"
            "If any init function exists (Initialize/Init/Open), call it first.\n"
            "After probing, write ONE plain-English sentence summarising what you discovered.\n"
            "Previously learned:\n" + context
        )

        conversation = [
            {"role": "system", "content": system_msg},
            {"role": "user",   "content": f"Probe {name}: {desc[:300]}"},
        ]

        for _round in range(3):
            resp = client.chat.completions.create(
                model=deployment,
                messages=conversation,
                tools=tools,
                tool_choice="auto",
                temperature=0,
            )
            msg = resp.choices[0].message
            if not msg.tool_calls:
                finding = msg.content or "(no output)"
                print(f"   {finding}\n")
                findings.append(f"{name}: {finding}")
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
                fn_inv = inv_map.get(tc.function.name)
                result = _execute_local(fn_inv, fn_args) if fn_inv else "[error] not found"
                print(f"   call: {tc.function.name}({fn_args}) -> {result}")
                conversation.append({"role": "tool", "tool_call_id": tc.id, "content": result})

    print("\n=== DISCOVERY SUMMARY ===================================================")
    for f in findings:
        print(f"  {f}")
    print("=" * 72 + "\n")


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    _load_env()

    ap = argparse.ArgumentParser(description="Local DLL debug runner (Ghidra JSON or invocables_map)")
    ap.add_argument("--dll",      required=True,        help="Path to the .dll file")
    ap.add_argument("--json",     required=True,        help="Ghidra JSON or invocables_map.json path")
    ap.add_argument("--prompt",   default=None,         help="Chat prompt (interactive if omitted)")
    ap.add_argument("--discover", action="store_true",  help="Autonomous probe loop across all functions")
    ap.add_argument("--model",    default=None,         help="Override model (e.g. gpt-4o)")
    ap.add_argument("--rounds",   type=int, default=8,  help="Max tool-call rounds per chat (default 8)")
    ns = ap.parse_args()

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
        _discover_loop(client, deployment, invocables)
        return

    prompt = ns.prompt
    if not prompt:
        print("\nEnter a prompt (Enter = 'initialize the library and get the version'):")
        prompt = input("  > ").strip() or "initialize the library and get the version"

    _chat_loop(client, deployment, invocables, prompt, max_rounds=ns.rounds)


if __name__ == "__main__":
    main()
