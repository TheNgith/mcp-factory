$f = "C:\mcp-factory\scripts\gui_bridge.py"
$lines = Get-Content $f
# Find the line index (0-based) of "async def _generate():" inside the file
$idx = -1
for ($i = 0; $i -lt $lines.Length; $i++) {
    if ($lines[$i] -match 'async def _generate\(\):') { $idx = $i; break }
}
if ($idx -eq -1) { Write-Host "ERROR: _generate not found"; exit 1 }
Write-Host "Found _generate at 0-based index $idx (line $($idx+1))"
$newLine = "        global _active_kill_event, _active_target_stem"
# Only insert if not already present on the very next line
if ($lines[$idx+1] -match "global _active_kill_event") {
    Write-Host "Already patched"
} else {
    $fixed = $lines[0..$idx] + @($newLine) + $lines[($idx+1)..($lines.Length-1)]
    Set-Content -Path $f -Value $fixed -Encoding UTF8
    Write-Host "Patched. Context:"
    $fixed[$idx..($idx+3)]
}
