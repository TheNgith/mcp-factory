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

// ── Windows runner VM (opt-in) ────────────────────────────────────────────
// Set deployWindowsRunner=true and supply runnerToken + runnerVmAdminPassword
// to provision the self-hosted runner for GUI automation CI jobs.
// Generate runnerToken: gh api repos/evanking12/mcp-factory/actions/runners/registration-token --method POST --jq .token
param deployWindowsRunner    = false
param githubRepo             = 'evanking12/mcp-factory'
// runnerToken and runnerVmAdminPassword are @secure() — pass on the CLI:
//   --parameters runnerToken=<TOKEN> runnerVmAdminPassword=<PW>

// ── GUI bridge ────────────────────────────────────────────────────────────
// URL and secret are stored in Key Vault (gui-bridge-url, gui-bridge-secret).
// The pipeline reads them via secretref — no parameter needed here.
// After VNet integration the VM gets static private IP 10.0.2.4; update the
// KV secret if the VM is redeployed and the IP changes.
