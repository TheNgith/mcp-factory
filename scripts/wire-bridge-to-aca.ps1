# wire-bridge-to-aca.ps1
# Run from local machine after the Azure VM is up.
# Sets GUI_BRIDGE_URL and GUI_BRIDGE_SECRET on the ACA pipeline container.
#
# Usage:
#   .\scripts\wire-bridge-to-aca.ps1 -BridgeSecret "<secret>" -BridgeIP "<vm-public-ip>"

param(
    [Parameter(Mandatory)] [string]$BridgeSecret,
    [Parameter(Mandatory)] [string]$BridgeIP,
    [string]$BridgePort    = "8090",
    [string]$ResourceGroup = "mcp-factory-rg",
    [string]$AcaApp        = "mcp-factory-pipeline",
    [string]$VmName        = "mcpfactory-runner-vm",
    [string]$HealthUrl     = "https://mcp-factory-pipeline.icycoast-8ddfa278.eastus.azurecontainerapps.io/health"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$BridgeUrl = "http://${BridgeIP}:${BridgePort}"

Write-Host "[1/3] Opening NSG port $BridgePort on VM $VmName..."
$nicId = az vm show -g $ResourceGroup -n $VmName --query "networkProfile.networkInterfaces[0].id" -o tsv
$nsgId = az network nic show --ids $nicId --query "networkSecurityGroup.id" -o tsv

if ($nsgId) {
    $nsgName = ($nsgId -split '/')[-1]
    $existing = az network nsg rule list --nsg-name $nsgName -g $ResourceGroup --query "[?name=='Allow-MCP-Bridge'].name" -o tsv
    if (-not $existing) {
        az network nsg rule create `
            --nsg-name $nsgName `
            --resource-group $ResourceGroup `
            --name "Allow-MCP-Bridge" `
            --priority 1100 `
            --protocol Tcp `
            --direction Inbound `
            --source-address-prefixes "*" `
            --destination-port-ranges $BridgePort `
            --access Allow | Out-Null
        Write-Host "[OK] NSG rule created for port $BridgePort"
    } else {
        Write-Host "[INFO] NSG rule already exists"
    }
} else {
    Write-Warning "Could not find NSG - open port $BridgePort manually if needed."
}

Write-Host "[2/3] Updating ACA $AcaApp with bridge env vars..."
az containerapp update `
    --name $AcaApp `
    --resource-group $ResourceGroup `
    --set-env-vars "GUI_BRIDGE_URL=$BridgeUrl" "GUI_BRIDGE_SECRET=$BridgeSecret" | Out-Null
Write-Host "[OK] ACA updated - GUI_BRIDGE_URL=$BridgeUrl"

Write-Host "[3/3] Waiting 15s for ACA revision to roll out..."
Start-Sleep -Seconds 15

try {
    $resp = Invoke-RestMethod -Uri $HealthUrl -TimeoutSec 10
    Write-Host "[OK] Pipeline health: $($resp | ConvertTo-Json -Compress)"
} catch {
    Write-Warning "Health check failed - ACA may still be restarting. Try: Invoke-RestMethod $HealthUrl"
}

Write-Host ""
Write-Host "=================================================="
Write-Host "  Bridge wired! Upload any .exe to the web UI"
Write-Host "  and GUI invocables will come back from the VM."
Write-Host "=================================================="
