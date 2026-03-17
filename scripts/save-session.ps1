# save-session.ps1
# Run from the repo root after each meaningful test/iteration.
# Creates a dated snapshot folder under sessions/ with all current artifacts.
#
# Usage:
#   .\scripts\save-session.ps1
#   .\scripts\save-session.ps1 -JobId "a0fc70e8" -Note "payment-flow-test"
#   .\scripts\save-session.ps1 -DownloadsDir "C:\Users\me\Downloads" -JobId "abc123"
#
# What it captures:
#   - Git commit hash + message (folder name)
#   - Any .md/.py/.json files in Downloads matching *example* or *refinement*
#   - A template chat-transcript.md for you to fill in
#   - The live artifacts from blob storage if you've downloaded them

param(
    [string]$JobId = "",
    [string]$Note = "",
    [string]$DownloadsDir = "$env:USERPROFILE\Downloads",
    [string]$SessionsRoot = "$PSScriptRoot\..\sessions"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ── 1. Get git info ────────────────────────────────────────────────────────────
$commitHash  = (git rev-parse --short HEAD 2>$null) ?? "nogit"
$commitMsg   = (git log -1 --format="%s" 2>$null) ?? "no-message"
# Sanitize commit message for use as folder name
$safeMsg = $commitMsg -replace '[^a-zA-Z0-9\-]', '-' -replace '-{2,}', '-' -replace '^-|-$', ''
$safeMsg = $safeMsg.Substring(0, [Math]::Min(40, $safeMsg.Length))

$datePart = Get-Date -Format "yyyy-MM-dd"
$noteStr  = if ($Note) { "-$($Note -replace '[^a-zA-Z0-9]','-')" } else { "" }
$folderName = "$datePart-$commitHash-$safeMsg$noteStr"
$sessionDir = Join-Path $SessionsRoot $folderName

New-Item -ItemType Directory -Force -Path $sessionDir | Out-Null
Write-Host "`n>>> Session folder: $sessionDir" -ForegroundColor Cyan

# ── 2. Copy artifact files from Downloads ─────────────────────────────────────
$patterns = @("*example*", "*refinement*", "*behavioral_spec*", "*api_reference*", "*mcp_schema*", "*report*")
$copied   = 0
foreach ($pattern in $patterns) {
    Get-ChildItem -Path $DownloadsDir -Filter $pattern -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Extension -in @(".md", ".py", ".json", ".txt", ".csv") } |
    ForEach-Object {
        $dest = Join-Path $sessionDir $_.Name
        Copy-Item $_.FullName -Destination $dest -Force
        Write-Host "  Copied: $($_.Name)" -ForegroundColor Green
        $copied++
    }
}
if ($copied -eq 0) { Write-Host "  (no matching files found in Downloads)" -ForegroundColor Yellow }

# ── 3. If job_id provided, copy any job-specific blobs you've downloaded ───────
if ($JobId) {
    Get-ChildItem -Path $DownloadsDir -Filter "*$JobId*" -File -ErrorAction SilentlyContinue |
    ForEach-Object {
        $dest = Join-Path $sessionDir $_.Name
        Copy-Item $_.FullName -Destination $dest -Force
        Write-Host "  Copied (job): $($_.Name)" -ForegroundColor Green
    }
}

# ── 4. Write transcript template ───────────────────────────────────────────────
$transcriptPath = Join-Path $sessionDir "chat-transcript.md"
if (-not (Test-Path $transcriptPath)) {
    $jobLine = if ($JobId) { "**Job ID:** ``$JobId``  " } else { "**Job ID:** (fill in)  " }
    $template = @"
# Session Transcript — $datePart
**Commit:** ``$commitHash`` $commitMsg  
$jobLine
**Component:** (fill in)

---

## Test prompt

**User:** ""

| Round | Tool | Args | Result |
|---|---|---|---|
| 1 | | | |

**LLM summary:**  
**Rounds:**  

---

## What worked

-

## What failed / open questions

-

## Schema changes made this session

-
"@
    Set-Content -Path $transcriptPath -Value $template -Encoding UTF8
    Write-Host "  Created: chat-transcript.md (template)" -ForegroundColor Green
}

# ── 5. Write a metadata file ───────────────────────────────────────────────────
$meta = [ordered]@{
    date        = $datePart
    commit      = $commitHash
    commit_msg  = $commitMsg
    job_id      = $JobId
    note        = $Note
    saved_at    = (Get-Date -Format "o")
    files       = (Get-ChildItem $sessionDir | Select-Object -ExpandProperty Name)
}
$meta | ConvertTo-Json | Set-Content -Path (Join-Path $sessionDir "session-meta.json") -Encoding UTF8

Write-Host "`n>>> Done. $copied artifact(s) saved to:" -ForegroundColor Cyan
Write-Host "    $sessionDir" -ForegroundColor White
Write-Host "`n    Edit chat-transcript.md to record what you tested." -ForegroundColor Gray
