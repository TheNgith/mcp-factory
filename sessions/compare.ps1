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
    Write-Host "index.json is empty - run save-session.ps1 first." -ForegroundColor Yellow
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
Write-Host "=== MCP Factory - Session Comparison ===" -ForegroundColor Cyan
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
$regressions = @()
if ($selected.Count -ge 2) {
    Write-Host ""
    Write-Host "── Regression check ─────────────────────────────────────────────" -ForegroundColor Cyan
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


# ── Generate DASHBOARD.md ─────────────────────────────────────────────────────
$dashPath = Join-Path $SessionsRoot "DASHBOARD.md"
$md       = [System.Collections.Generic.List[string]]::new()
$ts       = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")

$md.Add("# MCP Factory - Session Dashboard")
$md.Add("")
$md.Add("> Auto-generated $ts · ``compare.ps1``  ")
$md.Add("> Re-run ``.\sessions\compare.ps1`` or capture a new session to refresh.")
$md.Add("")
$md.Add("---")
$md.Add("")
$md.Add("## Session Overview")
$md.Add("")
$md.Add("| Session | Component | Successes | / Total | Vocab % | Coverage % | Tools | Verdict |")
$md.Add("|---------|-----------|-----------|---------|---------|------------|-------|---------|")
foreach ($s in $selected) {
    $fc      = $s.finding_counts
    $success = if ($fc)                              { [int]($fc.success) }                             else { 0 }
    $total   = if ($fc)                              { [int]($fc.total)   }                             else { 0 }
    $vcpct   = if ($null -ne $s.vocab_completeness)  { "{0:P0}" -f [double]$s.vocab_completeness }     else { "n/a" }
    $cov     = if ($null -ne $s.vocab_coverage)      { "{0:P0}" -f [double]$s.vocab_coverage }         else { "n/a" }
    $txTools = if ($s.transcript_metrics)            { $s.transcript_metrics.tool_calls }              else { "n/a" }
    $diag    = if ($s.verdict)                       { $s.verdict }                                     else { "-" }
    $comp    = if ($s.component)                     { $s.component }                                   else { "-" }
    $md.Add("| $($s.folder) | $comp | $success | $total | $vcpct | $cov | $txTools | $diag |")
}
$md.Add("")
$md.Add("---")
$md.Add("")

# Function status matrix
$fnOrder = [System.Collections.Specialized.OrderedDictionary]::new()
foreach ($s in $selected) {
    if ($s.function_status) {
        foreach ($prop in $s.function_status.PSObject.Properties) {
            if (-not $fnOrder.Contains($prop.Name)) { [void]$fnOrder.Add($prop.Name, $true) }
        }
    }
}
if ($fnOrder.Count -gt 0) {
    $md.Add("## Function Status")
    $md.Add("")
    $colLabels = @($selected | ForEach-Object {
        $f = $_.folder
        if ($f.Length -gt 19) { "…" + $f.Substring($f.Length - 16) } else { $f }
    })
    $allCols = @("Function") + $colLabels
    $md.Add("| " + ($allCols -join " | ") + " |")
    $md.Add("| " + (($allCols | ForEach-Object { "---" }) -join " | ") + " |")
    foreach ($fn in $fnOrder.Keys) {
        $cells = @($selected | ForEach-Object {
            if ($_.function_status -and $_.function_status.PSObject.Properties[$fn]) {
                $st = $_.function_status.PSObject.Properties[$fn].Value
                if ($st -eq "success") { "✅" } elseif ($st -eq "partial") { "⚠️" } else { "❌" }
            } else { "-" }
        })
        $md.Add("| ``$fn`` | " + ($cells -join " | ") + " |")
    }
    $md.Add("")
    $md.Add("---")
    $md.Add("")
}

# Regressions
$md.Add("## Regressions")
$md.Add("")
if ($regressions.Count -eq 0) {
    $msg = if ($selected.Count -lt 2) { "_Need ≥ 2 sessions to detect regressions._" } else { "✅ No regressions detected." }
    $md.Add($msg)
} else {
    $md.Add("| Function | Was | Now | From session | To session |")
    $md.Add("|----------|-----|-----|--------------|------------|")
    foreach ($r in $regressions) {
        $md.Add("| ``$($r.function)`` | $($r.was) | $($r.now) | $($r.from) | $($r.to) |")
    }
}
$md.Add("")
$md.Add("---")
$md.Add("")

# Trends (oldest vs latest in current selection)
if ($selected.Count -ge 2) {
    $first = $selected[0]
    $last  = $selected[-1]
    $fl = if ($first.folder.Length -gt 19) { "…" + $first.folder.Substring($first.folder.Length - 16) } else { $first.folder }
    $ll = if ($last.folder.Length  -gt 19) { "…" + $last.folder.Substring($last.folder.Length  - 16) } else { $last.folder }
    $md.Add("## Trends")
    $md.Add("")
    $md.Add("| Metric | $fl | $ll | Change |")
    $md.Add("|--------|------|------|--------|")
    # Vocab completeness
    $v1 = if ($null -ne $first.vocab_completeness) { [double]$first.vocab_completeness } else { $null }
    $v2 = if ($null -ne $last.vocab_completeness)  { [double]$last.vocab_completeness  } else { $null }
    $v1s = if ($null -ne $v1) { "{0:P0}" -f $v1 } else { "n/a" }
    $v2s = if ($null -ne $v2) { "{0:P0}" -f $v2 } else { "n/a" }
    $vd  = if ($null -ne $v1 -and $null -ne $v2) { $d = $v2 - $v1; if ($d -ge 0) { "+{0:P0}" -f $d } else { "{0:P0}" -f $d } } else { "n/a" }
    $md.Add("| Vocab completeness | $v1s | $v2s | $vd |")
    # Vocab coverage
    $c1 = if ($null -ne $first.vocab_coverage) { [double]$first.vocab_coverage } else { $null }
    $c2 = if ($null -ne $last.vocab_coverage)  { [double]$last.vocab_coverage  } else { $null }
    $c1s = if ($null -ne $c1) { "{0:P0}" -f $c1 } else { "n/a" }
    $c2s = if ($null -ne $c2) { "{0:P0}" -f $c2 } else { "n/a" }
    $cd  = if ($null -ne $c1 -and $null -ne $c2) { $d = $c2 - $c1; if ($d -ge 0) { "+{0:P0}" -f $d } else { "{0:P0}" -f $d } } else { "n/a" }
    $md.Add("| Vocab coverage | $c1s | $c2s | $cd |")
    # Success count
    $s1c = if ($first.finding_counts) { [int]$first.finding_counts.success } else { $null }
    $s2c = if ($last.finding_counts)  { [int]$last.finding_counts.success  } else { $null }
    $s1s = if ($null -ne $s1c) { "$s1c" } else { "n/a" }
    $s2s = if ($null -ne $s2c) { "$s2c" } else { "n/a" }
    $sd  = if ($null -ne $s1c -and $null -ne $s2c) { $d = $s2c - $s1c; if ($d -ge 0) { "+$d" } else { "$d" } } else { "n/a" }
    $md.Add("| Functions succeeding | $s1s | $s2s | $sd |")
    $md.Add("")
    $md.Add("---")
    $md.Add("")
}

$md.Add("*Last updated: $ts*")
$md | Set-Content $dashPath -Encoding UTF8
Write-Host "  Saved sessions/DASHBOARD.md" -ForegroundColor Green

Write-Host ""
Write-Host "=== Done ===" -ForegroundColor Green

