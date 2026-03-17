# Test Results - 2026-03-17

**Session:** 2026-03-17-33f9114-unknown-run2
**Commit:** 33f9114 - feat: gap answers auto-trigger refinement, move schema download to Generate section
**Job ID:** a0fc70e8
**vocab.json state:** pre-description-synthesis (description/user_context fields not yet present — gap answers submitted but refinement ran after snapshot)
**Legend:** ✅ Pass | ❌ Fail | ? Manual fill needed | N/A Not applicable | ~inferred from artifacts~

See [../../CONTOSO_CS_TEST_SUITE.md](../../CONTOSO_CS_TEST_SUITE.md) for full prompts.

---

## Key facts from artifacts (findings.json + vocab.json)

- `CS_GetVersion` → 131841, decoded by discovery as **2.3.1** (major.minor.patch confirmed working)
- `CS_GetDiagnostics` → customers=4, orders=2, initialized=yes — all fields labeled in vocab
- `CS_LookupCustomer(CUST-001)` → Alice Contoso, Gold tier, balance=$250.00 (25000 cents), points=1450, ACTIVE
- `CS_GetAccountBalance(CUST-003)` → sentinel (4294967295) — account is **LOCKED**
- `CS_ProcessPayment` → **BROKEN** — every probe returns 4294967291 (0xFFFFFFFB write denied); pending refinement
- `CS_UnlockAccount` → **BROKEN** — every probe returns sentinel; pending refinement
- `CS_RedeemLoyaltyPoints` → **pending refinement**
- `CS_ProcessRefund` → success with CUST-001 / ORD-20040301-0042
- `CS_CalculateInterest` → success (param_1=10000, param_2=500, param_3=12)
- vocab.json has: id_formats=[CUST-NNN, ORD-YYYYMMDD-NNNN], error_codes={0xFFFFFFFB=write denied, 0xFFFFFFFC=account locked}, cents semantics for balance/param_4

---

## Scoring Table

| ID | Description | ID Format | Amount/Value Encoding | Error Decode | Init Order | Overall |
|----|-------------|-----------|----------------------|--------------|------------|---------|
| T01 | Version decode | - | ✅~vocab present~ | - | - | ✅ |
| T02 | Initialized boolean | - | ? | - | - | ? |
| T03 | System counts | - | ✅~fields labeled~ | - | - | ✅ |
| T04 | Auto-format CUST-007 | ? | - | - | - | ? |
| T05 | Auto-format CUST-042 | ? | - | - | - | ? |
| T06 | Order ID + refund cents | ? | ? | - | - | ? |
| T07 | Reject malformed ID | ? | - | - | - | ? |
| T08 | Payment cents | ? | ? | - | - | ❌~CS_ProcessPayment broken~ |
| T09 | Refund cents | ? | ? | - | - | ? |
| T10 | Balance div 100 | - | ✅~25000→$250 confirmed~ | - | - | ✅ |
| T11 | Points integer | - | ? | - | - | ❌~CS_RedeemLoyaltyPoints pending~ |
| T12 | Diagnose locked | ? | - | ✅~CUST-003 locked confirmed~ | ? | ❌~CS_UnlockAccount broken~ |
| T13 | Already-active unlock | - | - | - | ? | ❌~CS_UnlockAccount broken~ |
| T14 | Payment on locked | - | - | ✅~0xFFFFFFFB in vocab~ | ? | ❌~both broken~ |
| T15 | 0xFFFFFFFB decode | - | - | ✅~error_codes in vocab~ | - | ✅ |
| T16 | 0xFFFFFFFC decode | - | - | ✅~error_codes in vocab~ | - | ✅ |
| T17 | Access violation | - | - | ? | - | ? |
| T18 | No-init payment | - | - | ? | ? | ? |
| T19 | Full profile fields | ✅~CUST-NNN used~ | ✅~balance in dollars~ | - | - | ✅ |
| T20 | Tier label | - | ✅~tier=Gold confirmed~ | - | - | ✅ |
| T21 | Contact fields | - | - | - | - | ? |
| T22 | Full happy path | ? | ? | - | ? | ❌~CS_ProcessPayment broken~ |
| T23 | End-to-end refund | ✅~ORD format in vocab~ | ? | - | ? | ? |
| T24 | Multi-customer session | ? | ? | ✅~CUST-003 locked auto-detected~ | ? | ❌~CS_ProcessPayment broken~ |
| T25 | Locked in multi-step | ? | - | ✅~LOCKED status in vocab~ | ? | ❌~CS_UnlockAccount broken~ |
| T26 | Zero amount | - | ? | - | - | ? |
| T27 | Over-redeem points | - | ? | ❌~CS_RedeemLoyaltyPoints pending~ | - | ❌ |
| T28 | LOCKED as ID confusion | ✅~LOCKED in id_formats~ | - | - | - | ? |

---

## Summary

| Category | Status |
|---|---|
| Read-only functions (GetVersion, GetDiagnostics, LookupCustomer, GetBalance, GetOrderStatus) | ✅ Working |
| Write functions (ProcessPayment, UnlockAccount, RedeemLoyaltyPoints) | ❌ All pending refinement |
| Error code decoding (T15, T16) | ✅ Vocab present and correct |
| ID format compliance | ? Needs manual observation |
| Cents encoding | ? Needs manual observation for write paths |

## Blockers for next session

1. **CS_ProcessPayment** returns write-denied on every probe — gap answers submitted, refinement needed
2. **CS_UnlockAccount** returns sentinel on every probe — same
3. **CS_RedeemLoyaltyPoints** — never successfully probed
4. Without working write functions, T08/T11/T12/T13/T14/T22/T24/T25/T27 cannot get a true Overall ✅

## Notes

- Gap answers were submitted just before this snapshot — refinement had not yet completed
- Tests with `?` in ID Format / Amount columns need manual fill from the actual chat transcript
- Next session should re-run Groups 3, 4, 7 after refinement completes to see if write functions unlock
- T15/T16 (error code decode) were green even without write functions — vocab injection confirmed working

