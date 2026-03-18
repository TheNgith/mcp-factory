# compare.ps1 — Phase 7-A: Cross-session regression detector
#
# Reads sessions/index.json and produces a side-by-side comparison table
# covering the last N sessions (default: all available, capped at 10).
#
# Usage:
#   .\sessions\compare.ps1
#   .\sessions\compare.ps1 -Count 3
#   .\sessions\compare.ps1 -Sessions "2026-03-17-abc1234-contoso-run1","2026-03-18-def5678-contoso-run2"
#   .\sessions\compare.ps1 -FailOnRegression   # exit 1 if any function regressed
#
# Regression: a function was "success" in session N-1 but is not "success" in session N.

param(
    [string[]]$Sessions       = @(),
    [int]     $Count          = 10,
    [switch]  $FailOnRegression,
    [string]  $SessionsRoot   = $PSScriptRoot
)

$indexPath = Join-Path $SessionsRoot "index.json"
if (-not (Test-Path $indexPath)) {
    Write-Host "No index.json found at $indexPath" -ForegroundColor Red
    exit 1
}

# ── Load index ────────────────────────────────────────────────────────────────
$rawJson = Get-Content $indexPath -Raw
$parsed  = ConvertFrom-Json $rawJson
$allSessions = if ($parsed -is [System.Array]) { @($parsed) } elseif ($null -ne $parsed) { @($parsed) } else { @() }

if ($allSessions.Count -eq 0) {
    Write-Host "index.json is empty — run save-session.ps1 first." -ForegroundColor Yellow
    exit 0
}

# If specific sessions were requested, filter; otherwise take the last $Count.
if ($Sessions.Count -gt 0) {
    $selected = @($allSessions | Where-Object { $Sessions -contains $_.folder })
} else {
    $selected = @($allSessions | Select-Object -Last $Count)
}

if ($selected.Count -eq 0) {
    Write-Host "No matching sessions found." -ForegroundColor Yellow
    exit 0
}

Write-Host ""
Write-Host "=== MCP Factory — Session Comparison ===" -ForegroundColor Cyan
Write-Host "  Comparing $($selected.Count) session(s)" -ForegroundColor DarkCyan
Write-Host ""

# ── Summary table ─────────────────────────────────────────────────────────────
$colWidth = 28
$header = "Session".PadRight($colWidth) + " | " +
          "Success".PadRight(7) + " | " +
          "Total".PadRight(5) + " | " +
          "Vocab%".PadRight(7) + " | " +
          "Coverage".PadRight(8) + " | " +
          "TxTools".PadRight(7) + " | " +
          "Verdict"
Write-Host $header -ForegroundColor White
Write-Host ("-" * ($header.Length + 4)) -ForegroundColor DarkGray

foreach ($s in $selected) {
    $fc       = $s.finding_counts
    $success  = if ($fc) { [int]($fc.success) } else { 0 }
    $total    = if ($fc) { [int]($fc.total)   } else { 0 }
    $vcpct    = if ($null -ne $s.vocab_completeness) { "{0:P0}" -f $s.vocab_completeness } else { "n/a" }
    $cov      = if ($null -ne $s.vocab_coverage)     { "{0:P0}" -f $s.vocab_coverage }     else { "n/a" }
    $txTools  = if ($s.transcript_metrics) { [int]($s.transcript_metrics.tool_calls) } else { "n/a" }
    $diag     = if ($s.verdict) { $s.verdict } else { "-" }

    $row = ($s.folder).PadRight($colWidth) + " | " +
           [string]$success.PadRight(7) + " | " +
           [string]$total.PadRight(5) + " | " +
           $vcpct.PadRight(7) + " | " +
           $cov.PadRight(8) + " | " +
           [string]$txTools.PadRight(7) + " | " +
           $diag
    Write-Host $row
}

# ── Regression detection ──────────────────────────────────────────────────────
if ($selected.Count -ge 2) {
    Write-Host ""
    Write-Host "── Regression check ─────────────────────────────────────────────" -ForegroundColor Cyan
    $regressions = @()
    for ($i = 1; $i -lt $selected.Count; $i++) {
        $prev = $selected[$i - 1]
        $curr = $selected[$i]
        $prevStatus = if ($prev.function_status) { $prev.function_status } else { $null }
        $currStatus = if ($curr.function_status) { $curr.function_status } else { $null }

        if ($null -eq $prevStatus -or $null -eq $currStatus) { continue }

        foreach ($prop in $prevStatus.PSObject.Properties) {
            $fn       = $prop.Name
            $prevSt   = $prop.Value
            $currSt   = if ($currStatus.PSObject.Properties[$fn]) { $currStatus.PSObject.Properties[$fn].Value } else { "(absent)" }

            if ($prevSt -eq "success" -and $currSt -ne "success") {
                $regressions += [PSCustomObject]@{
                    function = $fn
                    from     = $prev.folder
                    to       = $curr.folder
                    was      = $prevSt
                    now      = $currSt
                }
            }
        }
    }

    if ($regressions.Count -eq 0) {
        Write-Host "  No regressions detected ✅" -ForegroundColor Green
    } else {
        Write-Host "  REGRESSIONS DETECTED ❌" -ForegroundColor Red
        foreach ($r in $regressions) {
            Write-Host ("  " + $r.function + ": " + $r.was + " -> " + $r.now + "  (sessions: " + $r.from + " -> " + $r.to + ")") -ForegroundColor Red
        }
        if ($FailOnRegression) {
            Write-Host ""
            Write-Host "Exiting 1 due to -FailOnRegression flag." -ForegroundColor Red
            exit 1
        }
    }
} else {
    Write-Host ""
    Write-Host "  (Need ≥2 sessions for regression check)" -ForegroundColor DarkYellow
}

Write-Host ""
Write-Host "=== Done ===" -ForegroundColor Green
