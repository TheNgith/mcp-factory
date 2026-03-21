$py = "C:\Program Files\Python311\python.exe"
$out = & $py -m pip install psutil 2>&1
$out | ForEach-Object { Write-Host $_ }
# verify
$ver = & $py -c "import psutil; print('psutil', psutil.__version__)" 2>&1
Write-Host $ver
