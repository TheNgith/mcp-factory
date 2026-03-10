/*
  infra/runner-vm.bicep
  Deploys a Standard_D2s_v3 Windows Server 2022 VM that self-registers as
  a GitHub Actions runner with labels [self-hosted, windows, x64].

  The CustomScriptExtension calls scripts/install-github-runner.ps1 from
  the repo's main branch on first boot.  Supply runnerToken with a freshly
  generated GitHub registration token:

    gh api repos/evanking12/mcp-factory/actions/runners/registration-token \
       --method POST --jq .token

  ⚠️  runnerToken expires after 1 hour — generate it immediately before
  running `az deployment sub create`.

  Deploy (standalone, or as a module from main.bicep):
    az deployment group create \
      --resource-group mcp-factory-rg \
      --template-file infra/runner-vm.bicep \
      --parameters location=eastus prefix=mcpfactory \
                   runnerToken=<TOKEN> adminPassword=<PASSWORD>
*/

// ---------------------------------------------------------------------------
// Parameters
// ---------------------------------------------------------------------------

@description('Azure region.')
param location string

@description('Short resource prefix (must match main.bicep prefix).')
param prefix string

@description('GitHub repo in owner/repo format.')
param githubRepo string = 'evanking12/mcp-factory'

@description('Ephemeral GitHub Actions runner registration token (expires 1 h).')
@secure()
param runnerToken string

@description('Windows local administrator username.')
param adminUsername string = 'azureuser'

@description('Windows local administrator password (min 12 chars, complexity required).')
@secure()
param adminPassword string

@description('Resource ID of an existing subnet to place the VM in. When set, no VNet is created by this module. Pass vnetDeploy.outputs.vmSubnetId from main.bicep.')
param vmSubnetId string = ''

@description('Static private IP for the VM NIC. Leave empty for dynamic assignment. Set to 10.0.2.4 when using the shared VNet.')
param staticPrivateIp string = ''

// Use the shared VNet subnet when provided; otherwise create a standalone VNet.
var useSharedVnet    = !empty(vmSubnetId)
var standaloneSubnet = resourceId('Microsoft.Network/virtualNetworks/subnets', '${prefix}-runner-vnet', 'default')
var effectiveSubnet  = useSharedVnet ? vmSubnetId : standaloneSubnet

// ---------------------------------------------------------------------------
// Networking
// ---------------------------------------------------------------------------

// Standalone VNet — only deployed when vmSubnetId is NOT provided
resource vnet 'Microsoft.Network/virtualNetworks@2023-09-01' = if (!useSharedVnet) {
  name:     '${prefix}-runner-vnet'
  location: location
  properties: {
    addressSpace: { addressPrefixes: ['10.1.0.0/16'] }
    subnets: [
      {
        name:       'default'
        properties: { addressPrefix: '10.1.0.0/24' }
      }
    ]
  }
}

resource pip 'Microsoft.Network/publicIPAddresses@2023-09-01' = {
  name:     '${prefix}-runner-pip'
  location: location
  sku:      { name: 'Standard' }
  properties: {
    publicIPAllocationMethod: 'Static'
    dnsSettings: { domainNameLabel: '${prefix}-ghrunner' }
  }
}

resource nic 'Microsoft.Network/networkInterfaces@2023-09-01' = {
  name:     '${prefix}-runner-nic'
  location: location
  properties: {
    ipConfigurations: [
      {
        name: 'ipconfig1'
        properties: {
          privateIPAllocationMethod: empty(staticPrivateIp) ? 'Dynamic' : 'Static'
          privateIPAddress:          empty(staticPrivateIp) ? null      : staticPrivateIp
          publicIPAddress:           { id: pip.id }
          subnet:                    { id: effectiveSubnet }
        }
      }
    ]
  }
}

// ---------------------------------------------------------------------------
// VM
// ---------------------------------------------------------------------------

resource vm 'Microsoft.Compute/virtualMachines@2023-09-01' = {
  name:     '${prefix}-runner-vm'
  location: location
  properties: {
    hardwareProfile: { vmSize: 'Standard_D2s_v3' }
    osProfile: {
      computerName:  take('${prefix}-runner', 15)  // Windows name limit = 15 chars
      adminUsername: adminUsername
      adminPassword: adminPassword
      windowsConfiguration: {
        enableAutomaticUpdates: true
        patchSettings:          { patchMode: 'AutomaticByOS' }
        timeZone:               'UTC'
      }
    }
    storageProfile: {
      imageReference: {
        publisher: 'MicrosoftWindowsServer'
        offer:     'WindowsServer'
        sku:       '2022-datacenter-azure-edition'
        version:   'latest'
      }
      osDisk: {
        createOption:         'FromImage'
        managedDisk:          { storageAccountType: 'Premium_LRS' }
        diskSizeGB:           128
        deleteOption:         'Delete'
      }
    }
    networkProfile: {
      networkInterfaces: [
        { id: nic.id, properties: { deleteOption: 'Delete' } }
      ]
    }
    diagnosticsProfile: {
      bootDiagnostics: { enabled: true }
    }
  }
}

// ---------------------------------------------------------------------------
// CustomScriptExtension — install Python + GitHub Actions runner
// ---------------------------------------------------------------------------

resource runnerSetup 'Microsoft.Compute/virtualMachines/extensions@2023-09-01' = {
  parent:   vm
  name:     'RunnerSetup'
  location: location
  properties: {
    publisher:               'Microsoft.Compute'
    type:                    'CustomScriptExtension'
    typeHandlerVersion:      '1.10'
    autoUpgradeMinorVersion: true
    settings: {
      // The script is fetched from the public repo at deployment time.
      fileUris: [
        'https://raw.githubusercontent.com/${githubRepo}/main/scripts/install-github-runner.ps1'
      ]
    }
    protectedSettings: {
      // runnerToken stays out of deployment logs — passed via protectedSettings.
      commandToExecute: 'powershell.exe -ExecutionPolicy Bypass -File install-github-runner.ps1 -RepoUrl "https://github.com/${githubRepo}" -RunnerToken "${runnerToken}" -RunnerName "${prefix}-runner"'
    }
  }
}

// ---------------------------------------------------------------------------
// Outputs
// ---------------------------------------------------------------------------

@description('Public DNS FQDN of the runner VM')
output runnerFqdn string = pip.properties.dnsSettings.fqdn

@description('Public IP address of the runner VM')
output runnerPublicIp string = pip.properties.ipAddress
