```markdown
# ADR 0008: GUI Pipeline — Executable Launch, UIA-First Backend, and Open-File Action

**Date:** 2026-03-04  
**Status:** ACCEPTED  
**Owner:** Evan King  
**Relates to:** Section 4 (MCP Server Generation), generated `server.py` GUI execution path

---

## Problem Statement

Four independent bugs in the generated server's GUI execution path made the
Calculator demo non-functional and the Notepad demo incomplete:

1. **Wrong execution key** — `_execute_cli` looked for `target_path` / `dll_path`
   but `.exe` invocables store their path under `executable_path`.  Result: `target`
   resolved to `""`, producing `WinError 87 (incorrect parameter)` on every call.

2. **Spurious CLI argument** — Even if the path had resolved correctly, the command
   would have been `["calc.exe", "calc"]` — passing the binary's own name as a CLI
   argument to itself.  For "launch this app" invocables the correct command is just
   `[exe_path]`.

3. **Duplicate windows on MSIX/WinUI3 apps** — `_execute_cli`'s launch path called
   raw `subprocess.Popen`, bypassing `_ensure_gui_app` entirely.  Subsequent
   `button_click` calls found nothing in `_GUI_APP_INSTANCES` and triggered
   `_ensure_gui_app`, which always attempted the win32 Attempt A first — spawning the
   MSIX stub, watching it exit, leaving an orphan window, then falling through to UIA
   Attempt B which opened the real window.  Net result: two Calculator windows on
   every launch.

4. **No "open existing file" action** — `file_open` was implemented as a bare
   `menu_click` that triggered `File → Open` but never typed a filename into the
   dialog or confirmed it.  Reopening a previously saved file was impossible.

---

## Decision

### Fix 1 — Unified executable key lookup
`_execute_cli` now checks all three key names in priority order:
```python
target = (
    execution.get("executable_path")
    or execution.get("target_path")
    or execution.get("dll_path", "")
)
```

### Fix 2 — "Launch exe" detection
When `Path(target).stem.lower() == name.lower()` the invocable *is* the
executable (not a subcommand of a larger CLI tool).  Route through
`_ensure_gui_app` rather than constructing a `[target, name, …]` command:
```python
if exe_stem == name.lower():
    _ensure_gui_app(target, preferred_backend=_preferred)
    return f"Launched {Path(target).name}"
```
The standard `subprocess.run` path is preserved for real CLI tools like `zstd`.

### Fix 3 — `preferred_backend` skip for MSIX/WinUI3 apps
`_ensure_gui_app` now accepts a `preferred_backend: str = ""` parameter.  When
`"uia"` is passed, Attempt A (win32 Popen + PID connect) is skipped entirely and
control falls through directly to Attempt B (HWND-diff shell-launch).

`_execute_cli` detects the preferred backend by scanning sibling invocables for
the same `exe_path`:
```python
for _inv in INVOCABLES:
    _exec = _inv.get("execution") or {}
    if (_exec.get("exe_path", "").lower() == target.lower()
            and _exec.get("gui_backend") == "uia"):
        _preferred = "uia"
        break
```
Classic Win32 apps (Notepad, Paint, etc.) have no `gui_backend` on their sibling
invocables so `_preferred` stays `""` and Attempt A still fires — no regression.

### Fix 4 — `open_file` action type
A new `action_type == "open_file"` handler mirrors `save_as` exactly:
triggers `File → Open`, waits for the shell dialog, locates the filename
`Edit` control via four fallback strategies, types the path, and confirms with
the Open button.  The `file_open` invocable in the Notepad server was upgraded
from `action_type: menu_click` (no filename) to `action_type: open_file` with a
`filename` parameter.

### Bonus fix — WinUI3 button-click timing
Post-click sleep in the `button_click` handler was increased from `0.1 s` to
`0.25 s`.  At `0.1 s`, rapid sequential calls to the same button (e.g. two
`press_zero` in a row for "100") coalesced at the WinUI3 message queue before
the first click was committed, causing dropped inputs.

---

## Files Changed

| File | Change |
|------|--------|
| `src/generation/section4_generate_server.py` | `_execute_cli` key lookup; launch-exe detection; `preferred_backend` param on `_ensure_gui_app`; Attempt A guard; `open_file` action type; button-click sleep `0.1→0.25 s` |
| `generated/calculator-test2/server.py` | Same fixes applied to the live generated server |
| `generated/notepad/server.py` | `file_open` invocable upgraded to `open_file` action type with `filename` parameter; `open_file` handler added to `_execute_gui` |
| `generated/mcp-calc/server.py` | `_execute_cli` key lookup + launch-exe fix |
| `generated/zstd/server.py` | `_execute_cli` key lookup fix |
| `generated/test-component/server.py` | `_execute_cli` key lookup fix |

---

## Rationale

| Choice | Rationale |
|--------|-----------|
| **Sibling-scan for `preferred_backend`** | Discovery already stores `gui_backend` on every GUI invocable. Reading it at launch time requires no new pipeline stage and works for any future target that has UIA buttons. |
| **`_ensure_gui_app` for launch path** | Deduplication is already implemented there via `_GUI_APP_INSTANCES`. Routing through it guarantees the cache is warm before the first button press arrives. |
| **`open_file` as a new action type (not a menu_click variant)** | `menu_click` has no mechanism to interact with the resulting dialog. A dedicated action type mirrors the `save_as` pattern and is extensible to other dialog-driven file operations. |
| **`0.25 s` button sleep** | Empirically determined on Windows 11 / Calculator. Covers WinUI3 rendering latency without adding perceptible delay to the user-facing chat response. |

---

## Consequences

**Positive:**
- `calc` invocable now correctly launches Calculator (one window, no orphans).
- All `press_*` button invocables work reliably for multi-digit numbers.
- `file_open` in Notepad correctly types and confirms a filename in the Open dialog.
- Fixes are generic — apply to any new `.exe` target generated by the pipeline.

**Negative / Trade-offs:**
- `0.25 s` per button press adds ~2 s overhead for a 10-button sequence.  Acceptable
  for a demo; a future batched `press_sequence(buttons: list)` action could reduce
  this to one round-trip.
- Sibling scan in `_execute_cli` is O(n) in invocable count.  Negligible for current
  demo sizes (<100 invocables); not a concern.

---

## Alternatives Considered

| Alternative | Rejected because |
|-------------|-----------------|
| Increase `CREATE_NO_WINDOW` suppression | Root cause was wrong key + wrong command, not window suppression; suppression would make the bug worse for GUI launchers |
| Add `preferred_backend` to the invocable JSON at generation time | Correct long-term, but requires pipeline re-run; runtime sibling scan fixes all existing generated servers without regeneration |
| Higher sleep value (≥ 0.5 s) | Unnecessary; 0.25 s is sufficient and keeps the demo feeling responsive |

---

## Verification

- `calc` → `Launched calc.exe` (single window)
- `press_one`, `press_zero`, `press_zero` → Calculator displays `100` (no dropped clicks)
- `100 × 2 ÷ 10 =` sequence → result `20` displayed correctly
- `file_open(filename="demo_getty.txt")` → Notepad opens the file
- `Notepad --serve` demo: type → save_as → file_open → append → file_save — full round-trip confirmed

## References

- Commit: `02c1688` — prior `/chat` endpoint work that established `_execute_gui` structure
- `_GUI_APP_INSTANCES` cache: `src/generation/section4_generate_server.py`
- MSIX/WinUI3 HWND-diff launch: Attempt B in `_ensure_gui_app`
```
