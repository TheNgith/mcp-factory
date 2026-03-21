param()
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$GhidraVersion  = "11.2.1"
$GhidraDate     = "20241105"
$GhidraZip      = "ghidra_${GhidraVersion}_PUBLIC_${GhidraDate}.zip"
$GhidraUrl      = "https://github.com/NationalSecurityAgency/ghidra/releases/download/Ghidra_${GhidraVersion}_build/${GhidraZip}"
$GhidraInstall  = "C:\ghidra"
$DownloadDir    = "C:\Temp\ghidra_setup"

Write-Host "=== MCP Factory: Ghidra Headless Setup ===" -ForegroundColor Cyan

# Step 1: Java
Write-Host "[1/4] Checking Java..." -ForegroundColor Yellow
$java = Get-Command java -ErrorAction SilentlyContinue
if ($java) {
    $verLine = (& java -version 2>&1)[0].ToString()
    Write-Host "  Java found: $verLine" -ForegroundColor Green
    if ($verLine -match '"(\d+)') {
        $majorVer = [int]$Matches[1]
        if ($majorVer -lt 17) {
            Write-Host "  Java version $majorVer is too old, upgrading via winget..." -ForegroundColor Yellow
            winget install Microsoft.OpenJDK.21 --silent --accept-package-agreements --accept-source-agreements
        }
    }
} else {
    Write-Host "  Java not found, installing OpenJDK 21 via winget..." -ForegroundColor Yellow
    winget install Microsoft.OpenJDK.21 --silent --accept-package-agreements --accept-source-agreements
    $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("PATH","User")
}

$java = Get-Command java -ErrorAction SilentlyContinue
if (-not $java) {
    $jdkPath = Get-ChildItem "C:\Program Files\Microsoft\" -Filter "java.exe" -Recurse -ErrorAction SilentlyContinue |
               Select-Object -First 1 -ExpandProperty DirectoryName
    if ($jdkPath) {
        $env:PATH += ";$jdkPath"
        $machinePath = [System.Environment]::GetEnvironmentVariable("PATH","Machine")
        [System.Environment]::SetEnvironmentVariable("PATH", "$machinePath;$jdkPath", "Machine")
        Write-Host "  Added $jdkPath to system PATH" -ForegroundColor Green
    } else {
        Write-Error "Java install failed. Install OpenJDK 21 manually and re-run."
    }
}

# Step 2: Download
Write-Host "[2/4] Downloading Ghidra $GhidraVersion..." -ForegroundColor Yellow
New-Item -ItemType Directory -Force -Path $DownloadDir | Out-Null
$zipPath = Join-Path $DownloadDir $GhidraZip

if (Test-Path $zipPath) {
    Write-Host "  Already downloaded: $zipPath" -ForegroundColor Green
} else {
    Write-Host "  Fetching: $GhidraUrl"
    $ProgressPreference = "SilentlyContinue"
    Invoke-WebRequest -Uri $GhidraUrl -OutFile $zipPath -UseBasicParsing
    $sizeMB = [math]::Round((Get-Item $zipPath).Length / 1MB, 1)
    Write-Host "  Downloaded ${sizeMB} MB" -ForegroundColor Green
}

# Step 3: Extract
Write-Host "[3/4] Extracting to $GhidraInstall..." -ForegroundColor Yellow
if (Test-Path $GhidraInstall) {
    Write-Host "  Removing existing installation..."
    Remove-Item $GhidraInstall -Recurse -Force
}
Expand-Archive -Path $zipPath -DestinationPath "C:\" -Force
$extracted = Get-ChildItem "C:\" -Directory |
             Where-Object { $_.Name -like "ghidra_*_PUBLIC" } |
             Sort-Object LastWriteTime -Descending |
             Select-Object -First 1
if (-not $extracted) { Write-Error "Could not find extracted Ghidra directory under C:\" }
Rename-Item $extracted.FullName $GhidraInstall
Write-Host "  Extracted to $GhidraInstall" -ForegroundColor Green

# Step 4: Env var
Write-Host "[4/4] Setting GHIDRA_HOME..." -ForegroundColor Yellow
[System.Environment]::SetEnvironmentVariable("GHIDRA_HOME", $GhidraInstall, "Machine")
$env:GHIDRA_HOME = $GhidraInstall
Write-Host "  GHIDRA_HOME=$GhidraInstall" -ForegroundColor Green

# Verify
$headless = Join-Path $GhidraInstall "support\analyzeHeadless.bat"
if (Test-Path $headless) {
    Write-Host "=== SUCCESS ===" -ForegroundColor Green
    Write-Host "  analyzeHeadless: $headless"
    Write-Host "  Restart the bridge service to pick up GHIDRA_HOME."
} else {
    Write-Error "analyzeHeadless.bat not found at: $headless"
}