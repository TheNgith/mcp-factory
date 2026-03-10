<#
.SYNOPSIS
    Migrates the MCP Factory deployment to a VNet-integrated ACA environment.

.DESCRIPTION
    Azure Container Apps environments CANNOT have VNet added after creation.
    This script:
      1. Deletes the two ACA container apps (pipeline + UI)
      2. Deletes the ACA managed environment
      3. Re-deploys the full infra/main.bicep (which now includes VNet)
      4. Verifies the new environment has VNet wired in.

    The new environment will have a different default domain suffix.
    CI/CD will redeploy the container images after this script completes.

    GUI bridge credentials (gui-bridge-url, gui-bridge-secret) are read from
    Key Vault by the pipeline container via secretref — no parameter needed.

.PARAMETER RunnerToken
    Ephemeral GitHub Actions runner registration token (only needed if
    deployWindowsRunner=true). Expires after 1 hour — generate immediately
    before running:
      gh api repos/evanking12/mcp-factory/actions/runners/registration-token \
         --method POST --jq .token

.PARAMETER RunnerVmAdminPassword
    Windows admin password for the runner VM (only needed if
    deployWindowsRunner=true).

.PARAMETER DeployWindowsRunner
    Pass -DeployWindowsRunner to also redeploy the Windows runner VM
    into the new shared VNet. Requires RunnerToken + RunnerVmAdminPassword.

.EXAMPLE
    # Minimal — just migrate ACA + VNet (VM already exists):
    .\scripts\migrate-to-vnet.ps1

    # Full — also redeploy the runner VM:
    .\scripts\migrate-to-vnet.ps1 `
        -RunnerToken "AABB..." `
        -RunnerVmAdminPassword "P@ssw0rd123!" `
        -DeployWindowsRunner
#>

param(
    [string] $RunnerToken             = '',
    [string] $RunnerVmAdminPassword   = '',
    [switch] $DeployWindowsRunner
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$RG       = 'mcp-factory-rg'
$ENV_NAME = 'mcp-factory-env'
$PIPELINE = 'mcp-factory-pipeline'
$UI       = 'mcp-factory-ui'

Write-Host "`n=== MCP Factory VNet Migration ===" -ForegroundColor Cyan
Write-Host "This script will briefly take the deployment offline." -ForegroundColor Yellow
Write-Host "Estimated downtime: ~10-15 minutes.`n"

$confirm = Read-Host "Type 'yes' to proceed"
if ($confirm -ne 'yes') { Write-Host "Aborted."; exit 0 }

# ── Step 1: Delete container apps ─────────────────────────────────────────
Write-Host "`n[1/4] Deleting container apps..." -ForegroundColor Cyan

foreach ($app in @($PIPELINE, $UI)) {
    $exists = az containerapp show --name $app --resource-group $RG `
        --query name --output tsv 2>$null
    if ($exists) {
        Write-Host "  Deleting $app..."
        az containerapp delete --name $app --resource-group $RG --yes 2>&1 | Out-Null
        Write-Host "  $app deleted." -ForegroundColor Green
    } else {
        Write-Host "  $app not found, skipping."
    }
}

# ── Step 2: Delete ACA managed environment ────────────────────────────────
Write-Host "`n[2/4] Deleting ACA managed environment '$ENV_NAME'..." -ForegroundColor Cyan

$envExists = az containerapp env show --name $ENV_NAME --resource-group $RG `
    --query name --output tsv 2>$null
if ($envExists) {
    az containerapp env delete --name $ENV_NAME --resource-group $RG --yes 2>&1 | Out-Null
    Write-Host "  Environment deleted." -ForegroundColor Green
} else {
    Write-Host "  Environment not found, skipping."
}

# ── Step 3: Run Bicep deployment ───────────────────────────────────────────
Write-Host "`n[3/4] Running Bicep deployment (VNet-integrated)..." -ForegroundColor Cyan

$bicepRoot  = Join-Path $PSScriptRoot '..' 'infra'
$templateFile = Join-Path $bicepRoot 'main.bicep'
$paramsFile   = Join-Path $bicepRoot 'main.bicepparam'

$extraParams = @()

if ($DeployWindowsRunner) {
    if (-not $RunnerToken)           { Write-Error "-RunnerToken is required with -DeployWindowsRunner"; exit 1 }
    if (-not $RunnerVmAdminPassword) { Write-Error "-RunnerVmAdminPassword is required with -DeployWindowsRunner"; exit 1 }
    $extraParams += "deployWindowsRunner=true"
    $extraParams += "runnerToken=$RunnerToken"
    $extraParams += "runnerVmAdminPassword=$RunnerVmAdminPassword"
}

$paramArgs = $extraParams | ForEach-Object { "--parameters", $_ }

Write-Host "  az deployment sub create --location eastus ..."
az deployment sub create `
    --location eastus `
    --template-file $templateFile `
    --parameters $paramsFile `
    @paramArgs `
    --output table

if ($LASTEXITCODE -ne 0) {
    Write-Error "Bicep deployment failed. Check output above."
    exit 1
}
Write-Host "  Bicep deployment succeeded." -ForegroundColor Green

# ── Step 4: Verify VNet wired in ──────────────────────────────────────────
Write-Host "`n[4/4] Verifying VNet configuration..." -ForegroundColor Cyan

$vnetConfig = az containerapp env show `
    --name $ENV_NAME --resource-group $RG `
    --query "properties.vnetConfiguration" --output json 2>&1 | ConvertFrom-Json

if ($vnetConfig -and $vnetConfig.infrastructureSubnetId) {
    Write-Host "  VNet integrated: $($vnetConfig.infrastructureSubnetId)" -ForegroundColor Green
} else {
    Write-Warning "  VNet config not detected - check the deployment output."
}

$newDomain = az containerapp env show `
    --name $ENV_NAME --resource-group $RG `
    --query "properties.defaultDomain" --output tsv 2>&1

Write-Host "`n=== Migration complete ===" -ForegroundColor Green
Write-Host "New environment default domain: $newDomain"
Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. Trigger the CI/CD pipeline to rebuild and redeploy the container images."
Write-Host "     (push an empty commit or re-run the latest workflow run)"
Write-Host "  2. GUI_BRIDGE_URL and GUI_BRIDGE_SECRET are now read from Key Vault via secretref."
Write-Host "     No manual env var update needed — the KV secret gui-bridge-url holds the VM address."
Write-Host "  3. Smoke-test: curl https://{new-ui-fqdn}/health  (fqdn printed above)"
