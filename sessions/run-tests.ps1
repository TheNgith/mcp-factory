# run-tests.ps1
# Headless test runner: sends all 28 CONTOSO_CS_TEST_SUITE prompts to /api/chat
# and writes a machine-readable chat_transcript.txt ready for score-session.ps1.
#
# Usage:
#   .\sessions\run-tests.ps1 -ApiUrl "https://mcp-factory-ui.icycoast-8ddfa278.eastus.azurecontainerapps.io" -JobId "abc12345"
#   .\sessions\run-tests.ps1 -ApiUrl "..." -JobId "abc12345" -OutDir "sessions\2026-03-18-..."
#   .\sessions\run-tests.ps1 -ApiUrl "..." -JobId "abc12345" -Tests T04,T07,T15,T16   # specific tests only
#   .\sessions\run-tests.ps1 -ApiUrl "..." -JobId "abc12345" -ScoreAfter               # auto-score when done
#
# Produces:
#   <OutDir>\chat_transcript.txt   -- full transcript with per-test T-ID headers
#   <OutDir>\TEST_RESULTS.md       -- if -ScoreAfter is set
#
# Then call save-session.ps1 to bundle everything into a session snapshot.

param(
    [Parameter(Mandatory=$true)][string]$ApiUrl,
    [Parameter(Mandatory=$true)][string]$JobId,
    [string]  $OutDir      = "",          # defaults to sessions/_runs/{JobId}
    [string]  $ApiKey      = "",
    [string[]]$Tests       = @(),         # e.g. @("T04","T07") -- empty = all 28
    [int]     $TimeoutSec  = 180,         # per-prompt API timeout
    [int]     $Concurrency = 7,           # parallel requests (RunspacePool)
    [switch]  $ScoreAfter,               # run score-session.ps1 when done
    [string]  $SessionsRoot = $PSScriptRoot
)

$ErrorActionPreference = "Stop"

# Resolve OutDir
if (-not $OutDir) {
    $OutDir = Join-Path $SessionsRoot "_runs\$JobId"
}
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

# Headers
$headers = @{ "Content-Type" = "application/json" }
if ($ApiKey) { $headers["X-Pipeline-Key"] = $ApiKey }
$baseUrl = $ApiUrl.TrimEnd('/')

# -- Test suite -----------------------------------------------------------------
# Each entry: ID, prompt text
$testSuite = [ordered]@{
    T01 = "What version of the contoso CRM system is running?"
    T02 = "Is the CRM system currently initialized?"
    T03 = "How many customers and orders are currently tracked?"
    T04 = "Look up customer 7."
    T05 = "Get the account details for customer 42."
    T06 = "Process a refund for order ORD-20040301-0042 for customer CUST-001, refund amount `$18.50."
    T07 = "Look up customer ABC."
    T08 = "Process a `$25.00 payment for CUST-042."
    T09 = "Issue a `$150.99 refund to CUST-007 for order ORD-20260315-0117."
    T10 = "What is the current balance for CUST-010? Display it in dollars."
    T11 = "Customer CUST-042 wants to redeem 500 points. Process the redemption."
    T12 = "Customer CUST-003 says they can't log in. Diagnose and fix the problem."
    T13 = "Unlock the account for CUST-010 even though they're already active."
    T14 = "Process a `$30 payment for CUST-003."
    T15 = "I called CS_ProcessPayment and got back 4294967291. What does that mean and how do I fix it?"
    T16 = "CS_UnlockAccount returned 4294967292. What happened?"
    T17 = "CS_ProcessRefund returned an access violation. What went wrong?"
    T18 = "Try to process a `$50 payment for CUST-005 without initializing first."
    T19 = "Show me the full profile for customer CUST-001."
    T20 = "What loyalty tier is CUST-015 on?"
    T21 = "What email and phone number do we have on file for CUST-022?"
    T22 = "Customer CUST-042 wants to redeem 500 loyalty points and then process a payment of `$25.00. Initialize the system first, check their status, redeem the points, then process the payment."
    T23 = "Customer CUST-019 placed order ORD-20260315-0117 for `$99.00 but wants a full refund. Handle it end to end."
    T24 = "Process payments of `$10 for CUST-001, `$20 for CUST-002, and `$30 for CUST-003 in sequence. Report any failures."
    T25 = "Redeem 200 points for CUST-008, but first make sure their account is in good standing."
    T26 = "Process a `$0.00 payment for CUST-001."
    T27 = "Customer CUST-042 has 300 points. Redeem 500 points for them."
    T28 = "Look up LOCKED."
}

# Filter to requested tests
if ($Tests.Count -gt 0) {
    $runIds = $Tests | Where-Object { $testSuite.ContainsKey($_) }
    if ($runIds.Count -eq 0) {
        Write-Host "None of the requested test IDs found: $($Tests -join ', ')" -ForegroundColor Red
        exit 1
    }
} else {
    $runIds = $testSuite.Keys
}

# -- Run loop (parallel via RunspacePool, PS 5.1 compatible) -------------------
Write-Host ""
Write-Host "=== MCP Factory -- Headless Test Runner ===" -ForegroundColor Cyan
Write-Host "  API         : $baseUrl"
Write-Host "  Job ID      : $JobId"
Write-Host "  Tests       : $($runIds -join ', ')"
Write-Host "  Concurrency : $Concurrency"
Write-Host "  Output      : $OutDir"
Write-Host ""

# Script block executed in each runspace -- returns a PSCustomObject result
$workerScript = {
    param($baseUrl, $JobId, $headers, $id, $prompt, $TimeoutSec)

    $bodyJson = (@{ job_id=$JobId; messages=@(@{role="user";content=$prompt}) } | ConvertTo-Json -Depth 5 -Compress)
    $assistantText = ""
    $toolLines     = @()
    $errorMsg      = ""

    try {
        $resp = Invoke-WebRequest -Uri "$baseUrl/api/chat" -Method POST `
            -Headers $headers -Body $bodyJson -ContentType "application/json" `
            -TimeoutSec $TimeoutSec -UseBasicParsing

        $sb = [System.Text.StringBuilder]::new()
        foreach ($line in ($resp.Content -split "`n")) {
            $line = $line.Trim()
            if ($line -match '^data:\s*(.+)$') {
                try {
                    $evt = $Matches[1] | ConvertFrom-Json
                    switch ($evt.type) {
                        "token"       { [void]$sb.Append($evt.content) }
                        "tool_call"   {
                            $argsStr = if ($evt.args) { $evt.args | ConvertTo-Json -Compress -Depth 5 } else { "{}" }
                            $toolLines += "TOOL: $($evt.name)($argsStr)"
                        }
                        "tool_result" { $toolLines += "   -> $($evt.result)" }
                        "error"       { $errorMsg = $evt.message }
                    }
                } catch {}
            }
        }
        $assistantText = $sb.ToString().Trim()
    } catch {
        $errorMsg = $_.Exception.Message
    }

    [PSCustomObject]@{
        Id            = $id
        Prompt        = $prompt
        AssistantText = $assistantText
        ToolLines     = $toolLines
        Error         = $errorMsg
    }
}

# -- Dispatch all tests in parallel --------------------------------------------
$pool = [RunspaceFactory]::CreateRunspacePool(1, $Concurrency)
$pool.Open()

$pending = [System.Collections.Generic.List[hashtable]]::new()
foreach ($id in $runIds) {
    $ps = [PowerShell]::Create()
    $ps.RunspacePool = $pool
    [void]$ps.AddScript($workerScript).AddParameters(@{
        baseUrl    = $baseUrl
        JobId      = $JobId
        headers    = $headers
        id         = $id
        prompt     = $testSuite[$id]
        TimeoutSec = $TimeoutSec
    })
    $pending.Add(@{ PS = $ps; Handle = $ps.BeginInvoke(); Id = $id })
}

# -- Harvest results as they complete -----------------------------------------
$resultMap  = @{}
$passCount  = 0
$failCount  = 0
$remaining  = [System.Collections.Generic.List[hashtable]]::new($pending)

Write-Host "  Waiting for $($pending.Count) tests (concurrency=$Concurrency)..." -ForegroundColor DarkGray

while ($remaining.Count -gt 0) {
    $done = @($remaining | Where-Object { $_.Handle.IsCompleted })
    foreach ($job in $done) {
        $r = $job.PS.EndInvoke($job.Handle)[0]
        $job.PS.Dispose()
        $remaining.Remove($job)
        $resultMap[$r.Id] = $r

        if ($r.Error) {
            Write-Host "  $($r.Id) ... TIMEOUT/ERROR ($($r.Error))" -ForegroundColor Red
            $failCount++
        } else {
            $preview = if ($r.AssistantText.Length -gt 60) { $r.AssistantText.Substring(0,60) + "..." } else { $r.AssistantText }
            Write-Host "  $($r.Id) ... OK -- $preview" -ForegroundColor Green
            $passCount++
        }
    }
    if ($remaining.Count -gt 0) { Start-Sleep -Milliseconds 300 }
}

$pool.Close()
$pool.Dispose()

# -- Build transcript in original test order -----------------------------------
$transcriptParts = [System.Collections.Generic.List[string]]::new()
$transcriptParts.Add("# MCP Factory -- Chat Transcript (headless)")
$transcriptParts.Add("# Job: $JobId  |  Date: $(Get-Date -Format 'yyyy-MM-dd HH:mm')")
$transcriptParts.Add("")

foreach ($id in $runIds) {
    $r = $resultMap[$id]
    if (-not $r) { continue }

    $transcriptParts.Add("# ==== TEST $id ====")
    $transcriptParts.Add("# Prompt: $($r.Prompt)")
    $transcriptParts.Add("")
    $transcriptParts.Add("[USER]")
    $transcriptParts.Add($r.Prompt)
    $transcriptParts.Add("")

    if ($r.ToolLines.Count -gt 0) {
        $transcriptParts.Add("[TOOL CALLS]")
        foreach ($tl in $r.ToolLines) { $transcriptParts.Add($tl) }
        $transcriptParts.Add("")
    }

    if ($r.Error) {
        $transcriptParts.Add("[ERROR]")
        $transcriptParts.Add($r.Error)
    } else {
        $transcriptParts.Add("[ASSISTANT]")
        $transcriptParts.Add($r.AssistantText)
    }

    $transcriptParts.Add("")
    $transcriptParts.Add("---")
    $transcriptParts.Add("")
}

$transcriptParts | Set-Content (Join-Path $OutDir "chat_transcript.txt") -Encoding UTF8

# -- Summary --------------------------------------------------------------------
Write-Host ""
Write-Host "  Completed: $passCount responded, $failCount errored/timed-out" -ForegroundColor $(if ($failCount -eq 0) { "Green" } else { "Yellow" })
Write-Host "  Transcript: $(Join-Path $OutDir 'chat_transcript.txt')" -ForegroundColor DarkCyan
Write-Host ""

# -- Auto-score ----------------------------------------------------------------
if ($ScoreAfter) {
    $scoreScript = Join-Path $SessionsRoot "score-session.ps1"
    if (Test-Path $scoreScript) {
        Write-Host "  Auto-scoring..." -ForegroundColor Yellow
        & $scoreScript -SessionDir $OutDir -SessionsRoot $SessionsRoot
    } else {
        Write-Host "  score-session.ps1 not found -- skipping auto-score." -ForegroundColor DarkYellow
    }
}

Write-Host "  Done. Next steps:" -ForegroundColor White
Write-Host "    1. .\sessions\score-session.ps1 -SessionDir `"$OutDir`"" -ForegroundColor DarkCyan
Write-Host "    2. .\scripts\save-session.ps1 -ApiUrl `"$baseUrl`" -JobId `"$JobId`"" -ForegroundColor DarkCyan
Write-Host "    3. .\sessions\compare.ps1" -ForegroundColor DarkCyan
Write-Host ""
