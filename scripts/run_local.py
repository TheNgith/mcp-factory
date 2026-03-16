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


def _extract_dll_strings(dll_path: str, min_len: int = 6) -> dict[str, list[str]]:
    """Phase 0 static analysis: extract printable ASCII strings from the DLL binary.

    Returns a dict with categorised hints:
      - 'ids'       : likely input IDs (CUST-NNN, ORD-..., product codes etc.)
      - 'emails'    : email addresses embedded in the binary
      - 'status'    : status/enum strings (ACTIVE, PENDING, SHIPPED...)
      - 'formats'   : format strings (reveal output buffer structure)
      - 'all'       : complete flat list of unique printable strings
    """
    try:
        data = Path(dll_path).read_bytes()
    except Exception:
        return {"ids": [], "emails": [], "status": [], "formats": [], "all": []}

    # Extract all runs of printable ASCII of length >= min_len
    pat = re.compile(rb"[ -~]{" + str(min_len).encode() + rb",}")
    raw_strings: list[str] = []
    for m in pat.finditer(data):
        try:
            s = m.group(0).decode("ascii", errors="ignore").strip()
            if s:
                raw_strings.append(s)
        except Exception:
            pass
    unique = sorted(set(raw_strings))

    ids:     list[str] = []
    emails:  list[str] = []
    status:  list[str] = []
    formats: list[str] = []

    _STATUS_WORDS = {"active", "inactive", "pending", "shipped", "delivered",
                     "cancelled", "suspended", "complete", "unknown", "error",
                     "success", "enabled", "disabled", "locked", "unlocked"}

    for s in unique:
        sl = s.lower()
        if "%s" in s or "%d" in s or "%u" in s or "%f" in s or "%lu" in s:
            formats.append(s)
        elif re.match(r"[A-Z]{2,6}-[\w-]+", s) and len(s) < 40:
            # Looks like an ID/code: CUST-001, ORD-20040301-0042, PRO-SVC-ANNUAL
            ids.append(s)
        elif re.match(r"[\w.+-]+@[\w.-]+\.[a-z]{2,6}", s, re.I):
            emails.append(s)
        elif sl in _STATUS_WORDS or (sl.isupper() and 4 <= len(s) <= 16 and s.isalpha()):
            status.append(s)

    return {"ids": ids, "emails": emails, "status": status, "formats": formats, "all": unique}


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
1. ALWAYS call the init function (*Initialize*, *Init*, CS_Initialize) as the VERY FIRST call,
   even when exploring a function that seems unrelated to initialization.  This ensures consistent
   DLL state across all explorations.  Do NOT skip this step even if you think it was called before.
2. Call the target function with safe probe values.  For each parameter:
   - integer params (uint, int, ushort): try 0, then 1, then 64, then 256
   - input string/pointer params (byte*, char*): try "" (empty string), then "test", then integer 0
   - output pointer params (undefined4*, undefined8*, uint*, int*): omit from the call — the bridge
     auto-allocates an output buffer for params NOT included in the call.  You will see the written
     value reported as "param_N=<value>" appended to the return string.
   - bare undefined* output buffer + adjacent uint size param: omit BOTH — the bridge allocates a
     4096-byte buffer and auto-supplies size=4096.  Do NOT pass the size param as 0.
   - Batch multiple probe variants in ONE round if possible.
   ZERO-OUTPUT RETRY RULE: If the call returns SUCCESS (0) but every output param reports value=0,
   the input values were likely too small to produce a non-trivial result.  Before concluding the
   output is always zero, retry with MUCH LARGER numeric inputs:
     - For financial/calculation functions (Calc*, Compute*, Interest*, Rate*, Balance*):
       use principal=10000, rate=500, period=12 — typical basis-point and month scale.
     - For general numeric functions: try inputs 1000, 10000, 100000.
   Only classify the output param as "always returns 0" if it remains 0 after the large-value retry.
3. Classify the return value:
   - 0 = success for action functions (Initialize, Process*, Unlock*, Redeem*)
   - 4294967295 (0xFFFFFFFF) = error sentinel ("not found" / "invalid input")
   - 4294967294 (0xFFFFFFFE) = secondary error code ("null argument" / bad param)
   - 4294967293 (0xFFFFFFFD) = not initialized
   - 4294967292 (0xFFFFFFFC) = account locked or suspended
   - 4294967291 (0xFFFFFFFB) = write operation denied (system not in write-ready state)
   - IMPORTANT: for version/build/revision functions (GetVersion, GetBuild, GetRevision):
     the return value IS the version number (a UINT) — any non-zero integer is a VALID result,
     NOT an error. Mark status "success" and document the return as "version number as UINT".
     Decode as: major=(val>>16)&0xFF, minor=(val>>8)&0xFF, patch=val&0xFF → e.g. 131841=2.3.1.
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
   HARD CONSISTENCY RULES (always enforced):
   - If working_call is non-null, status MUST be "success" — no exceptions.
   - working_call MUST only be set when a call returned integer 0 (or a valid semantic
     integer for GetVersion-style functions).  If every probe returned a sentinel error
     code, set working_call to null and status to "error".
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
"notes": "<anything unusual: output buffers written values, required init, known failure modes.
              For GetVersion-style returns: decode the UINT and include e.g. 'decoded: 2.3.1'.
              For error-only results: document every error code seen and what input triggered it.>"
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


_VOCAB_UPDATE_SYSTEM = """\
You are a DLL reverse-engineering assistant maintaining a shared vocabulary table.
Given a new function enrichment, update the vocabulary with any NEW generalisable facts.
Output ONLY a JSON object with these optional keys (omit keys if nothing new to add):
{
  "string_param_convention": "<how string input params work in this DLL>",
  "id_formats": ["<each distinct ID pattern found, e.g. 'CUST-NNN', 'ORD-YYYYMMDD-NNNN', 'PRO-xxx'>"],
  "ignored_params": ["<any param that is always ignored or always 0>"],
  "init_sequence": "<what must be called before write functions work>",
  "write_blocked_by": "<what prevents write operations>",
  "output_format": "<how output buffers are structured, e.g. 'pipe-delimited key=value'>",
  "error_codes": {"<hex>": "<meaning>"},
  "notes": "<anything else generalisable across functions>"
}
IMPORTANT: 'id_formats' must be a LIST of all distinct patterns seen so far.
Different functions key on different entity types (customer IDs, order IDs, product codes).
Never assume the first/most-common ID format is 'the' primary key for all functions.
Only include keys where you have strong evidence. Keep values concise.
If nothing new was learned, output {}.
"""


def _update_vocabulary(client, deployment: str, vocab: dict, enrichment: dict) -> dict:
    """Ask the LLM to extract generalisable facts from one enrichment into the vocab table."""
    prompt = (
        f"Current vocabulary:\n{json.dumps(vocab, indent=2)}\n\n"
        f"New function enrichment:\n{json.dumps(enrichment, indent=2)}\n\n"
        "Update the vocabulary with any new generalisable facts. Output only the updated JSON."
    )
    try:
        resp = client.chat.completions.create(
            model=deployment,
            messages=[
                {"role": "system", "content": _VOCAB_UPDATE_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            temperature=0,
        )
        text = (resp.choices[0].message.content or "").strip()
        # Strip fences
        if text.startswith("```"):
            text = "\n".join(text.split("\n")[1:]).rstrip("`").strip()
        start = text.find("{")
        end   = text.rfind("}")
        if start != -1 and end != -1:
            updates = json.loads(text[start : end + 1])
            # Merge: lists are extended, dicts are merged, scalars are overwritten
            for k, v in updates.items():
                if isinstance(v, list) and isinstance(vocab.get(k), list):
                    existing = set(vocab[k])
                    vocab[k] = vocab[k] + [x for x in v if x not in existing]
                elif isinstance(v, dict) and isinstance(vocab.get(k), dict):
                    vocab[k].update(v)
                elif v:
                    vocab[k] = v
    except Exception:
        pass
    return vocab


def _vocab_block(vocab: dict) -> str:
    """Format the vocabulary table for injection into user prompts."""
    if not vocab:
        return ""
    lines = ["ACCUMULATED DLL KNOWLEDGE (apply these conventions immediately):"]
    for k, v in vocab.items():
        if k == "id_formats" and isinstance(v, list):
            # Explicit try-all instruction — this is the most important convention
            lines.append(
                f"  id_formats (try ALL of these for each unknown string param, "
                f"not just the most common one): {', '.join(str(x) for x in v)}"
            )
        elif isinstance(v, list):
            lines.append(f"  {k}: {', '.join(str(x) for x in v)}")
        elif isinstance(v, dict):
            for sk, sv in v.items():
                lines.append(f"  {k}[{sk}]: {sv}")
        else:
            lines.append(f"  {k}: {v}")
    return "\n".join(lines) + "\n"


def _probe_write_unlock(inv_map: dict, dll_strings: dict) -> dict:
    """Phase 1: Try to find and execute a write-mode unlock sequence.

    Systematically tries:
    1. CS_Initialize with integer mode arguments (0,1,2,4,8,16,256,512)
    2. Any function named *Begin*, *Start*, *Enable*, *Open*, *SetMode*, *Auth*
    3. CS_Initialize(mode) then immediately retrying a write sentinel function

    Returns a dict describing what was found:
    {
        "unlocked": bool,
        "sequence": [{"fn": name, "args": {...}, "result": "..."}],
        "write_fn_tested": name | None,   # first write-sentinel fn we tested
        "notes": str,
    }
    """
    _WRITE_SENTINELS = {0xFFFFFFFB}  # sentinel: write denied
    _INIT_NAMES = [n for n in inv_map if re.search(r"init(ializ)?", n, re.I)]
    _WRITE_FN_NAMES = [
        n for n in inv_map
        if re.search(r"(pay|redeem|unlock|process|write|commit|transfer|debit|credit)", n, re.I)
    ]
    _UNLOCK_PATTERNS = [
        r"begin", r"start", r"enable", r"open", r"setmode", r"auth",
        r"login", r"logon", r"connect", r"session", r"transaction",
        r"unlock",  # covers CS_UnlockAccount and similar
    ]
    _UNLOCK_FNS = [
        n for n in inv_map
        if any(re.search(p, n, re.I) for p in _UNLOCK_PATTERNS)
    ]

    # Pull known ID and email strings from Phase 0 extraction
    _known_ids    = (dll_strings or {}).get("ids", [])[:12]
    _known_emails = (dll_strings or {}).get("emails", [])[:8]
    # Short plain strings from binary that could be credentials/modes
    _known_tokens = [
        s for s in (dll_strings or {}).get("all", [])
        if 3 <= len(s) <= 24 and s.isascii() and "|"
        not in s and "%" not in s and s not in _known_ids
        and s not in _known_emails
    ][:20]

    sequence: list[dict] = []

    def _call(fn_name: str, args: dict) -> tuple[str, int | None]:
        inv = inv_map.get(fn_name)
        if not inv:
            return "[not found]", None
        result = _execute_local(inv, args)
        m = re.match(r"Returned:\s*(\d+)", result or "")
        ret = int(m.group(1)) if m else None
        return result, ret

    def _test_canary_unlocked(label: str) -> bool | None:
        """Call the canary and return True if write is now open, False if still blocked, None if no canary."""
        if not write_canary:
            return None
        w_result, w_ret = _call(write_canary, {"param_1": _known_ids[0] if _known_ids else "CUST-001", "param_2": 0})
        sequence.append({"fn": write_canary, "args": {"param_1": _known_ids[0] if _known_ids else "CUST-001", "param_2": 0}, "result": w_result})
        print(f"    canary {write_canary} -> {w_result}")
        if w_ret not in _WRITE_SENTINELS and w_ret == 0:
            print(f"  [phase1] UNLOCKED after {label}")
            return True
        return False

    # Choose the first write-sentinel function as our canary
    write_canary = _WRITE_FN_NAMES[0] if _WRITE_FN_NAMES else None

    print("\n[phase1] Write-unlock probe")
    print(f"  Init functions:  {_INIT_NAMES or '(none)'}")
    print(f"  Write canary:    {write_canary or '(none)'}")
    print(f"  Unlock fns:      {_UNLOCK_FNS or '(none)'}")
    print(f"  Known IDs:       {_known_ids[:6]}")
    print(f"  Known emails:    {_known_emails[:4]}")

    # Baseline: is write already available after a plain no-arg init?
    _call(next(iter(_INIT_NAMES), "CS_Initialize"), {})
    if write_canary:
        base_result, base_ret = _call(write_canary, {"param_1": _known_ids[0] if _known_ids else "CUST-001", "param_2": 0})
        sequence.append({"fn": write_canary, "args": {}, "result": base_result})
        if base_ret not in _WRITE_SENTINELS and base_ret == 0:
            print("  [phase1] Write already available after plain init — no unlock needed")
            return {"unlocked": True, "sequence": sequence, "write_fn_tested": write_canary,
                    "notes": "write available without special unlock"}

    # For init functions WITH declared params, try integer mode sweep
    for init_fn in _INIT_NAMES:
        inv_params = inv_map[init_fn].get("parameters", [])
        if not inv_params:
            print(f"  {init_fn} has no declared params — skipping integer mode sweep")
            continue
        for mode in (1, 2, 4, 8, 16, 32, 64, 128, 256, 512):
            result, ret = _call(init_fn, {"param_1": mode})
            sequence.append({"fn": init_fn, "args": {"param_1": mode}, "result": result})
            print(f"  {init_fn}(mode={mode}) -> {result}")
            if ret == 0 and _test_canary_unlocked(f"{init_fn}(mode={mode})"):
                return {"unlocked": True, "sequence": sequence, "write_fn_tested": write_canary,
                        "notes": f"write unlocked by {init_fn}(param_1={mode})"}
        # Also try string tokens if available
        for tok in _known_tokens[:8]:
            result, ret = _call(init_fn, {"param_1": tok})
            sequence.append({"fn": init_fn, "args": {"param_1": tok}, "result": result})
            print(f"  {init_fn}(tok={tok!r}) -> {result}")
            if ret == 0 and _test_canary_unlocked(f"{init_fn}(tok={tok!r})"):
                return {"unlocked": True, "sequence": sequence, "write_fn_tested": write_canary,
                        "notes": f"write unlocked by {init_fn}(param_1={tok!r})"}

    # Credential sweep: try every unlock/auth function with known ID × email combos
    # Covers unlock patterns like CS_UnlockAccount(customer_id, email) etc.
    _cred_pairs = [
        (cid, email)
        for cid in _known_ids
        for email in _known_emails
        if cid and email
    ]
    # Also try same-value pairs and token combos
    _cred_pairs += [(cid, cid) for cid in _known_ids]
    _cred_pairs += [(cid, tok) for cid in _known_ids[:4] for tok in _known_tokens[:4]]

    print(f"  Credential sweep: {len(_cred_pairs)} pairs across {_UNLOCK_FNS or '(none)'}")
    for unlock_fn in _UNLOCK_FNS:
        # First try no-arg and single-arg cases
        for args in [{}, {"param_1": 0}, {"param_1": 1}]:
            result, ret = _call(unlock_fn, args)
            sequence.append({"fn": unlock_fn, "args": args, "result": result})
            if ret == 0 and _test_canary_unlocked(f"{unlock_fn}({args})"):
                return {"unlocked": True, "sequence": sequence, "write_fn_tested": write_canary,
                        "notes": f"write unlocked by {unlock_fn}({args})"}

        # Then try credential pairs
        for (p1, p2) in _cred_pairs:
            result, ret = _call(unlock_fn, {"param_1": p1, "param_2": p2})
            sequence.append({"fn": unlock_fn, "args": {"param_1": p1, "param_2": p2}, "result": result})
            print(f"  {unlock_fn}({p1!r}, {p2!r}) -> {result}")
            if ret == 0:
                if _test_canary_unlocked(f"{unlock_fn}({p1!r}, {p2!r})"):
                    return {"unlocked": True, "sequence": sequence, "write_fn_tested": write_canary,
                            "notes": f"write unlocked by {unlock_fn}(param_1={p1!r}, param_2={p2!r})"}

    print("  [phase1] Write mode not unlocked — write functions will be probed with best-effort")
    tried = (
        f"Tried: plain init, integer modes on parameterised init fns, "
        f"credential sweep ({len(_cred_pairs)} ID×email/token pairs) on unlock-pattern fns. "
        "All write functions continued returning 0xFFFFFFFB. "
        "Likely requires an admin credential not embedded in the binary."
    )
    return {
        "unlocked": False,
        "sequence": sequence,
        "write_fn_tested": write_canary,
        "notes": tried,
    }


def _discover_loop(client, deployment, invocables: list[dict],
                   findings_path: Path | None = None,
                   dll_strings: dict | None = None,
                   write_mode: bool = False) -> list[dict]:
    """Probe every function, produce structured enrichment JSON, persist findings.

    Returns the list of enrichment dicts (one per function).
    If findings_path is given, load prior findings (skip already-enriched functions)
    and save after each function so a crash doesn't lose progress.
    dll_strings: output of _extract_dll_strings(), injected into system prompt.
    write_mode: if True, write-operation functions are probed after unlock sequence.
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
    print(f"\n[discover] {total} functions  model={deployment}  write_mode={write_mode}\n")

    # Shared vocabulary table — grows as we learn things about the DLL
    vocab: dict = {}
    # Seed with any facts already present in prior findings
    for e in enrichments:
        vocab = _update_vocabulary(client, deployment, vocab, e)
    if vocab:
        print(f"[vocab] Seeded from prior findings: {list(vocab.keys())}")

    # Write-unlock context injected into prompts for write-operation functions
    _WRITE_PATTERNS = re.compile(
        r"(pay|redeem|unlock|process|write|commit|transfer|debit|credit)", re.I
    )
    write_unlock_block = ""
    if write_mode:
        write_unlock_block = (
            "\nWRITE MODE ACTIVE: The write-unlock sequence has already been executed. "
            "Write functions (ProcessPayment, RedeemLoyaltyPoints, UnlockAccount etc.) "
            "should now succeed. Probe them with real customer IDs from STATIC ANALYSIS HINTS.\n"
        )

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

        # Build static-analysis hints block
        hints_block = ""
        if dll_strings:
            hint_parts = []
            if dll_strings.get("ids"):
                hint_parts.append("Known IDs/codes in binary: " + ", ".join(dll_strings["ids"][:20]))
            if dll_strings.get("emails"):
                hint_parts.append("Known emails: " + ", ".join(dll_strings["emails"][:10]))
            if dll_strings.get("status"):
                hint_parts.append("Known status values: " + ", ".join(dll_strings["status"][:15]))
            if dll_strings.get("formats"):
                hint_parts.append("Output format strings: " + " | ".join(dll_strings["formats"][:5]))
            if hint_parts:
                hints_block = "\nSTATIC ANALYSIS HINTS (strings extracted from DLL binary):\n" + "\n".join(hint_parts) + "\nUse these as probe values for string params before trying generic ones.\n"

        # Build per-function context: vocabulary + recent findings summary
        def _context_block() -> str:
            if not enrichments:
                return "  (none yet)"
            lines = []
            for e in enrichments[-6:]:
                wc = f" working_call={e['working_call']}" if e.get("working_call") else ""
                lines.append(f"  - {e['function']} ({e.get('status','?')}): {e.get('description','')}{wc}")
            return "\n".join(lines)

        vocab_section = _vocab_block(vocab)
        is_write_fn = bool(_WRITE_PATTERNS.search(name))

        conversation = [
            {"role": "system", "content": _DISCOVER_SYSTEM},
            {
                "role": "user",
                "content": (
                    f"Document this function:\n"
                    f"  Name: {name}\n"
                    f"  Prototype: {desc[:400]}\n"
                    f"  Parameters: {params_info or '(none)'}\n"
                    + hints_block
                    + ("\n" + vocab_section if vocab_section else "")
                    + (write_unlock_block if is_write_fn else "")
                    + f"\nPreviously discovered functions:\n{_context_block()}\n\n"
                    "Probe it and output the ENRICHMENT JSON block."
                ),
            },
        ]

        enrichment: dict | None = None
        # Track calls to THIS function that returned 0 — used to definitively
        # override the model's classification regardless of what JSON it emits.
        _SENTINELS = {
            0xFFFFFFFF, 0xFFFFFFFE, 0xFFFFFFFD, 0xFFFFFFFC, 0xFFFFFFFB,
        }
        _observed_successes: list[dict] = []  # args that produced return=0
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
                # Record successful calls to the current target function
                if tc.function.name == name and fn_inv:
                    m_ret = re.match(r"Returned:\s*(\d+)", result or "")
                    if m_ret:
                        rval = int(m_ret.group(1))
                        if rval == 0:
                            # Remove output params from working_call (auto-allocated)
                            clean_args = {
                                k: v for k, v in fn_args.items()
                                if v is not None and str(v).strip() not in ("", "None")
                            }
                            _observed_successes.append(clean_args)
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

            # ── Programmatic consistency enforcement ─────────────────────────
            # Priority 1: if we OBSERVED a successful call (return=0) during probing,
            # use the first one as the ground-truth working_call and force success.
            if _observed_successes:
                enrichment["working_call"] = _observed_successes[0]
                enrichment["status"] = "success"
            else:
                # Priority 2: verify any working_call the model claims
                wc = enrichment.get("working_call")
                if wc is not None:
                    verify_inv = inv_map.get(name)
                    if verify_inv:
                        vresult = _execute_local(verify_inv, wc)
                        m = re.match(r"Returned:\s*(\d+)", vresult or "")
                        if m:
                            vret = int(m.group(1))
                            if vret == 0 or (vret not in _SENTINELS and vret < 0xFFFFFFF0):
                                # Verified — force status to success
                                enrichment["status"] = "success"
                            else:
                                # working_call returned a sentinel — discard it
                                enrichment["working_call"] = None
                                if enrichment.get("status") == "success":
                                    enrichment["status"] = "error"
            # ─────────────────────────────────────────────────────────────────

            enrichments.append(enrichment)

            # Update the shared vocabulary with what we just learned
            vocab = _update_vocabulary(client, deployment, vocab, enrichment)

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



def _synthesize(client, deployment: str, findings: list[dict], out_path: Path | None = None) -> str:
    """Produce a unified API reference document from completed findings.

    The document contains:
      - Executive summary: what the DLL does, inferred data model
      - Initialization / usage flow
      - Function reference (grouped by category)
      - Error code reference
      - Known limitations
    """
    findings_json = json.dumps(findings, indent=2, ensure_ascii=False)

    system_msg = (
        "You are a senior technical writer. Given structured reverse-engineering findings "
        "for an undocumented Windows DLL, produce a complete API reference document in Markdown.\n\n"
        "The document MUST include these sections in order:\n"
        "## Overview\n"
        "  One paragraph explaining what this DLL does and what business domain it serves.\n\n"
        "## Data Model\n"
        "  Infer the key entities (e.g. Customer, Order) from output buffer format strings "
        "and parameter patterns. List their fields with types.\n\n"
        "## Initialization\n"
        "  The exact call sequence required before using other functions, with code example.\n\n"
        "## Function Reference\n"
        "  Group functions by category (Read, Write, Utility). For each function:\n"
        "  - Signature with semantic parameter names\n"
        "  - Description\n"
        "  - Parameters table (name | type | direction | description)\n"
        "  - Return values\n"
        "  - Example call\n\n"
        "## Error Code Reference\n"
        "  Table of all observed error codes with meanings.\n\n"
        "## Known Limitations\n"
        "  Functions that could not be fully documented and why.\n\n"
        "Be precise and concise. Use the semantic parameter names from the findings, not param_N."
    )

    user_msg = (
        f"Here are the reverse-engineering findings for this DLL:\n\n"
        f"```json\n{findings_json}\n```\n\n"
        "Produce the full API reference document now."
    )

    print(f"\n[synthesize] Generating API reference from {len(findings)} findings…")
    resp = client.chat.completions.create(
        model=deployment,
        messages=[
            {"role": "system", "content": system_msg},
            {"role": "user",   "content": user_msg},
        ],
        temperature=0.2,  # slight creativity for prose quality
    )
    doc = resp.choices[0].message.content or ""

    if out_path:
        out_path.write_text(doc, encoding="utf-8")
        print(f"[synthesize] Written to {out_path}")
    else:
        print("\n" + "=" * 72)
        print(doc)
        print("=" * 72)

    return doc


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    _load_env()

    ap = argparse.ArgumentParser(description="Local DLL debug runner (Ghidra JSON or invocables_map)")
    ap.add_argument("--dll",          default=None,         help="Path to the .dll file (required for chat/discover)")
    ap.add_argument("--json",         default=None,         help="Ghidra JSON or invocables_map.json path (required for chat/discover)")
    ap.add_argument("--prompt",       default=None,         help="Chat prompt (interactive if omitted)")
    ap.add_argument("--discover",     action="store_true",  help="Autonomous probe+document loop across all functions")
    ap.add_argument("--write-probe",  action="store_true",  help="Run Phase 1 write-unlock probe before discovery")
    ap.add_argument("--synthesize",   default=None,         metavar="FINDINGS_JSON",
                                                            help="Generate API reference from a completed findings JSON")
    ap.add_argument("--out",          default=None,         help="Output path for --synthesize (default: print to stdout)")
    ap.add_argument("--save",         default=None,         help="Save findings to this JSON file (auto-resumes if exists)")
    ap.add_argument("--report",       default=None,         help="Print a previously saved findings JSON (no LLM call)")
    ap.add_argument("--model",        default=None,         help="Override model (e.g. gpt-4o)")
    ap.add_argument("--rounds",       type=int, default=8,  help="Max tool-call rounds per chat (default 8)")
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

    if ns.synthesize is not None:
        # --synthesize <findings.json> [--out api_ref.md]  (no DLL/LLM discover needed)
        src_path = Path(ns.synthesize)
        if not src_path.exists():
            print(f"[!] Findings file not found: {src_path}")
            sys.exit(1)
        findings = json.loads(src_path.read_text(encoding="utf-8"))
        out_path = Path(ns.out) if ns.out else None
        _synthesize(client, deployment, findings, out_path=out_path)
        return

    if ns.discover:
        findings_path = Path(ns.save) if ns.save else None
        dll_strings   = _extract_dll_strings(dll_path)
        if dll_strings["ids"]:
            print(f"[phase0] Found {len(dll_strings['ids'])} IDs: {', '.join(dll_strings['ids'][:8])}")
        if dll_strings["formats"]:
            print(f"[phase0] Found {len(dll_strings['formats'])} format strings")

        write_unlocked = False
        if ns.write_probe:
            inv_map_local = {inv["name"]: inv for inv in invocables}
            unlock_result = _probe_write_unlock(inv_map_local, dll_strings)
            write_unlocked = unlock_result["unlocked"]
            print(f"[phase1] write-unlock probe: {unlock_result['notes']}")
            if write_unlocked:
                print(f"[phase1] unlock sequence: {unlock_result['sequence']}")

        _discover_loop(client, deployment, invocables,
                       findings_path=findings_path, dll_strings=dll_strings,
                       write_mode=write_unlocked)

        if ns.synthesize is not None and findings_path and findings_path.exists():
            findings = json.loads(findings_path.read_text(encoding="utf-8"))
            out_path = Path(ns.out) if ns.out else None
            _synthesize(client, deployment, findings, out_path=out_path)
        return

    prompt = ns.prompt
    if not prompt:
        print("\nEnter a prompt (Enter = 'initialize the library and get the version'):")
        prompt = input("  > ").strip() or "initialize the library and get the version"

    _chat_loop(client, deployment, invocables, prompt, max_rounds=ns.rounds)


if __name__ == "__main__":
    main()
