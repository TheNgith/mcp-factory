# Sessions

This folder contains the automated CI pipeline, test runner, scorer, and per-run output snapshots.

---

## Pipeline overview

The correct execution order â€” matching what the web UI does:

```
1. Upload DLL + Generate     generate a raw MCP schema from export names
2. Discover (explore)        probe the DLL; build initial vocab.json
3. Submit answers             inject domain knowledge into vocab.json
3.5 Re-Discover               probe again with enriched vocab â†’ working_call findings
4. Run tests                 28 headless prompts against /api/chat
5. Score                     deterministic regex rubric â†’ TEST_RESULTS.md
6. Save session snapshot     archive to sessions/_runs/{JobId}/
7. Compare                   regression summary across last N sessions
```

Step 3.5 is the critical one most often missed in one-shot pipelines: the model needs to probe the DLL **after** domain answers are in vocab.json so it produces correct `working_call` findings that the test-phase model can rely on.

---

## How to save a session

Run this from the repo root after each Discover / Refine / Chat cycle.  
**`-Note` is optional** Ã¢â‚¬â€ if you omit it, the script auto-derives `{component}-run{N}`:

```powershell
# Minimal Ã¢â‚¬â€ note is auto-derived (e.g. "contoso-cs-run1"):
.\scripts\save-session.ps1 -ApiUrl "https://mcp-factory-ui.icycoast-8ddfa278.eastus.azurecontainerapps.io" -JobId "YOUR_JOB_ID"

# With a custom note:
.\scripts\save-session.ps1 -ApiUrl "https://mcp-factory-ui.icycoast-8ddfa278.eastus.azurecontainerapps.io" -JobId "YOUR_JOB_ID" -Note "payment-test"
```

Then commit it:
```powershell
git add sessions/ ; git commit -m "session: YOUR_JOB_ID short-description" ; git push
```

---

## Parameters

| Parameter | Required | What to put | Example |
|---|---|---|---|
| `-ApiUrl` | yes | The UI URL (no trailing slash) | `https://mcp-factory-ui.icycoast-8ddfa278.eastus.azurecontainerapps.io` |
| `-JobId` | yes | The job ID shown in the UI after uploading a DLL | `a0fc70e8` |
| `-Note` | no | What you tested Ã¢â‚¬â€ auto-derived as `{component}-run{N}` if omitted | `payment-flow`, `unlock-fix` |

The job ID is visible in the browser URL bar on Step 3, or in the status panel after analysis completes.

---

## What each session folder contains

```
YYYY-MM-DD-{commit}-{note}/
Ã¢â€â€š
Ã¢â€Å“Ã¢â€â‚¬Ã¢â€â‚¬ SUMMARY.md                   Ã¢â€ Â AI quick-scan: commit, findings counts, working calls, gap questions
Ã¢â€Å“Ã¢â€â‚¬Ã¢â€â‚¬ code-changes.md              Ã¢â€ Â full git log + git diff of api/ ui/ scripts/ at snapshot time
Ã¢â€Å“Ã¢â€â‚¬Ã¢â€â‚¬ session-meta.json            Ã¢â€ Â job_id, component, commit, finding/gap counts, timestamps
Ã¢â€Å“Ã¢â€â‚¬Ã¢â€â‚¬ hints.txt                    Ã¢â€ Â verbatim hints + use_cases you provided
Ã¢â€Å“Ã¢â€â‚¬Ã¢â€â‚¬ clarification-questions.md   Ã¢â€ Â gap questions, technical detail, and submitted answers
Ã¢â€Å“Ã¢â€â‚¬Ã¢â€â‚¬ chat-transcript.md           Ã¢â€ Â fill this in after testing (auto-created as template)
Ã¢â€â€š
Ã¢â€Å“Ã¢â€â‚¬Ã¢â€â‚¬ schema/
Ã¢â€â€š   Ã¢â€Å“Ã¢â€â‚¬Ã¢â€â‚¬ 01-pre-enrichment.json   Ã¢â€ Â schema after Generate, BEFORE Discover runs
Ã¢â€â€š   Ã¢â€â€Ã¢â€â‚¬Ã¢â€â‚¬ 02-post-enrichment.json  Ã¢â€ Â schema after Discover + any Refine passes
Ã¢â€â€š
Ã¢â€â€Ã¢â€â‚¬Ã¢â€â‚¬ artifacts/
    Ã¢â€Å“Ã¢â€â‚¬Ã¢â€â‚¬ findings.json            Ã¢â€ Â every LLM-recorded finding (working calls, failures)
    Ã¢â€Å“Ã¢â€â‚¬Ã¢â€â‚¬ vocab.json               Ã¢â€ Â accumulated vocabulary (IDs, value semantics, gap answers)
    Ã¢â€Å“Ã¢â€â‚¬Ã¢â€â‚¬ api_reference.md         Ã¢â€ Â synthesized API reference document
    Ã¢â€Å“Ã¢â€â‚¬Ã¢â€â‚¬ behavioral_spec.py       Ã¢â€ Â typed Python stub with full docstrings
    Ã¢â€â€Ã¢â€â‚¬Ã¢â€â‚¬ invocables_map.json      Ã¢â€ Â complete enriched invocable definitions
```

**For an AI starting a new session:** read `SUMMARY.md` first (one-file overview), then `code-changes.md` to understand what changed in code, then `chat-transcript.md` for what was actually tested.

**Machine-readable index:** `sessions/index.json` Ã¢â‚¬â€ array of all sessions with finding counts, known IDs, gap counts, and folder paths. Scan this to find which session to look at without opening every folder.

---

## Comparing sessions

```powershell
# See what the schema learned between two runs:
git diff sessions/2026-03-17-.../schema/02-post-enrichment.json sessions/2026-03-18-.../schema/02-post-enrichment.json

# See what Discover added vs the raw Generate output within one run:
git diff --no-index sessions/2026-03-17-.../schema/01-pre-enrichment.json sessions/2026-03-17-.../schema/02-post-enrichment.json

# See how vocab grew over two sessions:
git diff sessions/2026-03-17-.../artifacts/vocab.json sessions/2026-03-18-.../artifacts/vocab.json
```

---

## Script reference

### `ci-run.ps1` â€” full pipeline (upload â†’ generate â†’ discover â†’ answers â†’ re-discover â†’ test â†’ score)

```powershell
.\sessions\ci-run.ps1 `
    -ApiUrl    "https://mcp-factory-pipeline.icycoast-8ddfa278.eastus.azurecontainerapps.io" `
    -ApiKey    "YOUR_KEY" `
    -DllPath   "C:\path\to\contoso_cs.dll" `
    -Hints     "Loyalty/rewards system. Customer IDs: CUST-NNN." `
    -UseCases  "Check balance, redeem points, process payment"
```

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `-ApiUrl` | yes | â€” | Pipeline API base URL |
| `-ApiKey` | no | env `PIPELINE_API_KEY` | `X-Pipeline-Key` header |
| `-DllPath` | yes* | env `MCP_FACTORY_DLL_PATH` | Path to the DLL to analyze |
| `-JobId` | no | â€” | Reuse an existing job (implies `-SkipUpload`) |
| `-Hints` | no | â€” | Free-text domain hints injected at generate time |
| `-UseCases` | no | â€” | Use cases injected at generate time |
| `-AnswersJson` | no | `sessions/contoso_cs/ANSWERS.json` | Path to pre-written gap answers |
| `-PollIntervalSec` | no | 10 | How often to poll job status |
| `-TimeoutMin` | no | 30 | Max minutes to wait for generate or discover |
| `-SkipUpload` | no | false | Skip upload+generate; requires `-JobId` |
| `-SkipDiscover` | no | false | Skip both discover passes (including re-discover) |
| `-SkipTests` | no | false | Skip test execution (score only) |
| `-SkipSave` | no | false | Skip save-session snapshot |
| `-FailOnRegress` | no | false | Exit 1 if score is lower than previous best |

---

### `run-tests.ps1` â€” headless test runner (run 28 prompts, write transcript)

```powershell
.\sessions\run-tests.ps1 -ApiUrl "..." -JobId "abc12345"
.\sessions\run-tests.ps1 -ApiUrl "..." -JobId "abc12345" -Tests T05,T14,T21,T23   # specific tests only
.\sessions\run-tests.ps1 -ApiUrl "..." -JobId "abc12345" -ScoreAfter              # auto-score when done
```

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `-ApiUrl` | yes | â€” | Pipeline API base URL |
| `-JobId` | yes | â€” | Job to run prompts against |
| `-ApiKey` | no | â€” | `X-Pipeline-Key` header |
| `-OutDir` | no | `_runs/{JobId}` | Where to write transcript + results |
| `-Tests` | no | all 28 | Comma-separated test IDs to run, e.g. `T05,T14` |
| `-TimeoutSec` | no | 180 | Per-prompt API timeout |
| `-Concurrency` | no | 3 | Parallel runspaces (reduce if hitting 429s) |
| `-ScoreAfter` | no | false | Run `score-session.ps1` automatically when done |

---

### `score-session.ps1` â€” score a transcript against the rubric

```powershell
.\sessions\score-session.ps1 -SessionDir "sessions\_runs\abc12345"
```

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `-SessionDir` | yes | â€” | Folder containing `chat_transcript.txt` |
| `-ShowAll` | no | false | Print all test results including PASSes |

---

### `compare.ps1` â€” cross-session regression table

```powershell
.\sessions\compare.ps1
.\sessions\compare.ps1 -Count 5
```

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `-Count` | no | 10 | How many recent sessions to compare |
| `-Sessions` | no | â€” | Explicit list of session folder names |
| `-FailOnRegression` | no | false | Exit 1 if any function regressed |

---

### `watch-and-run.ps1` â€” local CI watcher (auto-run on every git push)

```powershell
.\sessions\watch-and-run.ps1 -ApiUrl "..." -DllPath "C:\path\to\contoso_cs.dll"
```

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `-ApiUrl` | yes | â€” | Pipeline API base URL |
| `-DllPath` | yes | â€” | Path to the DLL |
| `-ApiKey` | no | env `PIPELINE_API_KEY` | Auth key |
| `-PollSec` | no | 30 | How often to check for new commits |
| `-RunOnce` | no | false | Run immediately then exit |

---

### `scripts/save-session.ps1` â€” snapshot a job into sessions/

```powershell
.\scripts\save-session.ps1 -ApiUrl "..." -JobId "abc12345"
.\scripts\save-session.ps1 -ApiUrl "..." -JobId "abc12345" -Note "payment-fix"
```

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `-ApiUrl` | yes | â€” | Pipeline API base URL |
| `-JobId` | yes | â€” | Job to snapshot |
| `-ApiKey` | no | env `MCP_FACTORY_API_KEY` | Auth key |
| `-Note` | no | auto (`{component}-run{N}`) | Short label for the session folder |
| `-TranscriptPath` | no | auto-searched in Downloads | Path to chat transcript file |

---

## Session index

| Date | Commit | Job ID | Note | Score |
|---|---|---|---|---|
| 2026-03-18 | unknown | `f4cad83c` | baseline (no bridge, no hints) | 23/28 (82%) |
| 2026-03-18 | unknown | `0c5a7b47` | schema-less + SkipDiscover | 12/28 (43%) |
| 2026-03-19 | `558ceb0` | `2026-03-18-8c16d04-post-sentinel-fix-run1` | unknown-run2 | unknown - (fill in after testing) |
| 2026-03-19 | `d8ecdfd` | `2026-03-18-8c16d04-post-sentinel-fix-run1` | unknown-run3 | unknown - (fill in after testing) |
| 2026-03-19 | `3c7338c` | `2026-03-18-8c16d04-post-sentinel-fix-run1` | unknown-run3 | unknown - (fill in after testing) |
| 2026-03-19 | `d564334` | `206a77f8` | contoso-baseline | contoso_cs - (fill in after testing) |
| 2026-03-19 | `d564334` | `206a77f8` | contoso-baseline | contoso_cs - (fill in after testing) |
| 2026-03-19 | `d564334` | `206a77f8` | contoso-baseline | contoso_cs - (fill in after testing) |
| 2026-03-19 | `32ccf0e` | `cfd26cf5` | manual-insert | contoso_cs - (fill in after testing) |

