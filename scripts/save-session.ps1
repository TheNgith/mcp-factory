# save-session.ps1
# Capture a point-in-time snapshot of a discovery job into sessions/.
#
# Usage:
#   .\scripts\save-session.ps1 -ApiUrl "https://your-ui.azurecontainerapps.io" -JobId "a0fc70e8"
#   .\scripts\save-session.ps1 -ApiUrl "https://your-ui.azurecontainerapps.io" -JobId "a0fc70e8" -Note "payment-test"
#
# -Note is optional. Auto-derived as "{component}-run{N}" if omitted.

param(
    [Parameter(Mandatory=$true)][string]$JobId,
    [Parameter(Mandatory=$true)][string]$ApiUrl,
    [string]$Note = "",
    [string]$SessionsRoot = "$PSScriptRoot\..\sessions",
    [string]$ApiKey = ""
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
if (Test-Path $vocabPath) {
    try {
        $vocab = Get-Content $vocabPath -Raw | ConvertFrom-Json
        $ids = $vocab.known_ids
        if ($ids) {
            $names = @($ids.PSObject.Properties | Select-Object -ExpandProperty Name)
            if ($names.Count -gt 0) { $knownIds = $names -join ", " }
        }
    } catch { }
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
$sm += "| Known IDs in vocab | " + $knownIds + " |`n`n---`n`n"
$sm += "## Working calls confirmed`n`n" + $workingBlock + "`n`n---`n`n"
$sm += "## Gap questions open`n`n" + $gapBlock + "`n`n---`n`n"
$sm += "## What to investigate next`n`n> Fill this in after testing`n`n-`n"
Set-Content -Path (Join-Path $sessionDir "SUMMARY.md") -Value $sm -Encoding UTF8
Write-Host "  Wrote SUMMARY.md" -ForegroundColor Green

# ?? 14. chat-transcript.md template ?????????????????????????????????????????
$transcriptPath = Join-Path $sessionDir "chat-transcript.md"
if (-not (Test-Path $transcriptPath)) {
    $t  = "# Chat Transcript - " + $datePart + "`n`n"
    $t += "**Commit:** " + $commitHash + " - " + $commitMsg + "`n"
    $t += "**Job ID:** " + $JobId + "`n"
    $t += "**Component:** " + $component + "`n"
    $t += "**Note:** " + $Note + "`n`n---`n`n"
    $t += "## Test prompts`n`n### Prompt 1`n`n**User:** `"`"`n`n"
    $t += "| Round | Tool | Args | Result |`n|---|---|---|---|`n| 1 | | | |`n`n"
    $t += "**Outcome:**`n`n---`n`n## What worked`n`n-`n`n"
    $t += "## What failed / open questions`n`n-`n`n## Follow-up for next session`n`n-`n"
    Set-Content -Path $transcriptPath -Value $t -Encoding UTF8
    Write-Host "  Created chat-transcript.md" -ForegroundColor Yellow
}

# ?? 15. TEST_RESULTS.md ?????????????????????????????????????????????????????
$templatePath = Join-Path $SessionsRoot "CONTOSO_CS_TEST_SUITE.md"
$resultsPath  = Join-Path $sessionDir "TEST_RESULTS.md"
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
    date           = $datePart
    commit         = $commitHash
    commit_msg     = $commitMsg
    job_id         = $JobId
    component      = $component
    note           = $Note
    folder         = $folderName
    finding_counts = [PSCustomObject]@{ success=$successCount; partial=$partialCount; failed=$failedCount; total=$totalFindings }
    gap_count      = $gapCount
    known_ids      = $knownIds
    saved_at       = (Get-Date -Format "o")
}
$existing = @()
if (Test-Path $indexPath) {
    try { $existing = @(Get-Content $indexPath -Raw | ConvertFrom-Json) } catch { }
}
$existing += $entry
$existing | ConvertTo-Json -Depth 5 | Set-Content $indexPath -Encoding UTF8
Write-Host "  Updated index.json" -ForegroundColor Green

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
