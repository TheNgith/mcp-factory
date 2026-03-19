# Test Results - 2026-03-18 22:39

**Session:** 0c5a7b47
**Commit:** unknown 
**Job ID:** unknown
**Score:** 12 / 28 non-skip tests (43%)   PASS: 12  FAIL: 15  WARN: 1  SKIP: 0

See [../../CONTOSO_CS_TEST_SUITE.md](../../CONTOSO_CS_TEST_SUITE.md) for full prompts.

---

## Scoring Table

| ID | Description | ID Format | Amount/Value | Error Decode | Init Order | Verdict |
|----|-------------|-----------|--------------|--------------|------------|---------|
| T01 | Version decode | - |  | - | - | âŒ FAIL |
| T02 | Initialized boolean | - |  | - | - | âœ… PASS |
| T03 | System counts | - | - | - | - | âœ… PASS |
| T04 | Auto-format CUST-007 |  | - | - | - | âš ï¸ WARN |
| T05 | Auto-format CUST-042 |  | - | - | - | âŒ FAIL |
| T06 | Order ID + refund cents |  |  | - | - | âŒ FAIL |
| T07 | Reject malformed ID |  | - | - | - | âœ… PASS |
| T08 | Payment cents | - |  | - | - | âŒ FAIL |
| T09 | Refund cents | - |  | - | - | âŒ FAIL |
| T10 | Balance div 100 | - |  | - | - | âŒ FAIL |
| T11 | Points integer | - |  | - | - | âœ… PASS |
| T12 | Diagnose locked |  | - |  |  | âŒ FAIL |
| T13 | Already-active unlock | - | - | - |  | âœ… PASS |
| T14 | Payment on locked | - | - |  |  | âŒ FAIL |
| T15 | 0xFFFFFFFB decode | - | - |  | - | âŒ FAIL |
| T16 | 0xFFFFFFFC decode | - | - |  | - | âœ… PASS |
| T17 | Access violation | - | - |  | - | âœ… PASS |
| T18 | No-init payment | - | - |  |  | âœ… PASS |
| T19 | Full profile fields |  |  | - | - | âŒ FAIL |
| T20 | Tier label | - |  | - | - | âœ… PASS |
| T21 | Contact fields | - | - | - | - | âŒ FAIL |
| T22 | Full happy path |  |  | - |  | âŒ FAIL |
| T23 | End-to-end refund |  |  | - |  | âŒ FAIL |
| T24 | Multi-customer |  |  |  |  | âœ… PASS |
| T25 | Locked in multi-step | - | - |  |  | âŒ FAIL |
| T26 | Zero amount | - |  | - | - | âŒ FAIL |
| T27 | Over-redeem points | - |  |  | - | âœ… PASS |
| T28 | LOCKED as ID confusion |  | - | - | - | âœ… PASS |

---

## Summary

| Category | Tests | Pass | Fail | Warn | Skip |
|----------|-------|------|------|------|------|
| Init & State | 4 | 3 | 1 | 0 | 0 |
| ID Format | 5 | 2 | 2 | 1 | 0 |
| Amount Encoding | 5 | 1 | 4 | 0 | 0 |
| Error Decode | 3 | 2 | 1 | 0 | 0 |
| Account Flow | 3 | 1 | 2 | 0 | 0 |
| Profile | 3 | 1 | 2 | 0 | 0 |
| Multi-step | 5 | 2 | 3 | 0 | 0 |
| **TOTAL** | **28** | **12** | **15** | **1** | **0** |

---

## Notes

> Auto-scored by score-session.ps1 on 2026-03-18 22:39
> Rubric: deterministic regex - no LLM calls.
> WARN = partial signal (mentioned in text but not confirmed in tool call args).
> SKIP = test block not found in transcript (run run-tests.ps1 to get per-test blocks).

---

<details>
<summary>Per-test raw results</summary>

```
T01  FAIL
T02  PASS
T03  PASS
T04  WARN
T05  FAIL
T06  FAIL
T07  PASS
T08  FAIL
T09  FAIL
T10  FAIL
T11  PASS
T12  FAIL
T13  PASS
T14  FAIL
T15  FAIL
T16  PASS
T17  PASS
T18  PASS
T19  FAIL
T20  PASS
T21  FAIL
T22  FAIL
T23  FAIL
T24  PASS
T25  FAIL
T26  FAIL
T27  PASS
T28  PASS
```

</details>
