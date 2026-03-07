# wire-bridge-to-aca.ps1
#
# Run this from your LOCAL machine (with az CLI logged in) AFTER running
# install-bridge-service.ps1 on the Windows runner VM.
#
# Usage:
#   .\scripts\wire-bridge-to-aca.ps1 -BridgeSecret "<secret>" -BridgeIP "<vm-public-ip>"
#
# What it does:
#   1. Stores BRIDGE_SECRET in Key Vault as 'gui-bridge-secret'
#   2. Adds NSG rule on the VM's NIC to allow port 8090 inbound
#   3. Adds GUI_BRIDGE_URL + GUI_BRIDGE_SECRET env vars to the ACA pipeline container
#   4. Smoke-tests the bridge via the ACA pipeline's /health endpoint

param(
    [Parameter(Mandatory)]
    [string]$BridgeSecret,

    [Parameter(Mandatory)]
    [string]$BridgeIP,

    [string]$BridgePort     = "8090",
    [string]$KeyVault       = "mcp-factory-kv",
    [string]$ResourceGroup  = "mcp-factory-rg",
    [string]$AcaApp         = "mcp-factory-pipeline",
    [string]$VmName         = "mcp-factory-runner",
    [string]$HealthUrl      = "https://mcp-factory-pipeline.calmsmoke-c4f97e21.eastus.azurecontainerapps.io/health"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$BridgeUrl = "http://${BridgeIP}:${BridgePort}"

Write-Host "[1/4] Storing bridge secret in Key Vault '$KeyVault'..."
az keyvault secret set `
    --vault-name $KeyVault `
    --name       "gui-bridge-secret" `
    --value      $BridgeSecret | Out-Null
Write-Host "[OK] Secret stored as 'gui-bridge-secret'"

Write-Host ""
Write-Host "[2/4] Opening NSG port $BridgePort on VM '$VmName'..."
# Find the NIC attached to the runner VM
$nicId = az vm show -g $ResourceGroup -n $VmName `
    --query "networkProfile.networkInterfaces[0].id" -o tsv
$nsgId = az network nic show --ids $nicId `
    --query "networkSecurityGroup.id" -o tsv

if ($nsgId) {
    az network nsg rule create `
        --nsg-name  (Split-Path $nsgId -Leaf) `
        --resource-group $ResourceGroup `
        --name      "Allow-MCP-Bridge" `
        --priority  1100 `
        --protocol  Tcp `
        --direction Inbound `
        --source-address-prefixes "*" `
        --destination-port-ranges $BridgePort `
        --access    Allow | Out-Null
    Write-Host "[OK] NSG inbound rule created for port $BridgePort"
} else {
    Write-Warning "Could not find NSG for VM '$VmName'. Open port $BridgePort manually in Azure Portal if needed."
}

Write-Host ""
Write-Host "[3/4] Updating ACA '$AcaApp' with bridge env vars..."
az containerapp update `
    --name           $AcaApp `
    --resource-group $ResourceGroup `
    --set-env-vars   "GUI_BRIDGE_URL=$BridgeUrl" "GUI_BRIDGE_SECRET=$BridgeSecret" | Out-Null
Write-Host "[OK] ACA updated — GUI_BRIDGE_URL=$BridgeUrl"

Write-Host ""
Write-Host "[4/4] Waiting 15s for ACA revision to roll out..."
Start-Sleep -Seconds 15

try {
    $resp = Invoke-RestMethod -Uri $HealthUrl -TimeoutSec 10
    Write-Host "[OK] Pipeline health: $($resp | ConvertTo-Json -Compress)"
} catch {
    Write-Warning "Health check failed — ACA may still be restarting. Try again in ~30s: Invoke-RestMethod $HealthUrl"
}

Write-Host ""
Write-Host "=================================================="
Write-Host "  Bridge wired! Upload any .exe to the web UI"
Write-Host "  and GUI invocables will come back from the VM."
Write-Host "=================================================="
