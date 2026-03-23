# Azure Infrastructure Cost Analysis

Generated: 2026-03-23
Budget remaining: ~$15 of Visual Studio Enterprise Subscription
Goal: Squeeze 2-3 more days of testing from remaining budget

---

## Current Spend Breakdown (March 1-23)

| Service | Spent | Daily Rate | Notes |
|---------|-------|-----------|-------|
| **Virtual Machines** | $34.27 | ~$1.49/day | **Biggest single cost** — Standard_D2s_v3 runner VM |
| **Cognitive Services** | ~$24.77 | ~$1.08/day | Azure OpenAI (S0) — gpt-4o + embeddings |
| **Storage** | $23.26 | ~$1.01/day | Blob storage (uploads + artifacts containers) |
| **Container Apps** | $21.49 | ~$0.93/day | Pipeline API + UI app, both at **min-replicas 1** |
| **Load Balancer** | $7.34 | ~$0.32/day | Platform-managed LB from VNet-integrated ACA env |
| **Other** | ~$12 | ~$0.52/day | Key Vault, ACR, Log Analytics, App Insights, NSG |
| **TOTAL** | $123.26 | **~$5.36/day** | At this rate: **$15 lasts 2.8 days** |

### Resource Groups
- `mcp-factory-rg`: $8.81 (the actual project)
- `capstone-cell-produc...`: $3.64 (old capstone — still running?)
- Other small groups: <$1 combined

---

## Problem: Three Things Running 24/7 That Don't Need To

### 1. Windows VM (Standard_D2s_v3) — $1.49/day

The runner VM (`mcpfactory-runner-vm`) runs Windows Server 2022 with:
- 2 vCPUs, 8 GB RAM
- 128 GB Premium SSD
- Public IP (Standard, static)

**It only needs to be running when the GUI bridge is actively executing DLL calls**
(i.e., during explore phases). Between tests, it should be deallocated.

**Action**: Deallocate when not testing.
```powershell
# Stop (deallocate — stops compute billing, keeps disk)
az vm deallocate --name mcpfactory-runner-vm --resource-group mcp-factory-rg

# Start when ready to test
az vm start --name mcpfactory-runner-vm --resource-group mcp-factory-rg
```
**Savings**: ~$1.39/day (disk still costs ~$0.10/day)

### 2. Container Apps at min-replicas 1 — $0.50-0.80/day

Both ACA apps are deployed with `--min-replicas 1` in **every** CI/CD workflow:
- `deploy-pipeline.yml` line 86: `--min-replicas 1`
- `deploy-ui.yml` line 86/105: `--min-replicas 1`
- `ci-cd.yml` lines 253/266: `--min-replicas 1`

This means both containers run 24/7 even when nobody is using the API.

**Action**: Scale to 0 when not testing.
```powershell
# Scale down (stops billing when idle)
az containerapp update --name mcp-factory-pipeline --resource-group mcp-factory-rg --min-replicas 0
az containerapp update --name mcp-factory-ui --resource-group mcp-factory-rg --min-replicas 0

# They auto-scale back up on first request (cold start ~30-60s)
```
**Savings**: ~$0.50-0.80/day (ACA consumption billing is per-second when running)

### 3. Old Capstone Resources — $3.64 so far this month

`capstone-cell-produc...` resource group is from the completed capstone.
If those resources are no longer needed, deleting the entire resource group
frees up that daily spend.

**Action**: Check what's in that RG. If it's the old project:
```powershell
az group delete --name <capstone-cell-production-rg-name> --yes
```
**Savings**: ~$0.16/day (small, but adds up)

---

## Projected Savings

| Scenario | Daily Burn | Days from $15 |
|----------|-----------|---------------|
| **Do nothing** | $5.36/day | **2.8 days** (runs out ~Mar 26) |
| **Deallocate VM** | $3.97/day | **3.8 days** (runs out ~Mar 27) |
| **VM + ACA min=0** | $3.17-3.47/day | **4.3-4.7 days** (runs out ~Mar 28) |
| **VM + ACA + delete old RG** | $3.01-3.31/day | **4.5-5.0 days** (runs out ~Mar 28-29) |

---

## Immediate Actions (do these NOW)

### Step 1: Deallocate the VM (saves the most)
```powershell
az vm deallocate --name mcpfactory-runner-vm --resource-group mcp-factory-rg
```

### Step 2: Scale ACA to 0 min replicas
```powershell
az containerapp update --name mcp-factory-pipeline --resource-group mcp-factory-rg --min-replicas 0
az containerapp update --name mcp-factory-ui --resource-group mcp-factory-rg --min-replicas 0
```

### Step 3: Update CI/CD workflows to not force min-replicas 1

Change all `--min-replicas 1` to `--min-replicas 0` in:
- `.github/workflows/deploy-pipeline.yml` (line 86)
- `.github/workflows/deploy-ui.yml` (lines 86, 105)
- `.github/workflows/ci-cd.yml` (lines 253, 266)

This prevents future deployments from re-enabling always-on.

### Step 4: Audit the old capstone resource group
```powershell
az resource list --resource-group <capstone-cell-production-rg> --output table
```
Delete if no longer needed.

---

## Testing Workflow with Cost Controls

When you want to run tests:
1. `az vm start --name mcpfactory-runner-vm --resource-group mcp-factory-rg`
2. Wait ~2 min for VM boot
3. Run your pipeline tests (ACA auto-scales from 0 on first request)
4. When done: `az vm deallocate --name mcpfactory-runner-vm --resource-group mcp-factory-rg`

The ACA apps can stay at min-replicas 0 permanently — they cold-start in
30-60 seconds on first request, which is fine for testing.

---

## What Can't Be Reduced

- **Azure OpenAI (S0)**: Base account cost exists. Token usage from overnight
  runs may have spiked this. Consider deleting the deployment when not testing,
  but re-creating takes time.
- **Storage**: Already Standard_LRS. Could purge old job blobs to reduce
  transaction costs slightly, but storage itself is cheap.
- **Load Balancer**: Platform-managed by ACA VNet integration. Can only be
  eliminated by removing VNet integration (not recommended).
- **ACR Basic**: ~$5/month fixed. Can't go lower without losing the registry.
- **Key Vault Standard**: Minimal per-operation cost. Negligible.

---

## Full Resource Inventory (mcp-factory-rg)

| Resource | Type | SKU | Daily Cost Est. | Essential? |
|----------|------|-----|-----------------|------------|
| mcp-factory-pipeline | Container App | 1 vCPU / 2 Gi, 0-5 replicas | $0.40-0.60 | Yes |
| mcp-factory-ui | Container App | 0.5 vCPU / 1 Gi, 0-3 replicas | $0.15-0.25 | Yes (for demos) |
| mcp-factory-env | ACA Environment | Consumption + VNet | $0.30 (LB) | Yes |
| mcpfactoryacr | Container Registry | Basic | $0.17 | Yes |
| mcpfactorystore | Storage Account | Standard_LRS | $1.01 | Yes |
| mcp-factory-kv | Key Vault | Standard | <$0.05 | Yes |
| mcp-factory-openai | Cognitive Services | S0 | $1.08 | Yes |
| mcp-factory-logs | Log Analytics | PerGB2018 | $0.10-0.30 | Optional |
| mcp-factory-insights | App Insights | Workspace | (included above) | Optional |
| mcpfactory-runner-vm | Virtual Machine | Standard_D2s_v3 | **$1.49** | **Only during testing** |
| mcpfactory-runner-pip | Public IP | Standard | $0.10 | With VM only |
| mcpfactory-search | AI Search | Free | $0.00 | Yes |
| VNet + NSGs | Networking | Standard | $0.00 | Yes |
