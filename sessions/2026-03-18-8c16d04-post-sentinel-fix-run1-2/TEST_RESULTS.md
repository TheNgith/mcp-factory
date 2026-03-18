# Test Results - 2026-03-18

**Session:** 2026-03-18-8c16d04-post-sentinel-fix-run1-2
**Commit:** 8c16d04 - fix: compare.ps1 encoding (UTF-8 BOM) + replace em-dash/delta breaks PS5.1 syntax
**Job ID:** cac5f644

See [../../CONTOSO_CS_TEST_SUITE.md](../../CONTOSO_CS_TEST_SUITE.md) for full prompts.

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

---

## Auto-populated context (from diagnosis_raw.json)

| Turn | Tools called | Sentinels | DLL errors |
|---|---|---|---|
| 2026-03-18T05:42:45Z 2026-03-18T05:43:57Z 2026-03-18T05:44:12Z 2026-03-18T05:45:00Z 2026-03-18T05:45:10Z 2026-03-18T05:46:19Z 2026-03-18T05:47:35Z 2026-03-18T05:48:12Z 2026-03-18T05:49:34Z | CS_GetVersion, CS_GetDiagnostics, CS_ProcessRefund, CS_LookupCustomer, CS_LookupCustomer, CS_LookupCustomer, CS_Initialize, CS_LookupCustomer, CS_LookupCustomer | 0 0 0 0 0 0 0 0 0 | 0 0 0 0 0 0 0 0 0 |

**All tools exercised:** CS_GetDiagnostics, CS_GetVersion, CS_Initialize, CS_LookupCustomer, CS_ProcessRefund

