# Test Results - 2026-03-18 14:52

**Session:** 2026-03-18-8c16d04-post-sentinel-fix-run1-2
**Commit:** 8c16d04 
**Job ID:** cac5f644
**Score:** 22 / 28 non-skip tests (79%)   PASS: 22  FAIL: 4  WARN: 2  SKIP: 0

See [../../CONTOSO_CS_TEST_SUITE.md](../../CONTOSO_CS_TEST_SUITE.md) for full prompts.

---

## Scoring Table

| ID | Description | ID Format | Amount/Value | Error Decode | Init Order | Verdict |
|----|-------------|-----------|--------------|--------------|------------|---------|
| T01 | Version decode | - |  | - | - | âœ… PASS |
| T02 | Initialized boolean | - |  | - | - | âœ… PASS |
| T03 | System counts | - | - | - | - | âœ… PASS |
| T04 | Auto-format CUST-007 |  | - | - | - | âš ï¸ WARN |
| T05 | Auto-format CUST-042 |  | - | - | - | âœ… PASS |
| T06 | Order ID + refund cents |  |  | - | - | âœ… PASS |
| T07 | Reject malformed ID |  | - | - | - | âœ… PASS |
| T08 | Payment cents | - |  | - | - | âŒ FAIL |
| T09 | Refund cents | - |  | - | - | âŒ FAIL |
| T10 | Balance div 100 | - |  | - | - | âœ… PASS |
| T11 | Points integer | - |  | - | - | âŒ FAIL |
| T12 | Diagnose locked |  | - |  |  | âœ… PASS |
| T13 | Already-active unlock | - | - | - |  | âœ… PASS |
| T14 | Payment on locked | - | - |  |  | âœ… PASS |
| T15 | 0xFFFFFFFB decode | - | - |  | - | âœ… PASS |
| T16 | 0xFFFFFFFC decode | - | - |  | - | âœ… PASS |
| T17 | Access violation | - | - |  | - | âœ… PASS |
| T18 | No-init payment | - | - |  |  | âœ… PASS |
| T19 | Full profile fields |  |  | - | - | âœ… PASS |
| T20 | Tier label | - |  | - | - | âœ… PASS |
| T21 | Contact fields | - | - | - | - | âœ… PASS |
| T22 | Full happy path |  |  | - |  | âŒ FAIL |
| T23 | End-to-end refund |  |  | - |  | âœ… PASS |
| T24 | Multi-customer |  |  |  |  | âš ï¸ WARN |
| T25 | Locked in multi-step | - | - |  |  | âœ… PASS |
| T26 | Zero amount | - |  | - | - | âœ… PASS |
| T27 | Over-redeem points | - |  |  | - | âœ… PASS |
| T28 | LOCKED as ID confusion |  | - | - | - | âœ… PASS |

---

## Summary

| Category | Tests | Pass | Fail | Warn | Skip |
|----------|-------|------|------|------|------|
| Init & State | 4 | 4 | 0 | 0 | 0 |
| ID Format | 5 | 4 | 0 | 1 | 0 |
| Amount Encoding | 5 | 2 | 3 | 0 | 0 |
| Error Decode | 3 | 3 | 0 | 0 | 0 |
| Account Flow | 3 | 3 | 0 | 0 | 0 |
| Profile | 3 | 3 | 0 | 0 | 0 |
| Multi-step | 5 | 3 | 1 | 1 | 0 |
| **TOTAL** | **28** | **22** | **4** | **2** | **0** |

---

## Notes

> Auto-scored by score-session.ps1 on 2026-03-18 14:52
> Rubric: deterministic regex - no LLM calls.
> WARN = partial signal (mentioned in text but not confirmed in tool call args).
> SKIP = test block not found in transcript (run run-tests.ps1 to get per-test blocks).

---

<details>
<summary>Per-test raw results</summary>

```
T01  PASS
T02  PASS
T03  PASS
T04  WARN
T05  PASS
T06  PASS
T07  PASS
T08  FAIL
T09  FAIL
T10  PASS
T11  FAIL
T12  PASS
T13  PASS
T14  PASS
T15  PASS
T16  PASS
T17  PASS
T18  PASS
T19  PASS
T20  PASS
T21  PASS
T22  FAIL
T23  PASS
T24  WARN
T25  PASS
T26  PASS
T27  PASS
T28  PASS
```

</details>
