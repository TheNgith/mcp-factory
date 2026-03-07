/*
  infra/main.bicep — MCP Factory infrastructure
  Deploys the complete MCP Factory stack from scratch.

  Usage:
    az deployment sub create \
      --location eastus \
      --template-file infra/main.bicep \
      --parameters infra/main.bicepparam

  Resources provisioned:
    • Resource Group
    • User-Assigned Managed Identity
    • Key Vault
    • Storage Account (containers: uploads, artifacts)
    • Azure Container Registry
    • Log Analytics Workspace
    • Application Insights
    • Azure OpenAI (gpt-4o + text-embedding-3-small deployments)
    • Container Apps Environment
    • Container App — mcp-factory-pipeline
    • Container App — mcp-factory-ui
*/

targetScope = 'subscription'

// ---------------------------------------------------------------------------
// Parameters
// ---------------------------------------------------------------------------

@description('Azure region for all resources.')
param location string = 'eastus'

@minLength(3)
@maxLength(12)
@description('Short prefix used to name every resource (e.g. mcpfactory).')
param prefix string = 'mcpfactory'

@allowed(['Basic', 'Standard', 'Premium'])
@description('SKU for Azure Container Registry.')
param acrSku string = 'Basic'

@description('Docker image tag for the pipeline container app.')
param pipelineImageTag string = 'latest'

@description('Docker image tag for the UI container app.')
param uiImageTag string = 'latest'

// ---------------------------------------------------------------------------
// Derived names — deterministic, collision-free
// ---------------------------------------------------------------------------

var rgName          = '${prefix}-rg'
var identityName    = '${prefix}-identity'
var kvName          = '${prefix}-kv'
var storageName     = replace('${prefix}store', '-', '')       // storage name: no hyphens, max 24 chars
var acrName         = replace('${prefix}acr', '-', '')
var logName         = '${prefix}-logs'
var appInsightsName = '${prefix}-appinsights'
var openAiName      = '${prefix}-openai'
var acaEnvName      = '${prefix}-env'
var pipelineAppName = '${prefix}-pipeline'
var uiAppName       = '${prefix}-ui'
var pipelineImage   = '${acrName}.azurecr.io/mcp-factory-pipeline:${pipelineImageTag}'
var uiImage         = '${acrName}.azurecr.io/mcp-factory-ui:${uiImageTag}'

// ---------------------------------------------------------------------------
// Resource Group
// ---------------------------------------------------------------------------

resource rg 'Microsoft.Resources/resourceGroups@2022-09-01' = {
  name:     rgName
  location: location
}

// ---------------------------------------------------------------------------
// Managed Identity
// ---------------------------------------------------------------------------

module identity 'br/public:avm/res/managed-identity/user-assigned-identity:0.2.1' = {
  name:  'identity'
  scope: rg
  params: {
    name:     identityName
    location: location
  }
}

// ---------------------------------------------------------------------------
// Key Vault  (RBAC auth; no stored secrets — placeholder for future use)
// ---------------------------------------------------------------------------

module kv 'br/public:avm/res/key-vault/vault:0.6.1' = {
  name:  'kv'
  scope: rg
  params: {
    name:                    kvName
    location:                location
    enableRbacAuthorization: true
    sku:                     'standard'
    softDeleteRetentionInDays: 7
    enablePurgeProtection:   false
    roleAssignments: [
      {
        // Key Vault Secrets User — Managed Identity
        roleDefinitionIdOrName: '4633458b-17de-408a-b874-0445c86b69e6'
        principalId:            identity.outputs.principalId
        principalType:          'ServicePrincipal'
      }
    ]
  }
}

// ---------------------------------------------------------------------------
// Storage Account (containers: uploads, artifacts)
// ---------------------------------------------------------------------------

module storage 'br/public:avm/res/storage/storage-account:0.9.1' = {
  name:  'storage'
  scope: rg
  params: {
    name:     storageName
    location: location
    skuName:  'Standard_LRS'
    kind:     'StorageV2'
    blobServices: {
      containers: [
        { name: 'uploads',   publicAccess: 'None' }
        { name: 'artifacts', publicAccess: 'None' }
      ]
    }
    roleAssignments: [
      {
        // Storage Blob Data Contributor — Managed Identity
        roleDefinitionIdOrName: 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
        principalId:            identity.outputs.principalId
        principalType:          'ServicePrincipal'
      }
    ]
  }
}

// ---------------------------------------------------------------------------
// Azure Container Registry
// ---------------------------------------------------------------------------

module acr 'br/public:avm/res/container-registry/registry:0.3.1' = {
  name:  'acr'
  scope: rg
  params: {
    name:     acrName
    location: location
    acrSku:   acrSku
    acrAdminUserEnabled: false
    roleAssignments: [
      {
        // AcrPull — Managed Identity (used by ACA to pull images)
        roleDefinitionIdOrName: '7f951dda-4ed3-4680-a7ca-43fe172d538d'
        principalId:            identity.outputs.principalId
        principalType:          'ServicePrincipal'
      }
      {
        // AcrPush — Managed Identity (used by GitHub Actions via federated credential)
        roleDefinitionIdOrName: '8311e382-0749-4cb8-b61a-304f252e45ec'
        principalId:            identity.outputs.principalId
        principalType:          'ServicePrincipal'
      }
    ]
  }
}

// ---------------------------------------------------------------------------
// Log Analytics + Application Insights
// ---------------------------------------------------------------------------

module logAnalytics 'br/public:avm/res/operational-insights/workspace:0.3.4' = {
  name:  'logAnalytics'
  scope: rg
  params: {
    name:     logName
    location: location
    skuName:  'PerGB2018'
    retentionInDays: 30
  }
}

module appInsights 'br/public:avm/res/insights/component:0.3.0' = {
  name:  'appInsights'
  scope: rg
  params: {
    name:            appInsightsName
    location:        location
    workspaceResourceId: logAnalytics.outputs.resourceId
    applicationType: 'web'
    kind:            'web'
  }
}

// ---------------------------------------------------------------------------
// Azure OpenAI
// ---------------------------------------------------------------------------

module openAi 'br/public:avm/res/cognitive-services/account:0.5.4' = {
  name:  'openAi'
  scope: rg
  params: {
    name:     openAiName
    location: location
    kind:     'OpenAI'
    sku:      'S0'
    deployments: [
      {
        name:  'gpt-4o'
        model: {
          format:  'OpenAI'
          name:    'gpt-4o'
          version: '2024-08-06'
        }
        sku: { name: 'Standard', capacity: 10 }
      }
      {
        name:  'text-embedding-3-small'
        model: {
          format:  'OpenAI'
          name:    'text-embedding-3-small'
          version: '1'
        }
        sku: { name: 'Standard', capacity: 30 }
      }
    ]
    roleAssignments: [
      {
        // Cognitive Services OpenAI User — Managed Identity
        roleDefinitionIdOrName: '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'
        principalId:            identity.outputs.principalId
        principalType:          'ServicePrincipal'
      }
    ]
  }
}

// ---------------------------------------------------------------------------
// Azure AI Search — free tier, semantic tool retrieval (P5)
// ---------------------------------------------------------------------------

var searchName = '${prefix}-search'

resource search 'Microsoft.Search/searchServices@2023-11-01' = {
  name:     searchName
  location: location
  sku: {
    name: 'free'
  }
  properties: {
    replicaCount:  1
    partitionCount: 1
    publicNetworkAccess: 'Enabled'
    authOptions: {
      aadOrApiKey: {
        aadAuthFailureMode: 'http403'
      }
    }
  }
}

// Grant the Managed Identity "Search Index Data Contributor" on the Search Service
resource searchRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name:  guid(search.id, identity.outputs.principalId, '8ebe5a00-799e-43f5-93ac-243d3dce84a7')
  scope: search
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '8ebe5a00-799e-43f5-93ac-243d3dce84a7')
    principalId:      identity.outputs.principalId
    principalType:    'ServicePrincipal'
  }
}

// ---------------------------------------------------------------------------
// Container Apps Environment
// ---------------------------------------------------------------------------

module acaEnv 'br/public:avm/res/app/managed-environment:0.4.5' = {
  name:  'acaEnv'
  scope: rg
  params: {
    name:                         acaEnvName
    location:                     location
    logAnalyticsWorkspaceResourceId: logAnalytics.outputs.resourceId
    zoneRedundant:                false
  }
}

// ---------------------------------------------------------------------------
// Container App — pipeline API
// ---------------------------------------------------------------------------

module pipelineApp 'br/public:avm/res/app/container-app:0.4.1' = {
  name:  'pipelineApp'
  scope: rg
  params: {
    name:        pipelineAppName
    location:    location
    environmentResourceId: acaEnv.outputs.resourceId
    managedIdentities: {
      userAssignedResourceIds: [ identity.outputs.resourceId ]
    }
    ingressExternal:   true
    ingressTargetPort: 8000
    scaleMinReplicas:  0
    scaleMaxReplicas:  5
    scaleRules: [
      {
        name: 'http-scale'
        http: { metadata: { concurrentRequests: '10' } }
      }
    ]
    containers: [
      {
        name:  'pipeline'
        image: pipelineImage
        resources: { cpu: '1.0', memory: '2Gi' }
        env: [
          { name: 'AZURE_CLIENT_ID',                value: identity.outputs.clientId }
          { name: 'AZURE_STORAGE_ACCOUNT',          value: storageName }
          { name: 'AZURE_OPENAI_ENDPOINT',          value: openAi.outputs.endpoint }
          { name: 'AZURE_OPENAI_DEPLOYMENT',        value: 'gpt-4o' }
          { name: 'AZURE_SEARCH_ENDPOINT',          value: 'https://${searchName}.search.windows.net' }
          { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsights.outputs.connectionString }
        ]
      }
    ]
    registries: [
      {
        server:   '${acrName}.azurecr.io'
        identity: identity.outputs.resourceId
      }
    ]
  }
}

// ---------------------------------------------------------------------------
// Container App — UI
// ---------------------------------------------------------------------------

module uiApp 'br/public:avm/res/app/container-app:0.4.1' = {
  name:  'uiApp'
  scope: rg
  params: {
    name:        uiAppName
    location:    location
    environmentResourceId: acaEnv.outputs.resourceId
    managedIdentities: {
      userAssignedResourceIds: [ identity.outputs.resourceId ]
    }
    ingressExternal:   true
    ingressTargetPort: 3000
    scaleMinReplicas:  0
    scaleMaxReplicas:  3
    scaleRules: [
      {
        name: 'http-scale'
        http: { metadata: { concurrentRequests: '10' } }
      }
    ]
    containers: [
      {
        name:  'ui'
        image: uiImage
        resources: { cpu: '0.5', memory: '1Gi' }
        env: [
          { name: 'AZURE_CLIENT_ID',     value: identity.outputs.clientId }
          { name: 'PIPELINE_API_URL',    value: 'https://${pipelineApp.outputs.fqdn}' }
        ]
      }
    ]
    registries: [
      {
        server:   '${acrName}.azurecr.io'
        identity: identity.outputs.resourceId
      }
    ]
  }
}

// ---------------------------------------------------------------------------
// Outputs
// ---------------------------------------------------------------------------

@description('Pipeline API URL')
output pipelineUrl string = 'https://${pipelineApp.outputs.fqdn}'

@description('Web UI URL')
output uiUrl string = 'https://${uiApp.outputs.fqdn}'

@description('ACR login server')
output acrLoginServer string = acr.outputs.loginServer

@description('Managed Identity client ID')
output managedIdentityClientId string = identity.outputs.clientId

@description('Managed Identity principal ID')
output managedIdentityPrincipalId string = identity.outputs.principalId

@description('Azure OpenAI endpoint')
output openAiEndpoint string = openAi.outputs.endpoint

@description('App Insights connection string')
output appInsightsConnectionString string = appInsights.outputs.connectionString

@description('Storage account name')
output storageAccountName string = storageName

@description('Key Vault URI')
output keyVaultUri string = kv.outputs.uri

@description('Azure AI Search endpoint')
output searchEndpoint string = 'https://${searchName}.search.windows.net'
