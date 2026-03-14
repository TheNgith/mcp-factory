<#
.SYNOPSIS
    Install Ghidra headless + OpenJDK 21 on the MCP Factory Azure VM.
    Run once from an elevated PowerShell session on the VM.

.USAGE
    .\scripts\_vm_install_ghidra.ps1

.NOTES
    Ghidra 11.2.1 requires JDK 17+.  This script installs OpenJDK 21 via
    winget (available on Win11/Server 2022) and downloads Ghidra from the
    official NSA GitHub releases.  Total disk: ~500 MB.
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ── Config ────────────────────────────────────────────────────────────────────
$GhidraVersion  = "11.2.1"
$GhidraDate     = "20241105"
$GhidraZip      = "ghidra_${GhidraVersion}_PUBLIC_${GhidraDate}.zip"
$GhidraUrl      = "https://github.com/NationalSecurityAgency/ghidra/releases/download/Ghidra_${GhidraVersion}_build/${GhidraZip}"
$GhidraInstall  = "C:\ghidra"
$DownloadDir    = "C:\Temp\ghidra_setup"

Write-Host "=== MCP Factory — Ghidra Headless Setup ===" -ForegroundColor Cyan

# ── Step 1: OpenJDK 21 ────────────────────────────────────────────────────────
Write-Host "`n[1/4] Checking Java..." -ForegroundColor Yellow
$java = Get-Command java -ErrorAction SilentlyContinue
if ($java) {
    $ver = (& java -version 2>&1 | Select-String -Pattern '(\d+)\.' | ForEach-Object { $_.Matches[0].Groups[1].Value })
    Write-Host "  Java found: $(& java -version 2>&1 | Select-Object -First 1)" -ForegroundColor Green
    if ([int]$ver -lt 17) {
        Write-Host "  Java < 17 detected — upgrading via winget..." -ForegroundColor Yellow
        winget install Microsoft.OpenJDK.21 --silent --accept-package-agreements --accept-source-agreements
    }
} else {
    Write-Host "  Java not found — installing OpenJDK 21 via winget..." -ForegroundColor Yellow
    winget install Microsoft.OpenJDK.21 --silent --accept-package-agreements --accept-source-agreements
    # Refresh PATH
    $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH", "Machine") + ";" +
                [System.Environment]::GetEnvironmentVariable("PATH", "User")
}

$java = Get-Command java -ErrorAction SilentlyContinue
if (-not $java) {
    # winget may have installed to a non-PATH location; find it
    $jdkPath = Get-ChildItem "C:\Program Files\Microsoft\" -Filter "java.exe" -Recurse -ErrorAction SilentlyContinue |
               Select-Object -First 1 -ExpandProperty DirectoryName
    if ($jdkPath) {
        $env:PATH += ";$jdkPath"
        [System.Environment]::SetEnvironmentVariable("PATH",
            [System.Environment]::GetEnvironmentVariable("PATH","Machine") + ";$jdkPath", "Machine")
        Write-Host "  Added $jdkPath to system PATH" -ForegroundColor Green
    } else {
        Write-Error "Java installation failed — install OpenJDK 21 manually and re-run."
    }
}

# ── Step 2: Download Ghidra ───────────────────────────────────────────────────
Write-Host "`n[2/4] Downloading Ghidra $GhidraVersion..." -ForegroundColor Yellow
New-Item -ItemType Directory -Force -Path $DownloadDir | Out-Null
$zipPath = Join-Path $DownloadDir $GhidraZip

if (Test-Path $zipPath) {
    Write-Host "  Already downloaded: $zipPath" -ForegroundColor Green
} else {
    Write-Host "  Fetching $GhidraUrl"
    $ProgressPreference = "SilentlyContinue"   # dramatically speeds up Invoke-WebRequest
    Invoke-WebRequest -Uri $GhidraUrl -OutFile $zipPath -UseBasicParsing
    Write-Host "  Downloaded $('{0:N1} MB' -f ((Get-Item $zipPath).Length / 1MB))" -ForegroundColor Green
}

# ── Step 3: Extract ───────────────────────────────────────────────────────────
Write-Host "`n[3/4] Extracting to $GhidraInstall..." -ForegroundColor Yellow
if (Test-Path $GhidraInstall) {
    Write-Host "  Removing existing installation..."
    Remove-Item $GhidraInstall -Recurse -Force
}
Expand-Archive -Path $zipPath -DestinationPath "C:\" -Force
# Ghidra zip extracts to ghidra_<version>_PUBLIC/; rename to C:\ghidra
$extracted = Get-ChildItem "C:\" -Directory | Where-Object { $_.Name -like "ghidra_*_PUBLIC" } |
             Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $extracted) { Write-Error "Could not find extracted Ghidra directory under C:\\" }
Rename-Item $extracted.FullName $GhidraInstall
Write-Host "  Extracted to $GhidraInstall" -ForegroundColor Green

# ── Step 4: Set GHIDRA_HOME machine env var ───────────────────────────────────
Write-Host "`n[4/4] Setting GHIDRA_HOME environment variable..." -ForegroundColor Yellow
[System.Environment]::SetEnvironmentVariable("GHIDRA_HOME", $GhidraInstall, "Machine")
$env:GHIDRA_HOME = $GhidraInstall
Write-Host "  GHIDRA_HOME=$GhidraInstall" -ForegroundColor Green

# ── Verify ────────────────────────────────────────────────────────────────────
$headless = Join-Path $GhidraInstall "support\analyzeHeadless.bat"
if (Test-Path $headless) {
    Write-Host "`n=== SUCCESS ===" -ForegroundColor Green
    Write-Host "  analyzeHeadless: $headless"
    Write-Host "  Restart the bridge service to pick up GHIDRA_HOME."
} else {
    Write-Error "analyzeHeadless.bat not found at expected location: $headless"
}
