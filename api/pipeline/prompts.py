"""api.pipeline.prompts – LLM prompt builders and generation functions.

Builds the exploration system message (_build_explore_system_message),
generates the post-exploration synthesis document (_synthesize), the
behavioral Python spec (_generate_behavioral_spec), and the confidence
gap questions (_generate_confidence_gaps).
"""

from __future__ import annotations

import json
import logging

from api.pipeline.helpers import _SENTINEL_DEFAULTS
from api.pipeline.vocab import _vocab_block

logger = logging.getLogger("mcp_factory.api")


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

    # Build sentinel table from calibrated values (falls back to defaults)
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
            "   - Tool outputs include a [CLASSIFICATION] line with normalized signed/unsigned forms, "
            "format guess, confidence, and source. Use that metadata before inventing meanings.\n"
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
            "   MANDATORY ERROR RECORDING:\n"
            "   - If after ≤10 probe calls a function STILL returns only sentinel error codes,\n"
            "     STOP probing immediately, call record_finding(status='error') with the sentinel codes\n"
            "     observed, and move on. Do NOT keep trying variations — you have a maximum tool-call budget.\n"
            "   - If a tool result says 'Policy blocked write probe', set status='error' and include the\n"
            "     policy stop reason in notes; do not brute-force alternate permutations.\n"
            "   - Failure to call record_finding is a critical violation. Every function MUST end with\n"
            "     a record_finding call: status='success' if return=0 was observed, status='error' otherwise.\n"
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
      "question": "<plain-English question for a business user — NO function names, NO error codes, NO jargon. E.g. 'Does processing a payment require any kind of setup or login step first?'>",
      "technical_question": "<developer-targeted question including the function name, observed error code, and specific unknown. E.g. 'CS_ProcessPayment returned 0xFFFFFFFB on every probe — which function must be called first to unlock write-mode, and are there argument format constraints?'>",
      "technical_detail": "<one sentence for developers: what was observed — include the actual return value or error code. E.g. 'CS_ProcessPayment consistently returned 0xFFFFFFFB (write denied) on every probe.'>"
    }
  ]
}

Rules:
- Maximum 5 gaps total — only the most impactful unknowns
- Only ask about genuinely uncertain things (not obvious things like what Initialize does)
- Prefer questions about: numeric value units, enum/status meanings, unknown ID formats,
  error conditions not observed, functions that always failed
- `question` must be plain English — a product owner or QA analyst should immediately understand it
  with no programming knowledge. Never include raw numbers, hex codes, or C function names.
- `technical_question` is for developers: include the function name, exact return codes, and phrase it
  as an action-oriented question (e.g. "what must be called first", "what argument format is required")
- `technical_detail` is for developers: include the actual observed values (return codes, hex, function name)
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


def _synthesize(
    client,
    model: str,
    findings: list[dict],
    vocab: dict | None = None,
    sentinels: dict[int, str] | None = None,
) -> str:
    """Generate a full API reference Markdown document from completed findings.

    Port of run_local.py's _synthesize() — same prompt, same section structure.
    Result is uploaded to blob as api_reference.md after exploration completes.

    AC-3: Now also receives vocab and sentinels so the synthesis LLM can
    cross-reference error codes, ID formats, and value semantics — not just
    the raw findings.
    """
    findings_json = json.dumps(findings, indent=2, ensure_ascii=False)

    # AC-3: Build supplementary context blocks
    _extra_context = ""
    if vocab:
        _vb = _vocab_block(vocab)
        if _vb:
            _extra_context += f"\n\nCross-function vocabulary (shared patterns):\n{_vb}"
    if sentinels:
        _sent_lines = "\n".join(
            f"  0x{k:08X} = {v}" for k, v in sorted(sentinels.items(), reverse=True)
        )
        _extra_context += f"\n\nCalibrated sentinel error codes:\n{_sent_lines}"

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
                        f"```json\n{findings_json}\n```"
                        f"{_extra_context}\n\n"
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
