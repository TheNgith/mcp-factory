# score-session.ps1
# Auto-score a session's chat_transcript.txt against the CONTOSO_CS_TEST_SUITE rubric.
# Writes TEST_RESULTS.md into the session folder.
#
# Usage:
#   .\sessions\score-session.ps1 -SessionDir "sessions\2026-03-18-8c16d04-post-sentinel-fix-run1-2"
#   .\sessions\score-session.ps1 -SessionDir "sessions\2026-03-18-8c16d04-post-sentinel-fix-run1-2" -Verbose
#
# Scoring marks: PASS | FAIL | SKIP (no data) | WARN (partial)
# The rubric is deterministic regex â€ no LLM calls needed.

param(
    [Parameter(Mandatory=$true)][string]$SessionDir,
    [string]$SessionsRoot = $PSScriptRoot,
    [switch]$ShowAll
)

$ErrorActionPreference = "Stop"

# Resolve path relative to SessionsRoot if not absolute
if (-not [System.IO.Path]::IsPathRooted($SessionDir)) {
    $SessionDir = Join-Path $SessionsRoot $SessionDir
}
if (-not (Test-Path $SessionDir)) {
    Write-Host "Session folder not found: $SessionDir" -ForegroundColor Red
    exit 1
}

$transcriptPath   = Join-Path $SessionDir "chat_transcript.txt"
$testResultsPath  = Join-Path $SessionDir "TEST_RESULTS.md"

if (-not (Test-Path $transcriptPath)) {
    Write-Host "No chat_transcript.txt in $SessionDir â€ run run-tests.ps1 first." -ForegroundColor Yellow
    exit 1
}

$transcript = Get-Content $transcriptPath -Raw

# â€â€ Split transcript into per-test blocks â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€
# run-tests.ps1 writes "# ==== TEST T01 ====" headers.
# Older manual transcripts may not have per-test headers â€ we do a best-effort
# match on the prompt text in that case and mark tests as SKIP.
$testBlocks = @{}
$pattern = [regex]'# ==== TEST (T\d+) ===='
$matches_ = $pattern.Matches($transcript)

if ($matches_.Count -gt 0) {
    for ($i = 0; $i -lt $matches_.Count; $i++) {
        $id    = $matches_[$i].Groups[1].Value
        $start = $matches_[$i].Index
        $end   = if ($i + 1 -lt $matches_.Count) { $matches_[$i+1].Index } else { $transcript.Length }
        $testBlocks[$id] = $transcript.Substring($start, $end - $start)
    }
} else {
    # Legacy format â€ entire transcript is one block; scoring will SKIP tool-arg checks
    $testBlocks["ALL"] = $transcript
    Write-Host "  Note: transcript has no T-ID headers. Use run-tests.ps1 for per-test scoring." -ForegroundColor Yellow
    Write-Host "  Applying whole-transcript scoring (limited accuracy)." -ForegroundColor Yellow
}

# â€â€ Helper â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€
function Has($block, $pattern) {
    if (-not $block) { return $false }
    return [bool]($block -match $pattern)
}
function Block($id) {
    if ($testBlocks.ContainsKey($id)) { return $testBlocks[$id] }
    if ($testBlocks.ContainsKey("ALL")) { return $testBlocks["ALL"] }
    return $null
}
function Score($id, $pass, $warn=$false) {
    if ($null -eq (Block $id)) { return "SKIP" }
    if ($pass)  { return "PASS" }
    if ($warn)  { return "WARN" }
    return "FAIL"
}

# â€â€ Per-test rubric â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€
# Each rule returns PASS / FAIL / SKIP / WARN

$results = [ordered]@{}

# T01 â€ Version decode: model reports a version number from CS_GetVersion
$b = Block "T01"
$results["T01"] = Score "T01" (
    (Has $b 'CS_GetVersion') -and (Has $b '\d+\.\d+')
)

# T02 â€ Initialized boolean: model says "initialized" or "yes" or "true"
$b = Block "T02"
$results["T02"] = Score "T02" (
    (Has $b '(?i)initializ') -or (Has $b '(?i)\byes\b') -or (Has $b '(?i)\btrue\b')
)

# T03 â€ System counts: model reports customer and order counts
$b = Block "T03"
$results["T03"] = Score "T03" (
    (Has $b '(?i)customer') -and (Has $b '(?i)order') -and (Has $b '\d+')
)

# T04 â€ Auto-format CUST-007: tool call must use "CUST-007", not raw "7"
$b = Block "T04"
$results["T04"] = Score "T04" (
    Has $b '"CUST-007"'
) -warn:(Has $b 'CUST-007')  # warn if mentioned in text but not in tool call

# T05 â€ Auto-format CUST-042: tool call must use "CUST-042"
$b = Block "T05"
$results["T05"] = Score "T05" (Has $b '"CUST-042"')

# T06 â€ Refund cents: CS_ProcessRefund must be called with 1850 (not 18.5 / 18.50)
$b = Block "T06"
$results["T06"] = Score "T06" (
    (Has $b 'CS_ProcessRefund') -and (Has $b '\b1850\b')
)

# T07 â€ Reject malformed ID: model should NOT call CS_LookupCustomer("ABC")
#        OR should tell user the format is invalid
$b = Block "T07"
$calledWithABC  = Has $b '"ABC"'
$saidInvalid    = Has $b '(?i)(invalid|not valid|format|CUST-[A-Z0-9]{3,})'
$results["T07"] = Score "T07" (-not $calledWithABC -or $saidInvalid)

# T08 â€ Payment cents: CS_ProcessPayment must be called with 2500 (not 25)
$b = Block "T08"
$results["T08"] = Score "T08" (
    (Has $b 'CS_ProcessPayment') -and (Has $b '\b2500\b')
)

# T09 â€ Refund cents: CS_ProcessRefund called with 15099 (not 150.99)
$b = Block "T09"
$results["T09"] = Score "T09" (
    (Has $b 'CS_ProcessRefund') -and (Has $b '\b15099\b')
)

# T10 â€ Balance div 100: model reports balance as dollars (contains $ + decimal)
$b = Block "T10"
$results["T10"] = Score "T10" (Has $b '\$\d+\.\d{2}')

# T11 â€ Points integer: CS_RedeemLoyaltyPoints called with 500
$b = Block "T11"
$results["T11"] = Score "T11" (
    (Has $b 'CS_RedeemLoyalt') -and (Has $b '\b500\b')
)

# T12 â€ Diagnose locked: model calls CS_UnlockAccount
$b = Block "T12"
$results["T12"] = Score "T12" (Has $b 'CS_UnlockAccount')

# T13 â€ Already-active unlock: model says account is not locked / already active
$b = Block "T13"
$results["T13"] = Score "T13" (
    Has $b '(?i)(already.{0,10}(active|unlocked)|not.{0,10}locked|account.{0,10}active)'
)

# T14 â€ Payment on locked account: model explains locked error
$b = Block "T14"
$results["T14"] = Score "T14" (
    Has $b '(?i)(locked|0xFFFFFFFC|4294967292|access denied)'
)

# T15 â€ 0xFFFFFFFB decode: model interprets as write access denied
$b = Block "T15"
$results["T15"] = Score "T15" (
    Has $b '(?i)(write.{0,20}(access|denied)|access.{0,20}denied|0xFFFFFFFB)'
)

# T16 â€ 0xFFFFFFFC decode: model interprets as account locked
$b = Block "T16"
$results["T16"] = Score "T16" (
    Has $b '(?i)(account.{0,20}lock|lock.{0,20}account|0xFFFFFFFC)'
)

# T17 â€ Access violation: model explains permission / access issue
$b = Block "T17"
$results["T17"] = Score "T17" (
    Has $b '(?i)(access.{0,20}(violation|denied)|permission|unauthorized)'
)

# T18 â€ No-init payment: model initializes first OR explains init required
$b = Block "T18"
$results["T18"] = Score "T18" (
    (Has $b 'CS_Initialize') -or (Has $b '(?i)(initializ|not.{0,20}init)')
)

# T19 â€ Full profile: response contains â‰¥3 distinct profile fields
$b = Block "T19"
$fieldCount = 0
foreach ($f in @('(?i)name', '(?i)email', '(?i)phone', '(?i)tier', '(?i)status', '(?i)address', '(?i)balance')) {
    if (Has $b $f) { $fieldCount++ }
}
$results["T19"] = Score "T19" ($fieldCount -ge 3)

# T20 â€ Tier label: model decodes tier as a human label
$b = Block "T20"
$results["T20"] = Score "T20" (
    Has $b '(?i)(Gold|Silver|Bronze|Platinum|tier\s+\d|loyalty.{0,20}tier)'
)

# T21 â€ Contact fields: email or phone present
$b = Block "T21"
$results["T21"] = Score "T21" (
    (Has $b '@') -or (Has $b '\d{3}[-. ]\d{3,4}')
)

# T22 â€ Full happy path: both CS_RedeemLoyaltyPoints AND CS_ProcessPayment called
$b = Block "T22"
$results["T22"] = Score "T22" (
    (Has $b 'CS_RedeemLoyalt') -and (Has $b 'CS_ProcessPayment')
)

# T23 â€ End-to-end refund: CS_ProcessRefund called
$b = Block "T23"
$results["T23"] = Score "T23" (Has $b 'CS_ProcessRefund')

# T24 â€ Multi-customer: CS_ProcessPayment called 3+ times (rough check)
$b = Block "T24"
if ($b) {
    $pmtCount = ([regex]::Matches($b, 'CS_ProcessPayment')).Count
    $results["T24"] = if ($pmtCount -ge 3) { "PASS" } elseif ($pmtCount -ge 1) { "WARN" } else { "FAIL" }
} else { $results["T24"] = "SKIP" }

# T25 â€ Locked multi-step: CS_GetAccountStatus or CS_LookupCustomer called before redeem
$b = Block "T25"
$results["T25"] = Score "T25" (
    (Has $b 'CS_GetAccountStatus') -or (Has $b 'CS_LookupCustomer')
)

# T26 â€ Zero amount: model calls with 0 or explains invalid
$b = Block "T26"
$results["T26"] = Score "T26" (
    (Has $b '(?i)(CS_ProcessPayment.*\b0\b|\bparam_2.*: 0\b|amount.*0|zero)') -or
    (Has $b '"param_2":0') -or
    (Has $b '"param_2": 0')
)

# T27 â€ Over-redeem: model reports insufficient points
$b = Block "T27"
$results["T27"] = Score "T27" (
    Has $b '(?i)(insufficient|not enough|below|only \d+ points|300 points)'
)

# T28 â€ LOCKED as ID confusion: model does NOT try LOCKED as a customer ID
$b = Block "T28"
$calledWithLOCKED = Has $b '"LOCKED"'
$saidInvalidOrStatus = Has $b '(?i)(invalid|not a valid|status.{0,20}LOCKED|LOCKED.{0,20}status)'
$results["T28"] = Score "T28" (-not $calledWithLOCKED -or $saidInvalidOrStatus)

# â€â€ Count results â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€
$pass = ($results.Values | Where-Object { $_ -eq "PASS" }).Count
$fail = ($results.Values | Where-Object { $_ -eq "FAIL" }).Count
$warn = ($results.Values | Where-Object { $_ -eq "WARN" }).Count
$skip = ($results.Values | Where-Object { $_ -eq "SKIP" }).Count
$total = $results.Count

# â€â€ Read session meta â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€
$metaPath = Join-Path $SessionDir "session-meta.json"
$meta = $null
try { $meta = Get-Content $metaPath -Raw | ConvertFrom-Json } catch {}
$sessionName = Split-Path $SessionDir -Leaf
$jobId       = if ($meta) { $meta.job_id } else { "unknown" }
$commit      = if ($meta) { $meta.commit  } else { "unknown" }
$commitMsg   = if ($meta) { $meta.commit_message } else { "" }

# â€â€ Symbol map â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€
$sym = @{ PASS="PASS"; FAIL="FAIL"; WARN="WARN"; SKIP="----" }

# â€â€ Build TEST_RESULTS.md â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€
$date = Get-Date -Format "yyyy-MM-dd HH:mm"
$pct  = if ($total - $skip -gt 0) { "{0:P0}" -f ($pass / ($total - $skip)) } else { "n/a" }

$lines = @()
$lines += "# Test Results - $date"
$lines += ""
$lines += "**Session:** $sessionName"
$lines += "**Commit:** $commit $commitMsg"
$lines += "**Job ID:** $jobId"
$lines += "**Score:** $pass / $($total - $skip) non-skip tests ($pct)   PASS: $pass  FAIL: $fail  WARN: $warn  SKIP: $skip"
$lines += ""
$lines += "See [../../CONTOSO_CS_TEST_SUITE.md](../../CONTOSO_CS_TEST_SUITE.md) for full prompts."
$lines += ""
$lines += "---"
$lines += ""
$lines += "## Scoring Table"
$lines += ""
$lines += "| ID | Description | ID Format | Amount/Value | Error Decode | Init Order | Verdict |"
$lines += "|----|-------------|-----------|--------------|--------------|------------|---------|"

# Column assignment: which dimension each test exercises
$dimMap = @{
    T01 = @("-",   "",   "-",   "-",  $results["T01"])
    T02 = @("-",   "",   "-",   "-",  $results["T02"])
    T03 = @("-",   "-",  "-",   "-",  $results["T03"])
    T04 = @("",    "-",  "-",   "-",  $results["T04"])
    T05 = @("",    "-",  "-",   "-",  $results["T05"])
    T06 = @("",    "",   "-",   "-",  $results["T06"])
    T07 = @("",    "-",  "-",   "-",  $results["T07"])
    T08 = @("-",   "",   "-",   "-",  $results["T08"])
    T09 = @("-",   "",   "-",   "-",  $results["T09"])
    T10 = @("-",   "",   "-",   "-",  $results["T10"])
    T11 = @("-",   "",   "-",   "-",  $results["T11"])
    T12 = @("",    "-",  "",    "",   $results["T12"])
    T13 = @("-",   "-",  "-",   "",   $results["T13"])
    T14 = @("-",   "-",  "",    "",   $results["T14"])
    T15 = @("-",   "-",  "",    "-",  $results["T15"])
    T16 = @("-",   "-",  "",    "-",  $results["T16"])
    T17 = @("-",   "-",  "",    "-",  $results["T17"])
    T18 = @("-",   "-",  "",    "",   $results["T18"])
    T19 = @("",    "",   "-",   "-",  $results["T19"])
    T20 = @("-",   "",   "-",   "-",  $results["T20"])
    T21 = @("-",   "-",  "-",   "-",  $results["T21"])
    T22 = @("",    "",   "-",   "",   $results["T22"])
    T23 = @("",    "",   "-",   "",   $results["T23"])
    T24 = @("",    "",   "",    "",   $results["T24"])
    T25 = @("-",   "-",  "",    "",   $results["T25"])
    T26 = @("-",   "",   "-",   "-",  $results["T26"])
    T27 = @("-",   "",   "",    "-",  $results["T27"])
    T28 = @("",    "-",  "-",   "-",  $results["T28"])
}
$descMap = @{
    T01="Version decode";         T02="Initialized boolean";    T03="System counts"
    T04="Auto-format CUST-007";   T05="Auto-format CUST-042";   T06="Order ID + refund cents"
    T07="Reject malformed ID";    T08="Payment cents";           T09="Refund cents"
    T10="Balance div 100";        T11="Points integer";          T12="Diagnose locked"
    T13="Already-active unlock";  T14="Payment on locked";       T15="0xFFFFFFFB decode"
    T16="0xFFFFFFFC decode";      T17="Access violation";        T18="No-init payment"
    T19="Full profile fields";    T20="Tier label";              T21="Contact fields"
    T22="Full happy path";        T23="End-to-end refund";       T24="Multi-customer"
    T25="Locked in multi-step";   T26="Zero amount";             T27="Over-redeem points"
    T28="LOCKED as ID confusion"
}

foreach ($id in $dimMap.Keys | Sort-Object) {
    $d       = $dimMap[$id]
    $verdict = $d[4]
    $mark    = switch ($verdict) {
        "PASS" { "âœ…" } "FAIL" { "âŒ" } "WARN" { "âš ï¸" } default { "--" }
    }
    $lines += "| $id | $($descMap[$id]) | $($d[0]) | $($d[1]) | $($d[2]) | $($d[3]) | $mark $verdict |"
}

$lines += ""
$lines += "---"
$lines += ""
$lines += "## Summary"
$lines += ""
$lines += "| Category | Tests | Pass | Fail | Warn | Skip |"
$lines += "|----------|-------|------|------|------|------|"

# Group by category
$groups = [ordered]@{
    "Init & State"    = @("T01","T02","T03","T18")
    "ID Format"       = @("T04","T05","T06","T07","T28")
    "Amount Encoding" = @("T08","T09","T10","T11","T26")
    "Error Decode"    = @("T15","T16","T17")
    "Account Flow"    = @("T12","T13","T14")
    "Profile"         = @("T19","T20","T21")
    "Multi-step"      = @("T22","T23","T24","T25","T27")
}
foreach ($grp in $groups.Keys) {
    $ids = $groups[$grp]
    $gp  = ($ids | Where-Object { $results[$_] -eq "PASS" }).Count
    $gf  = ($ids | Where-Object { $results[$_] -eq "FAIL" }).Count
    $gw  = ($ids | Where-Object { $results[$_] -eq "WARN" }).Count
    $gs  = ($ids | Where-Object { $results[$_] -eq "SKIP" }).Count
    $lines += "| $grp | $($ids.Count) | $gp | $gf | $gw | $gs |"
}
$lines += "| **TOTAL** | **$total** | **$pass** | **$fail** | **$warn** | **$skip** |"

$lines += ""
$lines += "---"
$lines += ""
$lines += "## Notes"
$lines += ""
$lines += "> Auto-scored by score-session.ps1 on $date"
$lines += "> Rubric: deterministic regex - no LLM calls."
$lines += "> WARN = partial signal (mentioned in text but not confirmed in tool call args)."
$lines += "> SKIP = test block not found in transcript (run run-tests.ps1 to get per-test blocks)."
$lines += ""
$lines += "---"
$lines += ""
$lines += "<details>"
$lines += "<summary>Per-test raw results</summary>"
$lines += ""
$lines += '```'
foreach ($id in $results.Keys | Sort-Object) {
    $lines += "$id  $($results[$id])"
}
$lines += '```'
$lines += ""
$lines += "</details>"

$lines | Set-Content $testResultsPath -Encoding UTF8

# â€â€ Console output â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€â€
Write-Host ""
Write-Host "=== Score Results ===" -ForegroundColor Cyan
Write-Host "  Session: $sessionName" -ForegroundColor White
Write-Host "  Score  : $pass / $($total - $skip) non-skip  ($pct)" -ForegroundColor $(if ($fail -eq 0) { "Green" } elseif ($fail -le 5) { "Yellow" } else { "Red" })
Write-Host ""

foreach ($id in $results.Keys | Sort-Object) {
    $v = $results[$id]
    $color = switch ($v) { "PASS" { "Green" } "FAIL" { "Red" } "WARN" { "Yellow" } default { "DarkGray" } }
    $mark  = switch ($v) { "PASS" { "[PASS]" } "FAIL" { "[FAIL]" } "WARN" { "[WARN]" } default { "[----]" } }
    if ($ShowAll -or $v -ne "SKIP") {
        Write-Host "  $mark $id  $($descMap[$id])" -ForegroundColor $color
    }
}

Write-Host ""
Write-Host "  Written: $testResultsPath" -ForegroundColor DarkCyan
Write-Host ""
