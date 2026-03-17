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
from typing import Any

from api.config import OPENAI_ENDPOINT, OPENAI_DEPLOYMENT, OPENAI_REASONING_DEPLOYMENT, OPENAI_API_KEY, OPENAI_EXPLORE_MODEL, ARTIFACT_CONTAINER
from api.executor import _execute_tool
from api.storage import _persist_job_status, _get_job_status, _patch_invocable, _save_finding, _patch_finding, _upload_to_blob
from api.telemetry import _openai_client
from api.explore_phases import (
    _SENTINEL_DEFAULTS, _MAX_EXPLORE_ROUNDS_PER_FUNCTION, _MAX_FUNCTIONS_PER_SESSION,
    _calibrate_sentinels, _probe_write_unlock, _infer_param_desc,
)
from api.explore_vocab import (
    _update_vocabulary, _generate_hypothesis, _backfill_schema_from_synthesis,
    _vocab_block, _uncertainty_score,
)
from api.explore_prompts import (
    _build_explore_system_message, _generate_behavioral_spec, _synthesize, _generate_confidence_gaps,
)

logger = logging.getLogger("mcp_factory.api")

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
