# S-06: Gap Resolution — Model Context Description

## What the model sees

### Phase 8: Gap Resolution
- **All accumulated context**: findings, vocab, sentinels, invocables,
  write_unlock_block, verification results, MC decisions
- **Per-function**: Same LLM agent setup as S-02 but with RICHER context
  (everything discovered so far is in the system message)
- LLM task: "This function previously failed. Here's everything we know now.
  Try different approaches to make it return 0."

### MC-6: Final Comprehensive Unlock
- **All MC decisions**: MC-3, MC-4, MC-5 reasoning and outcomes
- **Decompiled C code**: For ALL remaining failing functions
- **Full probe log**: Every attempt made across the pipeline
- **Verification results**: What succeeded and what failed in S-05
- LLM task: Code reasoning on decompiled C for remaining failures,
  with all prior knowledge as context. This is the most context-rich
  stage in the pipeline.

## What the model does NOT see
- How many pipeline runs have been done (no meta-awareness)
- Checkpoint system internals
- Bridge implementation details

## Key insight
MC-6 is the "last chance" stage. It has the MOST context of any stage
because it runs after everything else. This is where autonomous unlock
discovery typically succeeds — the LLM has seen all the sentinel codes,
all the verification results, and all the prior MC reasoning.

## Checkpoint artifacts
- `gap_resolution_log.json` — per-function retry outcomes
- `mc6_decision.json` — comprehensive unlock attempts and reasoning
- `winning_init_sequence.json` — updated if MC-6 discovers new sequence
