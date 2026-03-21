$url    = "http://localhost:8090/execute"
$secret = "BridgeSecret2026xVM01"
$exe    = "C:\Windows\System32\calc.exe"

function Invoke-Bridge($invocable) {
    $body = @{ invocable = $invocable; args = @{} } | ConvertTo-Json -Depth 10
    $resp = Invoke-RestMethod -Uri $url -Method POST -Body $body `
        -ContentType "application/json" `
        -Headers @{ "X-Bridge-Key" = $secret } -ErrorAction Stop
    return $resp
}

# Launch via CLI invocable (method=subprocess, executable_path) - matches real pipeline output
Write-Host "--- Step 1: Launch calc ---"
$r = Invoke-Bridge @{ name = "calc"; execution = @{ method = "subprocess"; executable_path = $exe; arg_style = "flag" } }
Write-Host ($r | ConvertTo-Json)

Start-Sleep -Seconds 4

# Button clicks via GUI invocable (method=gui_action, exe_path, button_name) - matches real pipeline output
Write-Host "--- Step 2: Press Four ---"
$r = Invoke-Bridge @{ name = "press_four"; execution = @{ method = "gui_action"; exe_path = $exe; action_type = "button_click"; button_name = "Four" } }
Write-Host ($r | ConvertTo-Json)

Start-Sleep -Seconds 1

Write-Host "--- Step 3: Press Multiply by ---"
$r = Invoke-Bridge @{ name = "press_multiply_by"; execution = @{ method = "gui_action"; exe_path = $exe; action_type = "button_click"; button_name = "Multiply by" } }
Write-Host ($r | ConvertTo-Json)

Start-Sleep -Seconds 1

Write-Host "--- Step 4: Press Two ---"
$r = Invoke-Bridge @{ name = "press_two"; execution = @{ method = "gui_action"; exe_path = $exe; action_type = "button_click"; button_name = "Two" } }
Write-Host ($r | ConvertTo-Json)

Start-Sleep -Seconds 1

Write-Host "--- Step 5: Press Equals ---"
$r = Invoke-Bridge @{ name = "press_equals"; execution = @{ method = "gui_action"; exe_path = $exe; action_type = "button_click"; button_name = "Equals" } }
Write-Host ($r | ConvertTo-Json)
