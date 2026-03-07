#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Creates an Azure Cost Management budget and email alerts for MCP Factory.

.DESCRIPTION
    §6 requirement: "cloud resources must not exceed $150 per month".
    This script provisions a $150/month subscription-level budget with two
    alert thresholds (80 % = $120 forecast  and  100 % = $150 actual) that
    send email notifications to the project team.

.NOTES
    Pre-requisites:
      - Azure CLI logged in:  az login
      - Correct subscription selected:
          az account set --subscription abb10328-e7f1-4d4a-9067-c1967fd70429

    Run once:
      .\scripts\setup_budget_alert.ps1

    Re-running is safe (idempotent: deletes and recreates the budget).
#>

param(
    [string]$SubscriptionId = "abb10328-e7f1-4d4a-9067-c1967fd70429",
    [string]$ResourceGroup   = "mcp-factory-rg",
    [string]$BudgetName      = "mcp-factory-monthly-150",
    [decimal]$BudgetAmount   = 150,

    # Single email shortcut (overrides list if set)
    [string]$EmailAddress = "",

    # Full team list — edit as needed
    [string[]]$NotificationEmails = @(
        "evan69@usf.edu"
    )
)

# Merge -EmailAddress into the list if provided
if ($EmailAddress -ne "") {
    if ($NotificationEmails -notcontains $EmailAddress) {
        $NotificationEmails = @($EmailAddress) + $NotificationEmails
    }
}

# ── 0. Verify az CLI ──────────────────────────────────────────────────────
if (-not (Get-Command az -ErrorAction SilentlyContinue)) {
    Write-Error "Azure CLI not found. Install from https://aka.ms/installazurecliwindows"
}

az account set --subscription $SubscriptionId | Out-Null
Write-Host "`n[budget] Subscription: $SubscriptionId" -ForegroundColor Cyan

# ── 1. Budget scope ───────────────────────────────────────────────────────
# Scope to resource group so only MCP Factory costs count toward the limit.
$Scope = "/subscriptions/$SubscriptionId/resourceGroups/$ResourceGroup"

# ── 2. Budget time window — rolling 12-month window from today ───────────
$StartDate = (Get-Date -Day 1).ToString("yyyy-MM-dd")           # 1st of this month
$EndDate   = (Get-Date -Day 1).AddYears(2).ToString("yyyy-MM-dd") # 2 years out

Write-Host "[budget] Start: $StartDate   End: $EndDate" -ForegroundColor Cyan

# ── 3. Build contact-emails JSON array ────────────────────────────────────
$emailArray = ($NotificationEmails | ForEach-Object { "`"$_`"" }) -join ","

# ── 4. PUT budget via ARM REST (no preview extension needed) ───────────────
$ApiVersion = "2023-11-01"
$Uri = "https://management.azure.com/subscriptions/$SubscriptionId/providers/Microsoft.Consumption/budgets/${BudgetName}?api-version=$ApiVersion"

$body = @"
{
  "properties": {
    "category": "Cost",
    "amount": $BudgetAmount,
    "timeGrain": "Monthly",
    "timePeriod": { "startDate": "$StartDate", "endDate": "$EndDate" },
    "filter": {
      "dimensions": {
        "name": "ResourceGroupName",
        "operator": "In",
        "values": ["$ResourceGroup"]
      }
    },
    "notifications": {
      "actual_gte_80":  { "enabled": true, "operator": "GreaterThanOrEqualTo", "threshold": 80,  "thresholdType": "Actual",    "contactEmails": [$emailArray] },
      "actual_gte_100": { "enabled": true, "operator": "GreaterThanOrEqualTo", "threshold": 100, "thresholdType": "Actual",    "contactEmails": [$emailArray] },
      "forecast_gte_90":{ "enabled": true, "operator": "GreaterThanOrEqualTo", "threshold": 90,  "thresholdType": "Forecasted", "contactEmails": [$emailArray] }
    }
  }
}
"@

Write-Host "[budget] Creating/updating budget '$BudgetName' ($BudgetAmount USD/month)..." -ForegroundColor Cyan
$result = az rest --method PUT --uri $Uri --body $body --headers "Content-Type=application/json" 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "[budget] ERROR: $result" -ForegroundColor Red
    exit 1
}

Write-Host "`n[budget] Budget created successfully." -ForegroundColor Green
Write-Host "  Name:    $BudgetName"
Write-Host "  Scope:   $Scope"
Write-Host "  Limit:   `$$BudgetAmount / month"
Write-Host "  Alerts:  80 % actual (\$$([math]::Round($BudgetAmount*0.8,0))), 100 % actual, 90 % forecasted"
Write-Host "  Emails:  $($NotificationEmails -join ', ')"
Write-Host ""
Write-Host "Verify in the Azure portal:" -ForegroundColor White
Write-Host "  https://portal.azure.com/#view/Microsoft_Azure_CostManagement/BudgetList.ReactView" -ForegroundColor Blue
