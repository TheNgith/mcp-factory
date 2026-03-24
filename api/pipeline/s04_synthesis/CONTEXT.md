# S-04: Synthesis — Model Context Description

## What the model sees

### Phase 6: API Reference Synthesis
- **All findings**: Complete per-function discovery results (status, working_call,
  descriptions, parameter semantics)
- **Vocabulary**: Full accumulated vocab with sentinel meanings
- **Sentinel table**: All calibrated error codes
- LLM task: "Generate comprehensive API reference documentation from these findings"

### MC-4: Init Sequence Clues
- No LLM calls. Regex parsing of the synthesis doc to find mentions of
  initialization modes, required call sequences, or function dependencies.

## What the model does NOT see
- Decompiled C code (not included in synthesis prompt — findings are the distilled view)
- Raw probe log entries
- MC coordinator decisions from S-03

## Checkpoint artifacts
- `api_reference.md` — full synthesis document
- `mc4_decision.json` — init sequence clues extracted from synthesis
