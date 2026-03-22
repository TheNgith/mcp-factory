"""
morning_report.py — Overnight run findings report.

Reads all isolation matrix and A/B run folders from the last N hours and
produces a consolidated markdown + JSON summary you can hand directly to
Copilot or Codex for analysis.

Usage:
    python scripts/morning_report.py
    python scripts/morning_report.py --hours 12 --out sessions/morning-report.md
    python scripts/morning_report.py --hours 24 --verbose
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_RUNS_ROOT = _REPO_ROOT / "sessions" / "_runs"

# Transition legend — extend as T-17..T-23 evaluators come online
_TRANSITION_LABELS = {
    "T-04": "probe-user-message-sample written",
    "T-05": "arg_sources in probe log",
    "T-06": "findings present after probe loop",
    "T-11": "synthesis produced api_reference.md",
    "T-14": "chat system context artifact present",
    "T-15": "chat tool reasoning artifact present",
    "T-17": "sentinel calibration decisions written",
    "T-18": "probe strategy summary written",
    "T-19": "param rename decisions written",
    "T-20": "synthesis coverage ≥ 100%",
    "T-21": "mini-session cited expert answer",
    "T-22": "chat cited vocab before tool call",
    "T-23": "backfill proposed semantic renames",
}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(s: str) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def _read_json(path: Path) -> Any | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _folder_mtime(p: Path) -> datetime:
    ts = p.stat().st_mtime
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def _collect_matrix_folders(hours: float) -> list[Path]:
    cutoff = _now_utc() - timedelta(hours=hours)
    results = []
    if not _RUNS_ROOT.exists():
        return results
    for p in sorted(_RUNS_ROOT.iterdir()):
        if not p.is_dir():
            continue
        if "isolation-matrix" not in p.name:
            continue
        if _folder_mtime(p) >= cutoff:
            results.append(p)
    return results


def _collect_ab_folders(hours: float) -> list[Path]:
    """Collect individual A/B run folders (not inside a matrix folder)."""
    cutoff = _now_utc() - timedelta(hours=hours)
    results = []
    if not _RUNS_ROOT.exists():
        return results
    for p in sorted(_RUNS_ROOT.iterdir()):
        if not p.is_dir():
            continue
        if "isolation-matrix" in p.name:
            continue
        readiness = p / "transition-readiness.json"
        if not readiness.exists():
            continue
        if _folder_mtime(p) >= cutoff:
            results.append(p)
    return results


def _summarize_transitions(readiness: dict) -> dict[str, str]:
    """Flatten leg_a and leg_b into worst-case per-transition status."""
    out: dict[str, str] = {}
    for leg_key in ("leg_a", "leg_b"):
        leg = (readiness or {}).get(leg_key) or {}
        for tid, row in (leg.get("transitions") or {}).items():
            status = str(row.get("status") or "unknown")
            current = out.get(tid, "pass")
            # priority: fail > warn > partial > missing > unknown > pass
            rank = {"fail": 0, "warn": 1, "partial": 2, "missing": 3, "unknown": 4, "pass": 5}.get
            if rank(status, 4) < rank(current, 4):
                out[tid] = status
    return out


def _summarize_cohesion(ab_compare: dict) -> dict:
    """Pick worst-case cohesion from A and B legs."""
    best: dict = {}
    for leg_key in ("A", "B"):
        leg = (ab_compare or {}).get(leg_key) or {}
        coh = leg.get("cohesion") or {}
        if not best:
            best = dict(coh)
        else:
            # Prefer whichever leg has fewer pass (more conservative)
            if int(coh.get("transition_pass") or 0) < int(best.get("transition_pass") or 0):
                best = dict(coh)
    return best


def _summarize_functions(ab_compare: dict) -> dict:
    out: dict = {}
    for leg_key in ("A", "B"):
        leg = (ab_compare or {}).get(leg_key) or {}
        fo = leg.get("function_outcome") or {}
        if not out:
            out = dict(fo)
        else:
            # Use worst-case (fewest successes)
            if int(fo.get("success") or 0) < int(out.get("success") or 0):
                out = dict(fo)
    return out


def _analyze_matrix_folder(folder: Path) -> dict:
    matrix = _read_json(folder / "transition-isolation-matrix.json")
    if not matrix:
        return {"folder": str(folder), "error": "missing transition-isolation-matrix.json"}

    cases_summary = []
    for case in (matrix.get("cases") or []):
        run_root = case.get("run_root")
        row: dict = {
            "name": case.get("name"),
            "family": case.get("family"),
            "ok": case.get("ok"),
            "readiness_pass": case.get("readiness_pass"),
            "readiness_reasons": case.get("readiness_reasons") or [],
        }
        if run_root:
            rp = Path(run_root)
            readiness = _read_json(rp / "transition-readiness.json")
            ab = _read_json(rp / "ab-compare.json")
            if readiness:
                row["deterministic"] = readiness.get("deterministic")
                row["transitions"] = _summarize_transitions(readiness)
            if ab:
                row["cohesion"] = _summarize_cohesion(ab)
                row["function_outcome"] = _summarize_functions(ab)
                row["model"] = (ab.get("run_manifest") or {}).get("model")
                row["hints_preview"] = (ab.get("run_manifest") or {}).get("hints_preview", "")[:80]
                row["hint_variant"] = (ab.get("run_manifest") or {}).get("hint_variant", "default")
        cases_summary.append(row)

    return {
        "folder": str(folder),
        "created_at": matrix.get("created_at"),
        "cases_requested": matrix.get("cases_requested"),
        "cases_completed": matrix.get("cases_completed"),
        "cases": cases_summary,
    }


def _status_icon(status: str | None) -> str:
    return {"pass": "✓", "warn": "~", "partial": "~", "fail": "✗", "missing": "?", None: "?"}.get(
        status or "?", "?"
    )


def _bool_icon(v: bool | None) -> str:
    if v is True:
        return "✓"
    if v is False:
        return "✗"
    return "?"


def _render_markdown(
    matrix_summaries: list[dict],
    standalone_summaries: list[dict],
    hours: float,
    generated_at: str,
) -> str:
    lines = [
        "# Overnight Run Report",
        "",
        f"Generated: {generated_at}",
        f"Lookback: last {hours:.0f} hours",
        f"Isolation matrix cycles: {len(matrix_summaries)}",
        f"Standalone A/B runs: {len(standalone_summaries)}",
        "",
    ]

    # ── Per-cycle matrix summary table ────────────────────────────────────────
    if matrix_summaries:
        lines += [
            "## Isolation Matrix Cycles",
            "",
            "One row per case per cycle. `pass` = all 4 target transitions passed + deterministic.",
            "",
            "| time | case | family | model | hint_variant | ready | det | T-04 | T-05 | T-14 | T-15 | fn_success | t_pass |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        for m in matrix_summaries:
            if m.get("error"):
                lines.append(f"| {m['folder']} | ERROR | | | | | | | | | | | |")
                continue
            ts = (m.get("created_at") or "")[:16].replace("T", " ")
            for c in m.get("cases") or []:
                tr = c.get("transitions") or {}
                coh = c.get("cohesion") or {}
                fo = c.get("function_outcome") or {}
                lines.append(
                    f"| {ts}"
                    f" | {c.get('name','')}"
                    f" | {c.get('family','')}"
                    f" | {c.get('model') or '?'}"
                    f" | {c.get('hint_variant') or 'default'}"
                    f" | {_bool_icon(c.get('readiness_pass'))}"
                    f" | {_bool_icon(c.get('deterministic'))}"
                    f" | {_status_icon(tr.get('T-04'))}"
                    f" | {_status_icon(tr.get('T-05'))}"
                    f" | {_status_icon(tr.get('T-14'))}"
                    f" | {_status_icon(tr.get('T-15'))}"
                    f" | {fo.get('success','?')}/{fo.get('total','?')}"
                    f" | {coh.get('transition_pass','?')}"
                    f" |"
                )
        lines.append("")

    # ── Trend: what changed across cycles ────────────────────────────────────
    if len(matrix_summaries) >= 2:
        lines += ["## Trend (first → last cycle)", ""]
        # Collect first and last for each case name
        first_by_case: dict[str, dict] = {}
        last_by_case: dict[str, dict] = {}
        for m in matrix_summaries:
            for c in m.get("cases") or []:
                name = c.get("name", "")
                if name not in first_by_case:
                    first_by_case[name] = c
                last_by_case[name] = c
        lines.append("| case | first_ready | last_ready | Δ fn_success | Δ T-04 | Δ T-05 |")
        lines.append("|---|---|---|---|---|---|")
        for name in first_by_case:
            first = first_by_case[name]
            last = last_by_case[name]
            # Function success delta
            f1 = (first.get("function_outcome") or {}).get("success")
            f2 = (last.get("function_outcome") or {}).get("success")
            if f1 is not None and f2 is not None:
                delta_fn = f"+{f2-f1}" if f2 >= f1 else str(f2 - f1)
            else:
                delta_fn = "?"
            t04_first = _status_icon((first.get("transitions") or {}).get("T-04"))
            t04_last = _status_icon((last.get("transitions") or {}).get("T-04"))
            t05_first = _status_icon((first.get("transitions") or {}).get("T-05"))
            t05_last = _status_icon((last.get("transitions") or {}).get("T-05"))
            lines.append(
                f"| {name}"
                f" | {_bool_icon(first.get('readiness_pass'))}"
                f" | {_bool_icon(last.get('readiness_pass'))}"
                f" | {delta_fn}"
                f" | {t04_first}→{t04_last}"
                f" | {t05_first}→{t05_last}"
                f" |"
            )
        lines.append("")

    # ── Context variant comparison ────────────────────────────────────────────
    # Group by hint_variant across all cases to show what context change did
    all_cases = [c for m in matrix_summaries for c in (m.get("cases") or [])]
    variant_cases: dict[str, list[dict]] = {}
    for c in all_cases:
        v = c.get("hint_variant") or "default"
        variant_cases.setdefault(v, []).append(c)

    if len(variant_cases) > 1:
        lines += ["## Context Variant Comparison", ""]
        lines.append("| hint_variant | cases_run | avg_fn_success | readiness_pass_rate | T-04_pass_rate |")
        lines.append("|---|---|---|---|---|")
        for variant, vlist in sorted(variant_cases.items()):
            n = len(vlist)
            fn_successes = [
                (c.get("function_outcome") or {}).get("success")
                for c in vlist
                if (c.get("function_outcome") or {}).get("success") is not None
            ]
            avg_fn = f"{sum(fn_successes)/len(fn_successes):.1f}" if fn_successes else "?"
            ready_rate = sum(1 for c in vlist if c.get("readiness_pass")) / n
            t04_pass = sum(
                1 for c in vlist
                if (c.get("transitions") or {}).get("T-04") == "pass"
            ) / n
            lines.append(
                f"| {variant} | {n} | {avg_fn}"
                f" | {ready_rate:.0%}"
                f" | {t04_pass:.0%}"
                f" |"
            )
        lines.append("")

    # ── Reasoning artifacts detected ─────────────────────────────────────────
    # After Tier 2/3/4 deploy: check which new artifact paths appeared
    REASONING_ARTIFACTS = [
        "evidence/stage-01-pre-probe/probe-user-message-sample.txt",
        "evidence/stage-01-pre-probe/probe-vocab-snapshot.json",
        "evidence/stage-02-probe-loop/probe-round-reasoning.json",
        "evidence/stage-02-probe-loop/probe-stop-reasons.json",
        "evidence/stage-02-probe-loop/probe-strategy-summary.json",
        "evidence/stage-02-probe-loop/param-rename-decisions.json",
        "evidence/stage-04-synthesis/synthesis-input-snapshot.json",
        "evidence/stage-04-synthesis/synthesis-coverage-check.json",
        "diagnostics/chat-tool-reasoning.json",
        "diagnostics/chat-system-context.txt",
        "diagnostics/chat-error-interpretation.json",
    ]
    # Check last matrix cycle's leg folders
    artifact_present: dict[str, bool] = {}
    if matrix_summaries:
        last_matrix = matrix_summaries[-1]
        for c in (last_matrix.get("cases") or []):
            run_root = None
            # Get run_root from the original matrix json
            for m_folder_str in [last_matrix["folder"]]:
                raw = _read_json(Path(m_folder_str) / "transition-isolation-matrix.json")
                if raw:
                    for raw_c in (raw.get("cases") or []):
                        if raw_c.get("name") == c.get("name"):
                            run_root = raw_c.get("run_root")
            if not run_root:
                continue
            for leg in ("leg-a", "leg-b"):
                leg_path = Path(run_root) / leg
                snapshot_dir = leg_path / "snapshot"
                if not snapshot_dir.exists():
                    # Try blob evidence paths in the session folder directly
                    continue
                for art in REASONING_ARTIFACTS:
                    art_path = snapshot_dir / art
                    if art_path.exists():
                        artifact_present[art] = True
                    elif art not in artifact_present:
                        artifact_present[art] = False
        if artifact_present:
            lines += ["## Reasoning Artifact Presence (last cycle)", ""]
            lines.append("Checks which Tier 2/3/4 reasoning artifacts appear in the latest snapshot.")
            lines.append("")
            lines.append("| artifact | present |")
            lines.append("|---|---|")
            for art, present in sorted(artifact_present.items()):
                lines.append(f"| `{art}` | {_bool_icon(present)} |")
            lines.append("")

    # ── What's still failing ─────────────────────────────────────────────────
    failing_reasons: dict[str, int] = {}
    for m in matrix_summaries:
        for c in (m.get("cases") or []):
            for r in (c.get("readiness_reasons") or []):
                failing_reasons[r] = failing_reasons.get(r, 0) + 1
    if failing_reasons:
        lines += ["## Most Common Failure Reasons (all cycles)", ""]
        lines.append("| reason | occurrences |")
        lines.append("|---|---|")
        for reason, count in sorted(failing_reasons.items(), key=lambda x: -x[1]):
            lines.append(f"| `{reason}` | {count} |")
        lines.append("")

    # ── Recommended next action ───────────────────────────────────────────────
    lines += ["## Recommended Next Action", ""]
    all_t04 = [
        (c.get("transitions") or {}).get("T-04")
        for m in matrix_summaries for c in (m.get("cases") or [])
    ]
    all_t05 = [
        (c.get("transitions") or {}).get("T-05")
        for m in matrix_summaries for c in (m.get("cases") or [])
    ]
    t04_pass_rate = sum(1 for s in all_t04 if s == "pass") / len(all_t04) if all_t04 else 0
    t05_pass_rate = sum(1 for s in all_t05 if s == "pass") / len(all_t05) if all_t05 else 0

    if not matrix_summaries:
        lines.append("- No overnight data found. Check the autopilot loop is running and has a valid API key.")
    elif t04_pass_rate == 0 and t05_pass_rate == 0:
        lines.append("- T-04 and T-05 are still 0% pass. Verify Tier 2/3 artifact deployment and container rebuild.")
        lines.append("- Check `logs/autopilot-transition.log` for auth errors.")
    elif t04_pass_rate < 1.0 or t05_pass_rate < 1.0:
        lines.append(f"- T-04 pass rate: {t04_pass_rate:.0%}, T-05 pass rate: {t05_pass_rate:.0%}")
        lines.append("- Partial improvement — check which case families are still at warn/partial.")
        lines.append("- Consider running Tier 3 reasoning artifacts if not yet deployed.")
    else:
        lines.append("- T-04 and T-05 are passing. Advance to T-14/T-15 closure and T-17..T-23 evaluators.")
        lines.append("- Consider widening the isolation matrix with model variants (gpt-4o vs gpt-4o-mini).")

    if len(variant_cases) == 1:
        lines.append("- Only one hint variant was tested overnight. Add context variants (hint_verbose, hint_minimal) for richer signal.")

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate overnight run findings report")
    parser.add_argument("--hours", type=float, default=10, help="Lookback window in hours (default: 10)")
    parser.add_argument("--out", default="", help="Output markdown path (default: sessions/morning-report-{date}.md)")
    parser.add_argument("--json", dest="json_out", default="", help="Also write JSON summary to this path")
    parser.add_argument("--verbose", action="store_true", help="Print full JSON to stdout as well")
    args = parser.parse_args()

    generated_at = _now_utc().strftime("%Y-%m-%d %H:%M UTC")
    date_slug = _now_utc().strftime("%Y-%m-%d")

    out_path = Path(args.out) if args.out else _REPO_ROOT / "sessions" / f"morning-report-{date_slug}.md"
    json_path = Path(args.json_out) if args.json_out else None

    print(f"Scanning {_RUNS_ROOT} (last {args.hours:.0f}h)…", flush=True)

    matrix_folders = _collect_matrix_folders(args.hours)
    standalone_folders = _collect_ab_folders(args.hours)

    print(f"  Found {len(matrix_folders)} matrix cycle(s), {len(standalone_folders)} standalone A/B run(s)", flush=True)

    matrix_summaries = [_analyze_matrix_folder(f) for f in matrix_folders]

    # Build JSON payload
    payload = {
        "generated_at": _now_utc().isoformat(),
        "lookback_hours": args.hours,
        "matrix_cycles": len(matrix_summaries),
        "standalone_ab_runs": len(standalone_folders),
        "matrix_summaries": matrix_summaries,
    }

    md = _render_markdown(matrix_summaries, [], args.hours, generated_at)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(md, encoding="utf-8")
    print(f"Report written: {out_path}", flush=True)

    if json_path:
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"JSON written  → {json_path}", flush=True)

    if args.verbose:
        print("\n" + md, flush=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
