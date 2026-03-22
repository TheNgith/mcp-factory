# Phase Gate Checklist

Date: 2026-03-21
Purpose: operational checklist for current MVP transition automation loops

## Gate 1: Discovery Satisfaction

Artifacts:
- leg-a/discovery-satisfaction.json
- leg-b/discovery-satisfaction.json
- run_root/discovery-satisfaction.json

Pass criteria (current defaults):
- finding_count >= 5
- resolved_fraction >= 0.5
- max_gaps threshold respected (if configured)

Fail indicators:
- finding_count = 0 repeatedly
- resolved_fraction missing or below threshold

Action on fail:
- do not skip discovery
- tune hints/context first
- retry one variable family at a time

## Gate 2: Transition Readiness

Artifact:
- run_root/transition-readiness.json

Target transitions:
- T-04
- T-05
- T-14
- T-15

Pass criteria:
- both legs pass session checks
- deterministic requirement met for A/B
- no non-pass target transitions

Fail indicators:
- reasons contains leg_a_failed, leg_b_failed, ab_not_deterministic
- reasons contains non_pass_transitions

Action on fail:
- keep same phase and adjust one variable
- avoid multi-family changes in one batch

## Gate 3: Determinism Stability

Evidence:
- ab-compare.json differences list
- transition-readiness.json deterministic field

Pass criteria:
- deterministic true for consecutive control runs
- differences trend down over successive batches

Fail indicators:
- repeated cross-leg function_outcome mismatches

Action on fail:
- reduce concurrency for diagnosis runs
- keep model fixed while stabilizing

## Gate 4: Operational Health

Checks:
- exactly one autopilot loop process active
- windows bridge available
- no repeated save-session strict contract failures

Commands:

powershell
Get-CimInstance Win32_Process |
  Where-Object { $_.Name -match 'powershell' -and $_.CommandLine -match 'autopilot_transition_loop.ps1' } |
  Select-Object ProcessId, CommandLine

powershell
Get-Content logs/autopilot-transition.log -Tail 100

## Batch Review Card

Capture per matrix batch:
- batch id
- model
- max_cases
- max_workers
- cases passed
- discovery gate pass/fail
- transition gate pass/fail
- determinism pass/fail
- unresolved function list delta

## Legacy Mapping

These legacy docs remain useful as reference but not source of truth for current operations:
- docs/PIPELINE-COHESION.md: historical root-cause analysis
- docs/PIPELINE-DIAGNOSTIC-CHECKLIST.md: older checks and scripts

Current source of truth for operations:
- docs/architecture/AUTOPILOT-OPERATIONS-RUNBOOK.md
- docs/architecture/PHASE-GATE-CHECKLIST.md

---

## Post-Session Diagnostic Script

> Migrated from docs/PIPELINE-DIAGNOSTIC-CHECKLIST.md (now deleted).
> Run from the session folder after save-session completes to verify stage-to-stage cohesion.
> Each check targets a specific inter-stage handoff. Failure = which layer is broken.

```powershell
# Run from repo root:  .\docs\architecture\run-diagnostics.ps1 -SessionDir sessions\<folder>
param([string]$SessionDir)

$pass = 0; $fail = 0; $warn = 0

# CHECK 1: Schema evolution — did enrichment change the schema?
$evo = Get-Content "$SessionDir\schema_evolution.json" -Raw -ErrorAction SilentlyContinue | ConvertFrom-Json
$anyChange = if ($evo) { ($evo.deltas | Where-Object { $_.changed -eq $true }).Count } else { -1 }
if ($anyChange -gt 0) {
    Write-Host "OK  SCHEMA: $anyChange stage(s) changed schema" -ForegroundColor Green; $pass++
} elseif ($anyChange -eq 0) {
    Write-Host "FAIL SCHEMA: Zero schema evolution across $($evo.checkpoint_count) checkpoints - enrichment broken (D-3 regression?)" -ForegroundColor Red; $fail++
} else {
    Write-Host "WARN SCHEMA: schema_evolution.json not found" -ForegroundColor Yellow; $warn++
}

# CHECK 2: Param enrichment — are params still raw Ghidra names?
$invMap = Get-Content "$SessionDir\artifacts\invocables_map.json" -Raw -ErrorAction SilentlyContinue | ConvertFrom-Json
if ($invMap) {
    $rawCount = 0; $totalParams = 0
    foreach ($prop in $invMap.PSObject.Properties) {
        $fn = $prop.Value
        if ($fn.parameters) { foreach ($p in $fn.parameters) { $totalParams++; if ($p.name -match '^param_\d+$') { $rawCount++ } } }
    }
    $enrichedPct = if ($totalParams -gt 0) { [math]::Round((1 - $rawCount/$totalParams) * 100) } else { 0 }
    if ($enrichedPct -ge 50) { Write-Host "OK  PARAMS: $enrichedPct% semantic ($($totalParams-$rawCount)/$totalParams)" -ForegroundColor Green; $pass++ }
    elseif ($enrichedPct -ge 20) { Write-Host "WARN PARAMS: $enrichedPct% semantic - partial" -ForegroundColor Yellow; $warn++ }
    else { Write-Host "FAIL PARAMS: $enrichedPct% semantic - enrichment not reaching schema" -ForegroundColor Red; $fail++ }
} else { Write-Host "WARN PARAMS: invocables_map.json not found" -ForegroundColor Yellow; $warn++ }

# CHECK 3: Findings consistency — duplicates and contradictions
$findings = Get-Content "$SessionDir\artifacts\findings.json" -Raw -ErrorAction SilentlyContinue | ConvertFrom-Json
if ($findings) {
    $fnGroups = $findings | Group-Object { $_.function }
    $contradictions = 0
    foreach ($grp in $fnGroups) {
        $statuses = $grp.Group | Select-Object -ExpandProperty status -Unique
        if (($statuses -contains "success") -and ($statuses -contains "error")) { $contradictions++ }
    }
    if ($contradictions -eq 0) { Write-Host "OK  FINDINGS: No contradictory statuses" -ForegroundColor Green; $pass++ }
    else { Write-Host "WARN FINDINGS: $contradictions function(s) have both success and error entries (D-2 — deduplicate to latest)" -ForegroundColor Yellow; $warn++ }
} else { Write-Host "WARN FINDINGS: findings.json not found" -ForegroundColor Yellow; $warn++ }

# CHECK 4: Mini-session enrichment — any "not found in job" errors?
$miniPath = "$SessionDir\mini_session_transcript.txt"
if (Test-Path $miniPath) {
    $miniText = Get-Content $miniPath -Raw
    $notFoundCount = ([regex]::Matches($miniText, "not found in job")).Count
    if ($notFoundCount -eq 0) { Write-Host "OK  MINI-SESSION: All enrich_invocable calls succeeded" -ForegroundColor Green; $pass++ }
    else { Write-Host "FAIL MINI-SESSION: $notFoundCount 'not found in job' errors (D-1 regression - _register_invocables missing)" -ForegroundColor Red; $fail++ }
} else { Write-Host "INFO MINI-SESSION: No transcript (answer-gaps not submitted)" -ForegroundColor Gray }

# CHECK 5: Vocab→schema bridge — vocab rich but schema bare?
$vocab = Get-Content "$SessionDir\artifacts\vocab.json" -Raw -ErrorAction SilentlyContinue | ConvertFrom-Json
if ($vocab) {
    $idFormats = $vocab.id_formats; $errorCodes = $vocab.error_codes
    $vocabRich = ($idFormats -and $idFormats.Count -gt 0) -and ($errorCodes -and $errorCodes.PSObject.Properties.Count -gt 0)
    if ($vocabRich -and $enrichedPct -ge 30) { Write-Host "OK  VOCAB-SCHEMA: Vocab rich + schema enriched" -ForegroundColor Green; $pass++ }
    elseif ($vocabRich -and $enrichedPct -lt 30) { Write-Host "FAIL VOCAB-SCHEMA: Vocab rich but schema bare (D-4 symptom — check D-1/D-3)" -ForegroundColor Red; $fail++ }
    else { Write-Host "WARN VOCAB-SCHEMA: Vocab itself thin — probe quality issue (Q-1/Q-4)" -ForegroundColor Yellow; $warn++ }
} else { Write-Host "WARN VOCAB-SCHEMA: vocab.json not found" -ForegroundColor Yellow; $warn++ }

# CHECK 6: Sentinel coverage — calibrated vs known from hints
$sentCal = Get-Content "$SessionDir\sentinel_calibration.json" -Raw -ErrorAction SilentlyContinue | ConvertFrom-Json
if ($vocab -and $sentCal -and $sentCal.meanings) {
    $knownFromVocab = if ($vocab.error_codes) { $vocab.error_codes.PSObject.Properties.Name } else { @() }
    $calibrated = $sentCal.meanings.PSObject.Properties.Name
    $missing = $knownFromVocab | Where-Object { $_ -notin $calibrated }
    if ($missing.Count -eq 0) { Write-Host "OK  SENTINELS: All known error codes calibrated" -ForegroundColor Green; $pass++ }
    else { Write-Host "WARN SENTINELS: $($missing.Count) code(s) not calibrated: $($missing -join ', ') (Q-1)" -ForegroundColor Yellow; $warn++ }
} else { Write-Host "WARN SENTINELS: No calibration data" -ForegroundColor Yellow; $warn++ }

# CHECK 7: Gap resolution effectiveness
$grlPath = "$SessionDir\gap_resolution_log.json"
if (Test-Path $grlPath) {
    $grl = Get-Content $grlPath -Raw | ConvertFrom-Json
    $grlLatest = @{}; foreach ($entry in $grl) { $grlLatest[$entry.function] = $entry }
    $resolved = ($grlLatest.Values | Where-Object { $_.status -eq "success" }).Count
    $total = $grlLatest.Count
    if ($resolved -gt 0) { Write-Host "OK  GAP-RESOLVE: $resolved/$total functions resolved" -ForegroundColor Green; $pass++ }
    else { Write-Host "WARN GAP-RESOLVE: 0/$total resolved — check probe quality or structural ceilings" -ForegroundColor Yellow; $warn++ }
} else { Write-Host "INFO GAP-RESOLVE: No gap_resolution_log.json" -ForegroundColor Gray }

Write-Host ""
Write-Host "Pipeline Health: $pass passed  $warn warnings  $fail failures" -ForegroundColor $(if ($fail -gt 0) { "Red" } elseif ($warn -gt 0) { "Yellow" } else { "Green" })
if ($fail -gt 0) { Write-Host "Layer 1 (plumbing) issue detected — fix before interpreting probe results" -ForegroundColor Red }
elseif ($warn -gt 0) { Write-Host "Layer 2 (probe quality) likely — see docs/architecture/CAUSALITY-ARTIFACT-LAYER-PLAN.md" -ForegroundColor Yellow }
else { Write-Host "All inter-stage handoffs healthy" -ForegroundColor Green }
```

---

## Manual Checks (when automated checks pass but results look wrong)

### A. Enrichment content quality
Open `invocables_map.json`, spot-check 3 functions with successful findings:
- [ ] `description` is a real sentence, not empty or raw Ghidra boilerplate
- [ ] At least one param renamed from `param_N` to a semantic name
- [ ] `criticality` and `depends_on` populated for write functions

### B. Findings→chat injection
Open `diagnostics/chat-system-context-turn0.txt` and search for `KNOWN WORKING PATTERNS`:
- [ ] Section exists
- [ ] Lists only the LATEST status per function (no contradictory duplicates)
- [ ] Working calls include actual argument values (not `null`)

### C. Vocab completeness
Open `human/collect-session-result.json` or `cohesion-report.json`:
- [ ] `transition_pass` ≥ 9 (most transitions passing)
- [ ] `hard_fail: false`
- [ ] `pipeline_verdict` is `OK` not `FROZEN` or `DEGRADED`

### D. Cross-stage timestamp sanity
Compare timestamps in order: `session-meta.json` run_started_at → findings.json recorded_at entries → mini_session_transcript.txt tool-call sequence → gap_resolution_log.json entries.
If gap_resolution timestamps are BEFORE mini-session findings timestamps, the log was snapshotted too early (D-2 historical pattern).

### E. State mutation detection
If a write function succeeds early but fails late, check whether an earlier probe drained a balance or consumed a one-time token:
- Search `diagnostics/probe-log.json` for the write function
- Compare return values across probe sequence (0 → 0xFFFFFFFB = state mutation, not a bug)
- If confirmed: state mutation is the cause; function is healthy (Q-5)

---

## Diagnostic Failure Lookup

| Check fails | Layer | Root cause |
|-------------|-------|------------|
| Schema zero evolution | L1 | D-3 regression: `_register_invocables` missing in `explore.py` |
| "not found in job" in mini-session | L1 | D-1 regression: `_register_invocables` missing in `explore_gap.py` |
| Params still `param_N` despite successful probes | L1 | D-5 regression or D-1/D-3 cascade |
| Contradictory findings per function | L1 | D-2: chat dedup not working |
| Vocab rich but schema bare | L1 | D-4 symptom — check D-1/D-3 first |
| Sentinels missing known codes | L2 | Q-1: calibration phase not seeding from `vocab["error_codes"]` |
| Many access violations | L3 | C-1: output buffer params not allocated in executor |
| Write functions always denied late in session | L2 | Q-5: state mutation from earlier probes |
| Mini-session infinite loop | Operational | FW-1: no `max_mini_session_rounds` ceiling |
