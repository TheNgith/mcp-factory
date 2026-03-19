# Test Results - 2026-03-19

**Session:** 2026-03-19-558ceb0-unknown-run2
**Commit:** 558ceb0 - refactor: move contoso_cs fixtures into sessions/contoso_cs/, update all path refs
**Job ID:** 2026-03-18-8c16d04-post-sentinel-fix-run1

See [../../contoso_cs/TEST_SUITE.md](../../contoso_cs/TEST_SUITE.md) for full prompts.

---

## Scoring Table

| ID | Description | ID Format | Amount/Value Encoding | Error Decode | Init Order | Overall |
|----|-------------|-----------|----------------------|--------------|------------|---------|
| T01 | Version decode | - | | - | - | |
| T02 | Initialized boolean | - | | - | - | |
| T03 | System counts | - | - | - | - | |
| T04 | Auto-format CUST-007 | | - | - | - | |
| T05 | Auto-format CUST-042 | | - | - | - | |
| T06 | Order ID + refund cents | | | - | - | |
| T07 | Reject malformed ID | | - | - | - | |
| T08 | Payment cents | - | | - | - | |
| T09 | Refund cents | - | | - | - | |
| T10 | Balance div 100 | - | | - | - | |
| T11 | Points integer | - | | - | - | |
| T12 | Diagnose locked | | - | | | |
| T13 | Already-active unlock | - | - | - | | |
| T14 | Payment on locked | - | - | | | |
| T15 | 0xFFFFFFFB decode | - | - | | - | |
| T16 | 0xFFFFFFFC decode | - | - | | - | |
| T17 | Access violation | - | - | | - | |
| T18 | No-init payment | - | - | | | |
| T19 | Full profile fields | | | - | - | |
| T20 | Tier label | - | | - | - | |
| T21 | Contact fields | - | - | - | - | |
| T22 | Full happy path | | | - | | |
| T23 | End-to-end refund | | | - | | |
| T24 | Multi-customer session | | | | | |
| T25 | Locked in multi-step | - | - | | | |
| T26 | Zero amount | - | | - | - | |
| T27 | Over-redeem points | - | | | - | |
| T28 | LOCKED as ID confusion | | - | - | - | |

---

## Notes

> Fill in observations, surprises, and follow-up questions below

-

