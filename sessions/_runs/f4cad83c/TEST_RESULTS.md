# Test Results - 2026-03-18 20:54

**Session:** f4cad83c
**Commit:** unknown 
**Job ID:** unknown
**Score:** 23 / 28 non-skip tests (82%)   PASS: 23  FAIL: 4  WARN: 1  SKIP: 0

See [../../CONTOSO_CS_TEST_SUITE.md](../../CONTOSO_CS_TEST_SUITE.md) for full prompts.

---

## Scoring Table

| ID | Description | ID Format | Amount/Value | Error Decode | Init Order | Verdict |
|----|-------------|-----------|--------------|--------------|------------|---------|
| T01 | Version decode | - |  | - | - | âœ… PASS |
| T02 | Initialized boolean | - |  | - | - | âœ… PASS |
| T03 | System counts | - | - | - | - | âœ… PASS |
| T04 | Auto-format CUST-007 |  | - | - | - | âš ï¸ WARN |
| T05 | Auto-format CUST-042 |  | - | - | - | âŒ FAIL |
| T06 | Order ID + refund cents |  |  | - | - | âœ… PASS |
| T07 | Reject malformed ID |  | - | - | - | âœ… PASS |
| T08 | Payment cents | - |  | - | - | âœ… PASS |
| T09 | Refund cents | - |  | - | - | âœ… PASS |
| T10 | Balance div 100 | - |  | - | - | âœ… PASS |
| T11 | Points integer | - |  | - | - | âœ… PASS |
| T12 | Diagnose locked |  | - |  |  | âœ… PASS |
| T13 | Already-active unlock | - | - | - |  | âœ… PASS |
| T14 | Payment on locked | - | - |  |  | âŒ FAIL |
| T15 | 0xFFFFFFFB decode | - | - |  | - | âœ… PASS |
| T16 | 0xFFFFFFFC decode | - | - |  | - | âœ… PASS |
| T17 | Access violation | - | - |  | - | âœ… PASS |
| T18 | No-init payment | - | - |  |  | âœ… PASS |
| T19 | Full profile fields |  |  | - | - | âœ… PASS |
| T20 | Tier label | - |  | - | - | âœ… PASS |
| T21 | Contact fields | - | - | - | - | âŒ FAIL |
| T22 | Full happy path |  |  | - |  | âœ… PASS |
| T23 | End-to-end refund |  |  | - |  | âŒ FAIL |
| T24 | Multi-customer |  |  |  |  | âœ… PASS |
| T25 | Locked in multi-step | - | - |  |  | âœ… PASS |
| T26 | Zero amount | - |  | - | - | âœ… PASS |
| T27 | Over-redeem points | - |  |  | - | âœ… PASS |
| T28 | LOCKED as ID confusion |  | - | - | - | âœ… PASS |

---

## Summary

| Category | Tests | Pass | Fail | Warn | Skip |
|----------|-------|------|------|------|------|
| Init & State | 4 | 4 | 0 | 0 | 0 |
| ID Format | 5 | 3 | 1 | 1 | 0 |
| Amount Encoding | 5 | 5 | 0 | 0 | 0 |
| Error Decode | 3 | 3 | 0 | 0 | 0 |
| Account Flow | 3 | 2 | 1 | 0 | 0 |
| Profile | 3 | 2 | 1 | 0 | 0 |
| Multi-step | 5 | 4 | 1 | 0 | 0 |
| **TOTAL** | **28** | **23** | **4** | **1** | **0** |

---

## Notes

> Auto-scored by score-session.ps1 on 2026-03-18 20:54
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
T05  FAIL
T06  PASS
T07  PASS
T08  PASS
T09  PASS
T10  PASS
T11  PASS
T12  PASS
T13  PASS
T14  FAIL
T15  PASS
T16  PASS
T17  PASS
T18  PASS
T19  PASS
T20  PASS
T21  FAIL
T22  PASS
T23  FAIL
T24  PASS
T25  PASS
T26  PASS
T27  PASS
T28  PASS
```

</details>
