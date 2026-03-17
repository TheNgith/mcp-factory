# contoso_cs.dll — Test Prompts

Copy and paste these prompts directly into the chat. One group at a time is enough for a quick smoke test. Run the full suite when comparing before/after a vocab or enrichment change.

Scoring is done in `TEST_RESULTS.md` inside each session folder (auto-created by `save-session.ps1`).

---

## GROUP 1 — Initialization & System State

**T01**
> What version of the contoso CRM system is running?

**T02**
> Is the CRM system currently initialized?

**T03**
> How many customers and orders are currently tracked?

---

## GROUP 2 — ID Format Compliance

**T04**
> Look up customer 7.

**T05**
> Get the account details for customer 42.

**T06**
> Process a refund for order ORD-20040301-0042 for customer CUST-001, refund amount $18.50.

**T07**
> Look up customer ABC.

---

## GROUP 3 — Amount & Value Encoding

**T08**
> Process a $25.00 payment for CUST-042.

**T09**
> Issue a $150.99 refund to CUST-007 for order ORD-20260315-0117.

**T10**
> What is the current balance for CUST-010? Display it in dollars.

**T11**
> Customer CUST-042 wants to redeem 500 points. Process the redemption.

---

## GROUP 4 — Account Status & Locked Flow

**T12**
> Customer CUST-003 says they can't log in. Diagnose and fix the problem.

**T13**
> Unlock the account for CUST-010 even though they're already active.

**T14**
> Process a $30 payment for CUST-003.

---

## GROUP 5 — Error Code Interpretation

**T15**
> I called CS_ProcessPayment and got back 4294967291. What does that mean and how do I fix it?

**T16**
> CS_UnlockAccount returned 4294967292. What happened?

**T17**
> CS_ProcessRefund returned an access violation. What went wrong?

**T18**
> Try to process a $50 payment for CUST-005 without initializing first.

---

## GROUP 6 — Customer Profile Fields

**T19**
> Show me the full profile for customer CUST-001.

**T20**
> What loyalty tier is CUST-015 on?

**T21**
> What email and phone number do we have on file for CUST-022?

---

## GROUP 7 — Multi-Step / Ordering

**T22**
> Customer CUST-042 wants to redeem 500 loyalty points and then process a payment of $25.00. Initialize the system first, check their status, redeem the points, then process the payment.

**T23**
> Customer CUST-019 placed order ORD-20260315-0117 for $99.00 but wants a full refund. Handle it end to end.

**T24**
> Process payments of $10 for CUST-001, $20 for CUST-002, and $30 for CUST-003 in sequence. Report any failures.

**T25**
> Redeem 200 points for CUST-008, but first make sure their account is in good standing.

---

## GROUP 8 — Edge Cases & Boundaries

**T26**
> Process a $0.00 payment for CUST-001.

**T27**
> Customer CUST-042 has 300 points. Redeem 500 points for them.

**T28**
> Look up LOCKED.

---

## Notes

- Scoring goes in `TEST_RESULTS.md` inside each session folder (auto-created by `save-session.ps1`)
- Most diagnostic tests: **T04, T06, T08–T11** (encoding issues invisible to the user but silently wrong at API level)
- Locked account regression group: **T12, T14, T25**
- Integration smoke test: **T22–T24** — if these pass, the happy path works end to end
