# S-00: Setup — Model Context Description

## What the model sees

### Phase 0.5: Sentinel Calibration
- **Export function list**: Names and signatures of all DLL exports
- **User hints**: Free-text context from job creation (e.g., "contoso customer service DLL")
- **Prior sentinel table**: If warm start, sentinel codes from prior run
- **Raw return values**: Results from calling each function with null/zero args
- LLM task: "Assign a SHORT plain-English meaning (3-8 words) to each of these 32-bit return codes"

### Phase 0a: Vocab Seed
- No LLM call. Deterministic seeding from user hints, prior vocab, and calibrated sentinels.

### Phase 0b: Static Analysis
- No LLM call. Binary tooling (strings, IAT imports, PE metadata).

## What the model does NOT see
- Raw DLL binary bytes
- Other DLLs in the system
- Bridge/executor implementation
- Previous run's full probe log (only carried-forward findings and vocab)

## Checkpoint artifacts
- `sentinel_calibration.json` — {hex_code: "human meaning"} map
- `vocab.json` — {id_formats: [...], value_semantics: {...}, ...}
- `static_analysis.json` — full binary analysis result
- `dll_strings.json` — embedded string evidence
