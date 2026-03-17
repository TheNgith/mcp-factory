# save-session.ps1
# Capture a full point-in-time snapshot of a discovery job into sessions/.
# Run after each meaningful test or iteration to build a progressive record
# you can git-diff across runs.
#
# What gets saved per session:
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
# Usage:
#   .\scripts\save-session.ps1 -ApiUrl "https://your-api.azurecontainerapps.io" -JobId "a0fc70e8"
#   .\scripts\save-session.ps1 -ApiUrl "http://localhost:8000" -JobId "a0fc70e8" -Note "payment-test"
#
# The -ApiUrl can be the UI proxy or the API directly (both expose the snapshot endpoint).

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
Pop-Location

$safeMsg    = ($commitMsg -replace '[^a-zA-Z0-9\-]', '-' -replace '-{2,}', '-' -replace '^-|-$', '').Substring(0, [Math]::Min(40, $commitMsg.Length))
$datePart   = Get-Date -Format "yyyy-MM-dd"
$noteStr    = if ($Note) { "-$($Note -replace '[^a-zA-Z0-9]','-')" } else { "" }
$folderName = "$datePart-$commitHash-$safeMsg$noteStr"
$sessionDir = Join-Path $SessionsRoot $folderName

New-Item -ItemType Directory -Force -Path $sessionDir            | Out-Null
New-Item -ItemType Directory -Force -Path "$sessionDir\schema"   | Out-Null
New-Item -ItemType Directory -Force -Path "$sessionDir\artifacts"| Out-Null

Write-Host "`n=== MCP Factory — Save Session ===" -ForegroundColor Cyan
Write-Host "  Commit : $commitHash  $commitMsg"
Write-Host "  Job ID : $JobId"
Write-Host "  Folder : $sessionDir`n"

# ── 2. Download snapshot ZIP from the API ─────────────────────────────────────
$snapshotUrl = "$($ApiUrl.TrimEnd('/'))/api/jobs/$JobId/session-snapshot"
$zipPath     = Join-Path $env:TEMP "mcp-session-$JobId.zip"

$headers = @{}
if ($ApiKey) { $headers["X-API-Key"] = $ApiKey }

Write-Host "  Downloading snapshot from $snapshotUrl …" -ForegroundColor Yellow
try {
    Invoke-WebRequest -Uri $snapshotUrl -Headers $headers -OutFile $zipPath -UseBasicParsing
    Write-Host "  Download complete ($([Math]::Round((Get-Item $zipPath).Length / 1KB, 1)) KB)" -ForegroundColor Green
} catch {
    Write-Error "  Failed to download snapshot: $_"
    exit 1
}

# ── 3. Extract ZIP into session folder ────────────────────────────────────────
Expand-Archive -Path $zipPath -DestinationPath $sessionDir -Force
Remove-Item $zipPath -Force
Write-Host "  Extracted to: $sessionDir"

# ── 4. Rename extracted schema files to match folder layout ───────────────────
# The ZIP already has schema/ and artifacts/ subdirs — nothing to rename.
# List what landed:
$files = Get-ChildItem -Path $sessionDir -Recurse -File
Write-Host "`n  Files saved:" -ForegroundColor Cyan
$files | ForEach-Object { Write-Host "    $($_.FullName.Replace($sessionDir, '').TrimStart('\/'))" }

# ── 5. Stamp commit info into session-meta.json ───────────────────────────────
$metaPath = Join-Path $sessionDir "session-meta.json"
if (Test-Path $metaPath) {
    $meta = Get-Content $metaPath -Raw | ConvertFrom-Json
    $meta | Add-Member -NotePropertyName "commit"      -NotePropertyValue $commitHash -Force
    $meta | Add-Member -NotePropertyName "commit_msg"  -NotePropertyValue $commitMsg  -Force
    $meta | Add-Member -NotePropertyName "note"        -NotePropertyValue $Note       -Force
    $meta | Add-Member -NotePropertyName "saved_at"    -NotePropertyValue (Get-Date -Format "o") -Force
    $meta | ConvertTo-Json -Depth 5 | Set-Content $metaPath -Encoding UTF8
}

# ── 6. Create chat-transcript.md template if not present ──────────────────────
$transcriptPath = Join-Path $sessionDir "chat-transcript.md"
if (-not (Test-Path $transcriptPath)) {
    $component = if ($meta) { $meta.component } else { "(fill in)" }
    $template = @"
# Session Transcript — $datePart
**Commit:** ``$commitHash`` — $commitMsg
**Job ID:** ``$JobId``
**Component:** $component

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
    Write-Host "`n  Created chat-transcript.md (fill this in after testing)" -ForegroundColor Yellow
}

# ── 7. Append a row to sessions/README.md index ───────────────────────────────
$readmePath = Join-Path $SessionsRoot "README.md"
if (Test-Path $readmePath) {
    $component  = if ($meta -and $meta.component -ne "unknown") { $meta.component } else { "(unknown)" }
    $newRow     = "| $datePart | ``$commitHash`` | ``$JobId`` | $($Note ? $Note : '-') | $component — (fill in key findings) |"
    $readmeText = Get-Content $readmePath -Raw
    if ($readmeText -notlike "*$JobId*") {
        # Append after the last table row
        $updated = $readmeText.TrimEnd() + "`n$newRow`n"
        Set-Content -Path $readmePath -Value $updated -Encoding UTF8
        Write-Host "  Updated sessions/README.md index" -ForegroundColor Green
    }
}

Write-Host "`n=== Done ===`n" -ForegroundColor Green
Write-Host "Next steps:"
Write-Host "  1. Paste your chat session into:"
Write-Host "     $transcriptPath" -ForegroundColor White
Write-Host "  2. Commit:"
Write-Host "     git add sessions\ ; git commit -m `"session: $folderName`" ; git push" -ForegroundColor White

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
