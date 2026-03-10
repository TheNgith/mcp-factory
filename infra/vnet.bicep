/*
  infra/vnet.bicep
  Shared Virtual Network for MCP Factory.

  Subnets
  -------
  • aca-infra  10.0.0.0/23  — ACA managed-environment infrastructure.
                              Delegated to Microsoft.App/environments.
                              Min /23 required by ACA workload-profile envs.
  • vm         10.0.2.0/24  — Windows runner / GUI-bridge VM.
                              NSG allows RDP from anywhere + bridge (8090)
                              from the aca-infra subnet only; denies 8090
                              from the internet.
*/

param location string
param prefix   string

// ---------------------------------------------------------------------------
// NSG — ACA infrastructure subnet
// Minimal rules; ACA manages its own internal traffic.
// ---------------------------------------------------------------------------

resource acaNsg 'Microsoft.Network/networkSecurityGroups@2023-09-01' = {
  name:     '${prefix}-aca-nsg'
  location: location
  properties: {
    securityRules: [
      {
        name: 'AllowVnetInbound'
        properties: {
          priority:                 100
          direction:                'Inbound'
          access:                   'Allow'
          protocol:                 '*'
          sourceAddressPrefix:      'VirtualNetwork'
          sourcePortRange:          '*'
          destinationAddressPrefix: 'VirtualNetwork'
          destinationPortRange:     '*'
        }
      }
      {
        name: 'AllowAzureLoadBalancerInbound'
        properties: {
          priority:                 110
          direction:                'Inbound'
          access:                   'Allow'
          protocol:                 '*'
          sourceAddressPrefix:      'AzureLoadBalancer'
          sourcePortRange:          '*'
          destinationAddressPrefix: '*'
          destinationPortRange:     '*'
        }
      }
    ]
  }
}

// ---------------------------------------------------------------------------
// NSG — VM subnet
// ---------------------------------------------------------------------------

resource vmNsg 'Microsoft.Network/networkSecurityGroups@2023-09-01' = {
  name:     '${prefix}-vm-nsg'
  location: location
  properties: {
    securityRules: [
      {
        // RDP kept open so admins can still reach the VM.
        name: 'AllowRDP'
        properties: {
          priority:                 1000
          direction:                'Inbound'
          access:                   'Allow'
          protocol:                 'Tcp'
          sourceAddressPrefix:      '*'
          sourcePortRange:          '*'
          destinationAddressPrefix: '*'
          destinationPortRange:     '3389'
        }
      }
      {
        // Only ACA infrastructure subnet can call the GUI bridge.
        name: 'AllowGUIBridgeFromACA'
        properties: {
          priority:                 1100
          direction:                'Inbound'
          access:                   'Allow'
          protocol:                 'Tcp'
          sourceAddressPrefix:      '10.0.0.0/23'
          sourcePortRange:          '*'
          destinationAddressPrefix: '*'
          destinationPortRange:     '8090'
        }
      }
      {
        // Block every other internet source from reaching port 8090.
        name: 'DenyGUIBridgeFromInternet'
        properties: {
          priority:                 1200
          direction:                'Inbound'
          access:                   'Deny'
          protocol:                 'Tcp'
          sourceAddressPrefix:      '*'
          sourcePortRange:          '*'
          destinationAddressPrefix: '*'
          destinationPortRange:     '8090'
        }
      }
    ]
  }
}

// ---------------------------------------------------------------------------
// Virtual Network
// ---------------------------------------------------------------------------

resource vnet 'Microsoft.Network/virtualNetworks@2023-09-01' = {
  name:     '${prefix}-vnet'
  location: location
  properties: {
    addressSpace: { addressPrefixes: ['10.0.0.0/16'] }
    subnets: [
      {
        name: 'aca-infra'
        properties: {
          addressPrefix:        '10.0.0.0/23'
          networkSecurityGroup: { id: acaNsg.id }
          delegations: [
            {
              name:       'aca-delegation'
              properties: { serviceName: 'Microsoft.App/environments' }
            }
          ]
        }
      }
      {
        name: 'vm'
        properties: {
          addressPrefix:        '10.0.2.0/24'
          networkSecurityGroup: { id: vmNsg.id }
        }
      }
    ]
  }
}

// ---------------------------------------------------------------------------
// Outputs
// ---------------------------------------------------------------------------

output vnetId      string = vnet.id
output acaSubnetId string = vnet.properties.subnets[0].id
output vmSubnetId  string = vnet.properties.subnets[1].id
