# Sessions

Each subfolder is a point-in-time snapshot of a discovery job, named:
```
YYYY-MM-DD-{commit}-{slug}/
```

---

## How to save a session

Run this from the repo root after each Discover / Refine / Chat cycle:

```powershell
.\scripts\save-session.ps1 -ApiUrl "https://mcp-factory-ui.icycoast-8ddfa278.eastus.azurecontainerapps.io" -JobId "YOUR_JOB_ID" -Note "short-description"
```

Then commit it:
```powershell
git add sessions/ ; git commit -m "session: YOUR_JOB_ID short-description" ; git push
```

---

## Parameters

| Parameter | What to put | Example |
|---|---|---|
| `-ApiUrl` | The UI URL (no trailing slash) | `https://mcp-factory-ui.icycoast-8ddfa278.eastus.azurecontainerapps.io` |
| `-JobId` | The job ID shown in the UI after uploading a DLL | `a0fc70e8` |
| `-Note` | One short kebab-case description of what you tested this run | `payment-flow`, `unlock-fix`, `initial-discover` |

The job ID is visible in the browser URL bar on Step 3, or in the status panel after analysis completes.

---

## What each session folder contains

```
YYYY-MM-DD-{commit}-{note}/
│
├── session-meta.json            ← job_id, component, commit, finding/gap counts, timestamps
├── hints.txt                    ← verbatim hints + use_cases you provided
├── clarification-questions.md   ← gap questions, technical detail, and submitted answers
├── chat-transcript.md           ← fill this in after testing (auto-created as template)
│
├── schema/
│   ├── 01-pre-enrichment.json   ← schema after Generate, BEFORE Discover runs
│   └── 02-post-enrichment.json  ← schema after Discover + any Refine passes
│
└── artifacts/
    ├── findings.json            ← every LLM-recorded finding (working calls, failures)
    ├── vocab.json               ← accumulated vocabulary (IDs, value semantics, gap answers)
    ├── api_reference.md         ← synthesized API reference document
    ├── behavioral_spec.py       ← typed Python stub with full docstrings
    └── invocables_map.json      ← complete enriched invocable definitions
```

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
| 2026-03-17 | `0b11f66` | `a0fc70e8` | gap-answer-boxes | CS_LookupCustomer working (CUST-001, param_3=0). CS_ProcessPayment write-denied — CS_Initialize not called first. CS_UnlockAccount null-argument on LOCKED status when account is already ACTIVE. |
