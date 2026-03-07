// infra/main.bicepparam — default parameter values for MCP Factory Bicep deployment.
// Usage:
//   az deployment sub create \
//     --location eastus \
//     --template-file infra/main.bicep \
//     --parameters infra/main.bicepparam

using './main.bicep'

param location       = 'eastus'
param prefix         = 'mcpfactory'
param acrSku         = 'Basic'
param pipelineImageTag = 'latest'
param uiImageTag       = 'latest'
