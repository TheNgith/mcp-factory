# model_comparison.ps1 — Run contoso_cs.dll through the full pipeline with different models
# Usage: .\scripts\model_comparison.ps1
# Requires: hints file at sessions/contoso_cs/contoso_cs.txt
#           DLL at tests/fixtures/contoso_legacy/contoso_cs.dll

$ErrorActionPreference = "Stop"
$base = "https://mcp-factory-pipeline.icycoast-8ddfa278.eastus.azurecontainerapps.io"
$apiKey = "BLO0DLLEAgW0XohMe2eN2Pip4PaCUTaE1QL6cVZXk4k"
$dll = "tests/fixtures/contoso_legacy/contoso_cs.dll"
$hints = Get-Content "sessions/contoso_cs/contoso_cs.txt" -Raw

$models = @("gpt-4o", "gpt-4-1", "gpt-4-1-mini", "o4-mini")
$results = @{}

function Invoke-Api {
    param([string]$Method, [string]$Uri, $Body, [string]$ContentType = "application/json")
    $headers = @{ "X-Pipeline-Key" = $apiKey }
    $params = @{ Uri = $Uri; Method = $Method; Headers = $headers }
    if ($Body -and $ContentType -eq "application/json") {
        $params.Body = ($Body | ConvertTo-Json -Depth 10)
        $params.ContentType = "application/json"
    }
    Invoke-RestMethod @params
}

function Wait-Job {
    param([string]$JobId, [string]$Phase, [int]$MaxWait = 600)
    $start = Get-Date
    while (((Get-Date) - $start).TotalSeconds -lt $MaxWait) {
        Start-Sleep -Seconds 10
        $status = Invoke-Api -Method GET -Uri "$base/api/jobs/$JobId"
        $s = $status.status
        $ep = $status.explore_phase
        $eprog = $status.explore_progress
        $msg = $status.message
        Write-Host "  [$Phase] status=$s explore_phase=$ep progress=$eprog msg=$($msg.Substring(0, [Math]::Min(80, $msg.Length)))"
        
        if ($Phase -eq "analyze" -and $s -eq "done") { return $status }
        if ($Phase -eq "explore" -and $ep -match "awaiting_clarification|done|error") { return $status }
        if ($s -eq "error" -and $Phase -eq "analyze") { return $status }
    }
    Write-Warning "Timeout waiting for $Phase on job $JobId"
    return $null
}

foreach ($model in $models) {
    Write-Host "`n========================================" -ForegroundColor Cyan
    Write-Host "MODEL: $model" -ForegroundColor Cyan
    Write-Host "========================================" -ForegroundColor Cyan
    
    # Step 1: Upload + Analyze
    Write-Host "Step 1: Uploading and analyzing..."
    $boundary = [System.Guid]::NewGuid().ToString()
    $LF = "`r`n"
    $fileBytes = [System.IO.File]::ReadAllBytes($dll)
    $fileEnc = [System.Text.Encoding]::GetEncoding("iso-8859-1").GetString($fileBytes)
    
    $bodyLines = (
        "--$boundary",
        "Content-Disposition: form-data; name=`"file`"; filename=`"contoso_cs.dll`"",
        "Content-Type: application/octet-stream$LF",
        $fileEnc,
        "--$boundary",
        "Content-Disposition: form-data; name=`"hints`"$LF",
        $hints,
        "--$boundary--$LF"
    ) -join $LF
    
    $analyzeResp = Invoke-RestMethod -Uri "$base/api/analyze" -Method POST `
        -Headers @{ "X-Pipeline-Key" = $apiKey } `
        -ContentType "multipart/form-data; boundary=$boundary" `
        -Body $bodyLines
    
    $jobId = $analyzeResp.job_id
    Write-Host "  Job ID: $jobId"
    
    # Wait for analyze to complete
    $analyzeResult = Wait-Job -JobId $jobId -Phase "analyze" -MaxWait 300
    if (-not $analyzeResult -or $analyzeResult.status -eq "error") {
        Write-Host "  ANALYZE FAILED for model $model" -ForegroundColor Red
        $results[$model] = @{ error = "analyze_failed" }
        continue
    }
    
    # Step 2: Generate
    Write-Host "Step 2: Generating MCP tools..."
    $generateBody = @{ job_id = $jobId }
    $genResp = Invoke-Api -Method POST -Uri "$base/api/generate" -Body $generateBody
    $toolCount = ($genResp.tools | Measure-Object).Count
    Write-Host "  Generated $toolCount tools"
    
    # Step 3: Explore with model override
    Write-Host "Step 3: Exploring with model=$model..."
    $exploreBody = @{
        explore_settings = @{
            mode = "normal"
            model = $model
        }
    }
    $exploreResp = Invoke-Api -Method POST -Uri "$base/api/jobs/$jobId/explore" -Body $exploreBody
    Write-Host "  Explore started: $($exploreResp.status)"
    
    # Wait for explore to complete (can take 10+ minutes)
    $exploreResult = Wait-Job -JobId $jobId -Phase "explore" -MaxWait 1200
    
    $results[$model] = @{
        job_id = $jobId
        explore_phase = $exploreResult.explore_phase
        explore_progress = $exploreResult.explore_progress
    }
    Write-Host "  DONE: phase=$($exploreResult.explore_phase) progress=$($exploreResult.explore_progress)" -ForegroundColor Green
}

# Summary
Write-Host "`n`n========================================" -ForegroundColor Yellow
Write-Host "MODEL COMPARISON SUMMARY" -ForegroundColor Yellow
Write-Host "========================================" -ForegroundColor Yellow
foreach ($model in $models) {
    $r = $results[$model]
    Write-Host "$model : job=$($r.job_id) phase=$($r.explore_phase) progress=$($r.explore_progress)"
}
Write-Host "`nDownload session snapshots with:"
foreach ($model in $models) {
    $r = $results[$model]
    if ($r.job_id) {
        Write-Host "  Invoke-RestMethod -Uri '$base/api/jobs/$($r.job_id)/session-snapshot' -Headers @{'X-Pipeline-Key'='$apiKey'} -OutFile 'sessions/model-cmp-$model.zip'"
    }
}
