# save-session.ps1
# Capture a full point-in-time snapshot of a discovery job into sessions/.
# Run after each meaningful test or iteration to build a progressive record
# you can reference and git-diff across runs.
#
# What gets saved per session:
#   SUMMARY.md                      — one-file overview (commit, findings counts, working calls, gaps)
#   code-changes.md                 — git log + git diff of api/ ui/ scripts/ at snapshot time
#   session-meta.json               — job_id, commit, component, timestamps, finding/gap counts
#   hints.txt                       — user hints + use_cases verbatim
#   clarification-questions.md      — gap questions, technical detail, and any answers given
#   chat-transcript.md              — template for you to fill in after testing
#   schema/01-pre-enrichment.json   — MCP schema right after Generate (before Discover)
#   schema/02-post-enrichment.json  — MCP schema after Discover/Refine completes
#   artifacts/findings.json         — all LLM-recorded findings
#   artifacts/vocab.json            — accumulated vocabulary (IDs, value semantics, notes, gap answers)
#   artifacts/api_reference.md      — synthesized API reference markdown
#   artifacts/behavioral_spec.py    — typed Python stub
#   artifacts/invocables_map.json   — full enriched invocable definitions
#
# Usage (Note is OPTIONAL — auto-derived from component + run number if omitted):
#   .\scripts\save-session.ps1 -ApiUrl "https://your-api.azurecontainerapps.io" -JobId "a0fc70e8"
#   .\scripts\save-session.ps1 -ApiUrl "http://localhost:8000" -JobId "a0fc70e8" -Note "payment-test"
#
# Folder name: YYYY-MM-DD-{commit}-{note-slug}
# An index is maintained at sessions/index.json (machine-readable) and sessions/README.md (human table).

param(
    [Parameter(Mandatory=$true)]
    [string]$JobId,

    [Parameter(Mandatory=$true)]
    [string]$ApiUrl,

    [string]$Note = "",
    [string]$SessionsRoot = "$PSScriptRoot\..\sessions",
    [string]$ApiKey = $env:MCP_FACTORY_API_KEY
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ── 1. Git metadata ────────────────────────────────────────────────────────────
Push-Location $PSScriptRoot\..
$commitHash = (git rev-parse --short HEAD 2>$null) ?? "nogit"
$commitMsg  = (git log -1 --format="%s" 2>$null) ?? "no-message"
$commitBody = (git log -1 --format="%B" 2>$null) ?? $commitMsg
$diffStat   = (git diff HEAD~1 HEAD --stat 2>$null) ?? "(no previous commit to diff against)"
$diffFull   = (git diff HEAD~1 HEAD -- api/ ui/ scripts/ 2>$null) ?? ""
Pop-Location

$datePart = Get-Date -Format "yyyy-MM-dd"

Write-Host "`n=== MCP Factory — Save Session ===" -ForegroundColor Cyan
Write-Host "  Commit : $commitHash  $commitMsg"
Write-Host "  Job ID : $JobId"

# ── 2. Download snapshot ZIP into a temp folder ────────────────────────────────
New-Item -ItemType Directory -Force -Path $SessionsRoot | Out-Null
$tempDir = Join-Path $SessionsRoot "_tmp-$JobId"
if (Test-Path $tempDir) { Remove-Item $tempDir -Recurse -Force }
New-Item -ItemType Directory -Force -Path $tempDir | Out-Null

$snapshotUrl = "$($ApiUrl.TrimEnd('/'))/api/jobs/$JobId/session-snapshot"
$zipPath     = Join-Path $env:TEMP "mcp-session-$JobId.zip"

$headers = @{}
if ($ApiKey) { $headers["X-API-Key"] = $ApiKey }

Write-Host "  Downloading snapshot from $snapshotUrl ..." -ForegroundColor Yellow
try {
    Invoke-WebRequest -Uri $snapshotUrl -Headers $headers -OutFile $zipPath -UseBasicParsing
    Write-Host "  Download complete ($([Math]::Round((Get-Item $zipPath).Length / 1KB, 1)) KB)" -ForegroundColor Green
} catch {
    Remove-Item $tempDir -Recurse -Force -ErrorAction SilentlyContinue
    Write-Error "  Failed to download snapshot: $_"
    exit 1
}

# ── 3. Extract ZIP into temp folder ───────────────────────────────────────────
Expand-Archive -Path $zipPath -DestinationPath $tempDir -Force
Remove-Item $zipPath -Force

# ── 4. Read metadata to get component name ────────────────────────────────────
$tempMetaPath = Join-Path $tempDir "session-meta.json"
$meta         = $null
$component    = "unknown"
if (Test-Path $tempMetaPath) {
    try {
        $meta      = Get-Content $tempMetaPath -Raw | ConvertFrom-Json
        $component = if ($meta.component -and $meta.component -ne "unknown") { $meta.component } else { "unknown" }
    } catch { }
}

# ── 5. Auto-derive note if not provided ───────────────────────────────────────
if (-not $Note) {
    $jobPrefix    = $JobId.Substring(0, [Math]::Min(6, $JobId.Length))
    $existingRuns = @(Get-ChildItem $SessionsRoot -Directory -ErrorAction SilentlyContinue |
                      Where-Object { $_.Name -like "*$jobPrefix*" }).Count
    $compSlug = ($component -replace '[^a-zA-Z0-9]', '-' -replace '-{2,}', '-').ToLower().TrimEnd('-')
    $Note     = "$compSlug-run$($existingRuns + 1)"
    Write-Host "  Auto-note: $Note" -ForegroundColor DarkCyan
}

# ── 6. Compute final folder name and rename temp → final ──────────────────────
$noteSlug   = ($Note -replace '[^a-zA-Z0-9]', '-' -replace '-{2,}', '-' -replace '^-|-$', '').ToLower()
$folderName = "$datePart-$commitHash-$noteSlug"
$sessionDir = Join-Path $SessionsRoot $folderName

# Disambiguate if a folder already exists with the same name
if (Test-Path $sessionDir) {
    $i = 2
    while (Test-Path "$sessionDir-$i") { $i++ }
    $sessionDir = "$sessionDir-$i"
    $folderName = Split-Path $sessionDir -Leaf
}
Rename-Item $tempDir $sessionDir
Write-Host "  Folder : $sessionDir`n"

# ── 7. Stamp commit info into session-meta.json ───────────────────────────────
$metaPath = Join-Path $sessionDir "session-meta.json"
if (Test-Path $metaPath) {
    $meta = Get-Content $metaPath -Raw | ConvertFrom-Json
    $meta | Add-Member -NotePropertyName "commit"      -NotePropertyValue $commitHash  -Force
    $meta | Add-Member -NotePropertyName "commit_msg"  -NotePropertyValue $commitMsg   -Force
    $meta | Add-Member -NotePropertyName "note"        -NotePropertyValue $Note        -Force
    $meta | Add-Member -NotePropertyName "saved_at"    -NotePropertyValue (Get-Date -Format "o") -Force
    $meta | ConvertTo-Json -Depth 5 | Set-Content $metaPath -Encoding UTF8
}

# ── 8. Write code-changes.md ──────────────────────────────────────────────────
$codeChangesContent = @"
# Code Changes — $commitHash

**Commit:** ``$commitHash`` — $commitMsg
**Saved at:** $(Get-Date -Format "o")

## Full commit message

``````
$commitBody
``````

## Files changed (stat)

``````
$diffStat
``````

## Diff (api/ ui/ scripts/)

``````diff
$diffFull
``````
"@
Set-Content -Path (Join-Path $sessionDir "code-changes.md") -Value $codeChangesContent -Encoding UTF8
Write-Host "  Wrote code-changes.md" -ForegroundColor Green

# ── 9. Parse findings.json for SUMMARY metrics ────────────────────────────────
$successCount  = 0; $partialCount = 0; $failedCount = 0; $totalFindings = 0
$workingCallsBlock = "(none recorded)"
$findingsPath  = Join-Path $sessionDir "artifacts\findings.json"
if (Test-Path $findingsPath) {
    try {
        $findings = Get-Content $findingsPath -Raw | ConvertFrom-Json
        if ($findings -is [array]) {
            $successCount  = @($findings | Where-Object { $_.status -eq "success" }).Count
            $partialCount  = @($findings | Where-Object { $_.status -eq "partial" }).Count
            $failedCount   = @($findings | Where-Object { $_.status -eq "failed"  }).Count
            $totalFindings = $findings.Count
            $successes     = @($findings | Where-Object { $_.status -eq "success" -and $_.working_call })
            if ($successes.Count -gt 0) {
                $workingCallsBlock = ($successes | ForEach-Object {
                    $fname = if ($_.function_name) { $_.function_name } elseif ($_.invocable_id) { $_.invocable_id } else { "unknown" }
                    "- **$fname**: ``$($_.working_call | ConvertTo-Json -Compress)``"
                }) -join "`n"
            }
        }
    } catch { }
}

# ── 10. Parse vocab.json for known IDs ────────────────────────────────────────
$knownIds = "(none recorded)"
$vocabPath = Join-Path $sessionDir "artifacts\vocab.json"
if (Test-Path $vocabPath) {
    try {
        $vocab = Get-Content $vocabPath -Raw | ConvertFrom-Json
        if ($vocab.known_ids -and $vocab.known_ids.PSObject.Properties.Name.Count -gt 0) {
            $knownIds = ($vocab.known_ids.PSObject.Properties.Name) -join ", "
        }
    } catch { }
}

# ── 11. Parse clarification-questions.md for gap count and content ────────────
$gapCount = 0
$gapBlock = "(none)"
$gapsPath = Join-Path $sessionDir "clarification-questions.md"
if (Test-Path $gapsPath) {
    $gapsText = Get-Content $gapsPath -Raw
    $gapCount = ([regex]::Matches($gapsText, '(?m)^##\s+')).Count
    $gapBlock = $gapsText
}

# ── 12. Write SUMMARY.md ──────────────────────────────────────────────────────
$summaryContent = @"
# Session Summary

**Date:** $datePart
**Component:** $component
**Job ID:** ``$JobId``
**Commit:** ``$commitHash`` — $commitMsg
**Note:** $Note

---

## What changed in this commit

See [code-changes.md](code-changes.md) for the full diff.

``````
$diffStat
``````

---

## Discovery state at snapshot time

| Metric | Value |
|---|---|
| Total findings | $totalFindings |
| Successful calls | $successCount |
| Partial | $partialCount |
| Failed | $failedCount |
| Gap questions open | $gapCount |
| Known IDs in vocab | $knownIds |

---

## Working calls confirmed (status = success)

$workingCallsBlock

---

## Gap questions open at snapshot time

$gapBlock

---

## What to investigate next

> Fill this in after testing — see chat-transcript.md

-
"@
Set-Content -Path (Join-Path $sessionDir "SUMMARY.md") -Value $summaryContent -Encoding UTF8
Write-Host "  Wrote SUMMARY.md" -ForegroundColor Green

# ── 13. Create chat-transcript.md template ────────────────────────────────────
$transcriptPath = Join-Path $sessionDir "chat-transcript.md"
if (-not (Test-Path $transcriptPath)) {
    $template = @"
# Chat Transcript — $datePart

**Commit:** ``$commitHash`` — $commitMsg
**Job ID:** ``$JobId``
**Component:** $component
**Note:** $Note

---

## Context / hints used

(see hints.txt)

---

## Test prompts

### Prompt 1

**User:** ""

| Round | Tool | Args | Result |
|---|---|---|---|
| 1 | | | |

**Outcome:**

---

## What worked

-

## What failed / open questions

-

## Follow-up for next session

-
"@
    Set-Content -Path $transcriptPath -Value $template -Encoding UTF8
    Write-Host "  Created chat-transcript.md template" -ForegroundColor Yellow
}

# ── 14. Update sessions/README.md index ───────────────────────────────────────
$readmePath = Join-Path $SessionsRoot "README.md"
if (Test-Path $readmePath) {
    $newRow     = "| $datePart | ``$commitHash`` | ``$JobId`` | $Note | $component — (fill in after testing) |"
    $readmeText = Get-Content $readmePath -Raw
    if ($readmeText -notlike "*$folderName*") {
        $updated = $readmeText.TrimEnd() + "`n$newRow`n"
        Set-Content -Path $readmePath -Value $updated -Encoding UTF8
        Write-Host "  Updated README.md index" -ForegroundColor Green
    }
}

# ── 15. Update sessions/index.json ────────────────────────────────────────────
$indexPath  = Join-Path $SessionsRoot "index.json"
$indexEntry = [PSCustomObject]@{
    date           = $datePart
    commit         = $commitHash
    commit_msg     = $commitMsg
    job_id         = $JobId
    component      = $component
    note           = $Note
    folder         = $folderName
    finding_counts = [PSCustomObject]@{
        success = $successCount
        partial = $partialCount
        failed  = $failedCount
        total   = $totalFindings
    }
    gap_count      = $gapCount
    known_ids      = $knownIds
    saved_at       = (Get-Date -Format "o")
}
$indexData = @()
if (Test-Path $indexPath) {
    try { $indexData = @(Get-Content $indexPath -Raw | ConvertFrom-Json) } catch { $indexData = @() }
}
$indexData += $indexEntry
$indexData | ConvertTo-Json -Depth 5 | Set-Content $indexPath -Encoding UTF8
Write-Host "  Updated index.json" -ForegroundColor Green

# ── 16. Summary output ────────────────────────────────────────────────────────
$files = Get-ChildItem -Path $sessionDir -Recurse -File
Write-Host "`n  Files saved:" -ForegroundColor Cyan
$files | ForEach-Object { Write-Host "    $($_.FullName.Replace($sessionDir, '').TrimStart('\/'))" }

Write-Host "`n=== Done ===`n" -ForegroundColor Green
Write-Host "Next steps:"
Write-Host "  1. Fill in chat-transcript.md with your test session"
Write-Host "  2. Update SUMMARY.md 'What to investigate next' section"
Write-Host "  3. Commit the session:"
Write-Host "     $transcriptPath" -ForegroundColor White
Write-Host "     git add sessions/ ; git commit -m `"session: $JobId $Note`" ; git push" -ForegroundColor Yellow
