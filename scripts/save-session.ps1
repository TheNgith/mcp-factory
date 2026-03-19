# save-session.ps1
# Capture a point-in-time snapshot of a discovery job into sessions/.
#
# Usage:
#   .\scripts\save-session.ps1 -ApiUrl "https://your-ui.azurecontainerapps.io" -JobId "a0fc70e8"
#   .\scripts\save-session.ps1 -ApiUrl "https://your-ui.azurecontainerapps.io" -JobId "a0fc70e8" -Note "payment-test"
#   .\scripts\save-session.ps1 -ApiUrl "..." -JobId "a0fc70e8" -TranscriptPath "C:\Users\you\Downloads\mcp-transcript-abc123.txt"
#
# -Note is optional. Auto-derived as "{component}-run{N}" if omitted.
# -TranscriptPath is optional. If omitted, the script searches Downloads for mcp-transcript-{JobId}.txt.
#   If the transcript was downloaded under a different name, pass it explicitly.

param(
    [Parameter(Mandatory=$true)][string]$JobId,
    [Parameter(Mandatory=$true)][string]$ApiUrl,
    [string]$Note = "",
    [string]$SessionsRoot = "$PSScriptRoot\..\sessions",
    [string]$ApiKey = "",
    [string]$TranscriptPath = "",
    [string]$OutDir = ""          # When set, merge all artifacts INTO this folder instead of creating a new dated one
)

if (-not $ApiKey) {
    $envKey = [System.Environment]::GetEnvironmentVariable("MCP_FACTORY_API_KEY")
    if ($envKey) { $ApiKey = $envKey }
}

# ?? 1. Git metadata ?????????????????????????????????????????????????????????
Push-Location $PSScriptRoot\..
$commitHash = git rev-parse --short HEAD 2>$null
$commitMsg  = git log -1 --format="%s" 2>$null
$commitBody = git log -1 --format="%B" 2>$null
$diffStat   = git diff HEAD~1 HEAD --stat 2>$null
$diffFull   = git diff HEAD~1 HEAD -- api/ ui/ scripts/ 2>$null
Pop-Location

if (-not $commitHash) { $commitHash = "nogit" }
if (-not $commitMsg)  { $commitMsg  = "no-message" }
if (-not $commitBody) { $commitBody = $commitMsg }
if (-not $diffStat)   { $diffStat   = "(no previous commit to diff against)" }
if (-not $diffFull)   { $diffFull   = "" }

$datePart = Get-Date -Format "yyyy-MM-dd"

Write-Host ""
Write-Host "=== MCP Factory - Save Session ===" -ForegroundColor Cyan
Write-Host "  Commit : $commitHash  $commitMsg"
Write-Host "  Job ID : $JobId"

# ?? 2. Prep temp folder ??????????????????????????????????????????????????????
New-Item -ItemType Directory -Force -Path $SessionsRoot | Out-Null
$tempDir = Join-Path $SessionsRoot ("_tmp-" + $JobId)
if (Test-Path $tempDir) { Remove-Item $tempDir -Recurse -Force }
New-Item -ItemType Directory -Force -Path $tempDir | Out-Null

# ?? 3. Download snapshot ZIP ?????????????????????????????????????????????????
$snapshotUrl = $ApiUrl.TrimEnd('/') + "/api/jobs/" + $JobId + "/session-snapshot"
$zipPath     = Join-Path $env:TEMP ("mcp-session-" + $JobId + ".zip")
$headers     = @{}
if ($ApiKey) { $headers["X-Pipeline-Key"] = $ApiKey }

Write-Host "  Downloading from $snapshotUrl ..." -ForegroundColor Yellow
try {
    Invoke-WebRequest -Uri $snapshotUrl -Headers $headers -OutFile $zipPath -UseBasicParsing
    $sizeKB = [Math]::Round((Get-Item $zipPath).Length / 1024, 1)
    Write-Host "  Downloaded $sizeKB KB" -ForegroundColor Green
} catch {
    if (Test-Path $tempDir) { Remove-Item $tempDir -Recurse -Force }
    Write-Host "  ERROR: download failed" -ForegroundColor Red
    Write-Host $_.Exception.Message
    exit 1
}

# ?? 4. Extract ZIP ???????????????????????????????????????????????????????????
Expand-Archive -Path $zipPath -DestinationPath $tempDir -Force
Remove-Item $zipPath -Force

# ?? 5. Read component from metadata ?????????????????????????????????????????
$meta      = $null
$component = "unknown"
$tempMeta  = Join-Path $tempDir "session-meta.json"
if (Test-Path $tempMeta) {
    try {
        $meta = Get-Content $tempMeta -Raw | ConvertFrom-Json
        if ($meta.component -and $meta.component -ne "unknown") {
            $component = $meta.component
        }
    } catch { }
}

# ?? 6. Auto-derive note ??????????????????????????????????????????????????????
if (-not $Note) {
    $prefix = $JobId.Substring(0, [Math]::Min(6, $JobId.Length))
    $runs   = @(Get-ChildItem $SessionsRoot -Directory -ErrorAction SilentlyContinue |
                Where-Object { $_.Name -like ("*" + $prefix + "*") }).Count
    $slug   = ($component -replace '[^a-zA-Z0-9]', '-' -replace '-{2,}', '-').ToLower().TrimEnd('-')
    $Note   = $slug + "-run" + ($runs + 1)
    Write-Host "  Auto-note: $Note" -ForegroundColor DarkCyan
}

# ?? 7. Final folder name ?????????????????????????????????????????????????????
if ($OutDir) {
    # Merge snapshot artifacts directly into the caller-supplied folder (e.g. _runs/{JobId})
    # instead of creating a new dated folder. This gives every ci-run ONE folder with both
    # the live test transcript/results AND the full snapshot artifacts.
    $sessionDir = $OutDir
    $folderName = Split-Path $sessionDir -Leaf
    New-Item -ItemType Directory -Force -Path $sessionDir | Out-Null
    Get-ChildItem $tempDir -Force | ForEach-Object {
        Copy-Item $_.FullName (Join-Path $sessionDir $_.Name) -Recurse -Force
    }
    Remove-Item $tempDir -Recurse -Force
} else {
    $noteSlug   = ($Note -replace '[^a-zA-Z0-9]', '-' -replace '-{2,}', '-' -replace '^-|-$', '').ToLower()
    $folderName = $datePart + "-" + $commitHash + "-" + $noteSlug
    $sessionDir = Join-Path $SessionsRoot $folderName

    if (Test-Path $sessionDir) {
        $i = 2
        while (Test-Path ($sessionDir + "-" + $i)) { $i++ }
        $sessionDir = $sessionDir + "-" + $i
        $folderName = Split-Path $sessionDir -Leaf
    }
    Move-Item $tempDir $sessionDir
}
Write-Host "  Folder : $sessionDir"
Write-Host ""

# ── 4-A. delta.md ────────────────────────────────────────────
# Compare new session against the most recent previous one
$prevSession = Get-ChildItem $SessionsRoot -Directory -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -ne $sessionDir } |
    Sort-Object Name -Descending | Select-Object -First 1

if ($prevSession) {
    $prevFindings = @()
    $prevFindPath = Join-Path $prevSession.FullName "artifacts\findings.json"
    if (Test-Path $prevFindPath) {
        try { $prevFindings = @(Get-Content $prevFindPath -Raw | ConvertFrom-Json) } catch { }
    }
    $prevVocab = $null
    $prevVocabPath = Join-Path $prevSession.FullName "artifacts\vocab.json"
    if (Test-Path $prevVocabPath) {
        try { $prevVocab = Get-Content $prevVocabPath -Raw | ConvertFrom-Json } catch { }
    }

    # Build prev function->status map
    $prevFnStatus = @{}
    foreach ($f in $prevFindings) { if ($f.function) { $prevFnStatus[$f.function] = $f.status } }

    $dm  = "# Delta: $($prevSession.Name) -> $folderName`n`n"
    $dm += "**Previous session:** $($prevSession.Name)`n"
    $dm += "**New session:** $folderName`n`n---`n`n"

    # Findings changes
    $gainedFns = @(); $lostFns = @(); $unchangedFns = @()
    $prevFindDir = Join-Path $sessionDir "artifacts\findings.json"
    $newFindings = @()
    if (Test-Path $prevFindDir) { try { $newFindings = @(Get-Content $prevFindDir -Raw | ConvertFrom-Json) } catch { } }
    $newFnStatus = @{}
    foreach ($f in $newFindings) { if ($f.function) { $newFnStatus[$f.function] = $f.status } }

    foreach ($fn in @($newFnStatus.Keys)) {
        if ($newFnStatus[$fn] -eq "success") {
            if (-not $prevFnStatus.ContainsKey($fn) -or $prevFnStatus[$fn] -ne "success") {
                $gainedFns += $fn
            } else { $unchangedFns += $fn }
        }
    }
    foreach ($fn in @($prevFnStatus.Keys)) {
        if ($prevFnStatus[$fn] -eq "success" -and (-not $newFnStatus.ContainsKey($fn) -or $newFnStatus[$fn] -ne "success")) {
            $lostFns += $fn
        }
    }

    $dm += "## Findings delta`n`n"
    $dm += "| Change | Functions |`n|---|---|`n"
    $dm += "| Newly working (OK) | " + $(if ($gainedFns.Count) { $gainedFns -join ", " } else { "(none)" }) + " |`n"
    $dm += "| Regressed (FAIL) | " + $(if ($lostFns.Count) { $lostFns -join ", " } else { "(none)" }) + " |`n"
    $dm += "| Unchanged (✅) | " + $unchangedFns.Count + " function(s) |`n`n"

    # Vocab changes
    $dm += "## Vocab delta`n`n"
    if ($null -ne $prevVocab) {
        $prevIds = @($prevVocab.id_formats) -join ", "
        $newIds  = if ($vocab) { @($vocab.id_formats) -join ", " } else { "(not yet parsed)" }
        $prevEc  = if ($prevVocab.error_codes) { @($prevVocab.error_codes.PSObject.Properties | Select-Object -ExpandProperty Name) } else { @() }
        $newEc   = if ($null -ne $vocab -and $vocab.error_codes) { @($vocab.error_codes.PSObject.Properties | Select-Object -ExpandProperty Name) } else { @() }
        $addedEc = $newEc | Where-Object { $_ -notin $prevEc }
        $removedEc = $prevEc | Where-Object { $_ -notin $newEc }
        $dm += "| Field | Previous | New |`n|---|---|---|`n"
        $dm += "| id_formats | $prevIds | $newIds |`n"
        $dm += "| error_codes added | | " + $(if ($addedEc) { $addedEc -join ", " } else { "(none)" }) + " |`n"
        $dm += "| error_codes removed | " + $(if ($removedEc) { $removedEc -join ", " } else { "(none)" }) + " | |`n"
    } else {
        $dm += "(no previous vocab to compare)`n"
    }

    Set-Content -Path (Join-Path $sessionDir "delta.md") -Value $dm -Encoding UTF8
    Write-Host "  Wrote delta.md (prev=$($prevSession.Name))" -ForegroundColor Green
} else {
    Write-Host "  Skipped delta.md (no previous session)" -ForegroundColor DarkYellow
}

# ?? 8. Stamp commit into session-meta.json ???????????????????????????????????
$metaPath = Join-Path $sessionDir "session-meta.json"
if (Test-Path $metaPath) {
    try {
        $m = Get-Content $metaPath -Raw | ConvertFrom-Json
        $m | Add-Member -NotePropertyName "commit"     -NotePropertyValue $commitHash -Force
        $m | Add-Member -NotePropertyName "commit_msg" -NotePropertyValue $commitMsg  -Force
        $m | Add-Member -NotePropertyName "note"       -NotePropertyValue $Note       -Force
        $m | Add-Member -NotePropertyName "saved_at"   -NotePropertyValue (Get-Date -Format "o") -Force
        $m | ConvertTo-Json -Depth 5 | Set-Content $metaPath -Encoding UTF8
    } catch { }
}

# ?? 9. code-changes.md ???????????????????????????????????????????????????????
$cc = "# Code Changes - " + $commitHash + "`n`n"
$cc += "**Commit:** " + $commitHash + " - " + $commitMsg + "`n"
$cc += "**Saved at:** " + (Get-Date -Format "o") + "`n`n---`n`n"
$cc += "## Commit message`n`n``````" + "`n" + $commitBody + "`n``````" + "`n`n"
$cc += "## Files changed`n`n``````" + "`n" + $diffStat + "`n``````" + "`n`n"
$cc += "## Diff (api/ ui/ scripts/)`n`n``````diff`n" + $diffFull + "`n``````" + "`n"
Set-Content -Path (Join-Path $sessionDir "code-changes.md") -Value $cc -Encoding UTF8
Write-Host "  Wrote code-changes.md" -ForegroundColor Green

# ?? 10. Parse findings.json ??????????????????????????????????????????????????
$successCount = 0; $partialCount = 0; $failedCount = 0; $totalFindings = 0
$workingBlock = "(none recorded)"
$functionStatus = @{}   # fn_name -> latest status (Phase 5-A)
$findPath = Join-Path $sessionDir "artifacts\findings.json"
if (Test-Path $findPath) {
    try {
        $findings = Get-Content $findPath -Raw | ConvertFrom-Json
        if ($findings) {
            $arr = @($findings)
            $totalFindings = $arr.Count
            foreach ($f in $arr) {
                $st = $f.status
                if ($st -eq "success") { $successCount++ }
                elseif ($st -eq "partial") { $partialCount++ }
                elseif ($st -eq "failed")  { $failedCount++ }
                if ($f.function) { $functionStatus[$f.function] = $st }
            }
            $wlines = @()
            foreach ($f in $arr) {
                if ($f.status -eq "success" -and $f.working_call) {
                    $fn = if ($f.function) { $f.function } else { "unknown" }
                    $wlines += "- **" + $fn + "**: " + ($f.working_call | ConvertTo-Json -Compress)
                }
            }
            if ($wlines.Count -gt 0) { $workingBlock = $wlines -join "`n" }
        }
    } catch { }
}

# ?? 11. Parse vocab.json ?????????????????????????????????????????????????????
$knownIds = "(none recorded)"
$vocabPath = Join-Path $sessionDir "artifacts\vocab.json"
$vocab = $null   # kept in scope for vocab_coverage and SUMMARY
if (Test-Path $vocabPath) {
    try {
        $vocab = Get-Content $vocabPath -Raw | ConvertFrom-Json
        # id_formats is a root-level array: ["CUST-NNN", "LOCKED", "ORD-YYYYMMDD-NNNN"]
        $ids = $vocab.id_formats
        if ($ids) { $knownIds = (@($ids) | Where-Object { $_ }) -join ", " }
    } catch { }
}
# Phase 5-C: vocab completeness = fraction of params with a human semantic name
$vocabCompleteness = $null
$invMapPath = Join-Path $sessionDir "artifacts\invocables_map.json"
if (Test-Path $invMapPath) {
    try {
        $invMap = Get-Content $invMapPath -Raw | ConvertFrom-Json
        $totalParams = 0; $namedParams = 0
        foreach ($prop in $invMap.PSObject.Properties) {
            foreach ($p in @($prop.Value.parameters)) {
                $totalParams++
                if ($p.name -and $p.name -notmatch '^param_\d+$') { $namedParams++ }
            }
        }
        if ($totalParams -gt 0) {
            $vocabCompleteness = [Math]::Round($namedParams / $totalParams, 2)
        }
    } catch { }
}

# ── 11.5. vocab_coverage.json (Phase 2-A) ──────────────────────────────────
# Check which vocab entries from vocab.json actually appear in model_context.txt
$vocabCoverageScore = $null
$contextPath = Join-Path $sessionDir "model_context.txt"
if ($null -ne $vocab -and (Test-Path $contextPath)) {
    try {
        $contextText = Get-Content $contextPath -Raw
        $ecTotal = 0; $ecInjected = 0; $ecMissing = @()
        if ($vocab.error_codes) {
            foreach ($key in $vocab.error_codes.PSObject.Properties.Name) {
                $ecTotal++
                if ($contextText -match [regex]::Escape($key)) { $ecInjected++ }
                else { $ecMissing += $key }
            }
        }
        $idInjected = $false
        if ($vocab.id_formats) {
            $idSample = @($vocab.id_formats)[0]
            if ($idSample) { $idInjected = $contextText -match [regex]::Escape($idSample) }
        }
        $vsKeys = @()
        if ($vocab.value_semantics) { $vsKeys = @($vocab.value_semantics.PSObject.Properties.Name) }
        $vocabCoverageScore = if ($ecTotal -gt 0) { [Math]::Round($ecInjected / $ecTotal, 2) } else { 1.0 }
        $vc = [PSCustomObject]@{
            error_codes_total    = $ecTotal
            error_codes_injected = $ecInjected
            error_codes_missing  = $ecMissing
            id_formats_injected  = $idInjected
            value_semantics_keys = $vsKeys
            coverage_score       = $vocabCoverageScore
        }
        $vc | ConvertTo-Json -Depth 3 | Set-Content -Path (Join-Path $sessionDir "vocab_coverage.json") -Encoding UTF8
        $cvColor = if ($vocabCoverageScore -lt 1.0) { "Yellow" } else { "Green" }
        Write-Host "  Wrote vocab_coverage.json (coverage=$vocabCoverageScore)" -ForegroundColor $cvColor
    } catch {
        Write-Host "  vocab_coverage.json skipped" -ForegroundColor DarkYellow
    }
}

# ?? 12. Parse clarification-questions.md ????????????????????????????????????
$gapCount = 0; $gapBlock = "(none)"
$gapsPath = Join-Path $sessionDir "clarification-questions.md"
if (Test-Path $gapsPath) {
    $gt = Get-Content $gapsPath -Raw
    $gapCount = ([regex]::Matches($gt, '(?m)^##\s+')).Count
    $gapBlock = $gt
}

# ── 12.5. Pre-parse static_analysis.json (required before SUMMARY.md) ─────────────
$staticAnalysisPath = Join-Path $sessionDir "static_analysis.json"
$staticVerification = $null; $staticVerdictStr = "n/a"
if (Test-Path $staticAnalysisPath) {
    try {
        $sa = Get-Content $staticAnalysisPath -Raw | ConvertFrom-Json
        $sentinelMisses = @(); $idMisses = @(); $capContradictions = @(); $svNotes = @()
        if ($sa.sentinel_constants -and $sa.sentinel_constants.harvested) {
            foreach ($h in $sa.sentinel_constants.harvested.PSObject.Properties) {
                if ($null -ne $vocab -and $vocab.error_codes -and
                    -not $vocab.error_codes.PSObject.Properties[$h.Name]) {
                    $sentinelMisses += $h.Name
                }
            }
        }
        $saFindText = if (Test-Path $findPath) { try { Get-Content $findPath -Raw } catch { "" } } else { "" }
        if ($sa.binary_strings -and $sa.binary_strings.ids_found) {
            foreach ($id in @($sa.binary_strings.ids_found)) {
                if ($saFindText -and $saFindText -notmatch [regex]::Escape($id)) { $idMisses += $id }
            }
        }
        $saNetCats = if ($sa.iat_capabilities -and $sa.iat_capabilities.categories) {
            @($sa.iat_capabilities.categories.PSObject.Properties.Name) |
                Where-Object { $_ -in @("networking","network","rpc") }
        } else { @() }
        if ($saNetCats.Count -eq 0 -and $saFindText -match "(?i)http|socket|winsock") {
            $capContradictions += "IAT has no network imports but findings mention HTTP/socket"
        }
        if ($sa.pe_version_info -and $sa.pe_version_info.FileDescription) {
            $svNotes += "PE identity: $($sa.pe_version_info.FileDescription)"
        }
        $saCount = if ($sa.sentinel_constants -and $sa.sentinel_constants.harvested) {
            @($sa.sentinel_constants.harvested.PSObject.Properties).Count } else { 0 }
        $saIdCnt = if ($sa.binary_strings -and $sa.binary_strings.ids_found) {
            @($sa.binary_strings.ids_found).Count } else { 0 }
        $saIat   = if ($sa.iat_capabilities -and $sa.iat_capabilities.categories) {
            @($sa.iat_capabilities.categories.PSObject.Properties.Name) } else { @() }
        $staticVerdictStr = if ($sentinelMisses.Count -gt 0 -or $capContradictions.Count -gt 0) { "FAIL" }
                            elseif ($idMisses.Count -gt 0) { "WARN" } else { "PASS" }
        $staticVerification = [PSCustomObject]@{
            sentinel_count            = $saCount;     sentinel_misses           = $sentinelMisses
            id_count                  = $saIdCnt;     id_misses                 = $idMisses
            iat_categories            = $saIat;       capability_contradictions = $capContradictions
            verdict                   = $staticVerdictStr; notes = $svNotes
            source = if ($sa.sentinel_constants.source) { $sa.sentinel_constants.source } else { "none" }
        }
    } catch { }
}

# ── 12.6. Pre-parse executor_trace.json (required before SUMMARY.md) ───────────
$execTracePath    = Join-Path $sessionDir "executor_trace.json"
$execTraceSummary = $null
if (Test-Path $execTracePath) {
    try {
        $et = @(Get-Content $execTracePath -Raw | ConvertFrom-Json)
        $etTotal = $et.Count; $etOk = 0; $etExcTypes = @{}
        $etFnFail = [System.Collections.Generic.List[string]]@()
        foreach ($entry in $et) {
            $tr = if ($entry.trace) { $entry.trace } else { $entry }
            $ok = if ($null -ne $tr.ok) { [bool]$tr.ok } else { -not ($tr.exception_class) }
            if ($ok) { $etOk++ } else {
                $exc = if ($tr.exception_class) { $tr.exception_class } else { "unknown" }
                $etExcTypes[$exc] = [int]($etExcTypes[$exc]) + 1
                $fn = if ($entry.function_name) { $entry.function_name }
                      elseif ($tr.function_name) { $tr.function_name } else { $null }
                if ($fn) { $etFnFail.Add("$fn ($exc)") }
            }
        }
        $execTraceSummary = [PSCustomObject]@{
            total_calls = $etTotal; ok = $etOk; failed = ($etTotal - $etOk)
            error_rate  = if ($etTotal -gt 0) { [Math]::Round(($etTotal - $etOk) / $etTotal, 2) } else { 0.0 }
            exception_types   = [PSCustomObject]$etExcTypes
            function_failures = @($etFnFail)
        }
        $etColor = if ($etTotal - $etOk -gt 0) { "Yellow" } else { "Green" }
        Write-Host ("  executor_trace.json: $etTotal calls, $etOk ok, $($etTotal - $etOk) failed") -ForegroundColor $etColor
    } catch { Write-Host "  executor_trace.json skipped" -ForegroundColor DarkYellow }
}

# ── 12.65. Pre-parse explore_probe_log.json (discover phase probe coverage) ─────
$probePath    = Join-Path $sessionDir "explore_probe_log.json"
$probeSummary = $null
if (Test-Path $probePath) {
    try {
        $probe = @(Get-Content $probePath -Raw | ConvertFrom-Json)
        $probeTotal   = $probe.Count
        $probeByPhase = @{}
        $probeFns     = [System.Collections.Generic.HashSet[string]]@()
        foreach ($entry in $probe) {
            $ph = if ($entry.phase) { $entry.phase } else { "unknown" }
            $probeByPhase[$ph] = [int]($probeByPhase[$ph]) + 1
            if ($entry.function) { [void]$probeFns.Add($entry.function) }
        }
        $probeSummary = [PSCustomObject]@{
            total_probes      = $probeTotal
            functions_probed  = $probeFns.Count
            by_phase          = [PSCustomObject]$probeByPhase
        }
        Write-Host ("  explore_probe_log.json: $probeTotal probes across $($probeFns.Count) functions") -ForegroundColor Green
    } catch { Write-Host "  explore_probe_log.json skipped" -ForegroundColor DarkYellow }
}

# ── 12.7. Pre-parse diagnosis_raw.json (required before SUMMARY.md + DIAGNOSIS.json) ──
$diagPath = Join-Path $sessionDir "diagnosis_raw.json"
$diagRaw  = @(); $diagOut = $null
if (Test-Path $diagPath) {
    try {
        $diagRaw  = @(Get-Content $diagPath -Raw | ConvertFrom-Json)
        $dCats    = @{}; $dSent = 0; $dErr = 0; $dRnds = 0
        foreach ($d in $diagRaw) {
            $dSent += [int]($d.sentinel_hits); $dErr += [int]($d.dll_errors)
            $dRnds += [int]($d.round_count)
            if ($d.dll_errors    -gt 0) { $dCats["dll_error"]       = [int]($dCats["dll_error"])       + $d.dll_errors }
            if ($d.sentinel_hits -gt 0) { $dCats["sentinel_0xffff"] = [int]($dCats["sentinel_0xffff"]) + $d.sentinel_hits }
        }
        $diagOut = [PSCustomObject]@{
            total_messages   = $diagRaw.Count; total_rounds     = $dRnds
            total_sentinels  = $dSent;         total_dll_errors = $dErr
            failure_categories = [PSCustomObject]$dCats
            verdict = if ($dSent -eq 0 -and $dErr -eq 0) { "clean" }
                      elseif ($dSent -gt 5 -or $dErr -gt 3) { "blocked" } else { "partial" }
        }
    } catch { }
}

# ── 12.8. model_context.txt size ────────────────────────────────────────────
$modelContextSizeKB = $null
if (Test-Path $contextPath) { $modelContextSizeKB = [Math]::Round((Get-Item $contextPath).Length / 1024, 1) }

# ── 13. SUMMARY.md — DEPRECATED ──────────────────────────────────────────────
# SUMMARY.md is a derived read-only artifact synthesised from the raw JSONs.
# It was removed from active maintenance because:
#   - it re-derived the same data already visible in findings.json, vocab.json,
#     executor_trace.json, diagnosis_raw.json, and chat_transcript.txt
#   - every new pipeline stage required a matching SUMMARY.md update — high
#     maintenance cost for zero diagnostic value over the raw files
#   - the pre-computation order bug (steps 12.5-12.8) proved it is easy to
#     silently produce wrong summaries, which is worse than no summary
#
# Diagnose sessions directly from the raw artifacts:
#   findings.json          — what the executor proved worked / failed
#   vocab.json             — accumulated semantic knowledge
#   executor_trace.json    — per-call backend trace (fn, args_keys, result_excerpt)
#   explore_probe_log.json  — every probe in discover/calibrate/verify/cross-validate phases
#   diagnosis_raw.json     — tools_called per chat, sentinel hit count
#   chat_transcript.txt    — full user/assistant/tool exchange with reasoning
#   model_context.txt      — exact system prompt the LLM received
#   static_analysis.json   — binary-derived vocab seeds (G-4/G-7/G-8/G-9)
#
# To resurrect SUMMARY.md generation, un-comment the block below.
#
# <SUMMARY.md generation removed — see git history for last working version>

# ?? 14. chat_transcript.txt — pull from API ????????????????????????????????????
$destTranscript = Join-Path $sessionDir "chat_transcript.txt"
# Transcript is now persisted server-side during chat. Pull via the snapshot ZIP
# (already extracted above). If not present in ZIP, fall back to Downloads search.
if (-not (Test-Path $destTranscript)) {
    # Fallback: look in Downloads for a manually-exported transcript
    $downloadsDir = Join-Path $env:USERPROFILE "Downloads"
    $autoMatch    = Join-Path $downloadsDir ("mcp-transcript-" + $JobId + ".txt")
    if ($TranscriptPath -and (Test-Path $TranscriptPath)) {
        Copy-Item $TranscriptPath $destTranscript
        Write-Host "  Copied provided transcript -> chat_transcript.txt" -ForegroundColor Green
    } elseif (Test-Path $autoMatch) {
        Copy-Item $autoMatch $destTranscript
        Write-Host "  Copied Downloads transcript -> chat_transcript.txt" -ForegroundColor Green
    } else {
        $recent = Get-ChildItem $downloadsDir -Filter "mcp-transcript-*.txt" -ErrorAction SilentlyContinue |
                  Where-Object { $_.LastWriteTime -gt (Get-Date).AddHours(-2) } |
                  Sort-Object LastWriteTime -Descending | Select-Object -First 1
        if ($recent) {
            Copy-Item $recent.FullName $destTranscript
            Write-Host "  Copied recent transcript ($($recent.Name)) -> chat_transcript.txt (verify job ID)" -ForegroundColor Yellow
        } else {
            Write-Host "  No transcript found (run more chat prompts to generate one server-side)" -ForegroundColor DarkYellow
        }
    }
} else {
    Write-Host "  chat_transcript.txt present in snapshot" -ForegroundColor Green
}

# ── 14.5. transcript metrics (Phase 5-B) ──────────────────────────────
$transcriptMetrics = [PSCustomObject]@{ tool_calls = 0; sentinel_hits = 0; dll_errors = 0 }
if (Test-Path $destTranscript) {
    try {
        $txLines = Get-Content $destTranscript
        $transcriptMetrics = [PSCustomObject]@{
            tool_calls    = (@($txLines | Where-Object { $_ -match '^🔧 ' })).Count
            sentinel_hits = (@($txLines | Where-Object { $_ -match '0xFFFFFFF|Returned: 429496729' })).Count
            dll_errors    = (@($txLines | Where-Object { $_ -match 'DLL call error' })).Count
        }
    } catch { }
}

# ── 14.75. DIAGNOSIS.json (Phase 3-C) — write pre-computed result (parsed in step 12.7) ──
if ($null -ne $diagOut) {
    $diagOut | ConvertTo-Json -Depth 3 | Set-Content -Path (Join-Path $sessionDir "DIAGNOSIS.json") -Encoding UTF8
    $dColor = if ($diagOut.verdict -eq 'clean') { 'Green' } elseif ($diagOut.verdict -eq 'blocked') { 'Red' } else { 'Yellow' }
    Write-Host "  Wrote DIAGNOSIS.json (verdict=$($diagOut.verdict))" -ForegroundColor $dColor
}

# ── 14.9. Static Analysis Verification (G-10) — write pre-computed result (parsed in step 12.5) ──
if ($null -ne $staticVerification) {
    $staticVerification | ConvertTo-Json -Depth 3 |
        Set-Content -Path (Join-Path $sessionDir "static_verification.json") -Encoding UTF8
    $saColor = switch ($staticVerdictStr) { "PASS" { "Green" } "WARN" { "Yellow" } "FAIL" { "Red" } default { "DarkYellow" } }
    Write-Host ("  Static analysis: $($staticVerification.sentinel_count) sentinels, " +
        "$($staticVerification.id_count) IDs, IAT:[" + ($staticVerification.iat_categories -join ",") +
        "] verdict=$staticVerdictStr") -ForegroundColor $saColor
    if (@($staticVerification.sentinel_misses).Count -gt 0) {
        Write-Host "    ⚠ Sentinel misses (harvested but not in vocab): $($staticVerification.sentinel_misses -join ', ')" -ForegroundColor Yellow
    }
    if (@($staticVerification.id_misses).Count -gt 0) {
        Write-Host "    → IDs in binary not used in any finding: $($staticVerification.id_misses -join ', ')" -ForegroundColor Cyan
    }
} else {
    Write-Host "  static_analysis.json not found (Phase 0 may not have run)" -ForegroundColor DarkYellow
}

# ?? 15. TEST_RESULTS.md ?????????????????????????????????????????????????????
$resultsPath  = Join-Path $sessionDir "TEST_RESULTS.md"
# Phase 8-A: diagnosis_raw pre-parsed in step 12.7 — reuse directly
$diagRawForTests = $diagRaw
if (Test-Path $templatePath) {
    $tr  = "# Test Results - " + $datePart + "`n`n"
    $tr += "**Session:** " + $folderName + "`n"
    $tr += "**Commit:** " + $commitHash + " - " + $commitMsg + "`n"
    $tr += "**Job ID:** " + $JobId + "`n`n"
    $tr += "See [../../contoso_cs/TEST_SUITE.md](../../contoso_cs/TEST_SUITE.md) for full prompts.`n`n---`n`n"
    $tr += "## Scoring Table`n`n"
    $tr += "| ID | Description | ID Format | Amount/Value Encoding | Error Decode | Init Order | Overall |`n"
    $tr += "|----|-------------|-----------|----------------------|--------------|------------|---------|`n"
    $tr += "| T01 | Version decode | - | | - | - | |`n"
    $tr += "| T02 | Initialized boolean | - | | - | - | |`n"
    $tr += "| T03 | System counts | - | - | - | - | |`n"
    $tr += "| T04 | Auto-format CUST-007 | | - | - | - | |`n"
    $tr += "| T05 | Auto-format CUST-042 | | - | - | - | |`n"
    $tr += "| T06 | Order ID + refund cents | | | - | - | |`n"
    $tr += "| T07 | Reject malformed ID | | - | - | - | |`n"
    $tr += "| T08 | Payment cents | - | | - | - | |`n"
    $tr += "| T09 | Refund cents | - | | - | - | |`n"
    $tr += "| T10 | Balance div 100 | - | | - | - | |`n"
    $tr += "| T11 | Points integer | - | | - | - | |`n"
    $tr += "| T12 | Diagnose locked | | - | | | |`n"
    $tr += "| T13 | Already-active unlock | - | - | - | | |`n"
    $tr += "| T14 | Payment on locked | - | - | | | |`n"
    $tr += "| T15 | 0xFFFFFFFB decode | - | - | | - | |`n"
    $tr += "| T16 | 0xFFFFFFFC decode | - | - | | - | |`n"
    $tr += "| T17 | Access violation | - | - | | - | |`n"
    $tr += "| T18 | No-init payment | - | - | | | |`n"
    $tr += "| T19 | Full profile fields | | | - | - | |`n"
    $tr += "| T20 | Tier label | - | | - | - | |`n"
    $tr += "| T21 | Contact fields | - | - | - | - | |`n"
    $tr += "| T22 | Full happy path | | | - | | |`n"
    $tr += "| T23 | End-to-end refund | | | - | | |`n"
    $tr += "| T24 | Multi-customer session | | | | | |`n"
    $tr += "| T25 | Locked in multi-step | - | - | | | |`n"
    $tr += "| T26 | Zero amount | - | | - | - | |`n"
    $tr += "| T27 | Over-redeem points | - | | | - | |`n"
    $tr += "| T28 | LOCKED as ID confusion | | - | - | - | |`n"
    $tr += "`n---`n`n## Notes`n`n> Fill in observations, surprises, and follow-up questions below`n`n-`n"
    # Phase 8-A: if diagnosis_raw has entries, append a quick-ref summary at the bottom
    if ($diagRawForTests.Count -gt 0) {
        $tr += "`n---`n`n## Auto-populated context (from diagnosis_raw.json)`n`n"
        $tr += "| Turn | Tools called | Sentinels | DLL errors |`n|---|---|---|---|`n"
        $allToolCalls = [System.Collections.Generic.List[string]]@()
        foreach ($d in $diagRawForTests) {
            $tools = if ($d.tools_called) { (@($d.tools_called) -join ", ") } else { "(none)" }
            $tr += "| $($d.recorded_at) | $tools | $($d.sentinel_hits) | $($d.dll_errors) |`n"
            foreach ($t in @($d.tools_called)) { if ($t) { $allToolCalls.Add($t) } }
        }
        if ($allToolCalls.Count -gt 0) {
            $uniqueTools = ($allToolCalls | Sort-Object -Unique) -join ", "
            $tr += "`n**All tools exercised:** $uniqueTools`n"
        }
    }
    Set-Content -Path $resultsPath -Value $tr -Encoding UTF8
    Write-Host "  Created TEST_RESULTS.md" -ForegroundColor Green
} else {
    Write-Host "  Skipped TEST_RESULTS.md (template not found at sessions/contoso_cs/TEST_SUITE.md)" -ForegroundColor DarkYellow
}

# ?? 16. README.md index ??????????????????????????????????????????????????????
$readmePath = Join-Path $SessionsRoot "README.md"
if (Test-Path $readmePath) {
    $row  = "| " + $datePart + " | ``" + $commitHash + "`` | ``" + $JobId + "`` | " + $Note + " | " + $component + " - (fill in after testing) |"
    $rdtx = Get-Content $readmePath -Raw
    if ($rdtx -notlike ("*" + $folderName + "*")) {
        $updated = $rdtx.TrimEnd() + "`n" + $row + "`n"
        Set-Content -Path $readmePath -Value $updated -Encoding UTF8
        Write-Host "  Updated README.md index" -ForegroundColor Green
    }
}

# ?? 17. index.json ???????????????????????????????????????????????????????????
$indexPath = Join-Path $SessionsRoot "index.json"
$entry = [PSCustomObject]@{
    date               = $datePart
    commit             = $commitHash
    commit_msg         = $commitMsg
    job_id             = $JobId
    component          = $component
    note               = $Note
    folder             = $folderName
    finding_counts     = [PSCustomObject]@{ success=$successCount; partial=$partialCount; failed=$failedCount; total=$totalFindings }
    gap_count          = $gapCount
    known_ids          = $knownIds
    vocab_completeness = $vocabCompleteness
    vocab_coverage     = $vocabCoverageScore
    transcript_metrics = $transcriptMetrics
    probe_summary      = $probeSummary
    function_status    = if ($functionStatus.Count -gt 0) { [PSCustomObject]$functionStatus } else { $null }
    saved_at           = (Get-Date -Format "o")
}
# Phase 0-C: robust read — PS5.1 ConvertFrom-Json unwraps 1-element arrays,
# so we detect the resulting bare object and re-wrap it.
$existing = @()
if (Test-Path $indexPath) {
    try {
        $rawJson  = Get-Content $indexPath -Raw
        $parsed   = ConvertFrom-Json $rawJson
        $rawArray = if ($parsed -is [System.Array]) { $parsed } elseif ($null -ne $parsed) { @($parsed) } else { @() }
        # Unwrap any PS5.1 serialisation artifact where a 1-element array round-tripped
        # through ConvertFrom-Json and back produces a {value:[...], Count:N} wrapper.
        foreach ($item in $rawArray) {
            if ($null -ne $item.PSObject.Properties['value'] -and $null -ne $item.PSObject.Properties['Count']) {
                foreach ($inner in @($item.value)) { $existing += $inner }
            } else {
                $existing += $item
            }
        }
    } catch { $existing = @() }
}
$existing += $entry
# Always emit a JSON array, even for a single entry (works around PS5 serialisation quirk).
$jsonOut = $existing | ConvertTo-Json -Depth 5
if ($jsonOut -and $jsonOut.TrimStart()[0] -ne '[') { $jsonOut = "[$jsonOut]" }
$jsonOut | Set-Content $indexPath -Encoding UTF8
Write-Host "  Updated index.json" -ForegroundColor Green

# ── 18. Refresh DASHBOARD.md ─────────────────────────────────────────────────
$comparePath = Join-Path $SessionsRoot "compare.ps1"
if (Test-Path $comparePath) {
    Write-Host ""
    Write-Host "─────────────────────────────────────────────────────────────────" -ForegroundColor DarkGray
    & $comparePath -SessionsRoot (Resolve-Path $SessionsRoot).Path -Count 10
    Write-Host "─────────────────────────────────────────────────────────────────" -ForegroundColor DarkGray
}

# ?? 18. List files and finish ????????????????????????????????????????????????
$files = Get-ChildItem -Path $sessionDir -Recurse -File
Write-Host ""
Write-Host "  Files saved:" -ForegroundColor Cyan
foreach ($f in $files) {
    Write-Host ("    " + $f.FullName.Replace($sessionDir, "").TrimStart("\/"))
}
Write-Host ""
Write-Host "=== Done ===" -ForegroundColor Green
Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. Run test prompts from sessions/contoso_cs/TEST_SUITE.md and fill in TEST_RESULTS.md"
Write-Host "  2. Diagnose from raw artifacts: findings.json, vocab.json, executor_trace.json, explore_probe_log.json, chat_transcript.txt"
$commitCmd = "git add sessions/ ; git commit -m `"session: " + $JobId + " " + $Note + "`" ; git push"
Write-Host "  3. Commit: $commitCmd"

