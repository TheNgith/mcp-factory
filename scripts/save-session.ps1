# save-session.ps1
# Capture a point-in-time snapshot and delegate contract intelligence to scripts/collect_session.py.

param(
    [Parameter(Mandatory=$true)][string]$JobId,
    [Parameter(Mandatory=$true)][string]$ApiUrl,
    [string]$Note = "",
    [string]$SessionsRoot = "$PSScriptRoot\..\sessions",
    [string]$ApiKey = "",
    [string]$TranscriptPath = "",
    [string]$OutDir = "",
    [switch]$CompatibilityMode,
    [switch]$EnforceHardFail
)

if (-not $ApiKey) {
    $envKey = [System.Environment]::GetEnvironmentVariable("MCP_FACTORY_API_KEY")
    if ($envKey) { $ApiKey = $envKey }
}

Push-Location $PSScriptRoot\..
$commitHash = git rev-parse --short HEAD 2>$null
$commitMsg  = git log -1 --format="%s" 2>$null
Pop-Location
if (-not $commitHash) { $commitHash = "nogit" }
if (-not $commitMsg)  { $commitMsg  = "no-message" }

$datePart = Get-Date -Format "yyyy-MM-dd"

Write-Host ""
Write-Host "=== MCP Factory - Save Session ===" -ForegroundColor Cyan
Write-Host "  Commit : $commitHash  $commitMsg"
Write-Host "  Job ID : $JobId"

New-Item -ItemType Directory -Force -Path $SessionsRoot | Out-Null
$tempDir = Join-Path $SessionsRoot ("_tmp-" + $JobId)
if (Test-Path $tempDir) { Remove-Item $tempDir -Recurse -Force }
New-Item -ItemType Directory -Force -Path $tempDir | Out-Null

$zipPath = Join-Path $env:TEMP ("mcp-session-" + $JobId + ".zip")
if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
$snapshotUrl = $ApiUrl.TrimEnd('/') + "/api/jobs/" + $JobId + "/session-snapshot"
$headers = @{}
if ($ApiKey) { $headers["X-Pipeline-Key"] = $ApiKey }

try {
    Write-Host "  Downloading from $snapshotUrl ..." -ForegroundColor Yellow
    Invoke-WebRequest -Uri $snapshotUrl -Headers $headers -OutFile $zipPath -UseBasicParsing
    $size = (Get-Item $zipPath).Length
    if ($size -lt 2048) {
        Write-Host "  ERROR: capture_too_small (zip < 2KB)" -ForegroundColor Red
        exit 1
    }
    Write-Host ("  Downloaded " + [Math]::Round($size / 1024, 1) + " KB") -ForegroundColor Green

    Expand-Archive -Path $zipPath -DestinationPath $tempDir -Force

    $component = "unknown"
    $tempMeta = Join-Path $tempDir "session-meta.json"
    if (Test-Path $tempMeta) {
        try {
            $meta = Get-Content $tempMeta -Raw | ConvertFrom-Json
            if ($meta.component -and $meta.component -ne "unknown") { $component = $meta.component }
        } catch { }
    }

    if (-not $Note) {
        $prefix = $JobId.Substring(0, [Math]::Min(6, $JobId.Length))
        $runs = @(Get-ChildItem $SessionsRoot -Directory -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -like ("*" + $prefix + "*") }).Count
        $slug = ($component -replace '[^a-zA-Z0-9]', '-' -replace '-{2,}', '-').ToLower().TrimEnd('-')
        if (-not $slug) { $slug = "unknown" }
        $Note = $slug + "-run" + ($runs + 1)
    }

    if ($OutDir) {
        $sessionDir = $OutDir
        $folderName = Split-Path $sessionDir -Leaf
        New-Item -ItemType Directory -Force -Path $sessionDir | Out-Null
        Get-ChildItem $tempDir -Force | ForEach-Object {
            Copy-Item $_.FullName (Join-Path $sessionDir $_.Name) -Recurse -Force
        }
    } else {
        $noteSlug = ($Note -replace '[^a-zA-Z0-9]', '-' -replace '-{2,}', '-' -replace '^-|-$', '').ToLower()
        if (-not $noteSlug) { $noteSlug = "run" }
        $folderName = $datePart + "-" + $commitHash + "-" + $noteSlug
        $sessionDir = Join-Path $SessionsRoot $folderName
        if (Test-Path $sessionDir) {
            $i = 2
            while (Test-Path ($sessionDir + "-" + $i)) { $i++ }
            $sessionDir = $sessionDir + "-" + $i
            $folderName = Split-Path $sessionDir -Leaf
        }
        Move-Item $tempDir $sessionDir
        New-Item -ItemType Directory -Force -Path $sessionDir | Out-Null
    }

    $humanDir = Join-Path $sessionDir "human"
    New-Item -ItemType Directory -Force -Path $humanDir | Out-Null

    Write-Host "  Folder : $sessionDir" -ForegroundColor Cyan

    $repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
    $collectScript = Join-Path $PSScriptRoot "collect_session.py"
    $venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
    $pythonExe = if (Test-Path $venvPython) { $venvPython } else { "python" }
    if (-not (Test-Path $collectScript)) {
        Write-Host "  ERROR: scripts/collect_session.py not found" -ForegroundColor Red
        exit 1
    }

    $savedAt = Get-Date -Format "o"
    $mode = if ($CompatibilityMode) { "compatibility" } else { "strict" }

    $collectArgs = @(
        $collectScript,
        "--session-dir", $sessionDir,
        "--mode", $mode,
        "--job-id", $JobId,
        "--folder", $folderName,
        "--component", $component,
        "--commit", $commitHash,
        "--commit-msg", $commitMsg,
        "--note", $Note,
        "--date", $datePart,
        "--saved-at", $savedAt
    )
    if ($EnforceHardFail) { $collectArgs += "--enforce-hard-fail" }

    & $pythonExe @collectArgs
    $collectExit = $LASTEXITCODE

    $collectPath = Join-Path $humanDir "collect-session-result.json"
    if (-not (Test-Path $collectPath)) {
        Write-Host "  ERROR: collect_session did not emit human/collect-session-result.json" -ForegroundColor Red
        exit 1
    }

    try {
        $collectResult = Get-Content $collectPath -Raw | ConvertFrom-Json
        $cv = [bool]$collectResult.contract.valid
        $hf = [bool]$collectResult.contract.hard_fail
        $cq = [string]$collectResult.session_save_meta.capture_quality
        Write-Host ("  Contract valid : " + $cv) -ForegroundColor $(if ($cv) {"Green"} else {"Yellow"})
        Write-Host ("  Hard fail      : " + $hf) -ForegroundColor $(if ($hf) {"Red"} else {"Green"})
        Write-Host ("  Capture quality: " + $cq) -ForegroundColor Cyan
    } catch {
        Write-Host "  WARNING: unable to parse collect-session-result summary" -ForegroundColor Yellow
    }

    if ($collectExit -eq 2) {
        Write-Host "  ERROR: strict hard-fail gate triggered" -ForegroundColor Red
    } elseif ($collectExit -ne 0) {
        Write-Host "  ERROR: strict contract validation failed" -ForegroundColor Red
    }

    exit $collectExit
}
finally {
    if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
    if (Test-Path $tempDir) { Remove-Item $tempDir -Recurse -Force }
}
