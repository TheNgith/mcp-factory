#!/usr/bin/env python3
"""
select_invocables.py — Sections 2-3 interactive CLI for MCP Factory.

Usage (from project root):

  # Analyse a binary and select invocables in one step
  python src/ui/select_invocables.py --target path/to/file.dll

  # Use an existing discovery JSON
  python src/ui/select_invocables.py --input artifacts/discovery-output.json

  # Feed a description hint to highlight relevant invocables (Section 2.b)
  python src/ui/select_invocables.py --target file.dll --description "handles zip compression"

Output: artifacts/selected-invocables.json (consumed by Section 4)
"""

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ── Windows UTF-8 fix ─────────────────────────────────────────────────────────
# Rich renders checkmarks and box-drawing characters that cp1252 can't encode.
# Reconfigure stdout/stderr to UTF-8 before Rich is imported.
if sys.platform == "win32":
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            try:
                _stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass

from rich.console import Console
from rich.table import Table
from rich import box

# ── paths ────────────────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS_DIR = PROJECT_ROOT / "artifacts"
DEFAULT_OUTPUT = ARTIFACTS_DIR / "selected-invocables.json"
MAIN_PY = PROJECT_ROOT / "src" / "discovery" / "main.py"
DISCOVERY_OUTPUT = ARTIFACTS_DIR / "discovery-output.json"

# ── confidence ────────────────────────────────────────────────────────────────

HIGH_CONFIDENCE = {"guaranteed", "high"}
LOW_CONFIDENCE = {"medium", "low"}

CONF_STYLE = {
    "guaranteed": "bold green",
    "high": "green",
    "medium": "yellow",
    "low": "red",
}

CONF_ICON = {
    "guaranteed": "✦",
    "high": "✓",
    "medium": "~",
    "low": "✗",
}

# ── console ───────────────────────────────────────────────────────────────────

# legacy_windows=False: use ANSI renderer on all Windows terminals,
# avoiding the cp1252 encode error for Unicode box/check characters.
console = Console(legacy_windows=False, highlight=False)


# ── discovery helpers ─────────────────────────────────────────────────────────

# Friendly label for each output file suffix that main.py produces
_KIND_LABELS = {
    "exports":          "native exports",
    "com_objects":      "COM interfaces",
    "cli":              "CLI commands",
    "dotnet_methods":   ".NET methods",
    "rpc":              "RPC interfaces",
    # Legacy protocol / spec analyzers
    "openapi_spec":              "OpenAPI operations",
    "openapi_openapi_spec":      "OpenAPI operations",
    "jsonrpc_spec":              "JSON-RPC methods",
    "jsonrpc_jsonrpc_spec":      "JSON-RPC methods",
    "sample_jsonrpc_jsonrpc_spec": "JSON-RPC methods",
    "wsdl_file":                 "SOAP operations",
    "corba_idl":                 "CORBA methods",
    "jndi_config":               "JNDI bindings",
    "pdb_file":                  "PDB symbols",
}


def _kind_label(path: Path) -> str:
    """Derive a friendly label from a *_mcp.json filename."""
    stem = path.stem  # e.g. shell32_exports_mcp or shell32_com_objects_mcp
    # strip trailing _mcp
    if stem.endswith("_mcp"):
        stem = stem[:-4]
    # strip leading <target>_
    parts = stem.split("_", 1)
    suffix = parts[1] if len(parts) > 1 else stem
    return _KIND_LABELS.get(suffix, suffix.replace("_", " "))


def run_discovery(target: Path, description: Optional[str]) -> dict:
    """Run src/discovery/main.py on *target*, collect ALL output JSONs,
    notify the user about hybrid files, and return a single merged data dict."""
    console.print(f"\n[bold cyan]Running discovery on:[/] {target}\n")
    cmd = [sys.executable, str(MAIN_PY), "--target", str(target), "--out", str(ARTIFACTS_DIR)]
    result = subprocess.run(cmd, cwd=str(PROJECT_ROOT))
    if result.returncode != 0:
        console.print("[red]Discovery failed — check output above.[/]")
        sys.exit(1)

    # Collect ALL *_mcp.json files written for this target stem.
    # For a directory target the aggregate is <dir>_scan_mcp.json; for a
    # single file it is <stem>_*_mcp.json — both are found by the glob below.
    candidates = sorted(
        ARTIFACTS_DIR.glob(f"{target.stem}*_mcp.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        console.print("[red]Could not find any discovery output JSON.[/]")
        sys.exit(1)

    return _load_and_merge(candidates)


def _load_and_merge(paths: list[Path]) -> dict:
    """Load one or more discovery JSONs and merge their invocables.

    If multiple files are found (hybrid binary), the user is notified clearly
    and all invocables are combined into a single list so the selection UI
    works on the full picture at once.
    """
    datasets: list[dict] = []
    for p in paths:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        for key in ("metadata", "invocables"):
            if key not in data:
                raise ValueError(f"{p.name}: missing key {key!r}")
        if isinstance(data["invocables"], list) and data["invocables"]:
            datasets.append(data)

    if not datasets:
        raise ValueError("No invocables found in any of the discovery outputs.")

    if len(datasets) == 1:
        return datasets[0]

    # ── Hybrid detected ──────────────────────────────────────────────────────
    labels = [_kind_label(Path(d.get("metadata", {}).get("target_path", "") or paths[i].name))
              for i, d in enumerate(datasets)]
    # fallback: use filenames if metadata target_path is unhelpful
    labels = [_kind_label(paths[i]) for i in range(len(datasets))]

    total = sum(len(d["invocables"]) for d in datasets)
    label_str = "  +  ".join(
        f"[bold]{len(d['invocables'])}[/] {labels[i]}"
        for i, d in enumerate(datasets)
    )
    console.print(
        f"\n[bold yellow]Hybrid binary detected[/] — "
        f"found {len(datasets)} output sets: {label_str}\n"
        f"  [dim]Merging into one list ({total} total invocables).[/]\n"
    )

    # Use metadata from the first (primary) dataset; merge invocables
    merged_metadata = datasets[0]["metadata"].copy()
    merged_invocables: list[dict] = []
    for i, d in enumerate(datasets):
        for inv in d["invocables"]:
            # Stamp a source_group field so the table can show it
            inv_copy = dict(inv)
            inv_copy["_source_group"] = labels[i]
            merged_invocables.append(inv_copy)

    merged_summary = {
        "total_invocables": total,
        "source_groups": {labels[i]: len(d["invocables"]) for i, d in enumerate(datasets)},
    }

    return {
        "metadata": merged_metadata,
        "invocables": merged_invocables,
        "summary": merged_summary,
        "_is_hybrid": True,
        "_hybrid_labels": labels,
    }


def load_discovery(path: Path) -> dict:
    """Load a single discovery JSON; also checks for sibling hybrid outputs."""
    # Look for all *_mcp.json files with the same target stem in the same directory
    stem_prefix = path.stem  # e.g. shell32_exports_mcp
    # Normalise: strip trailing _mcp to get the target base name
    base = stem_prefix[:-4] if stem_prefix.endswith("_mcp") else stem_prefix
    # base is now e.g. "shell32_exports" — strip the suffix part to get "shell32"
    # Strategy: find the shortest prefix that still matches all candidates
    siblings = sorted(path.parent.glob(f"*_mcp.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    # Filter to files sharing the same first token (target name)
    target_token = base.split("_")[0]
    related = [p for p in siblings if p.stem.startswith(target_token + "_") or p.stem == target_token]
    if not related:
        related = [path]
    return _load_and_merge(related)


# ── keyword highlight helpers ─────────────────────────────────────────────────

def extract_keywords(description: str) -> list[str]:
    """Return lowercase words ≥ 3 chars from *description*."""
    return [w.lower() for w in re.findall(r"[a-zA-Z]{3,}", description) if w.lower() not in {
        "the", "and", "for", "from", "that", "this", "with", "which", "have", "its",
    }]


def matches_hint(inv: dict, keywords: list[str]) -> bool:
    """True if any keyword appears in the invocable's name or description."""
    text = (inv.get("name", "") + " " + inv.get("description", "")).lower()
    return any(kw in text for kw in keywords)


# ── table rendering ───────────────────────────────────────────────────────────

def build_table(
    invocables: list[dict],
    selected: list[bool],
    keywords: list[str],
    is_hybrid: bool = False,
) -> Table:
    table = Table(
        box=box.SIMPLE_HEAVY,
        show_header=True,
        header_style="bold white on dark_blue",
        expand=False,
        padding=(0, 1),
    )
    table.add_column("#", justify="right", style="dim", width=5)
    table.add_column("Sel", justify="center", width=4)
    table.add_column("Name", no_wrap=False, min_width=28)
    if is_hybrid:
        table.add_column("Source", width=16, style="dim cyan")
    table.add_column("Kind", width=10)
    table.add_column("Conf", width=12)
    table.add_column("Description", no_wrap=False, min_width=35, max_width=60)

    for i, inv in enumerate(invocables, start=1):
        conf = inv.get("confidence", "low")
        style = CONF_STYLE.get(conf, "")
        icon = CONF_ICON.get(conf, "?")
        sel_mark = "✓" if selected[i - 1] else " "
        sel_style = "bold green" if selected[i - 1] else "dim"

        name = inv.get("name", "?")
        if keywords and matches_hint(inv, keywords):
            name = f"[bold magenta]{name}[/]"

        description = (inv.get("description", "") or "").strip()
        if len(description) > 60:
            description = description[:57] + "..."

        row_style = "on grey7" if i % 2 == 0 else ""

        row = [
            str(i),
            f"[{sel_style}]{sel_mark}[/]",
            name,
        ]
        if is_hybrid:
            row.append(inv.get("_source_group", ""))
        row += [
            inv.get("kind", ""),
            f"[{style}]{icon} {conf}[/]",
            description,
        ]
        table.add_row(*row, style=row_style)
    return table


def print_summary(invocables: list[dict], selected: list[bool]) -> None:
    total = len(invocables)
    n_sel = sum(selected)
    by_conf: dict[str, int] = {}
    for inv, sel in zip(invocables, selected):
        if sel:
            c = inv.get("confidence", "low")
            by_conf[c] = by_conf.get(c, 0) + 1

    parts = "  ".join(
        f"[{CONF_STYLE[c]}]{c}: {by_conf.get(c, 0)}[/]"
        for c in ("guaranteed", "high", "medium", "low")
    )
    console.print(f"\n[bold]Selected:[/] {n_sel}/{total}   {parts}")


def print_help() -> None:
    console.print("""
[bold]Commands:[/]
  [cyan]<n>[/]         Toggle item number n  (e.g. [cyan]5[/])
  [cyan]<a>-<b>[/]     Toggle range          (e.g. [cyan]3-10[/])
  [cyan]a[/]           Select all
  [cyan]n[/]           Select none
  [cyan]g[/]           Select [green]guaranteed[/] + [green]high[/] only (recommended)
  [cyan]m[/]           Toggle all [yellow]medium[/] on/off
  [cyan]l[/]           Toggle all [red]low[/] on/off
  [cyan]f <text>[/]    Filter view: show only rows where name contains <text>
  [cyan]r[/]           Reset filter (show all)
  [cyan]?[/]           Show this help
  [cyan]done[/]        Confirm selection and write output
  [cyan]q[/]           Quit without saving
""")


# ── parse range helper ────────────────────────────────────────────────────────

def parse_range_or_num(text: str, max_n: int) -> list[int]:
    """Return list of 0-based indices from '5' or '3-10' input."""
    text = text.strip()
    if "-" in text:
        parts = text.split("-", 1)
        lo, hi = int(parts[0].strip()), int(parts[1].strip())
        if lo < 1 or hi > max_n or lo > hi:
            raise ValueError(f"Range {text!r} is out of bounds (1–{max_n}).")
        return list(range(lo - 1, hi))
    else:
        n = int(text)
        if n < 1 or n > max_n:
            raise ValueError(f"Number {n} out of bounds (1–{max_n}).")
        return [n - 1]


# ── suggest component name ────────────────────────────────────────────────────

def suggest_name(metadata: dict) -> str:
    name = metadata.get("target_name", "target").lower()
    base = name.replace(".exe", "").replace(".dll", "").replace(" ", "-")
    return f"mcp-{base}"


# ── main interactive loop ─────────────────────────────────────────────────────

def run_ui(data: dict, description: Optional[str], out_path: Path) -> None:
    metadata = data["metadata"]
    invocables = data["invocables"]
    is_hybrid: bool = data.get("_is_hybrid", False)
    n = len(invocables)
    keywords = extract_keywords(description) if description else []

    # Default selection: guaranteed + high ON, medium + low OFF
    selected = [inv.get("confidence", "low") in HIGH_CONFIDENCE for inv in invocables]

    # Active filter (subset of visible indices, 0-based)
    filter_text: Optional[str] = None
    visible: list[int] = list(range(n))

    target_name = metadata.get("target_name", "unknown")
    console.rule(f"[bold cyan]MCP Factory — Invocable Selection[/]  [dim]{target_name}[/]")
    console.print(f"  [dim]Found [bold]{n}[/] invocables.  "
                  f"[green]guaranteed+high[/] selected by default.  "
                  f"[yellow]medium[/] and [red]low[/] are off.[/]")
    if keywords:
        console.print(f"  [magenta]Hint keywords:[/] {', '.join(keywords)}")
    console.print()

    def refresh():
        nonlocal visible
        if filter_text:
            visible = [i for i, inv in enumerate(invocables) if filter_text.lower() in inv.get("name", "").lower()]
        else:
            visible = list(range(n))

        view_invocables = [invocables[i] for i in visible]
        view_selected = [selected[i] for i in visible]
        tbl = build_table(view_invocables, view_selected, keywords, is_hybrid=is_hybrid)
        console.print(tbl)
        if filter_text:
            console.print(f"  [dim]Filter active: [bold]{filter_text!r}[/] — {len(visible)} of {n} rows shown.  Type [cyan]r[/] to reset.[/]")
        print_summary(invocables, selected)

    refresh()
    print_help()

    while True:
        try:
            raw = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[red]Aborted.[/]")
            sys.exit(0)

        if not raw:
            continue

        cmd = raw.lower()

        if cmd in ("q", "quit", "exit"):
            console.print("[red]Quit without saving.[/]")
            sys.exit(0)

        elif cmd in ("done", "d", "save"):
            break

        elif cmd == "?":
            print_help()
            continue

        elif cmd == "a":
            selected = [True] * n
            console.print("[green]All selected.[/]")

        elif cmd == "n":
            selected = [False] * n
            console.print("[dim]All deselected.[/]")

        elif cmd == "g":
            selected = [inv.get("confidence", "low") in HIGH_CONFIDENCE for inv in invocables]
            console.print("[green]guaranteed + high selected.[/]")

        elif cmd == "m":
            med_indices = [i for i, inv in enumerate(invocables) if inv.get("confidence") == "medium"]
            any_on = any(selected[i] for i in med_indices)
            for i in med_indices:
                selected[i] = not any_on
            verb = "deselected" if any_on else "selected"
            console.print(f"[yellow]medium[/] invocables {verb}.")

        elif cmd == "l":
            low_indices = [i for i, inv in enumerate(invocables) if inv.get("confidence") == "low"]
            any_on = any(selected[i] for i in low_indices)
            for i in low_indices:
                selected[i] = not any_on
            verb = "deselected" if any_on else "selected"
            console.print(f"[red]low[/] invocables {verb}.")

        elif cmd == "r":
            filter_text = None
            console.print("[dim]Filter cleared.[/]")

        elif cmd.startswith("f "):
            filter_text = raw[2:].strip()
            if not filter_text:
                filter_text = None
                console.print("[dim]Filter cleared.[/]")
            else:
                console.print(f"Filter: [bold]{filter_text!r}[/]")

        else:
            # Try numeric toggle — operate on *visible* indices
            try:
                rel_indices = parse_range_or_num(raw, len(visible))
                abs_indices = [visible[ri] for ri in rel_indices]
                # Toggle: if ALL are selected, deselect; otherwise select
                all_on = all(selected[ai] for ai in abs_indices)
                for ai in abs_indices:
                    selected[ai] = not all_on
                verb = "deselected" if all_on else "selected"
                names = ", ".join(invocables[ai]["name"] for ai in abs_indices[:3])
                if len(abs_indices) > 3:
                    names += f" … (+{len(abs_indices) - 3} more)"
                console.print(f"  {verb}: {names}")
            except (ValueError, IndexError) as exc:
                console.print(f"[red]? Unrecognised command.[/] ({exc})  Type [cyan]?[/] for help.")
                continue

        refresh()

    # ── confirm ───────────────────────────────────────────────────────────────
    # Strip internal UI fields before writing output, and normalize rich-format
    # invocables (schema v2.0 from discovery) into the flat format the generator
    # and _execute_dll expect.
    def _normalize(inv: dict) -> dict:
        out = {k: v for k, v in inv.items() if not k.startswith("_")}

        # ── execution ──────────────────────────────────────────────────────
        # Rich format stores execution inside mcp.execution; flat format has
        # it at the top level.  Hoist if needed.
        if "execution" not in out and "mcp" in out:
            mcp_exec = (out.get("mcp") or {}).get("execution")
            if mcp_exec:
                out["execution"] = mcp_exec

        # ── return_type ────────────────────────────────────────────────────
        # Prefer signature.return_type over a stale "unknown" top-level value.
        sig = out.get("signature") or {}
        sig_ret = sig.get("return_type", "")
        if sig_ret and (not out.get("return_type") or out.get("return_type") == "unknown"):
            out["return_type"] = sig_ret

        # ── parameters ────────────────────────────────────────────────────
        # If parameters list is empty but signature has a parameter string,
        # parse it into the flat [{name, type, required, description}] form.
        if not out.get("parameters") and sig.get("parameters"):
            parsed = []
            for chunk in sig["parameters"].split(","):
                tokens = chunk.strip().split()
                if len(tokens) >= 2:
                    raw_type = " ".join(tokens[:-1]).strip()
                    pname    = tokens[-1].lstrip("*")
                    parsed.append({
                        "name": pname,
                        "type": raw_type,
                        "required": True,
                        "description": raw_type,
                    })
            if parsed:
                out["parameters"] = parsed

        # ── clean up rich-only fields to keep the output tidy ─────────────
        # Keep signature/mcp for traceability but they won't confuse anything.
        return out

    selected_inv = [_normalize(inv) for inv, sel in zip(invocables, selected) if sel]
    n_sel = len(selected_inv)

    if n_sel == 0:
        console.print("\n[red]No invocables selected — nothing to save.[/]  Re-run and select at least one.")
        sys.exit(1)

    suggested = suggest_name(metadata)
    console.print(f"\n[bold]Component name[/] (name of the generated MCP server).")
    console.print(f"  Press Enter to accept [cyan]{suggested}[/].\n")
    try:
        comp_raw = input(f"  Component name [{suggested}]: ").strip()
    except (EOFError, KeyboardInterrupt):
        comp_raw = ""
    component_name = comp_raw if comp_raw else suggested

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "component_name": component_name,
        "metadata": metadata,
        "selected_invocables": selected_inv,
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    console.print(f"\n[bold green]✓ Saved.[/]  {n_sel} invocables → [cyan]{out_path}[/]")
    console.print(f"  Run [dim]python src/generation/section4_generate_server.py[/] to generate the MCP server.\n")


# ── entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="MCP Factory — Sections 2-3: specify binary and select invocables"
    )
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--target", type=Path, metavar="FILE_OR_DIR",
                     help="Binary / script to analyse, or an installed directory "
                          "(e.g. C:\\\\Program Files\\\\AppD\\\\).  "
                          "Runs discovery first; .dll/.exe/.tlb/scripts/etc. accepted.")
    src.add_argument("--input", type=Path, metavar="JSON",
                     help="Existing discovery-output JSON (skip discovery)")
    parser.add_argument("--description", "-d", type=str, default="",
                        metavar="TEXT",
                        help="Free-text description / hint (Section 2.b) — highlights matches in the list")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT,
                        metavar="PATH",
                        help=f"Output path (default: {DEFAULT_OUTPUT})")
    args = parser.parse_args()

    if args.target:
        if not args.target.exists():
            console.print(f"[red]Target not found:[/] {args.target}")
            sys.exit(1)
        data = run_discovery(args.target, args.description or None)
    else:
        input_path = args.input
        if not input_path.exists():
            console.print(f"[red]Input JSON not found:[/] {input_path}")
            sys.exit(1)
        data = load_discovery(input_path)

    run_ui(data, args.description or None, out_path=args.out)


if __name__ == "__main__":
    main()
