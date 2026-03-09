Set-Location C:\mcp-factory
git pull origin main *>&1 | Write-Host
& "C:\Program Files\Python311\python.exe" "C:\mcp-factory\scripts\_vm_dump_tree.py" 2>&1
