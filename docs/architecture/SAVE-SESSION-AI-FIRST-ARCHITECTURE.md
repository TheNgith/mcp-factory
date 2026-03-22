# Save-Session Architecture for AI-First Contract Workflows

Status: Draft v1
Owner: Pipeline architecture
Primary consumers: AI agents performing regression review, causality diagnosis, and release gating

Canonical location: this file is the single source of truth. The root-level
`docs/SAVE-SESSION-AI-FIRST-ARCHITECTURE.md` is a compatibility index only.

## 1. Purpose

Define the two-component data collection architecture:

1. `scripts/save-session.ps1` — OS-level orchestration only (download ZIP, extract, relay exit code)
2. `scripts/collect_session.py` — all evaluation intelligence (contract validation, transition scoring, dashboard output)

Authority sources:
- `docs/AI-FIRST-SNAPSHOT-CONTRACT.md` is the canonical schema/layout authority
- `docs/CAUSALITY-ARTIFACT-LAYER-PLAN.md` is the canonical transition-semantics authority

This document is implementation guidance for both components.

**Design rationale:** PowerShell is the correct language for Windows-native OS operations (credential
chain, ZIP extraction, git subprocess, CI exit codes). Python is the correct language for contract
parsing, transition evaluation, JSON generation, and any logic an AI agent needs to implement or
modify. Keeping intelligence in PowerShell creates two parse paths for the same schema, prevents
AI agent modification without PS expertise, and makes offline debugging impossible without the
full save-session invocation stack.

## 2. Authority Model

Precedence order:
1. `docs/AI-FIRST-SNAPSHOT-CONTRACT.md` for artifact paths, JSON shapes, required fields, and version policy
2. `docs/CAUSALITY-ARTIFACT-LAYER-PLAN.md` for transition meanings, severity intent, and gate semantics
3. `scripts/collect_session.py` for contract validation, transition evaluation, and derived output generation
4. `scripts/save-session.ps1` for OS-level packaging only (download, extract, invoke Python, relay exit code)

Rule: If save-session behavior conflicts with either source document, the fix goes in `collect_session.py`
not in PowerShell. PowerShell must not redefine, re-parse, or re-evaluate contract schema.

## 3. Non-Negotiable Principles

1. Machine-truth immutability
- save-session must never rewrite machine-truth files in-place

2. Machine-first diagnostics
- agents must be able to diagnose from contract JSON alone
- free-text transcript parsing is fallback only

3. Derived-output isolation
- all generated markdown/summaries/comparison rows go under `human/`

4. Single gate input
- automation gates consume `cohesion-report.json` first

5. Fail-closed version behavior
- if declared contract version is unsupported or required fields are missing, save-session exits non-zero

## 4. Required Contract Inputs

A session is contract-valid only if these files are present and parseable:
- `session-meta.json`
- `stage-index.json`
- `transition-index.json`
- `cohesion-report.json`

If any are missing, save-session must:
1. write `human/summary.md` describing the missing files
2. set process exit code non-zero
3. avoid generating pass-style dashboard outputs

## 5. Required Save-Session Outputs

save-session may write only these derived outputs:
- `human/summary.md`
- `human/dashboard-row.json`
- `human/compare-summary.md` (if compare mode is used)
- `human/session-save-meta.json`

All other outputs must be copies of source artifacts, not rewritten variants.

## 6. Contract-Safe Save Flow

**PowerShell layer (save-session.ps1) — ~30 lines:**
1. `Invoke-WebRequest` → download snapshot ZIP to `$env:TEMP`
2. `Expand-Archive` → extract to `sessions/_tmp-{JobId}/`
3. Resolve final session folder name (date + commit + note slug)
4. Move `_tmp-{JobId}` → `sessions/{folderName}/`
5. Invoke `python scripts/collect_session.py --session-dir $sessionDir [--enforce-hard-fail] [--compatibility-mode]`
6. Relay Python exit code to the calling shell/CI runner
7. Clean up `_tmp-{JobId}` on both success and failure

**Python layer (collect_session.py) — all intelligence:**
1. Detect and report 0-byte or absent contract files
2. Validate all 4 required contract files (parse JSON, check version field)
3. Parse `cohesion-report.json` → extract `hard_fail`, `pipeline_verdict`, failed transition IDs
4. Check capture quality (ZIP size sanity, `final_phase` terminal state, clock skew)
5. Write `human/session-save-meta.json` with `capture_quality: complete|degraded|failed`
6. Write `human/dashboard-row.json` from contract files only
7. Write `human/summary.md` from contract files only
8. Exit non-zero when:
   - contract files are missing/invalid and not in compatibility mode
   - `hard_fail` is true and `--enforce-hard-fail` flag is set

## 7. Must-Remove Legacy Behaviors

The following patterns violate AI-first contract handling and should be removed or moved into compatibility mode:

1. Mutating `session-meta.json`
- save metadata (`saved_at`, note, commit) must go to `human/session-save-meta.json`

2. Root-level derived outputs
- files like `delta.md` / `code-changes.md` at snapshot root should be moved to `human/`

3. Hardcoded legacy stage paths as primary logic
- examples: direct dependence on `stage-05-finalization/...`
- primary source should be `stage-index.json` + `transition-index.json`

4. Ad-hoc gate inference from prose/logs
- gate verdict must come from `cohesion-report.json`

## 8. Compatibility Mode (Temporary)

Until all pipelines emit full contract artifacts, save-session may support a `compatibility_mode` path:

- attempt legacy path resolution only if required contract files are missing
- write `human/session-save-meta.json` with:
  - `compatibility_mode: true`
  - `missing_contract_files: [...]`
  - `inferred_fields: [...]`
- never label compatibility outputs as fully contract-compliant

Compatibility mode should be removed after 2-3 successful contract-native runs.

## 9. Gating Semantics for AI Review

Gate priority order:
1. `cohesion-report.json.gates.hard_fail` (or equivalent top-level `hard_fail` if contract version defines it)
2. high-severity failed transitions from `transition-index.json`
3. stage failures from `stage-index.json`

AI review agents should open only evidence paths referenced by failed transitions first.

## 10. Implementation Order

1. Implement contract emitters + evaluator in API pipeline (`evaluate_cohesion.py`, `explore.py` instrumentation)
2. **Create `scripts/collect_session.py`** — Python data collection intelligence (see §17)
3. **Refactor `scripts/save-session.ps1`** to ~30-line PS orchestrator that invokes `collect_session.py`
4. Update compare/CI to consume `human/dashboard-row.json` and contract files only
5. Run 2-3 real sessions; verify `capture_quality: complete` in all `session-save-meta.json` outputs
6. Refactor API internals only where friction repeats and ownership boundaries are unstable

Reason: contract ambiguity is a larger risk than temporary code duplication. Language boundary
(PS owns OS tasks, Python owns intelligence) is a larger risk than the refactor cost.

## 11. Acceptance Criteria for Save-Session

1. Does not mutate machine-truth artifacts
2. Produces only derived files under `human/`
3. Uses contract files as primary parsing source
4. Fails closed on missing/invalid required contract fields
5. Produces deterministic `human/dashboard-row.json` from contract data
6. Supports temporary compatibility mode with explicit labeling

## 12. Re-Review Policy

Yes, this architecture should be re-reviewed after both source docs are in place and whenever either source changes.

Re-review triggers:
1. `docs/AI-FIRST-SNAPSHOT-CONTRACT.md` version change
2. Transition ID/status/severity rule changes in `docs/CAUSALITY-ARTIFACT-LAYER-PLAN.md`
3. Any save-session change that adds/removes outputs
4. First 2-3 production-like runs after contract-native implementation

Minimum re-review checklist:
1. Contract file presence/shape checks still match schema authority
2. Gate evaluation still depends on `cohesion-report.json` first
3. Human outputs remain isolated under `human/`
4. No machine-truth mutation reintroduced

## 13. Suggested Next Work Items

1. **Create `scripts/collect_session.py`** with contract validation, capture quality check, and `human/` output generation
2. **Reduce `scripts/save-session.ps1`** to download → extract → invoke Python → relay exit code
3. Add `--enforce-hard-fail` flag to `collect_session.py` (replaces PS `-EnforceHardFail` switch)
4. Add schema-version support map in `collect_session.py` (replaces PS version map)
5. Update compare script to consume only `human/dashboard-row.json` + contract files
6. Verify `collect_session.py` runs standalone on an already-extracted session folder (debugging use case)

## 16. Data Collection as the Iteration Loop

> **Why this section exists:** The sections above define what save-session must and must not do
> with artifacts once they are downloaded. This section addresses a prior question: what are the
> reliability requirements for the data collection step itself — before artifact parsing even begins?
> This distinction matters because save-session is the **only feedback boundary from Azure**, and
> the entire iteration loop depends on it.

### 16.1 The Azure Boundary Problem

The MCP Factory pipeline runs entirely inside Azure Container Apps. There is no local runtime
visibility. Blob storage is not directly accessible without API calls. The only mechanism for
extracting session data from the cloud to somewhere iteratable is:

```
POST /api/jobs/{job_id}/session-snapshot  →  ZIP  →  save-session.ps1  →  sessions/
```

This means save-session is not just a packaging step — it is the **exfiltration boundary**. If
any link in that chain degrades silently, iteration becomes impossible without knowing it.

Consequences:
- A failed save-session does not produce a corrupt artifact. It produces **no artifact at all**.
- A partial save-session (ZIP downloaded, some blobs absent) looks like a successful run until
  the contract compliance check. If compatibility mode is active, it may pass the gate anyway.
- A save-session that succeeds on a job that had a broken pipeline produces a complete,
  contract-valid capture of a broken run. This is correct behavior but must not be confused
  with a healthy run.

### 16.2 The Iteration Loop Depends On Successive Reliable Captures

The iteration loop is:

```
1. Make a code or config change
2. Run the pipeline on a real job (Azure)
3. save-session → sessions/{folder}/
4. compare.ps1 → DASHBOARD.md shows transition delta
5. Read changed T-xx verdicts → diagnose → next change
```

Step 3 is the only observable output of step 2. If step 3 is unreliable:
- Regressions introduced in step 1 are invisible
- Improvements introduced in step 1 produce no trackable signal
- compare.ps1 computes deltas across inconsistent snapshots

**A single bad capture in the middle of a series can make a working change look like a
regression, or conceal a real breakage.** This is why data collection quality is not
polish — it is a prerequisite for the contract system being trustworthy at all.

### 16.3 What "Reliable" Means at the Azure Boundary

Reliable does not mean "the script exits 0." It means:

| Requirement | Why it matters |
|---|---|
| **Idempotent re-run on same job ID** | A failed first run must not prevent a successful second run from producing a clean session folder |
| **Partial ZIP detection** | If the session-snapshot ZIP is missing expected blobs, this must be reported as `partial_capture: true`, not contract-compliant |
| **Empty/zero-byte file detection** | Contract files present at 0 bytes are structurally worse than absent files — they parse as empty JSON, failing silently |
| **Download size sanity check** | A ZIP below a minimum reasonable size (e.g. < 2 KB) should be treated as a failed API response, not a valid empty capture |
| **Clock skew detection** | If `run_finished_at` in session-meta is more than a few seconds in the past, the blobs may still be uploading — log a warning |
| **No silent compatibility-mode escalation** | If contracts are missing AND compatibility mode is off, do not proceed with partial outputs that look complete |

### 16.4 Idempotency Rule

Save-session must be safely re-runnable against the same job ID without corrupting a previous
successful capture.

Behavior required:
- If a session folder for this job already exists and was contract-valid: print a notice and
  do not overwrite. Require an explicit `-Force` flag.
- If a session folder for this job exists but was NOT contract-valid (partial or failed capture):
  allow re-run to replace it. Record `replaced_previous_partial: true` in `human/session-save-meta.json`.
- The `_tmp-{JobId}` working folder is always deleted on both success and failure.

This means an agent or developer can safely run save-session twice on the same job without
needing to inspect the sessions/ folder first.

### 16.5 Failure Modes and Required Responses

| Failure Mode | Current Behavior | Required Behavior |
|---|---|---|
| Download fails (network, auth, 5xx) | Exit non-zero | Exit non-zero + write `human/capture-failed.json` with reason + timestamp |
| ZIP is too small / empty | No check | Detect and exit non-zero with message `capture_too_small` |
| Contract files present but 0 bytes | Reported as parse error | Detect 0-byte files explicitly; report as `empty_contract_file` (distinct from missing or invalid) |
| Contract files missing, CompatibilityMode=false | Exit non-zero | Correct — also write `human/session-save-meta.json` with `contract_status: incomplete` |
| Contract files missing, CompatibilityMode=true | Proceeds | Correct — but must write `partial_capture: true` in `human/session-save-meta.json` |
| hard_fail=true, EnforceHardFail=false | Proceeds silently | Emit a visible `WARNING: hard_fail=true — run is contract-invalid for promotion` to stdout |
| Job still running (blobs still uploading) | No detection | Check `final_phase` in session-meta.json — if not in {done, canceled, error, awaiting_clarification}, log a warning and label session `potentially_incomplete` |

### 16.6 What a Good Capture Looks Like vs. a Degraded Capture

A **complete capture** satisfies all of:
- All 4 required contract files present, non-empty, valid JSON
- `cohesion-report.json.version` matches a supported contract version
- `session-meta.final_phase` is in the terminal state set
- ZIP size is above the minimum sanity threshold
- `human/session-save-meta.json` contains `partial_capture: false`

A **degraded capture** is any session folder where at least one of the above is false.
Degraded captures must not be used as comparison baselines in compare.ps1.

`human/session-save-meta.json` must always be written (even on failure where possible) and
must include a top-level `capture_quality` field: `complete | degraded | failed`.

### 16.7 Pre-Contract Capture (Transitional Guidance)

During the period before the API reliably emits all 4 contract files (Phase 1 of the
adoption plan), every capture is by definition `degraded` at the contract level.

This is acceptable IF:
- `compatibility_mode: true` is explicit in `human/session-save-meta.json`
- The degraded status is visible in DASHBOARD.md (not hidden behind a compatibility pass)
- compare.ps1 is not computing deltas between a degraded and a complete capture — it should
  flag this as an incompatible comparison

Once Phase 1 is complete and 2-3 real runs produce complete captures, compatibility mode
should be disabled and all subsequent captures must meet the complete capture standard.

### 16.9 Reasoning Capture as Iteration Signal

> **Core principle:** A session capture that proves execution happened but not *why the model
> reasoned as it did* is structurally incomplete for context engineering work. The data flow
> transitions (T-01..T-16) tell you whether artifacts moved between stages. They do not tell
> you whether the model *used* the context or *why* it chose the arguments it chose. Without
> reasoning artifacts, the iteration loop degrades to: "change a prompt, re-run, check if
> numbers moved" — no causal signal.

**What a reasoning-complete capture adds beyond a data-complete capture:**

| Stage | Key reasoning artifact | What it answers |
|---|---|---|
| S-01 | `vocab-update-raw-response.json` | Did the model correctly interpret my hints text into vocab, or did it miss ID patterns? |
| S-02 | `probe-round-reasoning.json` | Did the model cite the injected hints as the reason for its arg choices? |
| S-02 | `probe-stop-reasons.json` | Was the model finished probing, or was it cut off by a cap? |
| S-03 | `sentinel-calibration-decisions.json` | Did the model recognize the error codes I described in hints as sentinels? |
| S-04 | `synthesis-input-snapshot.json` | Did synthesis receive all findings, or were some dropped before the call? |
| S-06 | `expert-answer-interpretation.json` | Did the model use the expert answer I provided, or did it retry identical probes? |
| Chat | `chat-tool-reasoning.json` | Did the model cite vocab (id_formats, error_codes) before making tool calls? |

**The causal chain you need for context engineering iteration:**

```
Hints text
  → vocab-update-raw-response.json  (did model extract hints correctly?)
  → probe-user-message-sample.txt   (did hints reach probe user message?)
  → probe-round-reasoning.json      (did model CITE hints in its reasoning?)
  → probe-log.json[arg_sources]     (did model USE hint IDs as args?)
  → probe-stop-reasons.json         (did model probe enough to find the answer?)
  → findings.json                   (did model record what it found?)
  → synthesis-input-snapshot.json   (did findings reach synthesis?)
  → api-reference.md                (did synthesis cover all functions?)
```

Each arrow is a transition. Each artifact in the chain is observable. If you change the hints
text and re-run:
- `arg_source_distribution` (fraction `static_id` vs `fallback_string`) should increase
- `probe-round-reasoning.json` should mention the new ID patterns
- The transition that changed should be identifiable from the delta report

**Minimum reasoning-complete capture definition:**

A capture is considered reasoning-complete (distinct from data-complete) when it additionally contains:
- `evidence/stage-02-probe-loop/probe-round-reasoning.json`
- `evidence/stage-02-probe-loop/probe-stop-reasons.json`
- `evidence/stage-04-synthesis/synthesis-input-snapshot.json`
- `diagnostics/chat-tool-reasoning.json` (when chat stage fires)

Until these are emitted, `human/session-save-meta.json` should include:
```json
"reasoning_capture": "partial"
```

Once all four are present:
```json
"reasoning_capture": "complete"
```

`reasoning_capture: complete` is the prerequisite for T-17..T-23 transitions to evaluate
anything other than `warn`.

### 16.8 Relationship to the Contract Maturity Plan

The contract is only as trustworthy as the data collection that feeds it.

| Contract Phase | Data Collection Requirement |
|---|---|
| Phase 0 — no contract artifacts emitted | All captures degrade gracefully; compatibility mode on by default |
| Phase 1 — contract artifacts emitted | 2-3 complete captures required before treating compare.ps1 output as authoritative |
| Phase 2 — save-session strict mode | All 4 contract files required; hard_fail active; zero partial exports to DASHBOARD.md |
| Phase 3 — CI gate enabled | `capture_quality: complete` required before any CI stage consumes the session |

Moving to Phase 3 without reliable Phase 2 data collection is a false gate — it will
produce spurious blocks or spurious passes, not meaningful signal.

## 14. Runtime Rework Delta (Completed vs Deferred)

Completed in runtime/API phase:
1. Contract artifacts now emitted by API runtime:
- `session-meta.json`
- `stage-index.json`
- `transition-index.json`
- `cohesion-report.json`
2. Transition evaluation implemented for `T-01..T-16` with statuses:
- `pass`, `fail`, `warn`, `partial`, `not_applicable`
3. Deterministic hard gate implemented:
- `hard_fail = true` when any high-severity transition has `status=fail`
4. Edge-case semantics implemented:
- `T-04` checks user prompt path
- `T-11` checks findings + vocab into synthesis context
- `T-12/T-13` become `not_applicable` when answer-gaps path is not triggered
5. Instrumentation added:
- `backfill_result.json` emitted for `T-07`

Deferred by design in this phase:
1. No edits to `scripts/save-session.ps1`
2. No edits to compare scripts
3. No broad non-contract refactors
4. No strict save-session gate enablement yet

## 15. Pre-Integration Plan for 4 Blockers

### B1 — Real-run contract verification

Goal:
- confirm one real explore run and one answer-gaps run both emit valid contract artifacts.

Actions:
1. Run explore-only and answer-gaps-triggered jobs.
2. Validate required files exist and parse.
3. Validate `T-01..T-16` presence and severity/status shape.
4. Re-run one unchanged job to verify deterministic `hard_fail` behavior.

Exit criteria:
- both runs satisfy contract file + determinism checks.

### B2 — Prompt evidence hardening for T-04/T-14/T-15

Goal:
- remove reliance on proxy evidence for prompt-path transitions.

Actions:
1. Persist probe user prompt sample artifact.
2. Persist explicit chat turn-0 system context artifact.
3. Ensure transition evidence arrays reference concrete artifacts.
4. Keep `warn`/`partial` only for truly unobservable states.

Exit criteria:
- T-04/T-14/T-15 evaluate from explicit evidence paths.

### B3 — Canonical layout parity (`evidence/`)

Goal:
- normalize stage evidence to contract-authoritative `evidence/stage-*` layout.

Actions:
1. Add canonical path mapping (with backward-compatible aliases).
2. Update stage-index artifact references to canonical paths.
3. Add tests ensuring stage-index references resolve.

Exit criteria:
- stage-index uses canonical `evidence/` references and snapshot contains those paths.

### B4 — save-session compatibility + strict gate rollout

Goal:
- integrate save-session safely without mutating machine-truth artifacts.

Actions:
1. Add compatibility mode for missing contract files.
2. Add version support map and fail-closed behavior.
3. Add strict gate switch driven by `cohesion-report.json.gates.hard_fail`.
4. Roll out in two runs:
- run A compatibility mode
- run B strict gate mode

Exit criteria:
- save-session operates contract-first with deterministic gate behavior and no machine-truth rewrites.

## 17. Language Architecture — PS Orchestration + Python Intelligence

### 17.1 Why This Split

PowerShell is correct for exactly three things in this workflow:
1. Windows credential chain integration (`Invoke-WebRequest` with system auth)
2. ZIP extraction (`Expand-Archive`)
3. CI-compatible exit codes (PowerShell process exit propagates cleanly into ADO/GitHub Actions)

Everything else — parsing, validation, evaluation, output generation — belongs in Python because:
- AI agents can implement, modify, and test Python without PS expertise
- The contract schema changes only need to be reflected in one language
- `collect_session.py` can be run standalone against any already-extracted folder (critical for debugging)
- Python's `json`, `pathlib`, `hashlib` are significantly more reliable for schema work than PS equivalent

### 17.2 save-session.ps1 — Target State (~30 lines)

```
param(JobId, ApiUrl, Note, SessionsRoot, ApiKey, OutDir, CompatibilityMode, EnforceHardFail)

# 1. Download
Invoke-WebRequest $snapshotUrl -OutFile $zipPath

# 2. Extract
Expand-Archive $zipPath $tempDir

# 3. Resolve folder name (date + commit + note slug)
$sessionDir = Resolve-SessionFolder $SessionsRoot $JobId $Note

# 4. Move extracted contents to final folder
Move-Item $tempDir $sessionDir

# 5. Hand off to Python — all intelligence lives here
$args = @("scripts/collect_session.py", "--session-dir", $sessionDir)
if ($CompatibilityMode) { $args += "--compatibility-mode" }
if ($EnforceHardFail)   { $args += "--enforce-hard-fail" }
python @args

# 6. Relay Python exit code
exit $LASTEXITCODE
```

No JSON parsing. No contract field inspection. No dashboard generation. No hard_fail logic.
All of that is in Python.

### 17.3 collect_session.py — Responsibility Boundary

| Responsibility | collect_session.py | save-session.ps1 |
|---|---|---|
| Download ZIP | No | Yes |
| Extract ZIP | No | Yes |
| Name/move session folder | No | Yes |
| Detect 0-byte contract files | **Yes** | No |
| Parse and validate contract JSON | **Yes** | No |
| Evaluate capture quality | **Yes** | No |
| Write `human/session-save-meta.json` | **Yes** | No |
| Write `human/dashboard-row.json` | **Yes** | No |
| Write `human/summary.md` | **Yes** | No |
| Gate on `hard_fail` | **Yes** | No (relays exit code only) |
| git metadata | No | Yes (pre-handoff) |
| CI exit code | No (via process exit) | Yes (relays LASTEXITCODE) |

### 17.4 collect_session.py — Minimum Interface

```
python scripts/collect_session.py \
  --session-dir sessions/2026-03-21-abc1234-contoso-run3 \
  [--compatibility-mode] \
  [--enforce-hard-fail]
```

Exits 0 on `capture_quality: complete`.
Exits 1 on `capture_quality: degraded` (missing/invalid contracts, or `hard_fail` in gate mode).
Exits 2 on `capture_quality: failed` (download artifact absent, 0-byte files, unrecoverable parse error).

Always writes `human/session-save-meta.json` before exiting (even on exit 2 where possible).

### 17.5 compare_sessions.py — Same Language, Same Parse Path

Once `collect_session.py` exists, `sessions/compare.ps1` should become `scripts/compare_sessions.py`:

```
python scripts/compare_sessions.py --sessions-dir sessions/ [--last N] [--output sessions/DASHBOARD.md]
```

Reads `human/dashboard-row.json` from each session folder. Computes transition deltas. Writes
DASHBOARD.md. No PowerShell JSON parsing in the cross-session comparison path.

This completes the language boundary: PS handles OS, Python handles data. Every file an AI agent
needs to read, write, or modify is Python.

## 18. Solo-Maintainer Data Collection Workflow

This is the recommended operating model for one person. It aligns with the contract and causality
principles while keeping the maintenance footprint small.

### 18.1 Chosen Workflow

Use a two-scenario API-driven loop that matches the same API surface used by the web UI:

1. `explore_only`
2. `answer_gaps_triggered`

For each scenario:
1. Trigger run via the public job APIs used by UI.
2. Capture with `save-session.ps1` (PowerShell orchestration only).
3. Process with `collect_session.py` (all validation/gating intelligence).
4. Review machine-truth first:
- `cohesion-report.json.gates.hard_fail`
- failed high-severity transitions in `transition-index.json`
- `capture_quality` in `human/session-save-meta.json`
5. Re-run one unchanged scenario input and verify deterministic gate behavior.

### 18.2 Why This Was Chosen

1. Lowest operational overhead for a single owner.
- one small scenario set, one collector, one gate signal

2. Fully contract-aligned.
- single schema authority remains in `AI-FIRST-SNAPSHOT-CONTRACT.md`
- transition semantics remain in `CAUSALITY-ARTIFACT-LAYER-PLAN.md`
- this document defines only capture behavior

3. Deterministic and comparable.
- every run produces the same required artifacts
- blocker B1 acceptance can be proven from session artifacts, not logs

4. Future-safe without overbuilding.
- avoids introducing complex orchestration before the core gates are stable

### 18.3 Minimal Automation Cadence

For one person, use:

1. Manual dispatch workflow (`workflow_dispatch`) for on-demand validation before and after major changes.
2. Nightly workflow (`cron`) that runs both scenarios and uploads session artifacts.
3. Promotion block when either:
- `capture_quality != complete`
- new high-severity transition fail appears
- `hard_fail` flips to true on unchanged inputs

This gives enterprise-grade signal quality with solo-maintainer complexity.

### 18.4 Blocker B1 Execution Checklist

To close the open alignment item, run this exactly:

1. Execute one `explore_only` capture.
2. Execute one `answer_gaps_triggered` capture.
3. Verify in both captures:
- all four required contract files exist and parse
- `transition-index.json` contains `T-01..T-16` with severity/status
4. Repeat one unchanged run and verify deterministic `cohesion-report.json.gates.hard_fail`.
5. Record evidence paths in `human/summary.md` and remove the B1 pending note from `ALIGNMENT.md`.

### 18.5 Expansion Path Relative To Project Scope

As scope grows, extend this workflow in stages without changing the core principles:

1. Stage A (solo now): two scenarios, manual + nightly cadence.
2. Stage B (small team): run `explore_only` on PRs, both scenarios nightly.
3. Stage C (multi-component): scenario matrix by component/profile/model.
4. Stage D (release governance): enforce strict release gate on complete captures and high-severity transition stability.

Across all stages, keep these invariants:
- JSON contract is authoritative
- transition IDs and semantics remain stable
- PowerShell remains orchestration-only
- Python remains the only intelligence layer for capture evaluation

## 19. Coordinator Agent Integration (Q14/Q15/Q16/Q17 Requirements)

> **Why this section exists:** Q14 (tournament selection), Q15 (context ablation),
> Q16 (adaptive sentinel calibration), and Q17 (autonomous coordinator agent) each
> depend on save-session producing specific fields that do not yet exist in the
> compact manifest. This section defines exactly what the coordinator needs to read,
> what is currently emitted, what is missing, and what changes are required before
> the Q17 coordinator can operate autonomously.

### 19.1 Coordinator Topology

**One coordinator per component, not one global coordinator.**

A coordinator is scoped to a single component (DLL or target binary). It runs one full
Q17 playbook for that component. It does not coordinate across components simultaneously;
multi-component work is Phase F inside the Q17 playbook, deferred until the first
component reaches ≥10/13 function coverage.

**Agent hierarchy for one coordinator cycle:**

```
run_coordinator.py (1 instance per component)
  └─ run_set_orchestrator.py
        ├─ Pipeline Run 1  (Layer 1 control, profile=baseline)
        ├─ Pipeline Run 2  (Layer 1 control, profile=baseline)
        ├─ Pipeline Run 3  (Layer 1 control, profile=baseline)
        ├─ Pipeline Run 4  (Layer 2 ablation, ablation_variable=prompt_framing, ablation_value=systematic)
        ├─ Pipeline Run 5  (Layer 2 ablation, ablation_variable=vocab_ordering, ablation_value=ids_first)
        └─ Pipeline Run 6  (Layer 2 ablation, ablation_variable=tool_budget, ablation_value=8)
```

Each pipeline run is an existing `explore` API job — not a new agent type. The
coordinator dispatches existing infrastructure (the same jobs that run_ab_parallel.py
currently triggers), adds tagging, and waits for all to complete.

**Ratio:** 1 coordinator → 1 orchestrator → N+M pipeline runs per cycle.
Recommended: N=3 (Layer 1 control), M=3 (Layer 2 ablation) = 6 total per cycle.
The coordinator runs at most one cycle at a time; cycles are sequential, not parallel.

**Why the coordinator is not subdivided further:**
- Each pipeline run already contains multi-stage LLM reasoning (probe → sentinel →
  synthesis → harmonization). The coordinator's job is to read the outputs, not to
  subdivide the reasoning.
- A second coordinator (e.g. a "sentinel sub-coordinator" for Q16) would add
  coordination overhead without adding signal. Q16's adaptive calibration is a
  capability added to the run-set orchestrator, not a separate agent.

### 19.2 What the Coordinator Reads From Each Session

For the coordinator to make a promotion decision autonomously, it must read each session
in one pass without traversing the full artifact tree. This requires a compact manifest
(Q13) that contains ALL of the following fields:

**Group A — Already specified in Q13 (in QUESTIONS.md) and partially emitted:**

| Field | Where emitted | Current status |
|---|---|---|
| `job_id` | `session-meta.json` | ✅ Emitted |
| `component` | `session-meta.json` | ⚠️ Emitted but sometimes `"unknown"` (P1-3 bug) |
| `saved_at` | `human/session-save-meta.json` | ✅ Emitted |
| `commit` | `session-meta.json` | ✅ Emitted |
| `mode`, `model`, `max_rounds`, `max_tool_calls` | `session-meta.json` | ✅ Emitted |
| `contract_valid`, `hard_fail`, `capture_quality` | `cohesion-report.json` + `human/session-save-meta.json` | ✅ Emitted |
| `functions_success`, `functions_error` | `findings.json` aggregate | ✅ Emitted |
| Transition pass/fail/warn counts | `transition-index.json` | ✅ Emitted |

**Group B — Required for Q15 (context ablation) — NOT YET EMITTED:**

| Field | Meaning | Where it must go |
|---|---|---|
| `prompt_profile_id` | Name of the prompt profile used (e.g. `"baseline"`, `"systematic-framing"`, `"ids-first"`) | `session-meta.json` + compact manifest |
| `layer` | `1` (control) or `2` (ablation) | `session-meta.json` + compact manifest |
| `ablation_variable` | Which variable was changed from baseline (e.g. `"prompt_framing"`) — null for Layer 1 | `session-meta.json` + compact manifest |
| `ablation_value` | What the variable was set to (e.g. `"systematic"`) — null for Layer 1 | `session-meta.json` + compact manifest |
| `run_set_id` | UUID shared by all N+M runs in the same coordinator cycle | `session-meta.json` + compact manifest |

Without `prompt_profile_id` + `layer` + `ablation_variable`, the coordinator cannot
determine which run is a control and which is a variant. It cannot compute a delta.
It cannot make a promotion decision. These 5 fields are blockers for Q15 Phase 2.

**Group C — Required for Q16 (adaptive sentinel calibration) — NOT YET EMITTED:**

| Field | Meaning | Where it must go |
|---|---|---|
| `sentinel_table_snapshot` | The sentinel table as it existed at the START of this run's stage boundary | `session-meta.json` or dedicated `sentinel-calibration-state.json` |
| `sentinel_table_delta` | List of newly resolved codes at each stage boundary (empty list if none) | `sentinel-calibration-state.json` in evidence/ |
| `write_unlock_outcome` | `"blocked"`, `"resolved"`, `"not_attempted"` — outcome of write-unlock probe | `session-meta.json` + compact manifest |
| `write_unlock_sentinel` | The sentinel code that was blocking write-unlock (if applicable) | `sentinel-calibration-state.json` |

Without `sentinel_table_delta`, the coordinator cannot tell whether a subsequent stage
benefited from a newly resolved sentinel or was still operating under the original sparse
table. Q16's stage-boundary re-calibration produces no useful signal if the before/after
sentinel tables are not captured.

**Group D — Required for Q17 (coordinator state) — NOT YET EMITTED:**

| Field | Meaning | Where it must go |
|---|---|---|
| `coordinator_cycle` | Which iteration of the Q17 playbook this run belongs to (integer, 1-based) | `session-meta.json` + compact manifest |
| `playbook_step` | Which playbook phase triggered this run (`"A"`, `"B"`, `"C"`, `"D"`) | `session-meta.json` + compact manifest |
| `function_coverage_delta` | `functions_success` minus the current baseline `functions_success` | Computed by coordinator at read time; not emitted by pipeline |
| `union_merge_id` | UUID of the UNION merge artifact this run contributed to | Written by run_set_orchestrator after merge |

`coordinator_cycle` and `playbook_step` allow the coordinator to group runs correctly
when reading across many session folders. Without them, a re-run of the coordinator on
the same component would be unable to distinguish cycle 1 ablation runs from cycle 2
ablation runs.

### 19.3 What Is Currently Readable in One Pass

From the existing session structure, an AI agent can currently determine:

```
✅ Did the job complete without capture failure?         → human/session-save-meta.json:capture_quality
✅ Did any stage hard-fail?                             → cohesion-report.json:hard_fail
✅ Which stage transitions passed / failed / warned?    → transition-index.json (T-01..T-16)
✅ How many functions succeeded vs. errored?            → findings.json aggregate
✅ Which functions succeeded (by name)?                 → findings.json per-record status
✅ What error codes were observed per failing function? → findings.json per-record error_code
✅ Was the run contract-valid?                          → cohesion-report.json:pipeline_verdict
✅ What model / settings were used?                     → session-meta.json
```

From one pass, an AI agent CANNOT currently determine:

```
❌ Was this a control run or an ablation variant?       → prompt_profile_id, layer NOT emitted
❌ What context variable was changed and how?           → ablation_variable, ablation_value NOT emitted
❌ Which batch of N+M runs does this belong to?         → run_set_id NOT emitted
❌ Did sentinel calibration improve between stages?     → sentinel_table_delta NOT emitted
❌ Was write-unlock blocked or resolved and by what?    → write_unlock_outcome NOT emitted
❌ Which coordinator cycle does this run belong to?     → coordinator_cycle NOT emitted
❌ What was the function coverage delta vs. baseline?   → not computed by pipeline; coordinator must compute
```

The current save-session output is sufficient for:
- Human review of individual run outcomes
- Transition pass/fail tracking
- A/B comparison between two manually-selected runs

The current save-session output is **NOT sufficient** for:
- Autonomous coordinator promotion decisions (missing Groups B/C/D)
- Causal attribution (which prompt change improved which gate)
- Sentinel table evolution tracking across stage boundaries

### 19.4 What Needs to Change and Where

**Changes required in the API pipeline (not save-session itself):**

1. `api/explore.py` or `api/worker.py` — accept `prompt_profile_id`, `layer`,
   `ablation_variable`, `ablation_value`, `run_set_id`, `coordinator_cycle`,
   `playbook_step` as job parameters and write them to `session-meta.json`.
   - These are pass-through fields; the pipeline does not act on them.
   - They are set by `run_set_orchestrator.py` when it dispatches each run.
   - Estimated effort: ~1 hour (add fields to job request schema + persist to session-meta).

2. `api/explore_phases.py` — emit `sentinel-calibration-state.json` after each
   stage boundary (Phase 0.5 initial + Stage 1/2/3 boundaries).
   - Fields: `stage`, `sentinel_table_before`, `sentinel_table_after`, `resolved_delta`.
   - Estimated effort: ~2 hours.

3. `api/explore_phases.py` — emit `write_unlock_outcome` and `write_unlock_sentinel`
   to `session-meta.json` at write-unlock probe time.
   - Estimated effort: ~30 minutes.

**Changes required in save-session / collect_session.py:**

4. `scripts/collect_session.py` — when writing `human/dashboard-row.json`, include
   all Group B/C/D fields by reading from `session-meta.json`.
   - These fields flow through: pipeline writes them to `session-meta.json`;
     `collect_session.py` copies them into the compact manifest.
   - No parsing intelligence required — pure pass-through.
   - Estimated effort: ~30 minutes.

5. `human/dashboard-row.json` schema — add all Group B/C/D fields to the Q13 compact
   manifest spec in `docs/AI-FIRST-SNAPSHOT-CONTRACT.md`.
   - This is a schema version bump (v2 → v3 or a minor extension).
   - Estimated effort: ~1 hour to update the contract doc.

**No changes required in:**
- `scripts/save-session.ps1` — pure orchestration, receives no new parameters
- `sessions/compare.ps1` / `compare_sessions.py` — reads `human/dashboard-row.json`;
  if the compact manifest is extended, compare automatically gains the new fields
- `api/explore_prompts.py` — prompt profiles are a separate concern from session tagging

### 19.5 The Enrichment Feed: How Coordinator Output Loops Back Into the Pipeline

The coordinator enriches the pipeline in two directions:

**Direction 1 — Accumulated schema as next-stage seed (Q15 Phase 4):**
```
Coordinator cycle N:
  N+M runs → UNION merger → accumulated-schema-cycle-N.json
  ↓
Coordinator cycle N+1:
  run_set_orchestrator passes accumulated-schema to each new run as seeded_findings
  Each run starts from the richest possible knowledge base, not cold
```

This is the "stage-by-stage knowledge pipeline" from Q15. Currently, every run starts
cold. Once the UNION merger is wired in, each cycle's runs start from the union of all
prior cycle discoveries.

**Direction 2 — Promoted baseline as new Q15 Layer 1 control (Q17 promotion):**
```
If cycle N variant improved ≥1 gate without regression:
  Promoted variant's prompt_profile_id becomes new Layer 1 baseline
  coordinator-state.json updated: promoted_profile = "systematic-framing"
  Next cycle's N control runs use the promoted profile
  The improvement is compounded, not discarded
```

The coordinator does NOT modify any `api/*.py` files autonomously. The promotion is:
- A `coordinator-state.json` record noting what was promoted
- A `FINDINGS.md` batch entry documenting the delta
- A `sessions/_runs/baseline/` update (move the promoted run-set folder in)

A human reads the coordinator's structured report and decides whether to commit the
corresponding prompt profile change as a code edit. The coordinator's autonomous
authority ends at "record and report"; it does not extend to "edit code."

**Direction 3 — Sentinel table as cumulative session-meta field (Q16):**
```
Stage 0: sentinel_table = {hardcoded defaults + vocab error_codes}
Stage 1 boundary: scan all N+M probe logs → newly_resolved = [0xFFFFFFF9, ...]
Updated sentinel_table injected into Stage 2 runs as seeded_sentinels
Stage 2 boundary: scan again → any more newly_resolved codes
...and so on
```

This is currently not implemented. The current sentinel table is built once (Phase 0.5)
and frozen for the rest of the run. Q16's adaptive calibration requires the sentinel
table to flow through the save-session manifest so the coordinator can see whether
a given stage boundary produced new sentinel resolutions.

### 19.6 Are These Questions Clearly Laid Out in QUESTIONS.md?

**What is clearly covered:**
- Q13: compact manifest fields for single-run AI review — clear, specified to field level
- Q14: tournament selection logic for per-function winner selection — clear
- Q15: N+M run structure, Layer 1/2 distinction, UNION merger, promotion decision rule — clear
- Q16: stage-boundary re-calibration, write-unlock re-gating, cascade exploitation — clear
- Q17: coordinator operating contract, playbook phases A–F, stopping conditions, report format — clear

**What is NOT yet explicitly covered in QUESTIONS.md (gaps):**

1. The specific session-meta.json field additions required (Groups B/C/D above).
   Q13 lists what the compact manifest should contain but does not specify the ablation
   tagging fields that Q15/Q16/Q17 require. These are implied by Q15 but not named.

2. The coordinator topology (1 per component, N=3 M=3 ratio, sequential cycles).
   Q17 describes the coordinator's behavior but does not specify how many coordinators
   run concurrently or what the N+M ratio is per cycle. This is intentional — it is
   left as a runtime decision — but it should be documented as a parameter here.

3. The enrichment feed (accumulated schema as next-cycle seed, sentinel table as
   cumulative field). Q15 §4 in QUESTIONS.md describes the stage-by-stage pipeline
   but does not describe how save-session captures the state needed to resume from
   the correct point after an interruption.

4. Whether `reasoning_capture: complete` (§16.9 above) is required before a
   coordinator promotion decision is valid. Currently in QUESTIONS.md, Q15 promotion
   criteria are based on gate outcomes (T-xx pass/fail). If the coordinator promotes
   a variant that improved T-05 but the promotion was based on a reasoning-incomplete
   capture (a capture where the model's reasoning chain is opaque), the improvement
   may not be reproducible. This is the boundary between Q15 and Q13/§16.9 that no
   question currently makes explicit.

**Recommendation:** The four gaps above do not require new QUESTIONS.md entries.
They are implementation preconditions that belong in this document as amendments to
§16.9 (reasoning capture) and in `docs/AI-FIRST-SNAPSHOT-CONTRACT.md` as a field
table extension. No new open questions — these are engineering decisions with clear
answers, not architectural debates.

### 19.7 Minimum Viable Coordinator Read Configuration

For the coordinator to start a cycle autonomously, it must be able to read one file
per session and determine everything needed for a promotion decision. The target
`human/dashboard-row.json` shape for Q14/Q15/Q16/Q17 readiness:

```json
{
  "schema_version": "3.0",
  "job_id": "...",
  "component": "contoso_cs",
  "saved_at": "2026-03-22T...",
  "commit": "11f289d",
  "model": "gpt-4o",
  "mode": "dev",
  "max_rounds": 6,
  "max_tool_calls": 16,

  "contract_valid": true,
  "hard_fail": false,
  "capture_quality": "complete",
  "reasoning_capture": "complete",

  "functions_success": 4,
  "functions_error": 9,
  "functions_success_names": ["CS_GetVersion", "CS_GetDiagnostics", "CS_Initialize", "CS_CalculateInterest"],
  "functions_error_names": ["CS_GetAccountBalance", "CS_LookupCustomer", "..."],

  "gate_pass": 10,
  "gate_fail": 2,
  "gate_warn": 4,
  "gate_partial": 0,

  "prompt_profile_id": "baseline",
  "layer": 1,
  "ablation_variable": null,
  "ablation_value": null,
  "run_set_id": "runset-2026-03-22-abc123",
  "coordinator_cycle": 1,
  "playbook_step": "A",

  "write_unlock_outcome": "blocked",
  "write_unlock_sentinel": "0xFFFFFFFB",

  "sentinel_table_size": 3,
  "sentinel_new_codes_this_run": 0
}
```

This is approximately 35 fields. An AI agent or coordinator can read this one file and
know:
1. Whether this capture is trustworthy (groups: contract + capture + reasoning)
2. What the run produced (groups: functions, gates)
3. What context it ran under (groups: model, profile, ablation)
4. Where it fits in the coordinator's cycle (groups: run_set, cycle, playbook_step)
5. Whether sentinels or write-unlock are blocking progress (groups: sentinels)

All five groups are necessary for autonomous promotion. Missing any one group forces
the coordinator back to manual (human reads the full artifact tree).

**Current status vs. target:**
- Groups 1–2: ✅ mostly emitted today (modulo P1-3 component bug)
- Group 3 (model settings): ✅ emitted in session-meta.json, needs pass-through to dashboard-row
- Group 4 (ablation/cycle): ❌ not emitted — requires Q15 Phase 2 job parameter extension
- Group 5 (sentinels): ❌ not emitted — requires Q16 sentinel-calibration-state.json emitter
