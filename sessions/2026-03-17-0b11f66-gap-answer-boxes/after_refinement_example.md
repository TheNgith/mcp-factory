# MCP Factory — DLL Documentation Report

**Job ID:** `a0fc70e8`  
**Generated:** 2026-03-17 20:20:41 UTC  
**Functions documented:** 13

---

## `CS_CalculateInterest`

Calculates interest based on principal, rate, and period, returning the result in an output pointer.

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `param_1` | `uint` | Principal amount in cents. Example: 100000 for $1000.00. |
| `param_2` | `uint` | Interest rate as an integer percentage. Example: 5 for 5%. |
| `param_3` | `ushort` | Period in months for which interest is calculated. Example: 12 for one year. |
| `param_4` | `undefined4 *` | Output parameter to store the calculated interest amount in cents. Example: 5000 for $50.00. |

**Findings from exploration:**

- 
- Returns 0 on success with calculated interest in param_4; probe with param_1=10000, param_2=500, param_3=12 returned 0.
  - Working call: `{"param_1": 10000, "param_2": 500, "param_3": 12}`

---

## `CS_GetAccountBalance`

Retrieves the account balance for a given customer ID. Returns 0 on success with the balance in param_2.

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `param_1` | `byte *` | Customer ID to look up. Example: 'CUST-001'. |
| `param_2` | `undefined4 *` | Output parameter to store the balance in cents. Example: 12345 for $123.45. |

**Findings from exploration:**

- Returns 0 on success with balance in param_2; probe returned 25000 for 'CUST-001', 5000 for 'CUST-002', and 120000 for 'CUST-004'. 'CUST-003' returned sentinel error indicating account locked.

---

## `CS_GetDiagnostics`

Retrieves diagnostic information about the DLL, including version, customer count, order count, and call statistics.

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `param_1` | `undefined *` | Output buffer to store diagnostic information. Example output includes version, customer count, and order count. |
| `param_2` | `uint` | Integer input parameter |

**Findings from exploration:**

- Returns 0 on success with diagnostic information in the output, including version=2.3.1, customers=4, orders=2, calls=303, and initialized=yes; all probes succeeded with identical output.
  - Working call: `{"param_2": 0}`

---

## `CS_GetLoyaltyPoints`

Retrieves the loyalty points for a given customer ID.

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `param_1` | `byte *` | Customer ID to look up. Example: 'CUST-001'. |
| `param_2` | `undefined4 *` | Output parameter to store the loyalty points. Example: 150. |

**Findings from exploration:**

- Returns 0 on success with loyalty points in param_2; probe returned 1450 for 'CUST-001', 320 for 'CUST-002', 75 for 'CUST-003', and 18750 for 'CUST-004'. All other probes resulted in access violations.

---

## `CS_GetOrderStatus`

Retrieves detailed information about an order, including customer ID, item count, subtotal, discount, total amount, and order status.

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `param_1` | `byte *` | Order ID to look up. Example: 'ORD-20040301-0042'. |
| `param_2` | `undefined *` | Output buffer to store order information. Example output includes customer ID, item count, and total amount. |
| `param_3` | `uint` | Integer input parameter |

**Findings from exploration:**

- Returns 0 on success with detailed order information in the output, including customer ID, item count, subtotal, discount, total amount, and order status; probe returned status=DELIVERED for ORD-20040301-0042 and status=SHIPPED for ORD-20040315-0117.
  - Working call: `{"param_3": 0}`

---

## `CS_GetVersion`

Retrieves the version of the DLL as a packed UINT value, which can be decoded into major, minor, and patch numbers.

**Findings from exploration:**

- Returns a packed UINT version number; probe returned 131841, decoded as version 2.3.1.

---

## `CS_Initialize`

Initializes the DLL and sets up its internal state for subsequent function calls.

**Findings from exploration:**

- Returns 0 on success, indicating successful initialization of the DLL.

---

## `CS_LookupCustomer`

Retrieves detailed customer information including name, email, phone, balance, loyalty points, tier, and status when provided with a valid customer ID.

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `param_1` | `byte *` | Customer ID to look up. Example: 'CUST-001'. |
| `param_2` | `undefined *` | Output buffer to store customer information. Example output includes name, email, and balance. |
| `param_3` | `uint` | Integer input parameter |

**Findings from exploration:**

- Returns 0 on success with detailed customer information in the output; probe with param_1='CUST-001' returned id=CUST-001, name=Alice Contoso, email=alice@contoso.com, phone=555-0101, balance=$250.00, points=1450, tier=Gold, status=ACTIVE.
  - Working call: `{"param_3": 0}`

---

## `CS_ProcessPayment`

Processes a payment for a given customer ID and amount. Returns 0 on success.

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `param_1` | `byte *` | Customer ID for whom the payment is being processed. Example: 'CUST-001'. Must correspond to an active customer. |
| `param_2` | `uint` | Payment amount in cents. Example: 5000 for $50.00. Must be a positive integer. |

**Findings from exploration:**

- Returns 0 on success when param_1 is 'CUST-001' and param_2 is 0; all other probes returned sentinel error codes indicating write denied.
- 

---

## `CS_ProcessRefund`

Processes a refund for a given customer and order ID, returning a refund amount in cents in param_4.

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `param_1` | `byte *` | Input string — e.g. 'CUST-001' or 'ORD-20040301-0042' |
| `param_2` | `uint` | Integer input parameter (e.g. 0,) |
| `param_3` | `undefined8` | Parameter of type undefined8 |
| `param_4` | `uint *` | Output — receives integer result |

**Findings from exploration:**

- Returns 0 on success with refund amount in param_4; probe with param_1='CUST-001', param_2=0, param_3='ORD-20040301-0042' returned 555819869 cents (or $5558.19).
  - Working call: `{"param_2": 0, "param_3": "ORD-20040301-0042"}`

---

## `CS_RedeemLoyaltyPoints`

Attempts to redeem loyalty points for a given customer ID and point amount, but fails with sentinel error codes for write denied or account locked.

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `param_1` | `byte *` | Customer ID for whom the points are being redeemed. Example: 'CUST-001'. Must correspond to an active customer. |
| `param_2` | `uint` | Number of points to redeem. Example: 50. Must be less than or equal to the customer's current points balance. |
| `param_3` | `uint *` | Output parameter to store the updated loyalty points balance. Example: 100 after redemption. |

**Findings from exploration:**

- 

---

## `CS_UnlockAccount`

Unlocks a customer account based on the provided customer ID and current status.

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `param_1` | `byte *` | Customer ID of the account to unlock. Example: 'CUST-001'. |
| `param_2` | `byte *` | Current status of the account. Example: 'LOCKED'. |

**Findings from exploration:**

- Returns 0 on success when param_1 is 'CUST-001' and param_2 is 'LOCKED'; all other probes returned sentinel error codes indicating null argument or access violation.
- 
- Returns 0 on success when param_1 is 'CUST-001' and param_2 is 'LOCKED'; all other probes returned sentinel error codes indicating null argument or access violation.

---

## `entry`

This function is not found in the DLL and does not execute successfully.

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `param_1` | `HINSTANCE__ *` | DLL instance handle (Windows DllMain param) |
| `param_2` | `ulong` | Integer input parameter |
| `param_3` | `void *` | Output buffer — receives result data (omit from call; auto-allocated) |

---
