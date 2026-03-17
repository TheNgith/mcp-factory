# Sessions

Each subfolder is a point-in-time snapshot of a discovery job, named:
```
YYYY-MM-DD-{commit}-{slug}/
```

---

## How to save a session

Run this from the repo root after each Discover / Refine / Chat cycle.  
**`-Note` is optional** â€” if you omit it, the script auto-derives `{component}-run{N}`:

```powershell
# Minimal â€” note is auto-derived (e.g. "contoso-cs-run1"):
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
| `-Note` | no | What you tested â€” auto-derived as `{component}-run{N}` if omitted | `payment-flow`, `unlock-fix` |

The job ID is visible in the browser URL bar on Step 3, or in the status panel after analysis completes.

---

## What each session folder contains

```
YYYY-MM-DD-{commit}-{note}/
â”‚
â”œâ”€â”€ SUMMARY.md                   â† AI quick-scan: commit, findings counts, working calls, gap questions
â”œâ”€â”€ code-changes.md              â† full git log + git diff of api/ ui/ scripts/ at snapshot time
â”œâ”€â”€ session-meta.json            â† job_id, component, commit, finding/gap counts, timestamps
â”œâ”€â”€ hints.txt                    â† verbatim hints + use_cases you provided
â”œâ”€â”€ clarification-questions.md   â† gap questions, technical detail, and submitted answers
â”œâ”€â”€ chat-transcript.md           â† fill this in after testing (auto-created as template)
â”‚
â”œâ”€â”€ schema/
â”‚   â”œâ”€â”€ 01-pre-enrichment.json   â† schema after Generate, BEFORE Discover runs
â”‚   â””â”€â”€ 02-post-enrichment.json  â† schema after Discover + any Refine passes
â”‚
â””â”€â”€ artifacts/
    â”œâ”€â”€ findings.json            â† every LLM-recorded finding (working calls, failures)
    â”œâ”€â”€ vocab.json               â† accumulated vocabulary (IDs, value semantics, gap answers)
    â”œâ”€â”€ api_reference.md         â† synthesized API reference document
    â”œâ”€â”€ behavioral_spec.py       â† typed Python stub with full docstrings
    â””â”€â”€ invocables_map.json      â† complete enriched invocable definitions
```

**For an AI starting a new session:** read `SUMMARY.md` first (one-file overview), then `code-changes.md` to understand what changed in code, then `chat-transcript.md` for what was actually tested.

**Machine-readable index:** `sessions/index.json` â€” array of all sessions with finding counts, known IDs, gap counts, and folder paths. Scan this to find which session to look at without opening every folder.

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

## Session index

| Date | Commit | Job ID | Note | Key findings |
|---|---|---|---|---|
| 2026-03-17 | `0b11f66` | `a0fc70e8` | gap-answer-boxes | CS_LookupCustomer working (CUST-001, param_3=0). CS_ProcessPayment write-denied â€” CS_Initialize not called first. CS_UnlockAccount null-argument on LOCKED status when account is already ACTIVE. |
| 2026-03-17 | `b8e0744` | `a0fc70e8` | unknown-run2 | unknown - (fill in after testing) |
| 2026-03-17 | `b8e0744` | `a0fc70e8` | unknown-run2 | unknown - (fill in after testing) |

