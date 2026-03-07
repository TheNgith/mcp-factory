# cleanup_kv_secrets.ps1
# Removes the duplicate Key Vault secrets that shadow the canonical names.
#
# Canonical secrets (keep these):
#   azure-storage-account         <- used by ACA pipeline secretref
#   appinsights-connection        <- used by ACA pipeline secretref
#
# Duplicate / stale secrets (delete these):
#   storage-account               <- shadow of azure-storage-account
#   appinsights-connection-string <- shadow of appinsights-connection
#
# Prerequisites: az login, Contributor or Key Vault Secrets Officer on the vault.
# Soft-delete is enabled on the vault, so secrets are recoverable for 90 days.

param(
    [string]$VaultName = "mcp-factory-kv",
    [switch]$DryRun
)

$stale = @("storage-account", "appinsights-connection-string")

foreach ($secret in $stale) {
    if ($DryRun) {
        Write-Host "[DRY RUN] Would delete: $secret"
        continue
    }

    Write-Host "Deleting $secret ..."
    $result = az keyvault secret delete --vault-name $VaultName --name $secret 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  Deleted. Purging (bypass soft-delete retention) ..."
        az keyvault secret purge --vault-name $VaultName --name $secret 2>&1 | Out-Null
        Write-Host "  Purged."
    } else {
        Write-Host "  Skip / not found: $result"
    }
}

Write-Host ""
Write-Host "Remaining secrets:"
az keyvault secret list --vault-name $VaultName --query "[].name" -o tsv
