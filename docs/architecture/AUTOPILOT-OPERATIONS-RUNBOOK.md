# Autopilot Operations Runbook

Date: 2026-03-21
Audience: Operators running unattended A/B isolation loops

## Current Reality

- Background loops are currently active.
- Multiple loop processes can run at once if started more than once.
- Each loop writes to logs/autopilot-transition.log, so logs can interleave.

## What Is Running Now

Use this command to list active loop processes:

powershell
Get-CimInstance Win32_Process |
  Where-Object { $_.Name -match 'powershell' -and $_.CommandLine -match 'autopilot_transition_loop.ps1' } |
  Select-Object ProcessId, CommandLine

## Start Modes

### 1) Single smoke run (one cycle only)

Use when validating config quickly.

powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/autopilot_transition_loop.ps1 \
  -ApiUrl "https://mcp-factory-pipeline.icycoast-8ddfa278.eastus.azurecontainerapps.io" \
  -ApiKey "<PIPELINE_API_KEY>" \
  -PollSeconds 60 \
  -MaxCases 1 \
  -Model "gpt-4o-mini" \
  -Once

### 2) Continuous conservative overnight

Use when minimizing spend and reducing noise.

powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/autopilot_transition_loop.ps1 \
  -ApiUrl "https://mcp-factory-pipeline.icycoast-8ddfa278.eastus.azurecontainerapps.io" \
  -ApiKey "<PIPELINE_API_KEY>" \
  -PollSeconds 300 \
  -MaxCases 2 \
  -Model "gpt-4o-mini"

### 3) Continuous full matrix overnight

Use when you want all 4 isolation cases per cycle.

powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/autopilot_transition_loop.ps1 \
  -ApiUrl "https://mcp-factory-pipeline.icycoast-8ddfa278.eastus.azurecontainerapps.io" \
  -ApiKey "<PIPELINE_API_KEY>" \
  -PollSeconds 300 \
  -MaxCases 4 \
  -Model "gpt-4o-mini"

## Isolation Matrix Options

scripts/run_transition_isolation_matrix.py options:

- --max-cases N: run first N predefined cases.
- --parallel / --no-parallel: run cases concurrently or sequentially.
- --max-workers N: max parallel A/B pairs.
- --model NAME: default is gpt-4o-mini.

Recommended defaults:

- Cost-controlled: --max-cases 2 --max-workers 2
- Throughput: --max-cases 4 --max-workers 4

## Monitoring

### Tail log

powershell
Get-Content logs/autopilot-transition.log -Tail 100

### Watch latest run folders

powershell
Get-ChildItem sessions/_runs -Directory |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 10 Name, LastWriteTime

### Check latest matrix summary

powershell
$latest = Get-ChildItem sessions/_runs -Directory -Filter "*-isolation-matrix-*" |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1
Get-Content (Join-Path $latest.FullName "transition-isolation-matrix.json")

## Stop / Cleanup

### Stop all autopilot loops

powershell
Get-CimInstance Win32_Process |
  Where-Object { $_.Name -match 'powershell' -and $_.CommandLine -match 'autopilot_transition_loop.ps1' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }

### Stop only one loop by PID

powershell
Stop-Process -Id <PID> -Force

## Avoid Duplicate Loops

Before starting a new loop, check if one is already running. If yes, stop old ones first.

Operational rule:
- Exactly one continuous loop should run per environment.

## VM / GUI Bridge Note

Current runs rely on backend=bridge entries in probe logs.
Do not deallocate the Windows VM while autopilot is running, or bridge calls will fail.

## Legacy Docs: Keep, Migrate, or Delete

Reviewed docs:
- docs/PIPELINE-COHESION.md
- docs/PIPELINE-DIAGNOSTIC-CHECKLIST.md

Disposition recommendation:

1. Keep (archive as historical root-cause analysis): docs/PIPELINE-COHESION.md
- Still valuable for deep bug history (D-1..D-11, Q/C layers).
- Not ideal as day-to-day operator runbook.

2. Keep and partially migrate: docs/PIPELINE-DIAGNOSTIC-CHECKLIST.md
- Useful checklist logic still valid.
- PowerShell snippets should be modernized for current artifact names and phase gates.

3. Add modern architecture docs (this file + phase-gate checklist) and mark old docs as legacy.

## Suggested Next Doc Move

Create docs/architecture/PHASE-GATE-CHECKLIST.md with current gates:
- discovery-satisfaction.json pass
- transition-readiness.json pass
- determinism threshold met
- unresolved function trend improving

This should replace ad hoc interpretation of old checklist thresholds.
