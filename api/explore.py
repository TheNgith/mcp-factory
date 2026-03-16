"""api/explore.py – Autonomous reverse-engineering exploration worker.

_explore_worker(job_id, invocables) runs the LLM in a structured loop with a
reverse-engineering system prompt.  For each function it:
  1. Calls the function with probing arguments to observe behaviour.
  2. Calls enrich_invocable to write semantic names back to the schema.
  3. Calls record_finding to persist what was learned.

Job status is updated continuously with phase="exploring" and a progress
counter so the UI can display "Exploring functions… (3/12)".
"""

from __future__ import annotations

import json
import logging
import re as _re
import time
from collections import defaultdict
from typing import Any

from api.config import OPENAI_ENDPOINT, OPENAI_DEPLOYMENT, OPENAI_REASONING_DEPLOYMENT, OPENAI_API_KEY, OPENAI_EXPLORE_MODEL, ARTIFACT_CONTAINER
from api.executor import _execute_tool
from api.storage import _persist_job_status, _get_job_status, _patch_invocable, _save_finding, _patch_finding, _upload_to_blob
from api.telemetry import _openai_client

logger = logging.getLogger("mcp_factory.api")

_MAX_EXPLORE_ROUNDS_PER_FUNCTION = 3   # 3 rounds catches >95% of cases; 6 was wasteful
_MAX_FUNCTIONS_PER_SESSION = 50  # safety cap

_SENTINEL_DEFAULTS: dict[int, str] = {
    0xFFFFFFFF: "not found / invalid input",
    0xFFFFFFFE: "null argument",
    0xFFFFFFFD: "not initialized",
    0xFFFFFFFC: "account locked or suspended",
    0xFFFFFFFB: "write operation denied",
}


def _calibrate_sentinels(invocables: list[dict], client, model: str) -> dict[int, str]:
    """Phase 0.5: probe every exported function with no args and cluster the
    non-zero high-bit return values to discover this DLL's sentinel error codes.
    Falls back to _SENTINEL_DEFAULTS if nothing useful is found."""
    counts: dict[int, int] = defaultdict(int)
    val_fns: dict[int, list[str]] = defaultdict(list)

    for inv in invocables:
        try:
            result = _execute_tool(inv, {})
            m = _re.match(r"Returned:\s*(\d+)", result or "")
            if not m:
                continue
            val = int(m.group(1))
            if val == 0:
                continue
            counts[val] += 1
            val_fns[val].append(inv["name"])
        except Exception:
            pass

    candidates = {
        v: fns for v, fns in val_fns.items()
        if v >= 0x80000000 and counts[v] >= 2
    }
    if not candidates:
        return _SENTINEL_DEFAULTS

    cand_lines = "\n".join(
        f"  0x{v:08X} (decimal {v}) — returned by: {', '.join(fns[:6])}"
        for v, fns in sorted(candidates.items(), reverse=True)
    )
    prompt = (
        "Assign a SHORT plain-English meaning (3-8 words) to each of these "
        "32-bit return codes from an undocumented Windows DLL.\n"
        f"{cand_lines}\n"
        "Output ONLY a JSON object: {\"0xFFFFFF..\": \"meaning\", ...}"
    )
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
        )
        raw = (resp.choices[0].message.content or "{}").strip()
        if raw.startswith("```"):
            raw = "\n".join(raw.splitlines()[1:]).rstrip("`").strip()
        named: dict[str, str] = json.loads(raw)
        result_map = {}
        for k, meaning in named.items():
            try:
                result_map[int(k, 16)] = str(meaning)
            except (ValueError, TypeError):
                pass
        if result_map:
            return result_map
    except Exception as exc:
        logger.debug("[explore] sentinel calibration LLM call failed: %s", exc)
    return _SENTINEL_DEFAULTS


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


def _update_vocabulary(client, model: str, vocab: dict, enrichment: dict) -> dict:
    """Ask the LLM to extract generalisable facts from one enrichment into the vocab table.

    Port of run_local.py's _update_vocabulary — uses _VOCAB_UPDATE_SYSTEM system prompt,
    runs on every enrichment (not just successful ones), and merges carefully:
    lists are deduplicated-extended, dicts are deep-merged, scalars are overwritten.
    """
    prompt = (
        f"Current vocabulary:\n{json.dumps(vocab, indent=2)}\n\n"
        f"New function enrichment:\n{json.dumps(enrichment, indent=2)}\n\n"
        "Update the vocabulary with any new generalisable facts. Output only the updated JSON."
    )
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _VOCAB_UPDATE_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            temperature=0,
        )
        text = (resp.choices[0].message.content or "").strip()
        if text.startswith("```"):
            text = "\n".join(text.split("\n")[1:]).rstrip("`").strip()
        start = text.find("{")
        end   = text.rfind("}")
        if start != -1 and end != -1:
            updates = json.loads(text[start : end + 1])
            # Merge: lists are deduplicated-extended, dicts are deep-merged, scalars overwritten
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


def _probe_write_unlock(invocables: list[dict], dll_strings: dict) -> dict:
    """Phase 1: try to flip the DLL from read-only to write-ready.
    Tries CS_Initialize with mode integers, then any Begin/Enable/Auth-style
    functions, then a 56-pair credential sweep.  Returns unlock result dict."""
    _WRITE_SENTINELS = {0xFFFFFFFB}
    inv_map = {inv["name"]: inv for inv in invocables}
    _init_names = [n for n in inv_map if _re.search(r"init(ializ)?", n, _re.I)]
    _write_fn_names = [
        n for n in inv_map
        if _re.search(r"(pay|redeem|unlock|process|write|commit|transfer|debit|credit)", n, _re.I)
    ]
    tried = []

    # Detect no-param init variants first
    no_param_inits = [n for n in _init_names if not inv_map[n].get("parameters")]
    if no_param_inits:
        for n in no_param_inits:
            r = _execute_tool(inv_map[n], {})
            tried.append(f"{n}() -> {r}")

    # Try mode-based init
    for mode in (0, 1, 2, 4, 8, 16, 256, 512):
        for n in _init_names:
            if inv_map[n].get("parameters"):
                _r = _execute_tool(inv_map[n], {"param_1": mode})
                tried.append(f"{n}(mode={mode}) -> {_r}")
                # Test against first write fn
                if _write_fn_names:
                    _wfn = _write_fn_names[0]
                    _wr  = _execute_tool(inv_map[_wfn], {})
                    _ret_m = _re.match(r"Returned:\s*(\d+)", _wr or "")
                    _ret   = int(_ret_m.group(1)) & 0xFFFFFFFF if _ret_m else 0xFFFFFFFF
                    if _ret not in _WRITE_SENTINELS and _ret == 0:
                        return {"unlocked": True, "sequence": [{"fn": n, "args": {"param_1": mode}}],
                                "notes": f"unlocked with {n}(mode={mode})"}

    # Credential sweep using strings extracted from the binary
    _all_strings = dll_strings.get("ids", []) + dll_strings.get("misc", [])
    _cred_tokens = list(dict.fromkeys([  # preserve order, deduplicate
        s for s in _all_strings if 3 < len(s) < 40
    ]))[:28]
    _canary = _write_fn_names[0] if _write_fn_names else None
    for n in _init_names:
        if not inv_map[n].get("parameters"):
            continue
        for tok in _cred_tokens:
            _r = _execute_tool(inv_map[n], {"param_1": tok})
            tried.append(f"{n}(cred={tok!r}) -> {_r}")
            if _canary:
                _wr  = _execute_tool(inv_map[_canary], {})
                _ret_m = _re.match(r"Returned:\s*(\d+)", _wr or "")
                _ret   = int(_ret_m.group(1)) & 0xFFFFFFFF if _ret_m else 0xFFFFFFFF
                if _ret not in _WRITE_SENTINELS and _ret == 0:
                    return {"unlocked": True,
                            "sequence": [{"fn": n, "args": {"param_1": tok}}],
                            "notes": f"unlocked with {n}(cred={tok!r})"}

    return {"unlocked": False, "sequence": [], "write_fn_tested": _canary,
            "notes": f"write-unlock failed after {len(tried)} attempts"}


def _build_explore_system_message(
    invocables: list,
    findings: list,
    sentinels: dict[int, str] | None = None,
    vocab: dict | None = None,
) -> dict:
    """System message for the autonomous exploration agent."""
    fn_names = ", ".join(inv["name"] for inv in invocables)
    findings_block = ""
    if findings:
        lines = [
            f"  - {f.get('function','?')}: {f.get('finding','')}"
            + (f" (working call: {f['working_call']})" if f.get("working_call") else "")
            for f in findings
        ]
        findings_block = (
            "\nALREADY DISCOVERED (skip re-probing these):\n"
            + "\n".join(lines)
            + "\n"
        )

    # Build sentinel table from calibrated values (falls back to Contoso defaults)
    _sents = sentinels if sentinels is not None else _SENTINEL_DEFAULTS
    sentinel_lines = "\n".join(
        f"   - {val} (0x{val:08X}) = {meaning}"
        for val, meaning in sorted(_sents.items(), reverse=True)
    )

    vocab_block = ("\n" + _vocab_block(vocab) + "\n") if vocab else ""

    return {
        "role": "system",
        "content": (
            "You are a reverse-engineering agent. Your job is to systematically explore "
            "an undocumented Windows DLL and document what each function does.\n\n"
            "AVAILABLE FUNCTIONS: " + fn_names + "\n\n"
            "PROTOCOL:\n"
            "1. ALWAYS call the init function (Initialize, Init, CS_Initialize) as the VERY FIRST "
            "call when exploring each function, even if it seems unrelated to init. This ensures "
            "consistent DLL state. Do NOT skip this step even if called before.\n"
            "2. Call each function with safe probe values:\n"
            "   - integer params (uint, int, ushort): try 0, 1, 64, 256\n"
            "   - input string/byte* params: try '', 'test', and any STATIC ANALYSIS HINTS provided\n"
            "   - output pointer params (undefined4*, undefined8*, uint*): OMIT from the call — "
            "the executor auto-allocates these. Their values appear as 'param_N=<value>' in the result.\n"
            "   - undefined* output buffer + adjacent uint size param: OMIT BOTH — "
            "executor allocates 4096-byte buffer and supplies size=4096 automatically.\n"
            "   CRITICAL: NEVER pass a string or integer value for an output-buffer param "
            "(undefined*, undefined4*, undefined8*) — always omit it entirely or you will get an access violation.\n"
            "   ZERO-OUTPUT RETRY RULE: if the call returns 0 (SUCCESS) but every output param "
            "value is 0, the inputs were too small. Retry with LARGER values before concluding "
            "the output is always zero: for financial/calculation functions use principal=10000, "
            "rate=500, period=12; for general numeric functions try 1000, 10000, 100000.\n"
            "3. Classify the return value:\n"
            "   - 0 = success for action functions\n"
            + sentinel_lines + "\n"
            "   - For GetVersion/GetBuild/GetRevision: the return value IS a packed UINT version — "
            "any non-zero integer is SUCCESS, not an error. "
            "Decode as (val>>16)&0xFF . (val>>8)&0xFF . val&0xFF (e.g. 131841 → 2.3.1) and "
            "include the decoded string in record_finding notes.\n"
            "4. Once you have a working call OR have exhausted safe probes, call BOTH:\n"
            "   a. enrich_invocable — rename generic params (param_1 → semantic_name), set description.\n"
            "   b. record_finding   — persist what you discovered. The 'finding' field MUST be a "
            "complete non-empty sentence describing EXACTLY what you observed: the return value, "
            "output param values, or the error code seen. "
            "Example: 'Returns 0 on success with balance in param_2; CUST-001 returned 25000.' "
            "An empty string is NOT acceptable.\n"
            "   HARD CONSISTENCY for record_finding:\n"
            "   - If ANY probe returned 0 (or a valid semantic integer for GetVersion-style),\n"
            "     set status='success' and working_call to that exact args dict — NO EXCEPTIONS.\n"
            "   - If every probe returned a sentinel error code, set status='error' and working_call=null.\n"
            "   - Never set working_call to args that produced a sentinel error return (0xFFFFFFFF etc.).\n"
            "5. Move on to the next function. Stop when every function has been attempted.\n\n"
            "CONSTRAINTS:\n"
            "- The PRIMARY indicator of success/failure is ALWAYS the integer return value, "
            "not the output param values.\n"
            "- Never call dangerous functions (format, delete, write) with real data.\n"
            "- Keep probe values small and safe.\n"
            "- Be concise — after each function, proceed immediately to the next.\n"
            "- Do not ask for confirmation; work autonomously.\n"
            + vocab_block
            + findings_block
        ),
    }


def _synthesize(client, model: str, findings: list[dict]) -> str:
    """Generate a full API reference Markdown document from completed findings.

    Port of run_local.py's _synthesize() — same prompt, same section structure.
    Result is uploaded to blob as api_reference.md after exploration completes.
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
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_msg},
                {
                    "role": "user",
                    "content": (
                        f"Here are the reverse-engineering findings for this DLL:\n\n"
                        f"```json\n{findings_json}\n```\n\n"
                        "Produce the full API reference document now."
                    ),
                },
            ],
            temperature=0.2,
        )
        return resp.choices[0].message.content or ""
    except Exception as exc:
        logger.debug("_synthesize failed: %s", exc)
        return ""


def _explore_worker(job_id: str, invocables: list[dict]) -> None:
    """Background worker: explore each invocable with the LLM and enrich the schema."""
    if not OPENAI_ENDPOINT and not OPENAI_API_KEY:
        logger.warning("[%s] explore_worker: neither OPENAI_API_KEY nor AZURE_OPENAI_ENDPOINT configured — aborting", job_id)
        return

    from api.storage import _load_findings

    total = min(len(invocables), _MAX_FUNCTIONS_PER_SESSION)
    invocables = invocables[:total]

    logger.info("[%s] explore_worker: starting, %d functions to explore", job_id, total)

    # Update status to exploring
    _set_explore_status(job_id, 0, total, "Starting exploration…")

    # Build inv_map for tool dispatch
    inv_map: dict[str, dict] = {}
    for inv in invocables:
        inv_map[inv["name"]] = inv

    # Inject synthetic tools into inv_map
    _enrich_inv = {
        "name": "enrich_invocable",
        "source_type": "enrich",
        "_job_id": job_id,
        "execution": {"method": "enrich"},
        "parameters": [],
    }
    _findings_inv = {
        "name": "record_finding",
        "source_type": "findings",
        "_job_id": job_id,
        "execution": {"method": "findings"},
        "parameters": [],
    }
    inv_map["enrich_invocable"] = _enrich_inv
    inv_map["record_finding"] = _findings_inv

    # Build tool schemas list for the LLM
    from api.generate import run_generate as _run_gen  # noqa: F401
    from api.chat import _RECORD_FINDING_TOOL, _ENRICH_INVOCABLE_TOOL  # type: ignore

    # Build tools from invocables
    tool_schemas: list[dict] = []
    import re as _re
    for inv in invocables:
        props: dict = {}
        required: list = []
        for p in (inv.get("parameters") or []):
            if isinstance(p, str):
                p = {"name": p, "type": "string"}
            pname = p.get("name", "arg")
            json_type = p.get("json_type") or "string"
            props[pname] = {
                "type": json_type,
                "description": p.get("description") or p.get("type", "string"),
            }
            if p.get("direction", "in") != "out":
                required.append(pname)
        safe_name = _re.sub(r"[^a-zA-Z0-9_.\-]", "_", inv["name"])[:64]
        desc = inv.get("doc") or inv.get("description") or inv.get("signature") or inv["name"]
        tool_schemas.append({
            "type": "function",
            "function": {
                "name": safe_name,
                "description": desc,
                "parameters": {
                    "type": "object",
                    "properties": props,
                    "required": required,
                },
            },
        })
    tool_schemas.append(_RECORD_FINDING_TOOL)
    tool_schemas.append(_ENRICH_INVOCABLE_TOOL)

    client = _openai_client()
    # Use the dedicated explore model (gpt-4o-mini by default) for cost efficiency.
    # When using direct OpenAI key, OPENAI_EXPLORE_MODEL controls this.
    # When using Azure, fall back to the reasoning deployment.
    model = OPENAI_EXPLORE_MODEL if OPENAI_API_KEY else (OPENAI_REASONING_DEPLOYMENT or OPENAI_DEPLOYMENT)

    explored = 0
    try:
        prior_findings = _load_findings(job_id)
        already_explored = {f.get("function") for f in prior_findings if f.get("function")}

        # Phase 0.5: auto-calibrate sentinel error codes for this DLL
        sentinels = _SENTINEL_DEFAULTS
        try:
            logger.info("[%s] explore_worker: phase0.5 calibrating sentinels…", job_id)
            _set_explore_status(job_id, 0, total, "Calibrating error codes…")
            sentinels = _calibrate_sentinels(invocables, client, model)
            logger.info("[%s] explore_worker: phase0.5 sentinels: %s", job_id,
                        {f"0x{k:08X}": v for k, v in sentinels.items()})
        except Exception as _se:
            logger.debug("[%s] explore_worker: phase0.5 failed, using defaults: %s", job_id, _se)

        # Shared vocabulary table — grows as we learn things about this DLL
        vocab: dict = {}

        # Phase 0: extract static hints from the DLL binary (best-effort)
        _static_hints_block = ""
        _dll_strings: dict = {}
        try:
            dll_path = next(
                (inv.get("execution", {}).get("dll_path", "") for inv in invocables
                 if inv.get("execution", {}).get("dll_path")),
                "",
            )
            if dll_path:
                import re as _re2
                from pathlib import Path as _Path
                _data = _Path(dll_path).read_bytes()
                _text = _data.decode("ascii", errors="ignore")
                _raw  = sorted(set(m.group(0).strip() for m in _re2.finditer(r"[ -~]{6,}", _text) if m.group(0).strip()))
                _ids     = [s for s in _raw if _re2.match(r"[A-Z]{2,6}-[\w-]+", s) and len(s) < 40]
                _emails  = [s for s in _raw if _re2.match(r"[\w.+-]+@[\w.-]+\.[a-z]{2,}", s, _re2.I)]
                _fmts    = [s for s in _raw if "%" in s and any(c in s for c in ("s", "d", "u", "f", "lu")) and len(s) < 120]
                _dll_strings = {"ids": _ids, "emails": _emails, "all": _raw}
                _status  = [s for s in _raw if s.isupper() and 4 <= len(s) <= 16 and s.isalpha()
                            and s.lower() in {"active","inactive","pending","shipped","delivered",
                                              "cancelled","suspended","complete","unknown","locked","unlocked"}]
                parts = []
                if _ids:    parts.append("Known IDs/codes: " + ", ".join(_ids[:20]))
                if _emails: parts.append("Known emails: " + ", ".join(_emails[:10]))
                if _status: parts.append("Known status values: " + ", ".join(_status[:15]))
                if _fmts:   parts.append("Output format strings: " + " | ".join(_fmts[:5]))
                if parts:
                    _static_hints_block = (
                        "\nSTATIC ANALYSIS HINTS (strings extracted from DLL binary):\n"
                        + "\n".join(parts)
                        + "\nUse these as probe values for string params before trying generic ones.\n"
                    )
                    logger.info("[%s] explore_worker: phase0 found %d IDs, %d emails, %d formats",
                                job_id, len(_ids), len(_emails), len(_fmts))
        except Exception as _e:
            logger.debug("[%s] explore_worker: phase0 string extraction failed: %s", job_id, _e)

        # Phase 1: write-unlock probe — mirror of run_local.py --write-probe logic
        write_unlock_block = ""
        try:
            logger.info("[%s] explore_worker: phase1 write-unlock probe…", job_id)
            _set_explore_status(job_id, 0, total, "Testing write-mode unlock…")
            unlock_result = _probe_write_unlock(invocables, _dll_strings)
            if unlock_result.get("unlocked"):
                write_unlock_block = (
                    "\nWRITE MODE ACTIVE: The write-unlock sequence has already been executed. "
                    "Write functions (ProcessPayment, RedeemLoyaltyPoints, UnlockAccount etc.) "
                    "should now succeed. Probe them with real customer IDs from STATIC ANALYSIS HINTS.\n"
                )
                logger.info("[%s] explore_worker: phase1 UNLOCKED: %s", job_id, unlock_result["notes"])
            else:
                logger.info("[%s] explore_worker: phase1 not unlocked: %s", job_id,
                            unlock_result.get("notes", ""))
        except Exception as _we:
            logger.debug("[%s] explore_worker: phase1 write-unlock probe failed: %s", job_id, _we)

        for inv in invocables:
            fn_name = inv["name"]

            # Skip functions already documented in a previous session
            if fn_name in already_explored:
                explored += 1
                _set_explore_status(job_id, explored, total, f"Skipped {fn_name} (already documented)")
                continue

            _set_explore_status(job_id, explored, total, f"Exploring {fn_name}…")
            logger.info("[%s] explore_worker: starting %s (%d/%d)", job_id, fn_name, explored + 1, total)

            # Build a focused conversation just for this function
            prior = _load_findings(job_id)
            sys_msg = _build_explore_system_message(invocables, prior, sentinels=sentinels, vocab=vocab)
            _is_write_fn = bool(_re.search(
                r"(pay|redeem|unlock|process|write|commit|transfer|debit|credit)", fn_name, _re.I
            ))
            conversation = [
                sys_msg,
                {
                    "role": "user",
                    "content": (
                        f"Explore the function '{fn_name}'. "
                        "Call it with safe probe values, observe the result, "
                        "then call enrich_invocable and record_finding with what you learned. "
                        "Be brief — one summary sentence after you're done."
                        + _static_hints_block
                        + (write_unlock_block if _is_write_fn else "")
                    ),
                },
            ]

            # Track calls that returned 0 for ground-truth consistency enforcement
            _observed_successes: list[dict] = []
            _p_lookup = {p.get("name", ""): p for p in (inv.get("parameters") or [])}

            for _round in range(_MAX_EXPLORE_ROUNDS_PER_FUNCTION):
                try:
                    from typing import cast, Any as _Any
                    response = client.chat.completions.create(
                        model=model,
                        messages=conversation,
                        tools=cast(_Any, tool_schemas),
                        tool_choice="auto",
                        temperature=0,
                    )
                except Exception as exc:
                    logger.warning(
                        "[%s] explore_worker: OpenAI call failed for %s round %d: %s",
                        job_id, fn_name, _round, exc,
                    )
                    break

                msg = response.choices[0].message

                if not msg.tool_calls:
                    # Model finished — no more tool calls needed
                    break

                # Append assistant turn
                conversation.append({
                    "role": "assistant",
                    "content": msg.content or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,  # type: ignore[union-attr]
                                "arguments": tc.function.arguments,  # type: ignore[union-attr]
                            },
                        }
                        for tc in msg.tool_calls
                    ],
                })

                # Execute each tool call
                for tc in msg.tool_calls:
                    _fn = tc.function  # type: ignore[union-attr]
                    tc_name = _fn.name
                    try:
                        tc_args = json.loads(_fn.arguments)
                    except json.JSONDecodeError:
                        tc_args = {}

                    tc_inv = inv_map.get(tc_name)
                    if tc_inv is not None:
                        try:
                            tool_result = _execute_tool(tc_inv, tc_args)
                        except Exception as exc:
                            tool_result = f"Tool error: {exc}"
                    else:
                        tool_result = f"Tool '{tc_name}' not found."

                    # Ground-truth tracking: record direct observations of return=0
                    if tc_name == fn_name:
                        _ret_m = _re.match(r"Returned:\s*(\d+)", tool_result or "")
                        if _ret_m and int(_ret_m.group(1)) == 0:
                            _out_bases = frozenset({
                                "undefined", "undefined2", "undefined4", "undefined8",
                                "uint", "uint32_t", "int", "int32_t", "dword",
                                "ulong", "uint4", "uint8", "long", "ulong32",
                            })
                            _clean: dict = {}
                            for _k, _v in tc_args.items():
                                _p = _p_lookup.get(_k, {})
                                _pt = _p.get("type", "").lower().replace("const ", "").strip().rstrip(" *")
                                _is_out = "*" in _p.get("type", "") and _pt in _out_bases
                                if not _is_out and _p.get("direction", "in") != "out":
                                    _clean[_k] = _v
                            _observed_successes.append(_clean)

                    logger.info(
                        "[%s] explore_worker: tool=%s result=%s",
                        job_id, tc_name, str(tool_result)[:120],
                    )

                    conversation.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": tool_result,
                    })

            explored += 1
            already_explored.add(fn_name)
            _set_explore_status(job_id, explored, total, f"Completed {fn_name}")

            # Consistency enforcement (port of run_local.py _discover_loop logic)
            if _observed_successes:
                # Ground-truth override: we observed return=0 directly → force success
                try:
                    _patch_finding(job_id, fn_name, {
                        "working_call": _observed_successes[0],
                        "status": "success",
                    })
                    logger.info(
                        "[%s] explore_worker: ground-truth override for %s working_call=%s",
                        job_id, fn_name, _observed_successes[0],
                    )
                except Exception as _ce:
                    logger.debug("[%s] consistency patch failed for %s: %s", job_id, fn_name, _ce)
            else:
                # Verify the LLM's claimed working_call by re-running it
                _cur = _load_findings(job_id)
                _ff = next((f for f in reversed(_cur) if f.get("function") == fn_name), None)
                if _ff and _ff.get("working_call") is not None:
                    _vi = inv_map.get(fn_name)
                    if _vi:
                        try:
                            _vr = _execute_tool(_vi, _ff["working_call"])
                            _vm = _re.match(r"Returned:\s*(\d+)", _vr or "")
                            if _vm:
                                _vret = int(_vm.group(1))
                                if _vret not in sentinels and _vret <= 0xFFFFFFF0:
                                    _patch_finding(job_id, fn_name, {"status": "success"})
                                else:
                                    _patch_finding(job_id, fn_name, {
                                        "working_call": None, "status": "error",
                                    })
                                    logger.info(
                                        "[%s] explore_worker: discarded hallucinated working_call for %s",
                                        job_id, fn_name,
                                    )
                        except Exception as _ve:
                            logger.debug("[%s] working_call verify failed for %s: %s",
                                         job_id, fn_name, _ve)

            # Update shared vocabulary from the latest finding for this function
            last_finding = _load_findings(job_id)
            if last_finding:
                last = next(
                    (f for f in reversed(last_finding) if f.get("function") == fn_name), None
                )
                if last:
                    try:
                        vocab = _update_vocabulary(client, model, vocab, last)
                    except Exception as _ve:
                        logger.debug("[%s] vocab update failed for %s: %s", job_id, fn_name, _ve)

        # Synthesis: generate API reference Markdown document from all findings
        try:
            _syn_findings = _load_findings(job_id)
            if _syn_findings:
                logger.info("[%s] explore_worker: synthesizing API reference (%d fns)…",
                            job_id, len(_syn_findings))
                _set_explore_status(job_id, explored, total, "Synthesizing API reference…")
                _report = _synthesize(client, model, _syn_findings)
                if _report:
                    _upload_to_blob(
                        ARTIFACT_CONTAINER,
                        f"{job_id}/api_reference.md",
                        _report.encode("utf-8"),
                    )
                    logger.info("[%s] explore_worker: api_reference.md saved to blob", job_id)
        except Exception as _syn_e:
            logger.debug("[%s] explore_worker: synthesis failed: %s", job_id, _syn_e)

        # Mark exploration done — update job status back to previous terminal state
        # or set a new "explore_done" sub-status so the UI knows it finished.
        current = _get_job_status(job_id) or {}
        _persist_job_status(
            job_id,
            {
                **current,
                "explore_phase": "done",
                "explore_progress": f"{explored}/{total}",
                "updated_at": time.time(),
            },
            sync=True,
        )
        logger.info("[%s] explore_worker: finished %d/%d functions", job_id, explored, total)

    except Exception as exc:
        logger.error("[%s] explore_worker: fatal error: %s", job_id, exc)
        current = _get_job_status(job_id) or {}
        _persist_job_status(
            job_id,
            {
                **current,
                "explore_phase": "error",
                "explore_error": str(exc),
                "updated_at": time.time(),
            },
            sync=True,
        )


def _set_explore_status(job_id: str, explored: int, total: int, message: str) -> None:
    current = _get_job_status(job_id) or {}
    _persist_job_status(
        job_id,
        {
            **current,
            "explore_phase": "exploring",
            "explore_progress": f"{explored}/{total}",
            "explore_message": message,
            "updated_at": time.time(),
        },
    )
