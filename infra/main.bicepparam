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

// ── GUI bridge (set only when deployWindowsRunner=true) ───────────────────
// After VNet integration, the VM gets static private IP 10.0.2.4.
// guiBridgeSecret is @secure() — pass on the CLI:
//   --parameters guiBridgeSecret=<SECRET>
param guiBridgeUrl = 'http://10.0.2.4:8090'
