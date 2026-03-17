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


# ---------------------------------------------------------------------------
# Hypothesis-driven interpretation (Layer 2.5)
# ---------------------------------------------------------------------------

_HYPOTHESIS_SYSTEM = """\
You are a DLL reverse-engineering analyst. A function has been probed and raw numeric values
were observed. Give the most probable semantic interpretation of each ambiguous value, then
propose ONE targeted cross-validation call against an already-explored function to confirm it.

Draw on common Win32/enterprise DLL conventions:
- Financial amounts are commonly stored as cents (uint * 100 = dollars)
- Packed version UINTs: (val>>16)&0xFF . (val>>8)&0xFF . val&0xFF
- Status/handle integers > 100000 are likely handles, not error codes
- Output params named balance/amount/total in financial DLLs are overwhelmingly cents

Output ONLY valid JSON (no markdown fences):
{
  "interpretations": {
    "<param_name_or_return>": "<semantic meaning, e.g. 'balance in cents — divide by 100'>"
  },
  "confidence": "high|medium|low",
  "cross_validation": null
}
OR if cross-validation would help:
{
  "interpretations": {...},
  "confidence": "medium",
  "cross_validation": {
    "function": "<already-explored function name>",
    "args": {"<param>": "<value>"},
    "confirms": "<what matching result would prove>"
  }
}
"""


def _generate_hypothesis(
    client,
    model: str,
    fn_name: str,
    raw_result: str,
    vocab: dict,
    all_findings: list[dict],
) -> dict:
    """Interpret ambiguous numeric outputs from a function call using LLM reasoning.

    Returns dict with interpretations and optional cross-validation probe.
    Only fires when a successful call (return=0) produced non-trivial output values.
    """
    vocab_summary = json.dumps(vocab, ensure_ascii=False)[:600]
    explored_fns = list(dict.fromkeys(f.get("function", "") for f in all_findings if f.get("function")))
    findings_summary = "\n".join(
        f"  {f.get('function')}: {f.get('finding', '')[:120]}"
        for f in all_findings[-8:]
    )
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _HYPOTHESIS_SYSTEM},
                {
                    "role": "user",
                    "content": (
                        f"Function: {fn_name}\n"
                        f"Raw call result: {raw_result}\n\n"
                        f"Already-explored functions (usable for cross-validation): {', '.join(explored_fns)}\n\n"
                        f"DLL vocabulary so far:\n{vocab_summary}\n\n"
                        f"Recent findings:\n{findings_summary}\n\n"
                        "Produce the JSON interpretation now."
                    ),
                },
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
        )
        raw = resp.choices[0].message.content or "{}"
        return json.loads(raw)
    except Exception as exc:
        logger.debug("_generate_hypothesis failed for %s: %s", fn_name, exc)
        return {}


# ---------------------------------------------------------------------------
# Layer 3 schema backfill from synthesis document
# ---------------------------------------------------------------------------

_BACKFILL_SYSTEM = """\
You are a technical API documentation specialist. You have:
1. A synthesized API reference document produced from reverse-engineering findings
2. The current tool schema JSON for this DLL

Your task: produce patches to enrich each function's schema description and parameter
annotations using knowledge from the synthesis document that is NOT yet in the schema.

Focus on:
- Return value semantics with observed values (e.g. "returns balance as raw UINT in cents")
- Output parameter descriptions with confirmed example values
- Cross-references to related functions
- Any initialization requirements noted in the synthesis
- Criticality classification: mark functions that MUST be called before others work,
  or that are gating dependencies for write operations

Output ONLY valid JSON (no markdown fences):
{
  "patches": [
    {
      "function": "<function_name>",
      "description": "<enriched 1-2 sentence description including return semantics>",
      "criticality": "required_first|read|write|utility|unknown",
      "depends_on": ["<function that must be called before this one, if any>"],
      "param_patches": [
        {"name": "<param_name>", "description": "<enriched description with observed values>"}
      ]
    }
  ]
}

criticality values:
- required_first: must be called before ANY other function works (e.g. Initialize)
- read: safe read-only query, no side effects
- write: modifies state — payment, refund, redemption, update
- utility: diagnostic, versioning, metadata
- unknown: could not determine

Only include functions and params where the synthesis adds something beyond the current schema.
"""


def _backfill_schema_from_synthesis(
    client,
    model: str,
    synthesis_md: str,
    invocables: list[dict],
    job_id: str,
) -> None:
    """Layer 3: re-annotate stored schema using the completed synthesis document.

    After _synthesize() produces api_reference.md this pass asks the LLM to use the
    full synthesis context to enrich every function's description and parameter
    annotations with proven semantics (units, entity refs, example values).
    Results are patched back into stored invocables via _patch_invocable.
    """
    current_schema = [
        {
            "name": inv["name"],
            "description": inv.get("description") or inv.get("doc") or "",
            "parameters": [
                {
                    "name": p.get("name"),
                    "type": p.get("type"),
                    "description": p.get("description", ""),
                }
                for p in (inv.get("parameters") or [])
                if isinstance(p, dict)
            ],
        }
        for inv in invocables
    ]
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _BACKFILL_SYSTEM},
                {
                    "role": "user",
                    "content": (
                        f"## Synthesis Document\n\n{synthesis_md[:6000]}\n\n"
                        f"## Current Schema\n\n```json\n"
                        f"{json.dumps(current_schema, indent=2)[:4000]}\n```\n\n"
                        "Produce the patches JSON now."
                    ),
                },
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
        )
        patches_data = json.loads(resp.choices[0].message.content or "{}")
        patches = patches_data.get("patches", [])

        patched = 0
        for patch in patches:
            fn_name = patch.get("function", "")
            if not fn_name:
                continue
            target_inv = next((inv for inv in invocables if inv["name"] == fn_name), None)
            if not target_inv:
                continue

            update: dict = {}
            if patch.get("description"):
                update["description"] = patch["description"]
            if patch.get("criticality"):
                update["criticality"] = patch["criticality"]
            if patch.get("depends_on"):
                update["depends_on"] = patch["depends_on"]

            param_patches = {
                pp["name"]: pp["description"]
                for pp in patch.get("param_patches", [])
                if pp.get("name") and pp.get("description")
            }
            if param_patches:
                updated_params = []
                for p in (target_inv.get("parameters") or []):
                    if isinstance(p, dict):
                        pname = p.get("name", "")
                        if pname in param_patches:
                            p = {**p, "description": param_patches[pname]}
                    updated_params.append(p)
                update["parameters"] = updated_params

            if update:
                try:
                    _patch_invocable(job_id, fn_name, update)
                    patched += 1
                except Exception as _pe:
                    logger.debug("[%s] backfill: patch failed for %s: %s", job_id, fn_name, _pe)

        logger.info("[%s] backfill_schema: applied patches to %d/%d functions", job_id, patched, len(patches))
    except Exception as exc:
        logger.debug("[%s] _backfill_schema_from_synthesis failed: %s", job_id, exc)


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


# ---------------------------------------------------------------------------
# Active Learning-style uncertainty scoring
# ---------------------------------------------------------------------------

def _uncertainty_score(inv: dict) -> int:
    """Score a function by how ambiguous it looks before exploration.

    Lower score = simpler = explore earlier to build vocabulary faster.
    Higher score = more undefined types, more params = explore later when
    the accumulated vocab context makes probing more effective.
    This is the 'curriculum learning' flavour of active learning: easy first.
    """
    params = inv.get("parameters") or []
    score = 0
    for p in (params if isinstance(params, list) else []):
        if not isinstance(p, dict):
            continue
        t = (p.get("type") or "").lower()
        if "undefined" in t:
            score += 2   # completely unknown type — high ambiguity
        elif "*" in t:
            score += 1   # pointer — direction unclear
    score += len(params)  # more params = more to figure out
    # Prefer simple no-param or single-param functions first
    if not params:
        score = 0
    return score


# ---------------------------------------------------------------------------
# Confidence gap questions (self-assessment after exploration)
# ---------------------------------------------------------------------------

_GAP_SYSTEM = """\
You are reviewing the results of an automated DLL reverse-engineering session.
Your job is to identify what the system could NOT confidently determine and generate
targeted questions a domain expert could answer to fill those gaps.

A domain expert is someone who USES this system but may not have the source code —
they know what the application does at a business level (e.g. "yes, amounts are in cents",
"customer IDs always start with CUST-", "the status can also be PENDING").

Output ONLY valid JSON (no markdown fences):
{
  "gaps": [
    {
      "function": "<function_name or 'general'>",
      "uncertainty": "<what specifically is uncertain, in 1 sentence>",
      "question": "<a clear, specific question the domain expert could answer>"
    }
  ]
}

Rules:
- Maximum 5 gaps total — only the most impactful unknowns
- Only ask about genuinely uncertain things (not obvious things like what Initialize does)
- Prefer questions about: numeric value units, enum/status meanings, unknown ID formats,
  error conditions not observed, functions that always failed
- Questions must be answerable by a user familiar with the system but without source code
- If everything is well-documented and confident, return {"gaps": []}
"""


def _generate_confidence_gaps(
    client,
    model: str,
    findings: list[dict],
    invocables: list[dict],
) -> list[dict]:
    """Ask the LLM to self-assess its findings and identify confidence gaps.

    Returns a list of {function, uncertainty, question} dicts that the UI
    can surface as optional clarification prompts for the user.
    """
    findings_summary = json.dumps(
        [
            {
                "function": f.get("function"),
                "status": f.get("status"),
                "finding": (f.get("finding") or "")[:200],
                "working_call": f.get("working_call"),
                "interpretation": f.get("interpretation"),
            }
            for f in findings
        ],
        ensure_ascii=False,
    )
    failed = [inv["name"] for inv in invocables if not any(
        f.get("function") == inv["name"] and f.get("status") == "success"
        for f in findings
    )]
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _GAP_SYSTEM},
                {
                    "role": "user",
                    "content": (
                        f"Exploration findings:\n```json\n{findings_summary[:5000]}\n```\n\n"
                        f"Functions that never returned success: {failed or 'none'}\n\n"
                        "Generate the gaps JSON now."
                    ),
                },
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
        )
        data = json.loads(resp.choices[0].message.content or "{}")
        return data.get("gaps", [])
    except Exception as exc:
        logger.debug("_generate_confidence_gaps failed: %s", exc)
        return []


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
    use_cases: str = "",
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
            "1. ALWAYS call the init function (any function named Initialize, Init, Setup, Open, Connect, Startup, or similar) as the VERY FIRST "
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
            "Example: 'Returns 0 on success with balance in param_2; probe returned 25000.' "
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
            + (f"\nUSE CASES (provided by component owner — use these to guide hypothesis and value interpretation):\n{use_cases}\n" if use_cases else "")
            + vocab_block
            + findings_block
        ),
    }


# ---------------------------------------------------------------------------
# Behavioral specification generation (typed Python stub file)
# ---------------------------------------------------------------------------

_SPEC_SYSTEM = """\
You are a senior Python API designer. Given reverse-engineering findings for a Windows DLL,
produce a typed Python stub file that captures the BEHAVIORAL CONTRACT of the DLL.

This is NOT a reimplementation — it is a documented specification that developers use to
understand how to call the DLL via the MCP executor, what inputs it accepts, and what it returns.

Rules:
- One class per DLL named after the component (e.g. class NetworkConnectorDLL for a network_connector DLL)
- One method per exported function with full type annotations
- Docstrings MUST include:
    * What the function does (1 sentence)
    * Parameter descriptions with units and observed example values
    * Return value with unit annotation (e.g. "int: balance in cents, e.g. 25000 = $250.00")
    * Any prerequisite calls (e.g. "Requires initialize() first")
    * Error conditions observed (e.g. "Returns 0xFFFFFFFB if customer not found")
- Use Python type hints: str, int, float, bytes, Optional[str], etc.
- Output buffers (auto-allocated by executor) become return values, not parameters
- Mark write operations with a # WRITE comment on the method signature line
- Mark the required-first function with # REQUIRED FIRST
- Add a module-level docstring explaining the component's purpose and domain
- Add a usage example at the bottom as an if __name__ == '__main__' block

Output ONLY valid Python — no markdown fences, no explanation text.
"""


def _generate_behavioral_spec(
    client,
    model: str,
    findings: list[dict],
    invocables: list[dict],
    component_name: str,
    synthesis_md: str,
) -> str:
    """Generate a typed Python behavioral specification stub from enriched findings.

    Produces a .py file capturing the full API contract: typed stubs, docstrings
    with observed values and units, prerequisite annotations, and an example script.
    Uploaded to blob as behavioral_spec.py.
    """
    # Build compact schema for the LLM — combine findings + enriched invocables
    spec_input = []
    findings_by_fn: dict[str, list] = {}
    for f in findings:
        findings_by_fn.setdefault(f.get("function", ""), []).append(f)

    for inv in invocables:
        fn_name = inv["name"]
        fn_findings = findings_by_fn.get(fn_name, [])
        best = next((f for f in fn_findings if f.get("status") == "success"), fn_findings[0] if fn_findings else {})
        spec_input.append({
            "name": fn_name,
            "description": inv.get("description") or inv.get("doc") or "",
            "criticality": inv.get("criticality", "unknown"),
            "depends_on": inv.get("depends_on") or [],
            "parameters": [
                {
                    "name": p.get("name"),
                    "type": p.get("type"),
                    "json_type": p.get("json_type"),
                    "description": p.get("description", ""),
                    "direction": p.get("direction", "in"),
                }
                for p in (inv.get("parameters") or [])
                if isinstance(p, dict)
            ],
            "finding": best.get("finding", ""),
            "working_call": best.get("working_call"),
            "interpretation": best.get("interpretation") or {},
            "status": best.get("status", "unknown"),
        })

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _SPEC_SYSTEM},
                {
                    "role": "user",
                    "content": (
                        f"Component name: {component_name}\n\n"
                        f"Synthesis summary:\n{synthesis_md[:3000]}\n\n"
                        f"Function details:\n```json\n"
                        f"{json.dumps(spec_input, indent=2, ensure_ascii=False)[:6000]}\n```\n\n"
                        "Generate the Python behavioral specification file now."
                    ),
                },
            ],
            temperature=0.1,
        )
        content = resp.choices[0].message.content or ""
        # Strip any accidental markdown fences
        if content.startswith("```"):
            content = "\n".join(content.split("\n")[1:])
        if content.endswith("```"):
            content = content[: content.rfind("```")]
        return content.strip()
    except Exception as exc:
        logger.debug("_generate_behavioral_spec failed: %s", exc)
        return ""


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

        # Shared vocabulary table — grows as we learn things about this DLL.
        # Reload from blob if a previous session already built one (cross-session memory).
        vocab: dict = {}
        try:
            from api.storage import _download_blob as _dl_blob
            _vraw = _dl_blob(ARTIFACT_CONTAINER, f"{job_id}/vocab.json")
            vocab = json.loads(_vraw)
            logger.info("[%s] explore_worker: reloaded vocab from blob (%d keys)", job_id, len(vocab))
        except Exception:
            pass  # normal on first run

        # Seed vocab from user-supplied hints so the LLM starts informed
        # even before Phase 0 extracts strings from the binary.
        _use_cases_text = ""
        try:
            _job_meta = _get_job_status(job_id) or {}
            _user_hints = (_job_meta.get("hints") or "").strip()
            _use_cases_text = (_job_meta.get("use_cases") or "").strip()
            if _user_hints:
                import re as _re_h
                # Extract ID-like patterns (e.g. CUST-001, ORD-20040301-0042)
                _hint_ids = list(dict.fromkeys(_re_h.findall(r'[A-Z]{2,6}-[\w-]+', _user_hints)))
                if _hint_ids and "id_formats" not in vocab:
                    vocab["id_formats"] = _hint_ids
                # Store full hint text as notes for the LLM to reason from
                if "notes" not in vocab:
                    vocab["notes"] = f"User description: {_user_hints}"
                logger.info("[%s] explore_worker: seeded vocab from user hints: %s", job_id, _user_hints[:80])
        except Exception as _he:
            logger.debug("[%s] explore_worker: hints seed failed: %s", job_id, _he)

        # Phase 0: extract static hints from the DLL binary (best-effort)
        _static_hints_block = ""
        _dll_strings: dict = {}
        try:
            dll_path = next(
                (inv.get("execution", {}).get("dll_path", "") for inv in invocables
                 if inv.get("execution", {}).get("dll_path")),
                "",
            )
            import re as _re2
            from pathlib import Path as _Path
            _data: bytes | None = None
            # Primary: read from local path (works when running on Windows or
            # when the path is a valid Linux temp path from the upload worker).
            if dll_path:
                try:
                    _data = _Path(dll_path).read_bytes()
                except Exception:
                    pass
            # Fallback: download the original uploaded binary from Blob Storage.
            # This handles the common Azure deployment case where dll_path is a
            # Windows bridge VM path that doesn't exist on the Linux API container.
            if _data is None:
                try:
                    from api.storage import _download_blob
                    from api.config import UPLOAD_CONTAINER
                    # The upload worker stores the file as {job_id}/input<suffix>
                    # Try common DLL/EXE extensions, then any blob starting with input
                    for _ext in (".dll", ".exe", ".bin", ""):
                        try:
                            _data = _download_blob(UPLOAD_CONTAINER, f"{job_id}/input{_ext}")
                            break
                        except Exception:
                            pass
                except Exception:
                    pass
            if _data is not None:
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
                    "Write functions (any function whose name implies state changes — Process, Update, Set, Create, Delete, Transfer, Submit, Send, Redeem, Unlock) "
                    "should now succeed. Probe them with real ID values from STATIC ANALYSIS HINTS.\n"
                )
                logger.info("[%s] explore_worker: phase1 UNLOCKED: %s", job_id, unlock_result["notes"])
            else:
                logger.info("[%s] explore_worker: phase1 not unlocked: %s", job_id,
                            unlock_result.get("notes", ""))
        except Exception as _we:
            logger.debug("[%s] explore_worker: phase1 write-unlock probe failed: %s", job_id, _we)

        # Active Learning-style ordering: explore init functions first (unlock state),
        # then sort remaining by uncertainty score ascending (simpler → complex).
        # By the time the LLM reaches ambiguous multi-param functions, the vocab
        # table is rich with cross-function conventions learned from simpler ones.
        _INIT_RE = _re.compile(r"(init(ializ)?|startup|start|setup|open|login|logon|connect)", _re.I)
        _init_invs  = [inv for inv in invocables if _INIT_RE.search(inv["name"])]
        _other_invs = [inv for inv in invocables if not _INIT_RE.search(inv["name"])]
        _other_invs.sort(key=_uncertainty_score)
        invocables = _init_invs + _other_invs
        logger.info("[%s] explore_worker: ordered %d init + %d others by uncertainty",
                    job_id, len(_init_invs), len(_other_invs))

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
            sys_msg = _build_explore_system_message(invocables, prior, sentinels=sentinels, vocab=vocab, use_cases=_use_cases_text)
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
            _enrich_called = False
            _best_raw_result: str = ""  # best successful result captured for hypothesis generation
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

                    # Track whether enrich_invocable was called this function
                    if tc_name == "enrich_invocable":
                        _enrich_called = True

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
                            _best_raw_result = tool_result  # capture for hypothesis generation

                    logger.info(
                        "[%s] explore_worker: tool=%s result=%s",
                        job_id, tc_name, str(tool_result)[:120],
                    )

                    conversation.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": tool_result,
                    })

            # Force enrich_invocable if the model skipped it and the function has params
            if not _enrich_called and inv.get("parameters"):
                _cur_findings = _load_findings(job_id)
                _last_f = next(
                    (f for f in reversed(_cur_findings) if f.get("function") == fn_name), None
                )
                _finding_summary = (
                    f"Finding: {_last_f.get('finding', '')}. Notes: {_last_f.get('notes', '')}"
                    if _last_f else "No finding recorded."
                )
                try:
                    from typing import cast, Any as _Any
                    _enrich_resp = client.chat.completions.create(
                        model=model,
                        messages=conversation + [{
                            "role": "user",
                            "content": (
                                f"You did not call enrich_invocable for '{fn_name}'. "
                                f"Based on what you observed, call it now. "
                                f"Rename each param_N to a semantic name (e.g. customer_id, balance, output_buffer). "
                                f"For each parameter description, write what it does AND include an example value from your testing "
                                f"(e.g. 'Input ID string, e.g. the value you used in testing' or 'Output pointer — receives a result value, observed 25000'). "
                                f"Set the function description to a clear one-sentence summary. "
                                f"{_finding_summary}"
                            ),
                        }],
                        tools=cast(_Any, tool_schemas),
                        tool_choice={"type": "function", "function": {"name": "enrich_invocable"}},
                        temperature=0,
                    )
                    _em = _enrich_resp.choices[0].message
                    if _em.tool_calls:
                        _etc = _em.tool_calls[0]
                        try:
                            _eargs = json.loads(_etc.function.arguments)  # type: ignore[union-attr]
                        except json.JSONDecodeError:
                            _eargs = {}
                        _execute_tool(inv_map["enrich_invocable"], _eargs)
                        logger.info(
                            "[%s] explore_worker: forced enrich_invocable for %s",
                            job_id, fn_name,
                        )
                except Exception as _ee:
                    logger.debug("[%s] forced enrich failed for %s: %s", job_id, fn_name, _ee)

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

            # Hypothesis-driven interpretation: ask LLM what ambiguous output values mean,
            # then optionally cross-validate against an already-explored function.
            if _best_raw_result:
                try:
                    _hyp = _generate_hypothesis(
                        client, model, fn_name, _best_raw_result, vocab, _load_findings(job_id)
                    )
                    if _hyp.get("interpretations"):
                        _patch_finding(job_id, fn_name, {"interpretation": _hyp["interpretations"]})
                        # Merge into vocab so downstream functions benefit immediately
                        if "value_semantics" not in vocab:
                            vocab["value_semantics"] = {}
                        vocab["value_semantics"].update(_hyp["interpretations"])
                        logger.info("[%s] hypothesis for %s: %s", job_id, fn_name, _hyp["interpretations"])
                    # Run cross-validation if proposed and the target has already been explored
                    _cv = _hyp.get("cross_validation")
                    if (
                        _cv and isinstance(_cv, dict)
                        and _cv.get("function") in inv_map
                        and _cv.get("function") in already_explored
                    ):
                        _cv_inv = inv_map[_cv["function"]]
                        _cv_result = _execute_tool(_cv_inv, _cv.get("args") or {})
                        _patch_finding(
                            job_id, fn_name,
                            {"cross_validation": f"{_cv['function']}({_cv.get('args')}) → {_cv_result}"},
                        )
                        logger.info(
                            "[%s] cross-validated %s via %s: %s",
                            job_id, fn_name, _cv["function"], str(_cv_result)[:80],
                        )
                except Exception as _hyp_e:
                    logger.debug("[%s] hypothesis failed for %s: %s", job_id, fn_name, _hyp_e)

            # Update shared vocabulary from the latest finding for this function
            last_finding = _load_findings(job_id)
            if last_finding:
                last = next(
                    (f for f in reversed(last_finding) if f.get("function") == fn_name), None
                )
                if last:
                    try:
                        vocab = _update_vocabulary(client, model, vocab, last)
                        # Persist updated vocab to blob so next session starts informed
                        try:
                            _upload_to_blob(
                                ARTIFACT_CONTAINER,
                                f"{job_id}/vocab.json",
                                json.dumps(vocab).encode(),
                            )
                        except Exception as _vpe:
                            logger.debug("[%s] vocab persist failed: %s", job_id, _vpe)
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

                    # Layer 3: backfill schema descriptions from synthesis document.
                    # Uses the completed synthesis to enrich param descriptions with
                    # proven semantics (units, entity refs, example values).
                    try:
                        logger.info("[%s] explore_worker: layer3 schema backfill…", job_id)
                        _set_explore_status(job_id, explored, total, "Enriching schema from synthesis…")
                        _backfill_schema_from_synthesis(client, model, _report, invocables, job_id)
                    except Exception as _bf_e:
                        logger.debug("[%s] explore_worker: backfill failed: %s", job_id, _bf_e)

                    # Self-assessment: generate confidence gap questions for the user.
                    # Asks the LLM what it was uncertain about so the UI can surface
                    # targeted clarification prompts to domain experts.
                    try:
                        logger.info("[%s] explore_worker: generating confidence gaps…", job_id)
                        _set_explore_status(job_id, explored, total, "Generating clarification questions…")
                        _gaps = _generate_confidence_gaps(client, model, _syn_findings, invocables)
                        if _gaps:
                            logger.info("[%s] explore_worker: %d confidence gaps generated", job_id, len(_gaps))
                        # Always persist (even empty list) so UI knows the pass ran
                        _gap_current = _get_job_status(job_id) or {}
                        _persist_job_status(
                            job_id,
                            {**_gap_current, "explore_questions": _gaps},
                            sync=True,
                        )
                    except Exception as _gap_e:
                        logger.debug("[%s] explore_worker: confidence gaps failed: %s", job_id, _gap_e)

                    # Behavioral spec: typed Python stub file capturing the API contract.
                    try:
                        logger.info("[%s] explore_worker: generating behavioral spec…", job_id)
                        _set_explore_status(job_id, explored, total, "Generating behavioral specification…")
                        _component = (_get_job_status(job_id) or {}).get("component_name", "DLLComponent")
                        _spec_py = _generate_behavioral_spec(
                            client, model, _syn_findings, invocables, _component, _report
                        )
                        if _spec_py:
                            _upload_to_blob(
                                ARTIFACT_CONTAINER,
                                f"{job_id}/behavioral_spec.py",
                                _spec_py.encode("utf-8"),
                            )
                            logger.info("[%s] explore_worker: behavioral_spec.py saved to blob", job_id)
                    except Exception as _spec_e:
                        logger.debug("[%s] explore_worker: behavioral spec failed: %s", job_id, _spec_e)
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
