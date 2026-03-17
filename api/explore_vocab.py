"""api/explore_vocab.py – Vocabulary, hypothesis, and schema-backfill utilities.

Manages the cross-function shared vocabulary table (_update_vocabulary),
interprets ambiguous return values (_generate_hypothesis), re-annotates the
full schema after synthesis (_backfill_schema_from_synthesis), and scores
functions for curriculum-style exploration ordering (_uncertainty_score).
"""

from __future__ import annotations

import json
import logging
import re as _re

from api.storage import _patch_invocable

logger = logging.getLogger("mcp_factory.api")

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
                            desc = param_patches[pname]
                            desc_lower = desc.lower()
                            if _re.search(r"\boutput\b.*\bbuffer\b|\bbuffer\b.*\boutput\b|\bauto.allocated\b", desc_lower):
                                new_dir = "out"
                            elif _re.search(r"\binput\b|\bprovide[sd]?\b|\bpass\b|\bspecif", desc_lower):
                                new_dir = "in"
                            else:
                                new_dir = p.get("direction", "in")
                            p = {**p, "description": desc, "direction": new_dir}
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
    """Format the vocabulary table for injection into the chat system message.

    Priority order (highest → lowest, mirrors token-position importance):
      1. description   — one-sentence domain framing (before anything else)
      2. user_context  — verbatim use_cases from the integrating developer
      3. id_formats    — wrong format = every call fails; always include first
      4. error_codes   — needed to interpret every response correctly
      5. value_semantics — confirmed/inferred parameter and return meanings
      6. notes         — free-text cross-function observations
      7. everything else — write_blocked_by, init_sequence, output_format, etc.

    description and user_context are emitted as a preamble paragraph, not as
    table rows, so the model reads domain framing as natural text before the
    mechanical conventions list.
    """
    if not vocab:
        return ""

    lines = []

    # ── Preamble: domain framing (emitted before the KNOWLEDGE header) ──────
    _description = vocab.get("description", "").strip()
    _user_ctx    = vocab.get("user_context", "").strip()
    if _description or _user_ctx:
        lines.append("COMPONENT CONTEXT:")
        if _description:
            lines.append(f"  {_description}")
        if _user_ctx and _user_ctx != _description:
            lines.append(f"  Integration intent: {_user_ctx}")
        lines.append("")

    lines.append("ACCUMULATED DLL KNOWLEDGE (apply these conventions immediately):")

    # ── Tier 1: id_formats — try-all instruction ─────────────────────────────
    if "id_formats" in vocab and isinstance(vocab["id_formats"], list):
        lines.append(
            f"  id_formats (try ALL of these for each unknown string param, "
            f"not just the most common one): {', '.join(str(x) for x in vocab['id_formats'])}"
        )

    # ── Tier 2: error_codes ──────────────────────────────────────────────────
    if "error_codes" in vocab and isinstance(vocab["error_codes"], dict):
        for hex_code, meaning in vocab["error_codes"].items():
            lines.append(f"  error_codes[{hex_code}]: {meaning}")

    # ── Tier 3: value_semantics ───────────────────────────────────────────────
    if "value_semantics" in vocab and isinstance(vocab["value_semantics"], dict):
        for sk, sv in vocab["value_semantics"].items():
            lines.append(f"  value_semantics[{sk}]: {sv}")

    # ── Tier 4: notes ─────────────────────────────────────────────────────────
    if "notes" in vocab:
        lines.append(f"  notes: {vocab['notes']}")

    # ── Tier 5: everything else (skip preamble keys already emitted) ─────────
    _emitted = {"description", "user_context", "id_formats", "error_codes", "value_semantics", "notes"}
    for k, v in vocab.items():
        if k in _emitted:
            continue
        if isinstance(v, list):
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
