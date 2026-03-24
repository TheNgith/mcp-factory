# S-07: Finalization — Model Context Description

## What the model sees

### Phase 9: Behavioral Spec
- **All findings**: Final per-function results
- **API reference**: Synthesis document from S-04
- LLM task: "Generate a typed Python behavioral specification (dataclasses,
  function stubs, return types) from these findings."

### Phase 10: Harmonization
- No LLM calls. Deterministic scan to upgrade error findings
  that have direct-probe success evidence in the probe log.

### Finalize
- **Vocabulary**: Full accumulated vocab
- LLM task (small): "Write ONE sentence describing what this DLL does."
  (25 words max, specific domain terms)

## What the model does NOT see
- Pipeline performance metrics
- Checkpoint system state
- How the session will be saved/archived

## Checkpoint artifacts
- `behavioral_spec.py` — typed Python specification
- `harmonization_report.json` — deterministic fixes applied
- This is the terminal checkpoint. Pipeline is complete.
