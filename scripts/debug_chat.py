#!/usr/bin/env python3
"""
debug_chat.py — Local end-to-end chat+execution debugger.

Simulates the full chat → semantic tool selection → OpenAI call → ctypes
execution loop entirely locally.  No Azure container, no bridge needed.
Uses your local OPENAI_API_KEY or AZURE_OPENAI env vars.

Usage:
    python scripts/debug_chat.py
    python scripts/debug_chat.py --json artifacts/shell32_exports_mcp.json
    python scripts/debug_chat.py --json artifacts/kernel32_exports_mcp.json --top 8
    python scripts/debug_chat.py --prompt "what is the current process ID?"
    python scripts/debug_chat.py --prompt "is the user an admin?" --json artifacts/shell32_exports_mcp.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# ── project root on path ──────────────────────────────────────────────────────
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "scripts"))
sys.path.insert(0, str(_ROOT / "api"))

# ── suppress bridge BRIDGE_SECRET warning ─────────────────────────────────────
os.environ.setdefault("BRIDGE_SECRET", "local-debug")

from gui_bridge import _execute_dll_bridge, _resolve_exe_path  # noqa: E402


def _load_invocables(json_path: Path) -> list[dict]:
    data = json.loads(json_path.read_text(encoding="utf-8"))
    if "invocables" in data:
        return data["invocables"]
    print(f"[!] Expected 'invocables' key in {json_path.name}")
    sys.exit(1)


def _inv_to_tool(inv: dict) -> dict:
    """Convert an invocable dict to an OpenAI tool schema."""
    mcp = inv.get("mcp", {})
    schema = mcp.get("input_schema", {"type": "object", "properties": {}, "required": []})
    doc = inv.get("documentation", {})
    desc = (doc.get("summary") or doc.get("description") or inv.get("name", ""))[:200]
    return {
        "type": "function",
        "function": {
            "name": inv["name"],
            "description": desc,
            "parameters": schema,
        }
    }


def _get_execution(inv: dict) -> dict:
    if inv.get("execution"):
        return inv["execution"]
    return (inv.get("mcp") or {}).get("execution", {})


def _execute_local(inv: dict, args: dict) -> str:
    """Execute an invocable locally via ctypes."""
    execution = _get_execution(inv)
    method = execution.get("method", "")
    if method == "dll_import":
        return _execute_dll_bridge(inv, execution, args)
    return f"[local debug] method '{method}' not supported locally — only dll_import"


def _openai_client():
    """Build an OpenAI client from local env vars."""
    # Support both plain OpenAI and Azure OpenAI
    azure_endpoint = os.getenv("OPENAI_ENDPOINT") or os.getenv("AZURE_OPENAI_ENDPOINT")
    api_key = os.getenv("OPENAI_API_KEY") or os.getenv("AZURE_OPENAI_API_KEY")
    api_version = os.getenv("OPENAI_API_VERSION", "2024-10-21")
    deployment = os.getenv("OPENAI_DEPLOYMENT", "gpt-4o")

    if not api_key:
        print("[!] No OPENAI_API_KEY or AZURE_OPENAI_API_KEY set in environment.")
        print("    Set one and retry.")
        sys.exit(1)

    if azure_endpoint:
        from openai import AzureOpenAI
        client = AzureOpenAI(
            azure_endpoint=azure_endpoint,
            api_key=api_key,
            api_version=api_version,
        )
    else:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        deployment = os.getenv("OPENAI_DEPLOYMENT", "gpt-4o")

    return client, deployment


def _semantic_select(tools: list[dict], prompt: str, client, deployment: str, top_k: int) -> list[dict]:
    """Simple semantic selection: ask the model which tools are relevant."""
    if len(tools) <= top_k:
        return tools
    names = [t["function"]["name"] for t in tools]
    sel_prompt = (
        f"Given this user request: '{prompt}'\n\n"
        f"From the following function names, pick the {top_k} most relevant ones "
        f"(return ONLY a JSON array of the function names, nothing else):\n\n"
        + "\n".join(names)
    )
    try:
        resp = client.chat.completions.create(
            model=deployment,
            messages=[{"role": "user", "content": sel_prompt}],
            temperature=0,
        )
        raw = resp.choices[0].message.content.strip()
        # Strip markdown fences if present
        if raw.startswith("```"):
            raw = "\n".join(raw.split("\n")[1:])
            raw = raw.rstrip("`").strip()
        selected_names = set(json.loads(raw))
        selected = [t for t in tools if t["function"]["name"] in selected_names]
        print(f"[semantic] Selected {len(selected)}/{len(tools)} tools: {[t['function']['name'] for t in selected]}")
        return selected or tools[:top_k]
    except Exception as exc:
        print(f"[semantic] Selection failed ({exc}), using first {top_k}")
        return tools[:top_k]


def main() -> None:
    ap = argparse.ArgumentParser(description="Debug chat+execution locally")
    ap.add_argument("--json",   default=None, help="Artifacts invocables JSON path")
    ap.add_argument("--prompt", default=None, help="User message to send")
    ap.add_argument("--top",    type=int, default=8, help="Max tools to send to model (default 8)")
    ns = ap.parse_args()

    # Locate JSON
    artifacts = _ROOT / "artifacts"
    if ns.json:
        json_path = Path(ns.json)
        if not json_path.is_absolute():
            json_path = _ROOT / json_path
    else:
        json_path = artifacts / "kernel32_exports_mcp.json"
        if not json_path.exists():
            json_path = artifacts / "shell32_exports_mcp.json"
        print(f"[auto] Using {json_path.relative_to(_ROOT)}")

    invocables = _load_invocables(json_path)
    inv_map = {inv["name"]: inv for inv in invocables}
    tools = [_inv_to_tool(inv) for inv in invocables]
    print(f"[info] {len(invocables)} invocables loaded from {json_path.name}")

    # Prompt
    prompt = ns.prompt
    if not prompt:
        dll_name = json_path.stem.replace("_exports_mcp", "").replace("_mcp", "")
        print(f"\nEnter a question about {dll_name} (or press Enter for default):")
        prompt = input("  > ").strip()
        if not prompt:
            prompt = "what is the current process ID?"
        print()

    client, deployment = _openai_client()

    # Semantic selection
    active_tools = _semantic_select(tools, prompt, client, deployment, ns.top)

    # Build system message
    tool_names = ", ".join(t["function"]["name"] for t in active_tools)
    system_msg = (
        "You are an AI agent with direct control over a Windows DLL via MCP tools.\n"
        "When asked to perform an action, call the appropriate tool immediately.\n"
        f"You have access to these tools: {tool_names}."
    )

    conversation = [
        {"role": "system", "content": system_msg},
        {"role": "user",   "content": prompt},
    ]

    print(f"\n[chat] Sending to {deployment} with {len(active_tools)} tools...")
    print(f"[chat] User: {prompt}\n")

    # Agentic loop (max 5 rounds)
    for _round in range(5):
        response = client.chat.completions.create(
            model=deployment,
            messages=conversation,
            tools=active_tools,
            tool_choice="auto",
            temperature=0.2,
        )
        msg = response.choices[0].message

        if not msg.tool_calls:
            print(f"[assistant] {msg.content}")
            break

        # Execute tool calls
        assistant_turn = {
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                for tc in msg.tool_calls
            ],
        }
        conversation.append(assistant_turn)

        for tc in msg.tool_calls:
            func_name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except Exception:
                args = {}

            inv = inv_map.get(func_name)
            if inv:
                result = _execute_local(inv, args)
            else:
                result = f"[error] '{func_name}' not found in invocable map"

            print(f"  [tool] {func_name}({args}) → {result}")

            conversation.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result,
            })

    print("\n[done]")


if __name__ == "__main__":
    main()
