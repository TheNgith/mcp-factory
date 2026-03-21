# save-session.ps1 — Diagnostic Capabilities Reference

_Last updated: 2026-03-19 (rev 2 — all ZIP artifacts now parsed)_

This document maps every diagnostic step in `scripts/save-session.ps1`, what
information each step provides, the current gaps/limitations, and concrete
improvement ideas.

---

## What the script does (end-to-end)

1. Downloads the session snapshot ZIP from `/api/jobs/{JobId}/session-snapshot`
2. Extracts it into a dated folder under `sessions/`
3. Parses every artifact and computes derived metrics
4. Writes several new files into the session folder
5. Appends a row to `sessions/index.json` and `sessions/README.md`
6. Runs `compare.ps1` to refresh `DASHBOARD.md`

---

## Diagnostic steps and what they produce

### Step 4-A — `delta.md`
Compares the new session against the most-recent previous session folder.

**Provides:**
- Which functions moved to `success` this run (newly working)
- Which functions regressed from `success` to non-success
- Count of unchanged successes
- `id_formats` change between sessions
- `error_codes` added or removed from `vocab.json`

**Gaps / limitations:**
- Only compares against the immediately previous folder sorted by name, not
  against a specific baseline commit — so a re-run that creates a numbered
  suffix (`-2`, `-3`) may compare against the wrong session
- Vocab delta only checks `id_formats` and `error_codes`; `value_semantics`,
  `notes`, and `param_names` changes are not shown
- No probe-count delta (did we probe more functions than last time?)

---

### Step 8 — `session-meta.json` enrichment
Stamps `commit`, `commit_msg`, `note`, and `saved_at` back into the
server-generated `session-meta.json`.

**Provides:** Ground-truth link between a session folder and the exact code
commit that produced it.

---

### Step 9 — `code-changes.md`
Captures the full `git diff HEAD~1 HEAD` for `api/`, `ui/`, and `scripts/`.

**Provides:** Exact line-by-line diff that was deployed when this job ran.

**Gaps:** Diff is always HEAD~1..HEAD regardless of how many commits were
deployed. If the API was deployed two commits ago, the relevant diff is wrong.
Could be improved by recording `git log --oneline HEAD~5..HEAD` for context.

---

### Step 10 — Parse `findings.json`
Counts findings by status, builds the `function_status` map, extracts working
calls.

**Provides:**
- `finding_counts`: `{success, partial, failed, total}`
- `function_status`: per-function latest status
- Working calls listed for all `success` findings

---

### Step 11 — Parse `vocab.json` + vocab completeness
Cross-references `invocables_map.json` to compute what fraction of parameters
have semantic (non-`param_N`) names.

**Provides:**
- `knownIds`: `id_formats` as a comma string
- `vocabCompleteness`: ratio of semantically-named params to total params
  (0.0–1.0)

**Gaps:** Completeness only measures _names_, not _descriptions_. A param
could have a human name but a useless description (e.g. still says
"Ghidra-generated").

---

### Step 11.5 — `vocab_coverage.json`
Checks which `vocab.json` error codes and ID formats actually appear verbatim
in `model_context.txt`.

**Provides:**
- `error_codes_total` / `error_codes_injected` (how many codes the LLM saw)
- `error_codes_missing`: codes that were in vocab but never injected
- `id_formats_injected`: boolean — did at least the first ID pattern appear?
- `value_semantics_keys`: list of keys in that section
- `coverage_score`: `error_codes_injected / error_codes_total`

**Gaps:**
- `value_semantics` keys are listed but not checked for injection — only
  error_codes are scored
- A `coverage_score` of 1.0 is vacuously true when `error_codes` is empty
  (fixed by vocab error_code update, but older runs still show this)
- No check that ID format _examples_ appear (only the first entry checked)

---

### Step 12 — Parse `clarification-questions.md`
Counts `##` headings in the gap document.

**Provides:** `gapCount` — number of open confidence gaps the pipeline
identified.

---

### Step 12.9 — Parse `sentinel_calibration.json`
Phase 0.5 output: the hex sentinel codes discovered during calibration and
the plain-English meaning the LLM assigned to each.

**Provides:**
- `count`: how many sentinel codes were named
- `meanings`: the full code → meaning map (e.g. `0xFFFFFFFB → "write operation denied"`)
- Printed to console: first 3 code=meaning pairs
- Written into `sentinel_calibration` in `index.json`

---

### Step 12.95 — Parse `gap_resolution_log.json`
Per-gap outcome from the mini-session gap-answer pass.

**Provides:**
- `total`: number of gaps mini-sessions attempted to resolve
- `resolved` / `unresolved` counts
- Written into `gap_resolution` in `index.json`

**Gaps / limitations:**
- The `resolved` field depends on the pipeline writing a boolean `resolved` key
  per entry. If mini-sessions crash before writing, the file may be absent or
  partially written (silent omit in that case).

---

### Step 12.5 — Parse `static_analysis.json` → `static_verification.json`
Cross-references binary-derived data (sentinel constants, string IDs, IAT
capability categories) against runtime findings.

**Provides:**
- Sentinel count / misses (codes harvested by static analysis but absent from
  `vocab.json`)
- ID count / misses (IDs found in binary strings but never used in any finding)
- IAT capability categories (e.g. `file_io`, `crypto`, `networking`)
- `capability_contradictions`: e.g. no network imports but findings mention HTTP
- Verdict: `PASS` / `WARN` / `FAIL`
- Written as `static_verification.json`

**Gaps:**
- Only checks networking for capability contradictions; other categories
  (crypto, registry, process_injection) are imported but not cross-checked
- Source field on sentinel constants is recorded but not printed in the
  summary line

---

### Step 12.6 — Parse `executor_trace.json`
Parses the per-call trace from chat sessions and mini-sessions.

**Provides:**
- Total calls, ok count, failed count, error rate
- `exception_types` breakdown (e.g. `OSError: 5` × N)
- `function_failures`: list of `fn_name (exception_class)` for failed calls

**Gaps:**
- Does **not** cover discovery-phase probes — those go to `explore_probe_log.json`
  (step 12.65). This means executor_trace alone under-reports total executor activity.
- No per-function probe-attempt count; you can see a function failed but not
  how many times it was tried

---

### Step 12.65 — Parse `explore_probe_log.json`
Parses the discovery-phase probe log written by `_explore_worker`,
`_calibrate_sentinels`, working-call verify, and cross-validation.

**Provides:**
- `total_probes`: total executor calls made during discovery
- `functions_probed`: count of distinct functions that were probed
- `by_phase`: breakdown across `calibrate_sentinels`, `explore`, `verify`,
  `cross_validate`
- Written into `probe_summary` in `index.json`

**Gaps:**
- The script only aggregates counts — it does not check whether any specific
  function was probed **zero** times (functions in `invocables_map.json` that
  appear in no probe entry are likely cold-skipped or errored before probing)
- Probes-per-function distribution not computed (min/max/avg not visible in
  index.json; requires manual inspection of the raw file)
- No check for repeated identical probes (same args tried N times → suggests
  the LLM got stuck in a loop)

---

### Step 12.7 — Parse `diagnosis_raw.json`
Parses per-chat-turn tool call summaries.

**Provides:**
- Tools called per turn with timestamps
- Sentinel hits and DLL error counts per turn
- `DIAGNOSIS.json`: verdict (`clean` / `partial` / `blocked`), tool call
  category breakdown
- Auto-populates `TEST_RESULTS.md` with a turn-by-turn tool call table

---

### Step 14 — `chat_transcript.txt` resolution
Copies the chat transcript into the session folder from the ZIP (preferred),
then falls back to Downloads search.

**Provides:** Human-readable full exchange including `[REASONING]` blocks.

**Gaps:** If the transcript was not generated server-side (old runs before
`5e91ba3`) and is not in Downloads, it is silently absent. The script prints a
warning but `index.json` has no `transcript_present: true/false` field making
cross-session queries harder.

---

### Step 14.5 — Transcript metrics
Counts tool call lines, sentinel hits, and DLL errors in
`chat_transcript.txt`.

**Provides:** `transcriptMetrics`: `{tool_calls, sentinel_hits, dll_errors}`

---

### Step 14.9 — `static_verification.json`
Written from the data parsed in step 12.5.

---

### Step 15 — `TEST_RESULTS.md`
Creates a pre-populated test results table from `sessions/contoso_cs/TEST_SUITE.md`
with auto-filled turn data from `diagnosis_raw.json`.

---

### Step 17 — `index.json`
Appends one entry per run. Fields written:

| Field | Source |
|---|---|
| `date`, `commit`, `commit_msg`, `job_id`, `component`, `note`, `folder` | Git + meta |
| `finding_counts` | findings.json |
| `gap_count` | clarification-questions.md |
| `known_ids` | vocab.json |
| `vocab_completeness` | invocables_map.json |
| `vocab_coverage` | vocab_coverage.json |
| `transcript_present` | chat_transcript.txt presence |
| `mini_transcript_present` | mini_session_transcript.txt presence |
| `transcript_metrics` | chat_transcript.txt |
| `probe_summary.total_probes` | explore_probe_log.json |
| `probe_summary.functions_probed` | explore_probe_log.json |
| `probe_summary.unprobed_functions` | invocables_map − probe log |
| `probe_summary.verify_pass / verify_fail` | phase=verify entries in probe log |
| `probe_summary.probe_distribution` | min/max/avg probes per function |
| `probe_summary.sentinels_observed` | hex sentinels seen in live probe results |
| `param_desc_quality` | invocables_map param descriptions |
| `sentinel_calibration` | sentinel_calibration.json |
| `gap_resolution` | gap_resolution_log.json |
| `function_status` | findings.json |
| `saved_at` | wall clock |

---

## What the script does NOT currently tell you

| Gap | Impact | Suggested fix |
|---|---|---|
| Cross-session probe coverage trend | Can't see "probed 40 fns last run, 38 this run" without reading two index entries | `compare.ps1` could print a probe-coverage row alongside finding-counts |
| Whether sentinels in `sentinel_calibration` actually fired during probing | Calibration assigned meanings but maybe none returned | Cross-ref `sentinels_observed` in probe_summary against calibration keys |
| Param description quality trend across sessions | Can see boilerplate_count today, no delta | `delta.md` could include prev vs. new boilerplate count |

All other previously-identified gaps are now covered.

---

## Output files written per session

```
{session-folder}/
  session-meta.json              — server metadata + commit stamp
  code-changes.md                — git diff at save time
  delta.md                       — findings/vocab diff vs previous session
  vocab_coverage.json            — which vocab entries reached model_context.txt
  static_verification.json       — static analysis vs runtime cross-check
  DIAGNOSIS.json                 — chat session verdict (clean/partial/blocked)
  TEST_RESULTS.md                — pre-populated test scoring table
  artifacts/
    findings.json                — per-function probe results
    vocab.json                   — accumulated semantic knowledge
    invocables_map.json          — enriched schema
    explore_probe_log.json       — every executor call in discover phase
    executor_trace.json          — executor calls from chat + mini-sessions
    mini_session_transcript.txt  — gap-answer LLM exchange
    chat_transcript.txt          — user session with [REASONING] blocks
    model_context.txt            — exact system prompt the LLM received
    diagnosis_raw.json           — per-chat-turn tool call summary
    static_analysis.json         — binary-derived seeds (G-4/G-7/G-8/G-9)
    clarification-questions.md   — open gaps
    sentinel_calibration.json    — Phase 0.5: hex code → assigned meaning map
    gap_resolution_log.json      — per-gap resolved/unresolved from mini-sessions
    api_reference.md             — synthesised API doc
```

`sessions/index.json` and `sessions/README.md` are updated at the sessions
root level (not inside the session folder).
