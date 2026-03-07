#!/usr/bin/env pwsh
<#
.SYNOPSIS
    MCP Factory — Azure finalization script.
    Provisions the two remaining Azure resources, stores secrets, builds and
    pushes both Docker images, then deploys the ACA container apps.

.DESCRIPTION
    Run this once from the repo root after:
      az login
      az account set --subscription abb10328-e7f1-4d4a-9067-c1967fd70429

    Safe to re-run: each step checks for existing resources and skips or
    updates as appropriate.

    Steps:
      1. Provision Azure OpenAI + deploy gpt-4o model
      2. Provision Application Insights
      3. Store all secrets in Key Vault
      4. Build + push pipeline image  (Dockerfile)
      5. Build + push UI image        (Dockerfile.ui)
      6. Deploy / update ACA pipeline container app
      7. Deploy / update ACA UI container app

.NOTES
    Pre-requisites installed locally:
      - Azure CLI  (az login already done)
      - Docker Desktop running
#>

param(
    [string]$SubscriptionId    = "abb10328-e7f1-4d4a-9067-c1967fd70429",
    [string]$TenantId          = "bfddc1f5-1e88-471c-9b5e-44611ddd3c22",
    [string]$ResourceGroup     = "mcp-factory-rg",
    [string]$Location          = "eastus",

    # Managed Identity
    [string]$IdentityClientId  = "f70e3ce7-3790-49de-95b0-2de22fc0adbe",
    [string]$IdentityPrincipalId = "ef864658-442a-4dc1-b8d9-37f01f79fe8d",

    # Key Vault
    [string]$KeyVault          = "mcp-factory-kv",

    # Storage
    [string]$StorageAccount    = "mcpfactorystore",

    # Container Registry
    [string]$Acr               = "mcpfactoryacr",
    [string]$AcrHost           = "mcpfactoryacr.azurecr.io",

    # ACA
    [string]$AcaEnv            = "mcp-factory-env",
    [string]$AcaPipeline       = "mcp-factory-pipeline",
    [string]$AcaUi             = "mcp-factory-ui",

    # Azure OpenAI
    [string]$OpenAiAccount     = "mcp-factory-openai",
    [string]$OpenAiDeployment  = "gpt-4o",
    [string]$OpenAiModelVersion = "2024-11-20",

    # App Insights
    [string]$AppInsights       = "mcp-factory-insights",
    [string]$LogAnalytics      = "mcp-factory-logs",

    # Image tags
    [string]$Tag               = "latest"
)

# Never prompt for extension installs — install silently or skip
az config set extension.use_dynamic_install=yes_without_prompt | Out-Null

function Log-Step([string]$msg) {
    Write-Host "`n-- $msg" -ForegroundColor Cyan
}
function Log-Ok([string]$msg) {
    Write-Host "   OK: $msg" -ForegroundColor Green
}
function Log-Skip([string]$msg) {
    Write-Host "   SKIP: $msg (already exists)" -ForegroundColor Yellow
}
# Probe whether an Azure resource exists without letting a 404 abort the script.
# Returns $true if exit code is 0, $false otherwise.
function Test-AzResource([scriptblock]$cmd) {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'SilentlyContinue'
    try { & $cmd 2>&1 | Out-Null } catch {}
    $ErrorActionPreference = $prev
    return ($LASTEXITCODE -eq 0)
}

az account set --subscription $SubscriptionId | Out-Null
Write-Host "`n==================================================" -ForegroundColor White
Write-Host "  MCP Factory - Azure Finalization" -ForegroundColor White
Write-Host "  Subscription : $SubscriptionId" -ForegroundColor White
Write-Host "  Resource Group: $ResourceGroup / $Location" -ForegroundColor White
Write-Host "==================================================`n" -ForegroundColor White

# ══════════════════════════════════════════════════════════════
# STEP 1 — Azure OpenAI
# ══════════════════════════════════════════════════════════════
Log-Step "Step 1/7 — Azure OpenAI resource + gpt-4o deployment"

$openAiExists = Test-AzResource { az cognitiveservices account show --name $OpenAiAccount --resource-group $ResourceGroup }

if (-not $openAiExists) {
    Write-Host "   Creating Azure OpenAI account '$OpenAiAccount'…"
    az cognitiveservices account create `
        --name            $OpenAiAccount `
        --resource-group  $ResourceGroup `
        --location        $Location `
        --kind            OpenAI `
        --sku             S0 `
        --custom-domain   $OpenAiAccount `
        --output none
    Log-Ok "Azure OpenAI account created"
} else {
    Log-Skip "Azure OpenAI account '$OpenAiAccount'"
}

$deploymentExists = Test-AzResource { az cognitiveservices account deployment show --name $OpenAiAccount --resource-group $ResourceGroup --deployment-name $OpenAiDeployment }

if (-not $deploymentExists) {
    Write-Host "   Deploying model '$OpenAiDeployment'…"
    az cognitiveservices account deployment create `
        --name              $OpenAiAccount `
        --resource-group    $ResourceGroup `
        --deployment-name   $OpenAiDeployment `
        --model-name        gpt-4o `
        --model-version     $OpenAiModelVersion `
        --model-format      OpenAI `
        --sku-capacity      10 `
        --sku-name          Standard `
        --output none
    Log-Ok "Model '$OpenAiDeployment' deployed (capacity: 10 TPM * 1000)"
} else {
    Log-Skip "Model deployment '$OpenAiDeployment'"
}

# Retrieve the endpoint
$OpenAiEndpoint = (az cognitiveservices account show `
    --name $OpenAiAccount `
    --resource-group $ResourceGroup `
    --query "properties.endpoint" -o tsv)
Write-Host "   Endpoint: $OpenAiEndpoint"

# Grant Managed Identity the OpenAI User role (idempotent)
Write-Host "   Assigning Cognitive Services OpenAI User role to Managed Identity…"
$prev = $ErrorActionPreference; $ErrorActionPreference = 'SilentlyContinue'
az role assignment create `
    --assignee   $IdentityPrincipalId `
    --role       "Cognitive Services OpenAI User" `
    --scope      "/subscriptions/$SubscriptionId/resourceGroups/$ResourceGroup/providers/Microsoft.CognitiveServices/accounts/$OpenAiAccount" `
    2>&1 | Out-Null
$ErrorActionPreference = $prev
Log-Ok "Role assignment done (idempotent)"


# ══════════════════════════════════════════════════════════════
# STEP 2 — Application Insights
# ══════════════════════════════════════════════════════════════
Log-Step "Step 2/7 — Application Insights"

$laExists = Test-AzResource { az monitor log-analytics workspace show --workspace-name $LogAnalytics --resource-group $ResourceGroup }

if (-not $laExists) {
    Write-Host "   Creating Log Analytics workspace '$LogAnalytics'…"
    az monitor log-analytics workspace create `
        --workspace-name  $LogAnalytics `
        --resource-group  $ResourceGroup `
        --location        $Location `
        --output none
    Log-Ok "Log Analytics workspace created"
} else {
    Log-Skip "Log Analytics workspace '$LogAnalytics'"
}

$aiExists = Test-AzResource { az resource show --name $AppInsights --resource-group $ResourceGroup --resource-type "Microsoft.Insights/components" }

if (-not $aiExists) {
    $laId = (az monitor log-analytics workspace show `
        --workspace-name $LogAnalytics `
        --resource-group $ResourceGroup `
        --query id -o tsv)
    Write-Host "   Creating Application Insights '$AppInsights'…"
    $aiProps = ('{"Application_Type":"web","WorkspaceResourceId":"' + $laId + '"}')
    az resource create `
        --name           $AppInsights `
        --resource-group $ResourceGroup `
        --resource-type  "Microsoft.Insights/components" `
        --location       $Location `
        --api-version    "2020-02-02" `
        --properties     $aiProps `
        --output none
    Log-Ok "App Insights created"
} else {
    Log-Skip "App Insights '$AppInsights'"
}

$AppInsightsConnStr = (az resource show `
    --name           $AppInsights `
    --resource-group $ResourceGroup `
    --resource-type  "Microsoft.Insights/components" `
    --query          "properties.ConnectionString" -o tsv)
Write-Host "   Connection string: $($AppInsightsConnStr.Substring(0, [Math]::Min(60, $AppInsightsConnStr.Length)))…"


# ══════════════════════════════════════════════════════════════
# STEP 3 — Key Vault secrets
# ══════════════════════════════════════════════════════════════
Log-Step "Step 3/7 — Key Vault secrets"

$secrets = @{
    "openai-endpoint"          = $OpenAiEndpoint
    "openai-deployment"        = $OpenAiDeployment
    "azure-storage-account"    = $StorageAccount
    "azure-client-id"          = $IdentityClientId
    "appinsights-connection"   = $AppInsightsConnStr
}

foreach ($name in $secrets.Keys) {
    az keyvault secret set `
        --vault-name $KeyVault `
        --name       $name `
        --value      $secrets[$name] `
        --output none
    Log-Ok "Secret '$name' stored"
}


# ══════════════════════════════════════════════════════════════
# STEP 4 — Build + push pipeline image
# ══════════════════════════════════════════════════════════════
Log-Step "Step 4/7 — Build pipeline image (Dockerfile → $AcrHost/mcp-factory:$Tag)"

az acr login --name $Acr

$pipelineImage = "$AcrHost/mcp-factory:$Tag"
docker build -t "mcp-factory:$Tag" .
docker tag "mcp-factory:$Tag" $pipelineImage
docker push $pipelineImage
Log-Ok "Pipeline image pushed: $pipelineImage"


# ══════════════════════════════════════════════════════════════
# STEP 5 — Build + push UI image
# ══════════════════════════════════════════════════════════════
Log-Step "Step 5/7 — Build UI image (Dockerfile.ui → $AcrHost/mcp-factory-ui:$Tag)"

$uiImage = "$AcrHost/mcp-factory-ui:$Tag"
docker build -f Dockerfile.ui -t "mcp-factory-ui:$Tag" .
docker tag "mcp-factory-ui:$Tag" $uiImage
docker push $uiImage
Log-Ok "UI image pushed: $uiImage"


# ══════════════════════════════════════════════════════════════
# STEP 6 — Deploy / update ACA pipeline
# ══════════════════════════════════════════════════════════════
Log-Step "Step 6/7 — ACA pipeline container app"

$pipelineExists = Test-AzResource { az containerapp show --name $AcaPipeline --resource-group $ResourceGroup }

$commonEnv = @(
    "AZURE_STORAGE_ACCOUNT=secretref:azure-storage-account"
    "AZURE_OPENAI_ENDPOINT=secretref:openai-endpoint"
    "AZURE_OPENAI_DEPLOYMENT=secretref:openai-deployment"
    "AZURE_CLIENT_ID=$IdentityClientId"
    "APPLICATIONINSIGHTS_CONNECTION_STRING=secretref:appinsights-connection"
)
$envStr = $commonEnv -join " "

if (-not $pipelineExists) {
    Write-Host "   Creating ACA app '$AcaPipeline'…"
    Invoke-Expression ("az containerapp create " +
        "--name $AcaPipeline " +
        "--resource-group $ResourceGroup " +
        "--environment $AcaEnv " +
        "--image $pipelineImage " +
        "--target-port 8000 " +
        "--ingress external " +
        "--min-replicas 0 " +
        "--max-replicas 3 " +
        "--cpu 0.5 --memory 1.0Gi " +
        "--registry-server $AcrHost " +
        "--user-assigned /subscriptions/$SubscriptionId/resourceGroups/$ResourceGroup/providers/Microsoft.ManagedIdentity/userAssignedIdentities/mcp-factory-identity " +
        "--secrets " +
            "azure-storage-account=keyvaultref:https://$KeyVault.vault.azure.net/secrets/azure-storage-account,identityref:/subscriptions/$SubscriptionId/resourceGroups/$ResourceGroup/providers/Microsoft.ManagedIdentity/userAssignedIdentities/mcp-factory-identity " +
            "openai-endpoint=keyvaultref:https://$KeyVault.vault.azure.net/secrets/openai-endpoint,identityref:/subscriptions/$SubscriptionId/resourceGroups/$ResourceGroup/providers/Microsoft.ManagedIdentity/userAssignedIdentities/mcp-factory-identity " +
            "openai-deployment=keyvaultref:https://$KeyVault.vault.azure.net/secrets/openai-deployment,identityref:/subscriptions/$SubscriptionId/resourceGroups/$ResourceGroup/providers/Microsoft.ManagedIdentity/userAssignedIdentities/mcp-factory-identity " +
            "appinsights-connection=keyvaultref:https://$KeyVault.vault.azure.net/secrets/appinsights-connection,identityref:/subscriptions/$SubscriptionId/resourceGroups/$ResourceGroup/providers/Microsoft.ManagedIdentity/userAssignedIdentities/mcp-factory-identity " +
        "--env-vars $envStr " +
        "--output none")
    Log-Ok "ACA app '$AcaPipeline' created"
} else {
    Write-Host "   Updating ACA app '$AcaPipeline' with new image…"
    az containerapp update `
        --name           $AcaPipeline `
        --resource-group $ResourceGroup `
        --image          $pipelineImage `
        --output none
    Log-Ok "ACA app '$AcaPipeline' updated"
}

$PipelineUrl = (az containerapp show `
    --name           $AcaPipeline `
    --resource-group $ResourceGroup `
    --query          "properties.configuration.ingress.fqdn" -o tsv)
Write-Host "   Pipeline URL: https://$PipelineUrl"


# ══════════════════════════════════════════════════════════════
# STEP 7 — Deploy / update ACA UI
# ══════════════════════════════════════════════════════════════
Log-Step "Step 7/7 — ACA UI container app"

$uiExists = Test-AzResource { az containerapp show --name $AcaUi --resource-group $ResourceGroup }

if (-not $uiExists) {
    Write-Host "   Creating ACA app '$AcaUi'…"
    az containerapp create `
        --name           $AcaUi `
        --resource-group $ResourceGroup `
        --environment    $AcaEnv `
        --image          $uiImage `
        --target-port    3000 `
        --ingress        external `
        --min-replicas   0 `
        --max-replicas   2 `
        --cpu            0.25 --memory 0.5Gi `
        --registry-server $AcrHost `
        --user-assigned  "/subscriptions/$SubscriptionId/resourceGroups/$ResourceGroup/providers/Microsoft.ManagedIdentity/userAssignedIdentities/mcp-factory-identity" `
        --env-vars       "PIPELINE_URL=https://$PipelineUrl" `
        --output none
    Log-Ok "ACA app '$AcaUi' created"
} else {
    Write-Host "   Updating ACA app '$AcaUi' with new image and pipeline URL…"
    az containerapp update `
        --name           $AcaUi `
        --resource-group $ResourceGroup `
        --image          $uiImage `
        --set-env-vars   "PIPELINE_URL=https://$PipelineUrl" `
        --output none
    Log-Ok "ACA app '$AcaUi' updated"
}

$UiUrl = (az containerapp show `
    --name           $AcaUi `
    --resource-group $ResourceGroup `
    --query          "properties.configuration.ingress.fqdn" -o tsv)

# ══════════════════════════════════════════════════════════════
# SUMMARY
# ══════════════════════════════════════════════════════════════
Write-Host "`n==================================================" -ForegroundColor Green
Write-Host "  MCP Factory - Azure deployment complete!" -ForegroundColor Green
Write-Host "==================================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Pipeline API : https://$PipelineUrl/health"
Write-Host "  Web UI       : https://$UiUrl"
Write-Host ""
Write-Host "  Next: open the UI URL in a browser and run the demo." -ForegroundColor White
Write-Host ""
Write-Host "  To redeploy after code changes:" -ForegroundColor White
Write-Host "    docker build -t mcp-factory:latest . ; docker tag mcp-factory:latest $pipelineImage ; docker push $pipelineImage" -ForegroundColor DarkGray
Write-Host "    az containerapp update --name $AcaPipeline --resource-group $ResourceGroup --image $pipelineImage" -ForegroundColor DarkGray
Write-Host ""
