# Clarification Questions from Discovery

## 1. CS_ProcessPayment
**Question:** Are there specific conditions or prerequisites required to process a payment, such as account status or a particular setup step?

**Technical detail:** `CS_ProcessPayment consistently returned sentinel error codes indicating write denied, except when param_1 was 'CUST-001' and param_2 was 0.`

**Answer:** (unanswered)

## 2. CS_UnlockAccount
**Question:** What does the 'LOCKED' parameter represent, and are there specific scenarios where an account can be unlocked?

**Technical detail:** `CS_UnlockAccount returned success only when param_1 was 'CUST-001' and param_2 was 'LOCKED'; all other probes returned errors indicating null argument or access violation.`

**Answer:** (unanswered)

## 3. CS_RedeemLoyaltyPoints
**Question:** Are there any restrictions or conditions for redeeming loyalty points, such as minimum point thresholds or account status requirements?

**Technical detail:** `CS_RedeemLoyaltyPoints could not be successfully probed; no valid return values or behaviors were observed.`

**Answer:** (unanswered)

## 4. CS_GetOrderStatus
**Question:** What are the possible values for the order status, and do they represent specific stages in the order lifecycle?

**Technical detail:** `CS_GetOrderStatus returned 'DELIVERED' for a specific order, but no other status values were observed during probing.`

**Answer:** (unanswered)

## 5. CS_GetAccountBalance
**Question:** Does the balance include pending transactions, or is it strictly the current available balance?

**Technical detail:** `CS_GetAccountBalance returned balances for some customers but returned an error indicating 'account locked' for 'CUST-003'.`

**Answer:** (unanswered)
