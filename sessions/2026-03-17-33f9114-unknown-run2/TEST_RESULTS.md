# Test Results - 2026-03-17

**Session:** 2026-03-17-33f9114-unknown-run2
**Commit:** 33f9114 - feat: gap answers auto-trigger refinement, move schema download to Generate section
**Job ID:** a0fc70e8
**vocab.json state:** pre-description-synthesis (description/user_context fields not yet present — gap answers submitted but refinement ran after snapshot)
**Transcript coverage:** T01–T28 all run (full session with tool call detail now available)
**Customer dataset note:** CS_GetDiagnostics reports customers=4, but only CUST-001 is reliably accessible. CUST-002 and CUST-003 fail for CS_ProcessPayment despite nominally being in the dataset. CUST-007, CUST-010, CUST-015, CUST-019, CUST-022, CUST-042 are outside the dataset.
**Legend:** ✅ Pass | ❌ Fail | ? Manual fill needed | N/A Not applicable | ~inferred from artifacts~

See [../../CONTOSO_CS_TEST_SUITE.md](../../CONTOSO_CS_TEST_SUITE.md) for full prompts.

---

## Key facts from artifacts (findings.json + vocab.json)

- `CS_GetVersion` → 131841, decoded by discovery as **2.3.1** (major.minor.patch confirmed working)
- `CS_GetDiagnostics` → customers=4, orders=2, initialized=yes — all fields labeled in vocab
- `CS_LookupCustomer(CUST-001)` → Alice Contoso, **Platinum** tier (⚠️ was Gold in discovery artifacts), balance=$12562739.92 (⚠️ was $250/25000 cents), points=214371705 (⚠️ was 1450), ACTIVE — **data has drifted significantly since discovery run**
- `CS_GetAccountBalance(CUST-003)` → sentinel (4294967295) — account is **LOCKED** (per discovery; T12 session attempt returned access violation)
- `CS_ProcessPayment` → **functional for CUST-001** (T24: $10 payment succeeded); CUST-002/003 fail with access violation despite being in dataset; non-existent customers always fail
- `CS_UnlockAccount` → returns 0xFFFFFFFE (4294967294) for CUST-010 "already active" (T13); access violations elsewhere; pending refinement
- `CS_RedeemLoyaltyPoints` → access violation for CUST-042 (doesn't exist); untested on valid customers
- `CS_ProcessRefund` → success with CUST-001 / ORD-20040301-0042
- `CS_CalculateInterest` → success (param_1=10000, param_2=500, param_3=12)
- vocab.json has: id_formats=[CUST-NNN, ORD-YYYYMMDD-NNNN], error_codes={0xFFFFFFFB=write denied, 0xFFFFFFFC=account locked}, cents semantics for balance/param_4

---

## Scoring Table

| ID | Description | ID Format | Amount/Value Encoding | Error Decode | Init Order | Overall |
|----|-------------|-----------|----------------------|--------------|------------|---------|
| T01 | Version decode | - | ✅~vocab present~ | - | - | ✅ |
| T02 | Initialized boolean | - | - | - | - | ✅ |
| T03 | System counts | - | ✅~fields labeled~ | - | - | ✅ |
| T04 | Auto-format CUST-007 | ✅ | - | - | - | ❌~CUST-007 not in dataset (only 4 customers exist)~ |
| T05 | Auto-format CUST-042 | ✅ | - | - | - | ❌~CUST-042 not in dataset~ |
| T06 | Order ID + refund cents | ✅ | ✅~param_2=1850 for $18.50 confirmed from tool call~ | - | - | ✅~CS_ProcessRefund(CUST-001,1850,ORD-20040301-0042) returned 0; param_4=2488101671 (may be packed data)~ |
| T07 | Reject malformed ID | ❌~model attempted lookup of "ABC" instead of rejecting it~ | - | - | - | ❌ |
| T08 | Payment cents | ✅ | ✅~param_2=2500 for $25.00 confirmed from tool call~ | - | - | ❌~CUST-042 not in dataset~ |
| T09 | Refund cents | ✅ | ✅~param_2=15099 for $150.99 confirmed from tool call~ | - | - | ❌~CUST-007 not in dataset~ |
| T10 | Balance div 100 | - | ❌~CUST-010 not in dataset, conversion unobservable~ | - | - | ❌ |
| T11 | Points integer | - | ? | - | - | ❌~access violation (CUST-042 not in dataset)~ |
| T12 | Diagnose locked | ? | - | ❌~access violation for CUST-003; locked status not surfaced despite vocab~ | ? | ❌ |
| T13 | Already-active unlock | - | - | ~0xFFFFFFFE: "not found or already active" (reasonable; not in vocab)~ | ? | ✅~correct interpretation despite no vocab entry~ |
| T14 | Payment on locked | - | - | ❌~got access violation; 0xFFFFFFFB never surfaced despite being in vocab~ | ? | ❌ |
| T15 | 0xFFFFFFFB decode | - | - | ✅~error_codes in vocab~ | - | ✅ |
| T16 | 0xFFFFFFFC decode | - | - | ~said "access violation or malformed input" (should be "account locked" per vocab)~ | - | ✅~guidance actionable; ID format tip correct~ |
| T17 | Access violation | - | - | ✅~CUST-NNN / ORD-YYYYMMDD-NNNN / cents guidance all correct~ | - | ✅ |
| T18 | No-init payment | - | - | - | ~ambiguous: system was already initialized from opening exchange; CUST-005 not in dataset; model stated "initialization is mandatory" but test was inconclusive~ | ~ |
| T19 | Full profile fields | ✅ | ~balance pre-formatted as dollars by DLL in return string (no model conversion needed)~ | - | - | ✅~email, phone, tier, balance, points, status all returned for CUST-001~ |
| T20 | Tier label | - | - | - | - | ❌~CUST-015 not in dataset; tier=Platinum for CUST-001 (vocab had Gold — data drifted)~ |
| T21 | Contact fields | - | - | - | - | ❌~CUST-022 not in dataset; contact fields confirmed present for CUST-001 via T19~ |
| T22 | Full happy path | ✅~CUST-042 formatted correctly~ | ? | - | ✅~CS_Initialize called first~ | ❌~CUST-042 not in dataset~ |
| T23 | End-to-end refund | ✅~ORD-YYYYMMDD-NNNN format used~ | ? | - | ? | ❌~CUST-019 not in dataset~ |
| T24 | Multi-customer session | ✅ | ✅~CUST-001 $10 payment succeeded~ | - | ✅ | ~CUST-001 ✅; CUST-002/003 ❌ access violation despite being in dataset~ |
| T25 | Locked in multi-step | ? | - | ❌~CUST-008 not in dataset~ | ? | ❌~CUST-008 not in dataset~ |
| T26 | Zero amount | - | ✅~$0.00 payment processed successfully for CUST-001~ | - | - | ✅ |
| T27 | Over-redeem points | - | ? | - | - | ❌~CUST-042 not in dataset; over-redeem behavior untestable~ |
| T28 | LOCKED as ID confusion | ❌~model tried CS_LookupCustomer("LOCKED") instead of flagging invalid ID~ | - | - | - | ❌ |

---

## Summary

| Category | Status |
|---|---|
| Read-only functions on **CUST-001** (GetVersion, GetDiagnostics, LookupCustomer) | ✅ Working |
| CS_ProcessPayment on **CUST-001** | ✅ Working (T24/T26 confirmed) |
| CS_ProcessRefund on **CUST-001** | ✅ Working (T06 confirmed) |
| Read functions on **non-existent customers** (007, 010, 015, 019, 022, 042) | ❌ Access violation |
| CS_ProcessPayment on **CUST-002/003** (in dataset but failing) | ❌ Unexpected — investigate |
| CS_UnlockAccount | ~ 0xFFFFFFFE returned for CUST-010 (correctly interpreted); untested on CUST-003 |
| CS_RedeemLoyaltyPoints | ❌ Never successfully invoked |
| Cents encoding for write functions | ✅ Confirmed: param_2=1850/2500/15099 in all tool calls |
| Error code decoding — T15 (0xFFFFFFFB) | ✅ Correct |
| Error code decoding — T16 (0xFFFFFFFC) | ~ Imprecise ("access violation" vs "account locked") |
| Error code decoding — T17 (access violation explanation) | ✅ Correct format/encoding guidance |
| ID format auto-inference (T04/T05/T22: shorthand → CUST-00N) | ✅ Working |
| Invalid ID rejection — T07 ("ABC"), T28 ("LOCKED") | ❌ Both: model attempts call instead of rejecting upfront |
| Data drift from discovery | ⚠️ CUST-001 tier=Platinum (was Gold), balance and points changed significantly |

## Blockers for next session

1. **CUST-002/003 payment failures**: CS_ProcessPayment works for CUST-001 but fails (access violation) for CUST-002 and CUST-003, even though all are reportedly in the dataset. Investigate whether CUST-002/003 have null/corrupt records or a different param layout
2. **CS_UnlockAccount**: Untested on CUST-003 (locked per discovery). Re-run T12/T25 targeting CUST-003 specifically
3. **CS_RedeemLoyaltyPoints**: Never successfully invoked. Test with CUST-001 (has 214371705 points)
4. **T10 test design**: Prompt uses CUST-010 (doesn't exist). Re-run: "What is the balance for CUST-001? Display in dollars." — tests if model divides CS_GetAccountBalance raw integer by 100
5. **T20/T21 test design**: Use existing customers (CUST-001 through ~CUST-004) not CUST-015/022
6. **Invalid ID rejection (T07/T28)**: Model attempts CS_LookupCustomer with literally "ABC" or "LOCKED" — vocab has `id_formats` but no pre-call guard. Needs vocab/prompt fix
7. **Data drift**: vocab.json says tier=Gold, actual=Platinum. Discovery artifacts are stale. Re-run discovery or note that vocab is advisory-only not authoritative

## Notes

- Gap answers were submitted just before this snapshot — refinement had not yet completed when session ran
- **T06 cents encoding confirmed**: tool call shows `CS_ProcessRefund({"param_1":"CUST-001","param_2":1850,"param_3":"ORD-20040301-0042"})` — $18.50 correctly encoded as 1850 cents ✅
- **T08/T09 cents encoding confirmed**: `CS_ProcessPayment(param_2=2500)` for $25.00 and `CS_ProcessRefund(param_2=15099)` for $150.99 ✅ — encoding correct even though calls failed (customer not in dataset)
- **T24 major finding**: CS_ProcessPayment IS functional for CUST-001 ($10 payment succeeded) and $0 (T26). Previous "broken" assessment was confounded by testing on non-existent customers. CUST-002/003 still fail unexpectedly.
- **Data drift warning**: CS_LookupCustomer(CUST-001) now returns tier=Platinum, balance=$12562739.92, points=214371705 — all very different from discovery artifacts (Gold, $250, 1450 points). The CS_LookupCustomer balance is a pre-formatted dollar string returned by the DLL itself, not a cents value that the model converts. CS_GetAccountBalance (separate function) returns a raw integer.
- **T19 balance note**: The DLL itself returns `balance=$12562739.92` as part of the lookup string. T10's "display in dollars" test is specifically about CS_GetAccountBalance returning a raw integer and whether the model divides by 100.
- **T07/T28 regression confirmed from tool calls**: `CS_LookupCustomer({"param_1":"ABC"})` and `CS_LookupCustomer({"param_1":"LOCKED"})` both attempted. Model does not guard against non-conforming IDs before calling.
- **T13 positive confirmed**: CS_UnlockAccount returned 4294967294 (0xFFFFFFFE) for CUST-010 and model correctly said "not found or already active" — general error reasoning works without vocab entry.
- **T16 miss confirmed**: 0xFFFFFFFC IS in vocab as "account locked" but model responded "access violation or malformed input." This is a retrieval/matching gap, likely improvable after description-synthesis refinement.
- **T18 inconclusive**: System was already initialized from opening exchange. CUST-005 doesn't exist. The "initialization is mandatory" statement is correct guidance but the test couldn't demonstrate enforcement.
- **T20 design issue**: CUST-015 doesn't exist. Re-run with "What tier is CUST-001 on?" — this also lets us verify whether vocab correction (Platinum vs Gold) propagates into responses.
- T15, T17 cleanly confirm error-code guidance and format encoding advice are working.

