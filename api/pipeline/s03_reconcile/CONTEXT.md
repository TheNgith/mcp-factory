# S-03: Post-Probe Reconciliation — Model Context Description

## What the model sees

### Phase 4: Reconciliation
- No LLM calls. Deterministic scan of probe log vs findings to fix mismatches
  (e.g., a function that returned 0 in the probe log but was recorded as "error")

### MC-3: Write Failure Analysis
- No LLM calls. Heuristic pattern classification:
  - Uniform sentinel across all write functions → init problem
  - Mixed sentinels → individual function problems
  - Single write function failing differently → specific blocker
- MC-3 may trigger targeted unlock attempts using _execute_tool directly

### Phase 5: Sentinel Catalog
- No LLM calls. Promotes high-confidence sentinel codes to vocabulary.

## What the model does NOT see
- N/A — no LLM calls in this stage

## Important: write_unlock_block as downstream context
MC-3's output (the write_unlock_block string) IS model context for all subsequent
stages. If MC-3 cracks write-unlock, it sets:
  "WRITE MODE ACTIVE (resolved at mc3-post-reconcile): ..."
This text is injected into LLM prompts for stages S-04 through S-06.

## Checkpoint artifacts
- `reconciliation_report.json` — mismatches found and fixed
- `mc3_decision.json` — failure classification and any unlock attempts
- `sentinel_catalog.json` — promoted sentinel evidence
