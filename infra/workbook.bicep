/*
  infra/workbook.bicep
  Deploys an Azure Monitor Workbook that visualises MCP Factory telemetry
  from Application Insights.

  Tiles:
    1. Analyses this week        — count of discovery_complete events
    2. Avg invocables per job    — avg(toint(customDimensions["invocable_count"]))
    3. Tool call success rate    — % of chat_complete with tool_calls_total > 0
    4. Avg & P95 duration        — table of discovery / generate / chat latency
    5. Throughput timechart      — event volume over the selected time range

  Open in Azure Portal:
    portal.azure.com → mcp-factory-rg → mcpfactory-appinsights → Workbooks
*/

// ---------------------------------------------------------------------------
// Parameters
// ---------------------------------------------------------------------------

@description('Azure region (must match the App Insights region).')
param location string

@description('Resource ID of the Application Insights component that is the data source.')
param appInsightsId string

@description('Short resource prefix — used to name the workbook.')
param prefix string

// Workbook names must be GUIDs.
var workbookId = guid(appInsightsId, 'mcp-factory-ops-workbook')

// ---------------------------------------------------------------------------
// Workbook resource
// The serializedData property is the workbook definition as a JSON string.
// We load it from workbook-data.json at compile time via loadTextContent().
// ---------------------------------------------------------------------------

resource workbook 'microsoft.insights/workbooks@2022-04-01' = {
  name:     workbookId
  location: location
  kind:     'shared'
  properties: {
    displayName:    'MCP Factory Operations'
    serializedData: loadTextContent('workbook-data.json')
    sourceId:       appInsightsId
    category:       'workbook'
  }
}

// ---------------------------------------------------------------------------
// Outputs
// ---------------------------------------------------------------------------

@description('Azure Portal deep-link to the deployed Workbook')
output workbookUrl string = 'https://portal.azure.com/#resource${workbook.id}'
