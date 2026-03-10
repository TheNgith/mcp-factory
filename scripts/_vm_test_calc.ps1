$headers = @{"X-Bridge-Key" = "BridgeSecret2026xVM01"}
$body = '{"path":"C:\\Windows\\System32\\calc.exe"}'
try {
    $r = Invoke-RestMethod -Uri "http://localhost:8090/analyze" -Method POST -Headers $headers -Body $body -ContentType "application/json" -TimeoutSec 90
    $count = ($r.invocables | Measure-Object).Count
    Write-Host "INVOCABLE_COUNT=$count"
    $r.invocables | Select-Object -First 20 | ForEach-Object { Write-Host "  [$($_.source_type)] $($_.name): $($_.doc_comment)" }
    Write-Host "ERRORS: $($r.errors | ConvertTo-Json -Compress)"
} catch {
    Write-Host "ERROR: $($_.Exception.Message)"
    Write-Host "DETAIL: $($_.ErrorDetails.Message)"
}
