# Pipeline Diagnostic Checklist

> Run after every `save-session.ps1` to verify stage-to-stage cohesion.
> Each check targets a specific inter-stage handoff point.
> A failure at any check indicates which layer (plumbing / probe quality / structural) is broken.

---

## Quick automated checks (add to save-session.ps1 or run manually)

```powershell
# Run from the session folder after save-session.ps1 completes
param([string]$SessionDir)

$pass = 0; $fail = 0; $warn = 0

# ── CHECK 1: Schema evolution — did enrichment do anything? ──
$evo = Get-Content "$SessionDir\schema_evolution.json" -Raw | ConvertFrom-Json
$anyChange = ($evo.deltas | Where-Object { $_.changed -eq $true }).Count
if ($anyChange -gt 0) {
    Write-Host "✅ SCHEMA: $anyChange stage(s) changed schema" -ForegroundColor Green; $pass++
} else {
    Write-Host "❌ SCHEMA: Zero schema evolution across $($evo.checkpoint_count) checkpoints — enrichment likely broken" -ForegroundColor Red; $fail++
}

# ── CHECK 2: Param enrichment — are params still raw Ghidra names? ──
$invMap = Get-Content "$SessionDir\artifacts\invocables_map.json" -Raw | ConvertFrom-Json
$rawCount = 0; $totalParams = 0
foreach ($prop in $invMap.PSObject.Properties) {
    $fn = $prop.Value
    if ($fn.parameters) {
        foreach ($p in $fn.parameters) {
            $totalParams++
            if ($p.name -match '^param_\d+$') { $rawCount++ }
        }
    }
}
$enrichedPct = if ($totalParams -gt 0) { [math]::Round((1 - $rawCount/$totalParams) * 100) } else { 0 }
if ($enrichedPct -ge 50) {
    Write-Host "✅ PARAMS: $enrichedPct% have semantic names ($($totalParams - $rawCount)/$totalParams)" -ForegroundColor Green; $pass++
} elseif ($enrichedPct -ge 20) {
    Write-Host "⚠️  PARAMS: $enrichedPct% have semantic names — enrichment partially working" -ForegroundColor Yellow; $warn++
} else {
    Write-Host "❌ PARAMS: $enrichedPct% have semantic names — enrichment not reaching schema" -ForegroundColor Red; $fail++
}

# ── CHECK 3: Findings consistency — duplicates and contradictions ──
$findings = Get-Content "$SessionDir\artifacts\findings.json" -Raw | ConvertFrom-Json
$fnGroups = $findings | Group-Object { $_.function }
$contradictions = 0
foreach ($grp in $fnGroups) {
    $statuses = $grp.Group | Select-Object -ExpandProperty status -Unique
    if ($statuses.Count -gt 1 -and ($statuses -contains "success") -and ($statuses -contains "error")) {
        $contradictions++
    }
}
if ($contradictions -eq 0) {
    Write-Host "✅ FINDINGS: No contradictory statuses per function" -ForegroundColor Green; $pass++
} else {
    Write-Host "⚠️  FINDINGS: $contradictions function(s) have both 'success' and 'error' entries — deduplicate to latest" -ForegroundColor Yellow; $warn++
}

# ── CHECK 4: Mini-session enrichment — any "not found in job" errors? ──
$miniPath = "$SessionDir\mini_session_transcript.txt"
if (Test-Path $miniPath) {
    $miniText = Get-Content $miniPath -Raw
    $notFoundCount = ([regex]::Matches($miniText, "not found in job")).Count
    if ($notFoundCount -eq 0) {
        Write-Host "✅ MINI-SESSION: All enrich_invocable calls succeeded" -ForegroundColor Green; $pass++
    } else {
        Write-Host "❌ MINI-SESSION: $notFoundCount 'not found in job' errors — _register_invocables missing" -ForegroundColor Red; $fail++
    }
} else {
    Write-Host "── MINI-SESSION: No mini_session_transcript.txt (gap answers not submitted?)" -ForegroundColor Gray
}

# ── CHECK 5: Vocab→schema bridge — do enriched params use vocab knowledge? ──
$vocab = Get-Content "$SessionDir\artifacts\vocab.json" -Raw | ConvertFrom-Json
$idFormats = $vocab.id_formats
$errorCodes = $vocab.error_codes
$vocabRich = ($idFormats -and $idFormats.Count -gt 0) -and ($errorCodes -and $errorCodes.PSObject.Properties.Count -gt 0)
if ($vocabRich -and $enrichedPct -ge 30) {
    Write-Host "✅ VOCAB→SCHEMA: Vocab has content and schema shows enrichment" -ForegroundColor Green; $pass++
} elseif ($vocabRich -and $enrichedPct -lt 30) {
    Write-Host "❌ VOCAB→SCHEMA: Vocab is rich but schema has no enrichment — bridge broken" -ForegroundColor Red; $fail++
} else {
    Write-Host "⚠️  VOCAB→SCHEMA: Vocab itself is thin — probe quality issue" -ForegroundColor Yellow; $warn++
}

# ── CHECK 6: Sentinel coverage — calibrated vs known from hints ──
$sentCal = Get-Content "$SessionDir\sentinel_calibration.json" -Raw -ErrorAction SilentlyContinue | ConvertFrom-Json
$knownFromVocab = @()
if ($errorCodes) { $knownFromVocab = $errorCodes.PSObject.Properties.Name }
if ($sentCal -and $sentCal.meanings) {
    $calibrated = $sentCal.meanings.PSObject.Properties.Name
    $missing = $knownFromVocab | Where-Object { $_ -notin $calibrated }
    if ($missing.Count -eq 0) {
        Write-Host "✅ SENTINELS: All known error codes were calibrated" -ForegroundColor Green; $pass++
    } else {
        Write-Host "⚠️  SENTINELS: $($missing.Count) known code(s) not calibrated: $($missing -join ', ')" -ForegroundColor Yellow; $warn++
    }
} else {
    Write-Host "⚠️  SENTINELS: No calibration data" -ForegroundColor Yellow; $warn++
}

# ── CHECK 7: Gap resolution effectiveness ──
$grlPath = "$SessionDir\gap_resolution_log.json"
if (Test-Path $grlPath) {
    $grl = Get-Content $grlPath -Raw | ConvertFrom-Json
    $grlLatest = @{}
    foreach ($entry in $grl) {
        $grlLatest[$entry.function] = $entry
    }
    $resolved = ($grlLatest.Values | Where-Object { $_.status -eq "success" }).Count
    $total = $grlLatest.Count
    if ($resolved -gt 0) {
        Write-Host "✅ GAP-RESOLUTION: $resolved/$total targeted functions resolved" -ForegroundColor Green; $pass++
    } else {
        Write-Host "⚠️  GAP-RESOLUTION: 0/$total targeted functions resolved — answers may not help without probe quality improvements" -ForegroundColor Yellow; $warn++
    }
} else {
    Write-Host "── GAP-RESOLUTION: No gap_resolution_log.json" -ForegroundColor Gray
}

# ── SUMMARY ──
Write-Host ""
Write-Host "Pipeline Health: $pass passed, $warn warnings, $fail failures" -ForegroundColor $(if ($fail -gt 0) { "Red" } elseif ($warn -gt 0) { "Yellow" } else { "Green" })
if ($fail -gt 0) {
    Write-Host "Layer 1 (plumbing) issues detected — fix before interpreting probe results" -ForegroundColor Red
} elseif ($warn -gt 0) {
    Write-Host "Layer 2 (probe quality) issues likely — see PIPELINE-COHESION.md for diagnosis" -ForegroundColor Yellow
} else {
    Write-Host "All inter-stage handoffs healthy" -ForegroundColor Green
}
```

---

## Manual checks (when automated checks pass but results still look wrong)

### A. Enrichment content quality

Open `invocables_map.json` and spot-check 3 functions that had successful findings:

- [ ] `description` field is a real sentence, not empty or "Recovered by Ghidra"
- [ ] At least one param renamed from `param_N` to a semantic name
- [ ] `criticality` and `depends_on` are populated for write functions

### B. Findings→chat injection

Open `model_context.txt` and search for `KNOWN WORKING PATTERNS`:

- [ ] Section exists
- [ ] Lists only the LATEST status per function (not duplicated entries)
- [ ] Working calls include actual argument values (not `null`)

### C. Vocab completeness

Open `vocab_coverage.json`:

- [ ] `coverage_score` ≥ 0.8 (most error codes were injected into model context)
- [ ] `error_codes_missing` is empty
- [ ] `id_formats_injected` is true

### D. Cross-stage timestamp sanity

Compare timestamps in chronological order:

1. `session-meta.json` → `created_at` (job started)
2. `findings.json` entries → `recorded_at` (probes happened)
3. `mini_session_transcript.txt` → tool call sequence (gap answers processed)
4. `gap_resolution_log.json` → entries (resolution snapshot)

If gap_resolution timestamps are BEFORE mini-session findings timestamps,
the log was snapshotted too early (known issue with D-2).

### E. State mutation detection

If a write function (payment, refund, redeem) succeeds early in discovery but
fails later, check whether an earlier probe drained the account:

- Search `explore_probe_log.json` for the write function name
- Check if return values change from `0` → `0xFFFFFFFB` across probes
- If so, state mutation is the cause, not a function bug

---

## What each failure means

| Check fails | Layer | What to look at |
|---|---|---|
| Schema zero evolution | L1 | D-1/D-3: `_register_invocables` missing |
| "not found in job" in mini-session | L1 | D-1: `explore_gap.py` registration |
| Params still `param_N` despite successful probes | L1 | D-1/D-3: enrichment not reaching schema |
| Contradictory findings | L1 | D-2: append-only without dedup |
| Vocab rich but schema bare | L1 | D-4: symptom of D-1/D-3 |
| Sentinels missing known codes | L2 | Q-1: calibration phase not probing enough |
| Many access violations | L3 | C-1: output buffer params not allocated |
| Write functions always denied | L2/L3 | Q-5 (state mutation) or C-2 (crypto) |

---

## Run History — Checklist Results

### Run 3cc5d9b (job 22204c7b) — 2026-03-20

**Config:** dev profile, floor=3, max_rounds=2, max_tool_calls=3, gap=✓, clarify=□, deterministic_fallback=✓  
**Previous run for comparison:** 8211c70 (job 1259f0e5)

| Check | Result | Detail |
|-------|--------|--------|
| CHECK 1: Schema evolution | ✅ PASS | 3 stages changed (enrichment, discovery, gap-resolution) |
| CHECK 2: Param enrichment | ❌ FAIL | 28/30 params still `param_N` — 0% semantic names |
| CHECK 3: Findings consistency | ⚠️ WARN | Contradictions exist in raw findings (success+error per fn), but chat dedup (COH-3) filters to latest |
| CHECK 4: Mini-session enrichment | ── SKIP | No mini_session_transcript.txt (clarification disabled, no user gap answers submitted) |
| CHECK 5: Vocab→schema bridge | ❌ FAIL | Vocab is rich (id_formats + error_codes populated) but params still `param_N` |
| CHECK 6: Sentinel coverage | ⚠️ WARN | 2/4 known codes calibrated. 0xFFFFFFFB and 0xFFFFFFFC from hints not auto-calibrated |
| CHECK 7: Gap resolution effectiveness | ── N/A | No gap_resolution_log.json — auto gap resolution ran but doesn't write a log (see D-6) |

**Summary: 1 pass, 2 warnings, 2 failures, 2 skipped**

#### What improved vs previous run (8211c70)

- ✅ Schema evolution now works at all stages (Phase A fixes COH-1/COH-2)
- ✅ Descriptions are enriched with probe findings (not raw Ghidra decompiled C)
- ✅ Probe depth improved: 60 probes / 13 functions = avg 4.6/fn (was ~2.5)
- ✅ Findings dedup in chat context working (COH-3/COH-4)
- ✅ CS_GetOrderStatus gained (was error, now success)
- ✅ Gap resolution produces schema changes (checkpoint 05 ≠ 04)

#### What still needs work

- ❌ **D-5: Param names never renamed** — The single biggest schema quality gap. `enrich_invocable` writes better descriptions but the JSON keys remain `param_1`, `param_2`, etc. LLM and downstream consumers see generic names. This is the #1 blocker for param enrichment CHECK 2.
- ❌ **D-6: Auto gap-resolution log missing** — `_attempt_gap_resolution` doesn't write `gap_resolution_log.json`, only the user-answer path does. No observability into what the auto gap pass actually tried/achieved.
- ❌ **D-7: gap_count misleading** — Shows 0 despite 3 functions still in error. Counts clarification questions, not actual unresolved functions.
- ⚠️ **Q-6: Cap profile sensitivity** — dev profile (max 3 tool calls) regresses CS_UnlockAccount and CS_ProcessRefund vs deploy (max 10). Write functions with multi-step init sequences need higher tool call caps.
- ⚠️ **Q-1: Sentinel calibration incomplete** — Only auto-calibrates codes seen during probing. Codes from hints (0xFFFFFFFB, 0xFFFFFFFC) not ingested into calibration.

---

---

### Commit 9b46b3b — Pre-run status update (2026-03-20)

**What changed:**

| Fix | File | Change | Expected Impact |
|-----|------|--------|----------------|
| D-5 | `storage.py` `_patch_invocable` | Auto-derive semantic param names from descriptions via regex when LLM omits `name` key | CHECK 2 (param enrichment) should jump from 0% → 50%+ |
| D-6 | `explore_gap.py` `_attempt_gap_resolution` | Write `gap_resolution_log.json` at end of auto gap-resolution | CHECK 7 changes from N/A to measurable |
| D-7 | `main.py` session-snapshot endpoint | `gap_count` = unresolved functions (status≠success), `question_count` = clarification Qs | `session-meta.json` gap_count should be 3 (not 0) for same results |
| Dev profile | `main.py` mode_defaults | `max_tool_calls: 3→5`, `clarification_questions_enabled: false→true` | Mitigates Q-6 (cap-profile regression), enables clarification phase |
| Bridge cache | `scripts/gui_bridge.py` | `_ANALYSIS_CACHE_TTL: 3600→∞` | Same Ghidra schema reused across runs without expiry |

**Expected checklist outcomes for next run (9b46b3b):**

| Check | Previous (3cc5d9b) | Expected |
|-------|--------------------|---------|
| CHECK 1: Schema evolution | ✅ PASS | ✅ PASS |
| CHECK 2: Param enrichment | ❌ 0% | ⚠️/✅ 20-70% (D-5 auto-derive) |
| CHECK 3: Findings consistency | ⚠️ WARN | ⚠️ WARN (append-only by design) |
| CHECK 4: Mini-session enrichment | SKIP | ── Depends on clarification questions generating gap answers |
| CHECK 5: Vocab→schema bridge | ❌ FAIL | ⚠️/✅ (D-5 should cause param names to match vocab terms) |
| CHECK 6: Sentinel coverage | ⚠️ WARN | ⚠️ WARN (Q-1 not addressed this commit) |
| CHECK 7: Gap resolution | N/A | ✅/⚠️ (D-6 log now written) |

**Discovery output evaluation (for caching decision):**

The 3cc5d9b invocables_map.json was reviewed for caching suitability:
- ✅ All 12 CS_* exports + entry point correctly identified
- ✅ Ghidra decompiled C available for every function
- ✅ Calling convention correct (__fastcall / x64)
- ✅ Static analysis recovered test data IDs, emails, format strings
- ✅ Function descriptions are LLM-enriched (semantic, not raw Ghidra)
- ⚠️ `byte *` input params mislabeled as `direction: "out"` (Ghidra mapping heuristic)
- ⚠️ `entry` (DllMain) included — should be filtered out
- ⚠️ Some `required: []` when params are actually mandatory
- ❌ All params still `param_N` — D-5 fix addresses this

**Verdict:** Discovery output is **good enough to cache** for iterating on explore/enrich stages. The function list, types, signatures, and decompiled C are all correct. The issues (direction labels, required arrays, param names) are all addressed by downstream enrichment/patching, not the discovery phase itself.

---

## When to re-run this checklist

- After every `save-session.ps1`
- After any code change to `explore.py`, `explore_gap.py`, `storage.py`, `chat.py`, `executor.py`
- After container redeployment (tests the cold-start registration path)
