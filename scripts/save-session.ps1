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
    [string]$TranscriptPath = ""
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
if ($ApiKey) { $headers["X-API-Key"] = $ApiKey }

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
    $dm += "| Newly working (✅) | " + (if ($gainedFns.Count) { $gainedFns -join ", " } else { "(none)" }) + " |`n"
    $dm += "| Regressed (❌) | " + (if ($lostFns.Count) { $lostFns -join ", " } else { "(none)" }) + " |`n"
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
        $dm += "| error_codes added | | " + (if ($addedEc) { $addedEc -join ", " } else { "(none)" }) + " |`n"
        $dm += "| error_codes removed | " + (if ($removedEc) { $removedEc -join ", " } else { "(none)" }) + " | |`n"
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
                    $fn = if ($f.function_name) { $f.function_name } elseif ($f.invocable_id) { $f.invocable_id } else { "unknown" }
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

# ?? 13. SUMMARY.md ???????????????????????????????????????????????????????????
$sm  = "# Session Summary`n`n"
$sm += "> For pipeline architecture and how vocab.json/schema/findings fit together, see [../WORKFLOW.md](../WORKFLOW.md)`n`n"
$sm += "> The exact system message the LLM received is in [model_context.txt](model_context.txt)`n`n"
$sm += "**Date:** " + $datePart + "`n"
$sm += "**Component:** " + $component + "`n"
$sm += "**Job ID:** " + $JobId + "`n"
$sm += "**Commit:** " + $commitHash + " - " + $commitMsg + "`n"
$sm += "**Note:** " + $Note + "`n`n---`n`n"
$sm += "## What changed in this commit`n`n"
$sm += "See [code-changes.md](code-changes.md) for the full diff.`n`n"
$sm += "``````" + "`n" + $diffStat + "`n``````" + "`n`n---`n`n"
$sm += "## Discovery state`n`n"
$sm += "| Metric | Value |`n|---|---|`n"
$sm += "| Total findings | " + $totalFindings + " |`n"
$sm += "| Successful calls | " + $successCount + " |`n"
$sm += "| Partial | " + $partialCount + " |`n"
$sm += "| Failed | " + $failedCount + " |`n"
$sm += "| Gap questions open | " + $gapCount + " |`n"
$sm += "| Vocab coverage (error codes) | " + (if ($null -ne $vocabCoverageScore) { $vocabCoverageScore } else { "(n/a)" }) + " |`n"
$sm += "| Vocab completeness (named params) | " + (if ($null -ne $vocabCompleteness) { $vocabCompleteness } else { "(n/a)" }) + " |`n"
$sm += "| Known IDs in vocab | " + $knownIds + " |`n`n---`n`n"
$sm += "## Working calls confirmed`n`n" + $workingBlock + "`n`n---`n`n"
$sm += "## Gap questions open`n`n" + $gapBlock + "`n`n---`n`n"
$sm += "## What to investigate next`n`n> Fill this in after testing`n`n-`n"
Set-Content -Path (Join-Path $sessionDir "SUMMARY.md") -Value $sm -Encoding UTF8
Write-Host "  Wrote SUMMARY.md" -ForegroundColor Green

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
            sentinel_hits = (@($txLines | Where-Object { $_ -match 'Returned: 429496729[345]' })).Count
            dll_errors    = (@($txLines | Where-Object { $_ -match 'DLL call error' })).Count
        }
    } catch { }
}

# ── 14.75. DIAGNOSIS.json (Phase 3-C) ──────────────────────────────────
$diagPath = Join-Path $sessionDir "diagnosis_raw.json"
if (Test-Path $diagPath) {
    try {
        $diagRaw = @(Get-Content $diagPath -Raw | ConvertFrom-Json)
        $categories = @{}
        $totalSentinels = 0; $totalDllErrors = 0; $totalRounds = 0
        foreach ($d in $diagRaw) {
            $totalSentinels += [int]($d.sentinel_hits)
            $totalDllErrors += [int]($d.dll_errors)
            $totalRounds    += [int]($d.round_count)
            if ($d.dll_errors -gt 0) {
                $cat = "dll_error"
                $categories[$cat] = [int]($categories[$cat]) + $d.dll_errors
            }
            if ($d.sentinel_hits -gt 0) {
                $cat = "sentinel_0xffff"
                $categories[$cat] = [int]($categories[$cat]) + $d.sentinel_hits
            }
        }
        $diagOut = [PSCustomObject]@{
            total_messages  = $diagRaw.Count
            total_rounds    = $totalRounds
            total_sentinels = $totalSentinels
            total_dll_errors = $totalDllErrors
            failure_categories = $categories
            verdict = if ($totalSentinels -eq 0 -and $totalDllErrors -eq 0) { "clean" }
                      elseif ($totalSentinels -gt 5 -or $totalDllErrors -gt 3) { "blocked" }
                      else { "partial" }
        }
        $diagOut | ConvertTo-Json -Depth 3 | Set-Content -Path (Join-Path $sessionDir "DIAGNOSIS.json") -Encoding UTF8
        Write-Host "  Wrote DIAGNOSIS.json (verdict=$($diagOut.verdict))" -ForegroundColor $(if ($diagOut.verdict -eq 'clean') { 'Green' } elseif ($diagOut.verdict -eq 'blocked') { 'Red' } else { 'Yellow' })
    } catch {
        Write-Host "  DIAGNOSIS.json skipped" -ForegroundColor DarkYellow
    }
}

# ?? 15. TEST_RESULTS.md ?????????????????????????????????????????????????????
$templatePath = Join-Path $SessionsRoot "CONTOSO_CS_TEST_SUITE.md"
$resultsPath  = Join-Path $sessionDir "TEST_RESULTS.md"
# Phase 8-A: load diagnosis_raw for pre-filling context in TEST_RESULTS
$diagRawForTests = @()
if ($diagPath -and (Test-Path $diagPath)) {
    try { $diagRawForTests = @(Get-Content $diagPath -Raw | ConvertFrom-Json) } catch { }
}
if (Test-Path $templatePath) {
    $tr  = "# Test Results - " + $datePart + "`n`n"
    $tr += "**Session:** " + $folderName + "`n"
    $tr += "**Commit:** " + $commitHash + " - " + $commitMsg + "`n"
    $tr += "**Job ID:** " + $JobId + "`n`n"
    $tr += "See [../../CONTOSO_CS_TEST_SUITE.md](../../CONTOSO_CS_TEST_SUITE.md) for full prompts.`n`n---`n`n"
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
    Write-Host "  Skipped TEST_RESULTS.md (template not found at sessions/CONTOSO_CS_TEST_SUITE.md)" -ForegroundColor DarkYellow
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
    function_status    = if ($functionStatus.Count -gt 0) { [PSCustomObject]$functionStatus } else { $null }
    saved_at           = (Get-Date -Format "o")
}
# Phase 0-C: robust read — PS5.1 ConvertFrom-Json unwraps 1-element arrays,
# so we detect the resulting bare object and re-wrap it.
$existing = @()
if (Test-Path $indexPath) {
    try {
        $rawJson = Get-Content $indexPath -Raw
        $parsed  = ConvertFrom-Json $rawJson
        if ($parsed -is [System.Array]) { $existing = $parsed }
        elseif ($null -ne $parsed)      { $existing = @($parsed) }
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
Write-Host "  1. Run test prompts from sessions/CONTOSO_CS_TEST_SUITE.md and fill in TEST_RESULTS.md"
Write-Host "  2. Update SUMMARY.md 'What to investigate next' section"
$commitCmd = "git add sessions/ ; git commit -m `"session: " + $JobId + " " + $Note + "`" ; git push"
Write-Host "  3. Commit: $commitCmd"
