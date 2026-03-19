# run-contoso.ps1
# Convenience launcher for contoso_cs.dll — wraps ci-run.ps1 with all the
# contoso-specific defaults pre-filled so you only need to type one line.
#
# Usage:
#   .\sessions\run-contoso.ps1 -ApiUrl "https://mcp-factory-pipeline.icycoast-8ddfa278.eastus.azurecontainerapps.io" -ApiKey "YOUR_KEY"
#
# Optional overrides:
#   -DllPath   "C:\other\path\contoso_cs.dll"   # override default DLL location
#   -JobId     "abc12345"                        # skip upload, reuse existing job
#   -SkipDiscover                                # skip exploration phase
#   -SkipSave                                    # don't write session snapshot

param(
    [Parameter(Mandatory=$true)][string]$ApiUrl,
    [string]$ApiKey       = "",
    [string]$DllPath      = "C:\Users\evanw\Downloads\mcp-test-binaries\contoso_cs.dll",
    [string]$JobId        = "",
    [switch]$SkipDiscover,
    [switch]$SkipSave
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot

# Read hints and use cases from the committed fixture files
$Hints    = Get-Content (Join-Path $root "contoso_cs\HINTS.txt")    -Raw
$UseCases = Get-Content (Join-Path $root "contoso_cs\USE_CASES.txt") -Raw

$args = @{
    ApiUrl    = $ApiUrl
    ApiKey    = $ApiKey
    DllPath   = $DllPath
    Hints     = $Hints.Trim()
    UseCases  = $UseCases.Trim()
}
if ($JobId)        { $args['JobId']        = $JobId; $args['SkipUpload'] = $true }
if ($SkipDiscover) { $args['SkipDiscover'] = $true }
if ($SkipSave)     { $args['SkipSave']     = $true }

& (Join-Path $root "ci-run.ps1") @args
exit $LASTEXITCODE
