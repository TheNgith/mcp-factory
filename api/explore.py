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
import os as _os
import re as _re
import threading as _threading
import time
from concurrent.futures import ThreadPoolExecutor as _TPE
from typing import Any

from api.config import OPENAI_ENDPOINT, OPENAI_DEPLOYMENT, OPENAI_REASONING_DEPLOYMENT, OPENAI_API_KEY, OPENAI_EXPLORE_MODEL, ARTIFACT_CONTAINER
from api.executor import _execute_tool
from api.storage import _persist_job_status, _get_job_status, _patch_invocable, _save_finding, _patch_finding, _upload_to_blob, _download_blob
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


def _build_tool_schemas(invocables: list[dict]) -> list[dict]:
    """Build OpenAI tool call schemas from a list of invocable dicts."""
    from api.chat import _RECORD_FINDING_TOOL, _ENRICH_INVOCABLE_TOOL  # type: ignore
    tool_schemas: list[dict] = []
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
    return tool_schemas


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
    tool_schemas = _build_tool_schemas(invocables)

    client = _openai_client()
    # Use the dedicated explore model (gpt-4o-mini by default) for cost efficiency.
    # When using direct OpenAI key, OPENAI_EXPLORE_MODEL controls this.
    # When using Azure, fall back to the reasoning deployment.
    model = OPENAI_EXPLORE_MODEL if OPENAI_API_KEY else (OPENAI_REASONING_DEPLOYMENT or OPENAI_DEPLOYMENT)

    explored = 0
    try:
        prior_findings = _load_findings(job_id)
        already_explored = {f.get("function") for f in prior_findings if f.get("function")}

        # Snapshot the schema before any exploration mutates it.
        # Stored as mcp_schema_t0.json so the session-snapshot endpoint can
        # include it as "pre-enrichment" alongside the final post-explore schema.
        try:
            _raw_schema = _download_blob(ARTIFACT_CONTAINER, f"{job_id}/mcp_schema.json")
            _upload_to_blob(ARTIFACT_CONTAINER, f"{job_id}/mcp_schema_t0.json", _raw_schema)
            logger.info("[%s] explore_worker: pre-enrichment schema snapshot saved", job_id)
        except Exception as _snap_e:
            logger.debug("[%s] explore_worker: schema snapshot failed: %s", job_id, _snap_e)

        # Phase 0.5: auto-calibrate sentinel error codes for this DLL
        sentinels = _SENTINEL_DEFAULTS
        try:
            logger.info("[%s] explore_worker: phase0.5 calibrating sentinels…", job_id)
            _set_explore_status(job_id, 0, total, "Calibrating error codes…")
            sentinels = _calibrate_sentinels(invocables, client, model)
            logger.info("[%s] explore_worker: phase0.5 sentinels: %s", job_id,
                        {f"0x{k:08X}": v for k, v in sentinels.items()})
            try:
                _upload_to_blob(
                    ARTIFACT_CONTAINER,
                    f"{job_id}/sentinel_calibration.json",
                    json.dumps({f"0x{k:08X}": v for k, v in sentinels.items()}, indent=2).encode(),
                )
            except Exception as _sce:
                logger.debug("[%s] explore_worker: sentinel artifact upload failed: %s", job_id, _sce)
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

        # Persist calibrated error codes from sentinels into vocab so
        # vocab_coverage.json can score against real DLL-specific codes.
        if sentinels is not _SENTINEL_DEFAULTS:
            vocab.setdefault("error_codes", {f"0x{k:08X}": v for k, v in sentinels.items()})

        # Seed vocab from user-supplied hints so the LLM starts informed
        # even before Phase 0 extracts strings from the binary.
        _use_cases_text = ""
        try:
            _job_meta = _get_job_status(job_id) or {}
            _user_hints = (_job_meta.get("hints") or "").strip()
            _use_cases_text = (_job_meta.get("use_cases") or "").strip()
            if _user_hints:
                # Extract ID-like patterns (e.g. CUST-001, ORD-20040301-0042)
                _hint_ids = list(dict.fromkeys(_re.findall(r'[A-Z]{2,6}-[\w-]+', _user_hints)))
                if _hint_ids and "id_formats" not in vocab:
                    vocab["id_formats"] = _hint_ids
                # Strip DOMAIN ANSWER entries injected by the answer_gaps endpoint
                # before writing notes so synthetic answers don't pollute the vocab.
                _clean_hints = " | ".join(
                    p.strip() for p in _user_hints.split(" | ")
                    if p.strip() and not p.strip().startswith("DOMAIN ANSWER")
                )
                if _clean_hints and "notes" not in vocab:
                    vocab["notes"] = f"User description: {_clean_hints}"
                logger.info("[%s] explore_worker: seeded vocab from user hints: %s", job_id, _user_hints[:80])
            # Persist use_cases into vocab so the chat phase sees it even after
            # vocab["notes"] may be overwritten by later vocabulary updates.
            if _use_cases_text and "user_context" not in vocab:
                vocab["user_context"] = _use_cases_text
                logger.info("[%s] explore_worker: seeded vocab[user_context] from use_cases", job_id)
        except Exception as _he:
            logger.debug("[%s] explore_worker: hints seed failed: %s", job_id, _he)

        # Phase 0: static enrichment — G-4 IAT, G-7 binary strings, G-8 PE version info, G-9 Capstone sentinels
        _static_hints_block = ""
        _dll_strings: dict = {}
        _static_analysis_result: dict = {}
        try:
            dll_path = next(
                (inv.get("execution", {}).get("dll_path", "") for inv in invocables
                 if inv.get("execution", {}).get("dll_path")),
                "",
            )
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
                from api.static_analysis import run_static_analysis, build_vocab_seeds, build_static_hints_block as _build_shb
                _dll_name = _Path(dll_path).name if dll_path else "unknown.dll"
                _static_analysis_result = run_static_analysis(_data, _dll_name)

                # G-7: promote binary evidence into vocab as first-class facts
                # (binary strings are ground truth — they override nothing the user
                # set, but fill any vocab key the user left empty)
                _vocab_seeds = build_vocab_seeds(_static_analysis_result, vocab)
                for _k, _v in _vocab_seeds.items():
                    vocab.setdefault(_k, _v)
                    if _k not in vocab or vocab[_k] == _v:
                        vocab[_k] = _v  # ensure seed takes effect even if key exists but is falsy
                if _vocab_seeds:
                    logger.info("[%s] explore_worker: phase0 vocab seeds applied: %s",
                                job_id, list(_vocab_seeds.keys()))

                # Build legacy-compat dll_strings dict for _probe_write_unlock
                _bs = _static_analysis_result.get("binary_strings", {})
                _dll_strings = {
                    "ids":    _bs.get("ids_found", []),
                    "emails": _bs.get("emails_found", []),
                    "all":    _bs.get("ids_found", []) + _bs.get("emails_found", []),
                }

                # Build text block appended to LLM prompt (secondary role after vocab)
                _static_hints_block = _build_shb(_static_analysis_result)
                _static_analysis_result["injected_into_prompt"] = bool(_static_hints_block)
                _static_analysis_result["static_hints_block_length"] = len(_static_hints_block)

                # Persist static_analysis.json to blob so save-session can verify it
                try:
                    _upload_to_blob(
                        ARTIFACT_CONTAINER,
                        f"{job_id}/static_analysis.json",
                        json.dumps(_static_analysis_result, indent=2).encode(),
                    )
                    logger.info("[%s] explore_worker: phase0 static_analysis.json uploaded", job_id)
                except Exception as _sa_err:
                    logger.debug("[%s] explore_worker: static_analysis.json upload failed: %s", job_id, _sa_err)

                logger.info("[%s] explore_worker: phase0 G-4/G-7/G-8/G-9 complete — "
                            "%d IDs, %d sentinels, IAT:%s",
                            job_id,
                            len(_bs.get("ids_found", [])),
                            len(_static_analysis_result.get("sentinel_constants", {}).get("harvested", {})),
                            list(_static_analysis_result.get("iat_capabilities", {}).get("categories", {}).keys()))
        except Exception as _e:
            logger.debug("[%s] explore_worker: phase0 static enrichment failed: %s", job_id, _e)

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

        # ── Per-function exploration (parallel when EXPLORE_CONCURRENCY > 1) ────────
        _CONCURRENCY = int(_os.getenv("EXPLORE_CONCURRENCY", "1"))
        _lock = _threading.Lock()
        _state = {"explored": explored}

        def _explore_one(inv: dict) -> None:
            fn_name = inv["name"]

            # Skip functions already documented in a previous session (thread-safe)
            _skip = False
            with _lock:
                if fn_name in already_explored:
                    _state["explored"] += 1
                    _skip = True
                else:
                    _vocab_snap = dict(vocab)
            if _skip:
                _set_explore_status(job_id, _state["explored"], total, f"Skipped {fn_name} (already documented)")
                return

            _set_explore_status(job_id, _state["explored"], total, f"Exploring {fn_name}…")
            logger.info("[%s] explore_worker: starting %s (%d/%d)", job_id, fn_name, _state["explored"] + 1, total)

            # Build a focused conversation just for this function
            prior = _load_findings(job_id)
            sys_msg = _build_explore_system_message(invocables, prior, sentinels=sentinels, vocab=_vocab_snap, use_cases=_use_cases_text)
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

            with _lock:
                _state["explored"] += 1
                already_explored.add(fn_name)
            _set_explore_status(job_id, _state["explored"], total, f"Completed {fn_name}")
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
                        client, model, fn_name, _best_raw_result, _vocab_snap, _load_findings(job_id)
                    )
                    if _hyp.get("interpretations"):
                        _patch_finding(job_id, fn_name, {"interpretation": _hyp["interpretations"]})
                        # Merge into local vocab snapshot so the update is persisted
                        if "value_semantics" not in _vocab_snap:
                            _vocab_snap["value_semantics"] = {}
                        _vocab_snap["value_semantics"].update(_hyp["interpretations"])
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
                        # Mutates _vocab_snap in-place; merge learned facts into shared vocab
                        _update_vocabulary(client, model, _vocab_snap, last)
                        with _lock:
                            for _mk, _mv in _vocab_snap.items():
                                if _mk.startswith("_"):
                                    continue
                                if isinstance(_mv, list) and isinstance(vocab.get(_mk), list):
                                    _existing = set(map(str, vocab[_mk]))
                                    vocab[_mk] = vocab[_mk] + [x for x in _mv if str(x) not in _existing]
                                elif isinstance(_mv, dict) and isinstance(vocab.get(_mk), dict):
                                    vocab[_mk].update(_mv)
                                elif _mv and _mk not in vocab:
                                    vocab[_mk] = _mv
                        # Persist updated vocab to blob so next session starts informed
                        try:
                            _upload_to_blob(
                                ARTIFACT_CONTAINER,
                                f"{job_id}/vocab.json",
                                json.dumps(_vocab_snap).encode(),
                            )
                        except Exception as _vpe:
                            logger.debug("[%s] vocab persist failed: %s", job_id, _vpe)
                    except Exception as _ve:
                        logger.debug("[%s] vocab update failed for %s: %s", job_id, fn_name, _ve)

        # Run exploration: parallel if EXPLORE_CONCURRENCY > 1, else sequential
        if _CONCURRENCY > 1:
            logger.info("[%s] explore_worker: running %d functions with concurrency=%d",
                        job_id, len(invocables), _CONCURRENCY)
            with _TPE(max_workers=_CONCURRENCY) as _pool:
                list(_pool.map(_explore_one, invocables))
        else:
            for inv in invocables:
                _explore_one(inv)

        explored = _state["explored"]

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

                    # Gap resolution pass: attempt one more focused round on functions
                    # that failed every probe in the main loop, using known-good IDs
                    # and explicit retry strategies.  Resolved functions won't appear
                    # in the gap questions that reach the user.
                    try:
                        logger.info("[%s] explore_worker: gap resolution pass…", job_id)
                        _set_explore_status(job_id, explored, total, "Retrying failed functions…")
                        _attempt_gap_resolution(
                            job_id, invocables, client, model,
                            sentinels, vocab, _use_cases_text,
                            inv_map, tool_schemas,
                        )
                    except Exception as _gr_e:
                        logger.debug("[%s] explore_worker: gap resolution failed: %s", job_id, _gr_e)

                    # Self-assessment: generate confidence gap questions for the user.
                    # Runs AFTER gap resolution, so only genuinely unresolvable unknowns
                    # (undocumented error codes, business rules) surface to the user.
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

        # Synthesize a one-sentence domain description from accumulated vocab.
        # Stored as vocab["description"] so the chat phase can prepend it to the
        # system message as domain framing before the mechanical rules.
        # Only generated once — skipped if a previous session already built it.
        if vocab and "description" not in vocab:
            try:
                _desc_seed = vocab.get("user_context") or vocab.get("notes") or ""
                _vs = vocab.get("value_semantics") or {}
                _vs_sample = "; ".join(f"{k}: {v}" for k, v in list(_vs.items())[:8])
                _ids = ", ".join(vocab.get("id_formats") or [])
                _desc_prompt = (
                    "Based on the following accumulated knowledge about a DLL, write ONE sentence "
                    "describing what this component does for a developer integrating it. "
                    "Be specific — name the business domain, key entities, and operations. "
                    "Do not mention 'DLL' or 'function'. Max 25 words.\n\n"
                    + (f"User context: {_desc_seed}\n" if _desc_seed else "")
                    + (f"Known ID formats: {_ids}\n" if _ids else "")
                    + (f"Value semantics: {_vs_sample}\n" if _vs_sample else "")
                )
                _desc_resp = client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": _desc_prompt}],
                    temperature=0,
                    max_tokens=60,
                )
                _desc_text = (_desc_resp.choices[0].message.content or "").strip().strip('"')
                if _desc_text:
                    vocab["description"] = _desc_text
                    logger.info("[%s] explore_worker: synthesized description: %s", job_id, _desc_text)
            except Exception as _desc_e:
                logger.debug("[%s] explore_worker: description synthesis failed: %s", job_id, _desc_e)

        # Persist final vocab with description + user_context
        if vocab:
            try:
                _upload_to_blob(
                    ARTIFACT_CONTAINER,
                    f"{job_id}/vocab.json",
                    json.dumps(vocab).encode(),
                )
            except Exception as _vfin_e:
                logger.debug("[%s] explore_worker: final vocab persist failed: %s", job_id, _vfin_e)

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


def _attempt_gap_resolution(
    job_id: str,
    invocables: list[dict],
    client,
    model: str,
    sentinels: dict,
    vocab: dict,
    use_cases_text: str,
    inv_map: dict,
    tool_schemas: list[dict],
) -> None:
    """Targeted second-pass re-probe of functions that failed every probe in the main loop.

    Runs before gap questions are generated so questions that the system CAN answer
    automatically never reach the user.  A bounded conversation with explicit retry
    strategies (re-init, known-good IDs, parameter permutation) is run for each
    failed function.  Findings are updated in-place via _patch_finding so that the
    subsequent _generate_confidence_gaps() call sees the resolved results.
    """
    from api.storage import _load_findings

    all_findings = _load_findings(job_id)

    failed_invs = [
        inv for inv in invocables
        if not any(
            f.get("function") == inv["name"] and f.get("status") == "success"
            for f in all_findings
        )
    ]
    if not failed_invs:
        logger.info("[%s] gap_resolution: no failed functions — skipping", job_id)
        return

    fn_list = [inv["name"] for inv in failed_invs]
    logger.info("[%s] gap_resolution: targeted retry of %d function(s): %s", job_id, len(failed_invs), fn_list)

    # Build a concise "known-good" block from successful findings so the LLM can
    # reuse proven IDs and argument shapes from sibling functions.
    successful_findings = [f for f in all_findings if f.get("status") == "success" and f.get("working_call")]
    kb_lines = [f"  - {sf['function']}({sf['working_call']})" for sf in successful_findings[:6]]
    kb_block = (
        "\nKNOWN-GOOD CALLS (reuse these IDs/values as inputs):\n" + "\n".join(kb_lines) + "\n"
    ) if kb_lines else ""

    for i, inv in enumerate(failed_invs):
        fn_name = inv["name"]
        _set_explore_status(job_id, i, len(failed_invs), f"Gap resolution: retrying {fn_name}…")

        prev_finding = next((f for f in reversed(all_findings) if f.get("function") == fn_name), None)
        prev_ctx = (
            f"Previous attempt: {prev_finding.get('finding', 'no finding')}.\n"
            if prev_finding else ""
        )

        sys_msg = _build_explore_system_message(
            invocables, _load_findings(job_id),
            sentinels=sentinels, vocab=vocab, use_cases=use_cases_text,
        )
        conversation = [
            sys_msg,
            {
                "role": "user",
                "content": (
                    f"SECOND-PASS RETRY for '{fn_name}'.\n"
                    f"{prev_ctx}"
                    f"{kb_block}\n"
                    "This function failed every probe in the first pass. "
                    "Try these strategies in order:\n"
                    "1. Call the init function first (even if called before), then call this function.\n"
                    "2. Use the customer/order IDs from the KNOWN-GOOD CALLS above.\n"
                    "3. Permute numeric parameters: try 0, 1, 100, 1000, 10000.\n"
                    "4. For string params: try empty string, then each known-good ID format.\n"
                    "Goal: find ANY call that returns 0. Once found, call record_finding with "
                    "status='success' and working_call set to the exact args that worked.\n"
                    "If still failing after all strategies, call record_finding with "
                    "status='error' and note the exact error code(s) observed."
                ),
            },
        ]

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
                logger.warning("[%s] gap_resolution: OpenAI call failed for %s round %d: %s",
                               job_id, fn_name, _round, exc)
                break

            msg = response.choices[0].message
            if not msg.tool_calls:
                break

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

            for tc in msg.tool_calls:
                _fn = tc.function  # type: ignore[union-attr]
                tc_name = _fn.name
                try:
                    tc_args = json.loads(_fn.arguments)
                except json.JSONDecodeError:
                    tc_args = {}

                tc_inv = inv_map.get(tc_name)
                tool_result = _execute_tool(tc_inv, tc_args) if tc_inv else f"Tool '{tc_name}' not found."

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

                logger.info("[%s] gap_resolution: tool=%s result=%s", job_id, tc_name, str(tool_result)[:120])
                conversation.append({"role": "tool", "tool_call_id": tc.id, "content": tool_result})

        # Consistency enforcement — same logic as main loop
        if _observed_successes:
            _patch_finding(job_id, fn_name, {"working_call": _observed_successes[0], "status": "success"})
            logger.info("[%s] gap_resolution: resolved %s → success %s", job_id, fn_name, _observed_successes[0])
        else:
            # Verify any working_call the LLM may have claimed
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
                                logger.info("[%s] gap_resolution: resolved %s via verification", job_id, fn_name)
                            else:
                                _patch_finding(job_id, fn_name, {"working_call": None, "status": "error"})
                    except Exception as _ve:
                        logger.debug("[%s] gap_resolution: verify failed for %s: %s", job_id, fn_name, _ve)


def _run_gap_answer_mini_sessions(job_id: str, invocables: list[dict]) -> None:
    """Targeted mini-sessions driven by user gap answers.

    For each function that has an answer in vocab.gap_answers, fires a focused
    LLM session with the domain expert answer injected as opening context.
    After all mini-sessions complete, re-runs confidence gap generation so the
    UI reflects the updated understanding (Task 3 — re-discovery loop).
    """
    from api.storage import _load_findings

    if not OPENAI_ENDPOINT and not OPENAI_API_KEY:
        return

    try:
        # Load vocab to get gap_answers
        vocab: dict = {}
        try:
            raw = _download_blob(ARTIFACT_CONTAINER, f"{job_id}/vocab.json")
            vocab = json.loads(raw)
        except Exception:
            pass

        gap_answers: dict = vocab.get("gap_answers") or {}
        # Only retry specific functions — skip 'general' answers (they feed hints only)
        targeted = {fn: ans for fn, ans in gap_answers.items() if fn != "general" and (ans or "").strip()}
        if not targeted:
            logger.info("[%s] gap_mini_sessions: no function-specific answers — skipping", job_id)
            return

        client = _openai_client()
        model = OPENAI_EXPLORE_MODEL if OPENAI_API_KEY else (OPENAI_REASONING_DEPLOYMENT or OPENAI_DEPLOYMENT)

        _job_meta = _get_job_status(job_id) or {}
        _use_cases_text = _job_meta.get("use_cases", "")
        sentinels = _SENTINEL_DEFAULTS

        inv_map: dict[str, dict] = {inv["name"]: inv for inv in invocables}
        inv_map["enrich_invocable"] = {
            "name": "enrich_invocable", "source_type": "enrich", "_job_id": job_id,
            "execution": {"method": "enrich"}, "parameters": [],
        }
        inv_map["record_finding"] = {
            "name": "record_finding", "source_type": "findings", "_job_id": job_id,
            "execution": {"method": "findings"}, "parameters": [],
        }
        tool_schemas = _build_tool_schemas(invocables)

        explore_questions = _job_meta.get("explore_questions") or []

        logger.info("[%s] gap_mini_sessions: %d answered function(s) to retry: %s",
                    job_id, len(targeted), list(targeted))

        for i, (fn_name, answer_text) in enumerate(targeted.items()):
            inv = inv_map.get(fn_name)
            if not inv:
                logger.debug("[%s] gap_mini_sessions: no invocable for %s — skipping", job_id, fn_name)
                continue

            _set_explore_status(job_id, i, len(targeted), f"Gap mini-session: {fn_name}\u2026")
            logger.info("[%s] gap_mini_sessions: starting mini-session for %s", job_id, fn_name)

            all_findings = _load_findings(job_id)
            prev_finding = next((f for f in reversed(all_findings) if f.get("function") == fn_name), None)
            prev_ctx = (
                f"Previous attempt: {prev_finding.get('finding', 'no finding')}.\n"
                if prev_finding else ""
            )

            # Inject the technical_question from the gap if available — gives the mini-session
            # a specific action-oriented goal derived from the original gap generation.
            fn_gap = next((g for g in explore_questions if g.get("function") == fn_name), {})
            technical_q = fn_gap.get("technical_question", "")
            technical_ctx = f"Technical context: {technical_q}\n" if technical_q else ""

            sys_msg = _build_explore_system_message(
                invocables, all_findings,
                sentinels=sentinels, vocab=vocab, use_cases=_use_cases_text,
            )
            conversation = [
                sys_msg,
                {
                    "role": "user",
                    "content": (
                        f"DOMAIN EXPERT ANSWER for '{fn_name}'.\n"
                        f"{technical_ctx}"
                        f"{prev_ctx}"
                        f"A domain expert answered: {answer_text!r}\n\n"
                        f"Use this information to re-probe '{fn_name}' now. "
                        "Apply the expert's answer to determine the correct prerequisite calls, "
                        "argument formats, or conditions needed for a successful call. "
                        "Goal: find a call that returns 0 (success). "
                        "When done, call enrich_invocable and record_finding with what you found. "
                        "If every probe still fails after applying the answer, call "
                        "record_finding(status='error') with exact codes seen."
                    ),
                },
            ]

            _p_lookup = {p.get("name", ""): p for p in (inv.get("parameters") or [])}
            _observed_successes: list[dict] = []
            _enrich_called = False

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
                    logger.warning("[%s] gap_mini_sessions: OpenAI call failed for %s round %d: %s",
                                   job_id, fn_name, _round, exc)
                    break

                msg = response.choices[0].message
                if not msg.tool_calls:
                    break

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

                for tc in msg.tool_calls:
                    _fn = tc.function  # type: ignore[union-attr]
                    tc_name = _fn.name
                    try:
                        tc_args = json.loads(_fn.arguments)
                    except json.JSONDecodeError:
                        tc_args = {}

                    tc_inv = inv_map.get(tc_name)
                    tool_result = _execute_tool(tc_inv, tc_args) if tc_inv else f"Tool '{tc_name}' not found."

                    if tc_name == "enrich_invocable":
                        _enrich_called = True

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

                    logger.info("[%s] gap_mini_sessions: tool=%s result=%s",
                                job_id, tc_name, str(tool_result)[:120])
                    conversation.append({"role": "tool", "tool_call_id": tc.id, "content": tool_result})

            # Ground-truth consistency enforcement — same logic as main explore loop
            if _observed_successes:
                _patch_finding(job_id, fn_name, {"working_call": _observed_successes[0], "status": "success"})
                logger.info("[%s] gap_mini_sessions: resolved %s \u2192 success %s",
                            job_id, fn_name, _observed_successes[0])
            else:
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
                                    _patch_finding(job_id, fn_name, {"working_call": None, "status": "error"})
                        except Exception as _ve:
                            logger.debug("[%s] gap_mini_sessions: verify failed for %s: %s",
                                         job_id, fn_name, _ve)

        # Task 3 — Re-discovery loop: re-run confidence gap generation after all mini-sessions
        # so the UI reflects the updated understanding. Gaps for resolved functions are dropped.
        try:
            logger.info("[%s] gap_mini_sessions: re-generating confidence gaps after mini-sessions\u2026", job_id)
            _set_explore_status(job_id, len(targeted), len(targeted), "Re-assessing gaps\u2026")
            updated_findings = _load_findings(job_id)
            new_gaps = _generate_confidence_gaps(client, model, updated_findings, invocables)
            resolved = {f.get("function") for f in updated_findings if f.get("status") == "success"}
            new_gaps = [g for g in new_gaps if g.get("function") not in resolved]
            _gap_current = _get_job_status(job_id) or {}
            _persist_job_status(job_id, {**_gap_current, "explore_questions": new_gaps}, sync=True)
            logger.info("[%s] gap_mini_sessions: %d gap(s) remain after re-assessment", job_id, len(new_gaps))
        except Exception as _re_e:
            logger.debug("[%s] gap_mini_sessions: re-gap generation failed: %s", job_id, _re_e)

        # Write gap resolution log artifact so session snapshots can report
        # what changed (resolved vs. still-open) across the mini-session run.
        try:
            _targeted_fns = {inv.get("name") for inv in targeted}
            _grl_findings = _load_findings(job_id)
            _grl = [
                {
                    "function":    f.get("function"),
                    "status":      f.get("status"),
                    "working_call": f.get("working_call"),
                    "confidence":  f.get("confidence"),
                    "successes":   f.get("successes", 0),
                    "attempts":    f.get("attempts", 0),
                }
                for f in _grl_findings
                if f.get("function") in _targeted_fns
            ]
            _upload_to_blob(
                ARTIFACT_CONTAINER,
                f"{job_id}/gap_resolution_log.json",
                json.dumps(_grl, indent=2).encode(),
            )
            logger.info("[%s] gap_mini_sessions: gap_resolution_log written (%d entries)", job_id, len(_grl))
        except Exception as _grl_e:
            logger.debug("[%s] gap_mini_sessions: gap_resolution_log upload failed: %s", job_id, _grl_e)

        _cur_status = _get_job_status(job_id) or {}
        _persist_job_status(
            job_id,
            {**_cur_status, "explore_phase": "done", "updated_at": time.time()},
            sync=True,
        )
        logger.info("[%s] gap_mini_sessions: complete", job_id)

    except Exception as exc:
        logger.error("[%s] gap_mini_sessions: fatal error: %s", job_id, exc)


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
