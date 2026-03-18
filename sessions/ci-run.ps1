# ci-run.ps1
# Full automated E2E pipeline: upload DLL -> Generate -> Discover -> Answer gaps
#                              -> Run 28 tests -> Score -> Save session -> Compare
#
# Run this on your desktop or in GitHub Actions after every push.
# The bridge must be open on the VM for DLL execution to work.
#
# Usage (desktop):
#   .\sessions\ci-run.ps1 -ApiUrl "https://mcp-factory-ui.icycoast-8ddfa278.eastus.azurecontainerapps.io" -DllPath "C:\path\to\contoso_cs.dll"
#
# Usage (GitHub Actions) -- see .github/workflows/e2e-test.yml
#   Secrets needed: PIPELINE_API_URL, PIPELINE_API_KEY
#
# Flags:
#   -SkipUpload     -- reuse an existing job (pass -JobId to skip upload+generate)
#   -SkipDiscover   -- skip exploration (use existing vocab)
#   -SkipTests      -- score only, no new test run
#   -SkipSave       -- don't call save-session.ps1 (useful in CI to save API calls)
#   -FailOnRegress  -- exit 1 if score is lower than previous session

param(
    [Parameter(Mandatory=$true)][string]$ApiUrl,
    [string]$DllPath        = "",           # path to .dll on this machine
    [string]$JobId          = "",           # skip upload if already have a job
    [string]$ApiKey         = "",
    [string]$Hints          = "",           # free-text hints for discovery
    [string]$UseCases       = "",           # use cases for discovery
    [string]$AnswersJson    = "",           # path to gap answers JSON (default: CONTOSO_CS_ANSWERS.json)
    [string]$SessionsRoot   = $PSScriptRoot,
    [int]   $PollIntervalSec = 10,          # how often to poll job status
    [int]   $TimeoutMin     = 30,           # max wait for generate+discover
    [switch]$SkipUpload,
    [switch]$SkipDiscover,
    [switch]$SkipTests,
    [switch]$SkipSave,
    [switch]$FailOnRegress
)

$ErrorActionPreference = "Stop"
$startTime = Get-Date

# -- Resolve env vars (GitHub Actions passes these as env, not params) ---------
if (-not $ApiUrl  -and $env:PIPELINE_API_URL)  { $ApiUrl  = $env:PIPELINE_API_URL }
if (-not $ApiKey  -and $env:PIPELINE_API_KEY)  { $ApiKey  = $env:PIPELINE_API_KEY }
if (-not $DllPath -and $env:MCP_FACTORY_DLL_PATH) { $DllPath = $env:MCP_FACTORY_DLL_PATH }

$base    = $ApiUrl.TrimEnd('/')
$headers = @{ "Accept" = "application/json" }
if ($ApiKey) { $headers["X-API-Key"] = $ApiKey }

Write-Host ""
Write-Host "================================================" -ForegroundColor Cyan
Write-Host "  MCP Factory -- Full E2E CI Run" -ForegroundColor Cyan
Write-Host "  $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" -ForegroundColor DarkCyan
Write-Host "================================================" -ForegroundColor Cyan
Write-Host "  API: $base"
if ($DllPath) { Write-Host "  DLL: $DllPath" }
Write-Host ""

# -- Helper: poll a job until status is done/error or timeout -----------------
function Wait-ForJob {
    param([string]$Id, [string]$Phase, [int]$MaxMin = $TimeoutMin)
    $deadline = (Get-Date).AddMinutes($MaxMin)
    $dots = 0
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds $PollIntervalSec
        try {
            $r = Invoke-RestMethod -Uri "$base/api/jobs/$Id" -Headers $headers -UseBasicParsing
        } catch {
            Write-Host "  [poll] HTTP error: $($_.Exception.Message)" -ForegroundColor DarkYellow
            continue
        }
        $st = $r.status
        $ph = if ($r.explore_phase) { "  [$($r.explore_phase)] $($r.explore_progress)" } else { "" }
        $dots++
        if ($dots % 6 -eq 0) {
            Write-Host "  [wait] $Phase status=$st$ph" -ForegroundColor DarkGray
        }
        if ($Phase -eq "generate" -and $st -in @("done","error")) { return $r }
        if ($Phase -eq "discover" -and $r.explore_phase -in @("done","complete","error","failed")) { return $r }
        if ($Phase -eq "generate" -and $r.generate_phase -in @("done","complete","error","failed")) { return $r }
    }
    Write-Host "  [TIMEOUT] $Phase did not complete within $MaxMin minutes" -ForegroundColor Red
    return $null
}

function Write-Step { param($n, $msg) Write-Host "  STEP $n -- $msg" -ForegroundColor White }
function Write-OK   { param($msg)     Write-Host "    OK: $msg" -ForegroundColor Green }
function Write-Fail { param($msg)     Write-Host "    FAIL: $msg" -ForegroundColor Red; exit 1 }
function Write-Skip { param($msg)     Write-Host "    SKIP: $msg" -ForegroundColor DarkGray }

# =============================================================================
# STEP 1 -- Upload DLL + trigger Generate
# =============================================================================
if ($SkipUpload -and $JobId) {
    Write-Step 1 "Upload -- skipped (using existing job: $JobId)"
    Write-Skip "Reusing job $JobId"
} else {
    Write-Step 1 "Upload DLL + trigger Generate"

    if (-not $DllPath -or -not (Test-Path $DllPath)) {
        Write-Fail "DllPath not found: '$DllPath'. Pass -DllPath or set MCP_FACTORY_DLL_PATH."
    }

    $fileBytes = [System.IO.File]::ReadAllBytes($DllPath)
    $fileName  = Split-Path $DllPath -Leaf

    # Build multipart form manually (Invoke-RestMethod handles this with -Form)
    $formParams = @{ file = Get-Item $DllPath }
    if ($Hints)    { $formParams["hints"]     = $Hints }
    if ($UseCases) { $formParams["use_cases"] = $UseCases }

    try {
        $resp = Invoke-RestMethod -Uri "$base/api/analyze" -Method POST `
            -Headers $headers -Form $formParams -UseBasicParsing
        $JobId = $resp.job_id
        Write-OK "Uploaded $fileName -> job_id: $JobId"
    } catch {
        Write-Fail "Upload failed: $($_.Exception.Message)"
    }

    # Poll until generate (schema extraction) is done
    Write-Host "  Waiting for Generate to complete..." -ForegroundColor DarkGray
    $result = Wait-ForJob -Id $JobId -Phase "generate"
    if (-not $result) { Write-Fail "Generate timed out." }
    if ($result.status -eq "error") { Write-Fail "Generate error: $($result.error)" }
    Write-OK "Generate complete. status=$($result.status)"
}

# =============================================================================
# STEP 2 -- Trigger Discover (explore)
# =============================================================================
if ($SkipDiscover) {
    Write-Step 2 "Discover -- skipped"
} else {
    Write-Step 2 "Trigger Discover (explore all invocables)"

    try {
        $r = Invoke-RestMethod -Uri "$base/api/jobs/$JobId/explore" -Method POST `
            -Headers $headers -ContentType "application/json" -Body "{}" -UseBasicParsing
        Write-OK "Discover queued"
    } catch {
        # 409 means already running -- that's fine
        if ($_.Exception.Response.StatusCode -eq 409) {
            Write-OK "Discover already running"
        } else {
            Write-Fail "Discover trigger failed: $($_.Exception.Message)"
        }
    }

    Write-Host "  Waiting for Discover to complete (this takes a few minutes)..." -ForegroundColor DarkGray
    $result = Wait-ForJob -Id $JobId -Phase "discover"
    if (-not $result) { Write-Fail "Discover timed out." }
    Write-OK "Discover complete. explore_phase=$($result.explore_phase)"
}

# =============================================================================
# STEP 3 -- Submit clarification answers (skip UI entirely)
# =============================================================================
Write-Step 3 "Submit clarification answers"

# Find answers file
if (-not $AnswersJson) {
    $AnswersJson = Join-Path $SessionsRoot "CONTOSO_CS_ANSWERS.json"
}

if (Test-Path $AnswersJson) {
    $ansBody = Get-Content $AnswersJson -Raw
    try {
        $r = Invoke-RestMethod -Uri "$base/api/jobs/$JobId/answer-gaps" -Method POST `
            -Headers $headers -ContentType "application/json" -Body $ansBody -UseBasicParsing
        Write-OK "Submitted $($r.answers_stored) answers from $AnswersJson"
    } catch {
        Write-Host "    WARN: answer-gaps failed (non-fatal): $($_.Exception.Message)" -ForegroundColor Yellow
    }
} else {
    Write-Skip "No answers file at $AnswersJson -- clarification questions unanswered"
}

# =============================================================================
# STEP 4 -- Run 28 test prompts (headless, one at a time)
# =============================================================================
if ($SkipTests) {
    Write-Step 4 "Run tests -- skipped"
    $outDir = Join-Path $SessionsRoot "_runs\$JobId"
} else {
    Write-Step 4 "Run 28 test prompts (headless)"

    $runScript = Join-Path $SessionsRoot "run-tests.ps1"
    if (-not (Test-Path $runScript)) {
        Write-Fail "run-tests.ps1 not found at $runScript"
    }

    $outDir = Join-Path $SessionsRoot "_runs\$JobId"
    New-Item -ItemType Directory -Force -Path $outDir | Out-Null

    & $runScript -ApiUrl $base -JobId $JobId -OutDir $outDir `
        $(if ($ApiKey) { @("-ApiKey", $ApiKey) } else { @() })

    if ($LASTEXITCODE -ne 0) {
        Write-Host "    WARN: run-tests.ps1 exited with $LASTEXITCODE (some tests may have failed)" -ForegroundColor Yellow
    }
    Write-OK "Tests complete. Transcript: $outDir\chat_transcript.txt"
}

# =============================================================================
# STEP 5 -- Auto-score
# =============================================================================
Write-Step 5 "Auto-score transcript"

$scoreScript = Join-Path $SessionsRoot "score-session.ps1"
if (Test-Path $scoreScript) {
    & $scoreScript -SessionDir $outDir -SessionsRoot $SessionsRoot
    Write-OK "Scored. See $outDir\TEST_RESULTS.md"
} else {
    Write-Skip "score-session.ps1 not found"
}

# Read score for summary + regression check
$passCount  = 0
$totalCount = 0
$pct        = "n/a"
$resultsFile = Join-Path $outDir "TEST_RESULTS.md"
if (Test-Path $resultsFile) {
    $content = Get-Content $resultsFile -Raw
    if ($content -match 'Score:\s+(\d+)\s*/\s*(\d+)') {
        $passCount  = [int]$Matches[1]
        $totalCount = [int]$Matches[2]
        if ($totalCount -gt 0) { $pct = "{0:P0}" -f ($passCount / $totalCount) }
    }
}

# =============================================================================
# STEP 6 -- Save session snapshot
# =============================================================================
if ($SkipSave) {
    Write-Step 6 "Save session -- skipped"
} else {
    Write-Step 6 "Save session snapshot"

    $saveScript = Join-Path $SessionsRoot "..\scripts\save-session.ps1"
    if (Test-Path $saveScript) {
        & $saveScript -ApiUrl $base -JobId $JobId -TranscriptPath (Join-Path $outDir "chat_transcript.txt")
        Write-OK "Session saved"
    } else {
        Write-Skip "save-session.ps1 not found at $saveScript"
    }
}

# =============================================================================
# STEP 7 -- Compare with prior sessions
# =============================================================================
Write-Step 7 "Compare with prior sessions"

$compareScript = Join-Path $SessionsRoot "compare.ps1"
if (Test-Path $compareScript) {
    & $compareScript -Count 5
} else {
    Write-Skip "compare.ps1 not found"
}

# =============================================================================
# Summary
# =============================================================================
$elapsed = [Math]::Round(((Get-Date) - $startTime).TotalMinutes, 1)

Write-Host ""
Write-Host "================================================" -ForegroundColor Cyan
Write-Host "  E2E Run Complete -- $elapsed min" -ForegroundColor Cyan
Write-Host "  Job:   $JobId" -ForegroundColor White
Write-Host "  Score: $passCount / $totalCount  ($pct)" -ForegroundColor $(
    if ($pct -eq "n/a")                             { "Gray" }
    elseif ([double]($pct.TrimEnd('%')) -ge 85)     { "Green" }
    elseif ([double]($pct.TrimEnd('%')) -ge 70)     { "Yellow" }
    else                                            { "Red" }
)
Write-Host "================================================" -ForegroundColor Cyan
Write-Host ""

# -- Regression check ----------------------------------------------------------
if ($FailOnRegress -and $totalCount -gt 0) {
    # Read previous best from index.json
    $indexPath = Join-Path $SessionsRoot "index.json"
    $prevBest  = 0
    if (Test-Path $indexPath) {
        try {
            $idx = Get-Content $indexPath -Raw | ConvertFrom-Json
            $allScores = @($idx | Where-Object { $_.test_score } | ForEach-Object { [int]$_.test_score })
            if ($allScores.Count -gt 0) { $prevBest = ($allScores | Measure-Object -Maximum).Maximum }
        } catch {}
    }
    if ($passCount -lt $prevBest) {
        Write-Host "  REGRESSION: $passCount < previous best $prevBest" -ForegroundColor Red
        exit 1
    }
}
