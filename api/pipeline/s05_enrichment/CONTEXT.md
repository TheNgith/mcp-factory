# S-05: Enrichment + Verification — Model Context Description

## What the model sees

### Phase 7: Schema Backfill
- No LLM calls. Regex parsing of api_reference.md to extract parameter
  descriptions and backfill them into invocable schemas.

### Phase 7b: Verification
- No LLM calls. Deterministic execution of each "success" finding's
  working_call against the DLL to confirm it actually returns 0.

### MC-5: Read-to-Write Chaining
- No LLM calls. Heuristic matching:
  - If CS_GetAccountBalance("CUST-001") verified → "CUST-001" is confirmed valid
  - Chain this into CS_ProcessPayment(customer_id="CUST-001", ...)
  - Execute the chained call to see if it succeeds

## What the model does NOT see
- N/A — no LLM calls in this stage

## Important: DLL state persistence
Verification executes real DLL calls. With DLL caching on the bridge,
state from these calls persists. This means:
- CS_Initialize in verification sets up state for subsequent calls
- CS_ProcessPayment in verification earns loyalty points
- These side effects persist and can help MC-5's chained write attempts

## Checkpoint artifacts
- `backfill_changes.json` — schema fields updated from synthesis
- `verification_results.json` — per-function verification outcomes
- `mc5_decision.json` — chaining attempts and results
