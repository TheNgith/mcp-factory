# Session Summary

> For pipeline architecture and how vocab.json/schema/findings fit together, see [../WORKFLOW.md](../WORKFLOW.md)

> The exact system message the LLM received is in [model_context.txt](model_context.txt)

**Date:** 2026-03-18
**Component:** unknown
**Job ID:** cac5f644
**Commit:** 8c16d04 - fix: compare.ps1 encoding (UTF-8 BOM) + replace em-dash/delta breaks PS5.1 syntax
**Note:** post-sentinel-fix-run1

---

## What changed in this commit

See [code-changes.md](code-changes.md) for the full diff.

```
 sessions/compare.ps1 | 13 +++++++------  1 file changed, 7 insertions(+), 6 deletions(-)
```

---

## Discovery state

| Metric | Value |
|---|---|
| Total findings | 21 |
| Successful calls | 9 |
| Partial | 0 |
| Failed | 0 |
| Gap questions open | 5 |
| Known IDs in vocab | CUST-NNN, ORD-YYYYMMDD-XXXX |

---

## Working calls confirmed

- **unknown**: {}
- **unknown**: {}
- **unknown**: {"param_2":0}
- **unknown**: {}
- **unknown**: {}
- **unknown**: {"param_2":100,"param_3":"ORD-20040301-0042"}
- **unknown**: {"param_1":0,"param_2":0,"param_3":0}
- **unknown**: {}
- **unknown**: {"param_3":0}

---

## Gap questions open

# Clarification Questions from Discovery

## 1. CS_ProcessPayment
**Question:** Are there any specific conditions or prerequisites required before processing a payment, such as account status or permissions?

**Technical detail:** `CS_ProcessPayment consistently returned 4294967291, indicating write access denied or unsupported operation.`

**Answer:** (unanswered)

## 2. CS_UnlockAccount
**Question:** What conditions or inputs are required to successfully unlock an account? For example, does the account need to be in a specific locked state?

**Technical detail:** `CS_UnlockAccount consistently returned 4294967294, indicating invalid input or access denied.`

**Answer:** (unanswered)

## 3. CS_RedeemLoyaltyPoints
**Question:** Are there any restrictions or account conditions that must be met before redeeming loyalty points, such as a minimum balance or unlocked status?

**Technical detail:** `CS_RedeemLoyaltyPoints returned 4294967291 for write access denied and 4294967292 for account locked.`

**Answer:** (unanswered)

## 4. CS_GetOrderStatus
**Question:** What are the possible statuses an order can have, and do they include any intermediate or error states beyond 'DELIVERED'?

**Technical detail:** `CS_GetOrderStatus interpretation suggests 'status' is a string like 'DELIVERED,' but no other statuses were observed or documented.`

**Answer:** (unanswered)

## 5. CS_LookupCustomer
**Question:** What are the possible values for the 'status' field of a customer account, and do they include states beyond 'ACTIVE'?

**Technical detail:** `CS_LookupCustomer interpretation indicates 'status' can be 'ACTIVE,' but no other statuses were observed or documented.`

**Answer:** (unanswered)


---

## What to investigate next

> Fill this in after testing

-

