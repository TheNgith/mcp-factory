# S-02: Probe Loop — Model Context Description

## What the model sees per function call

### System Message (shared across all functions)
- **Invocable registry**: Names, signatures, parameter types for ALL exported functions
- **Prior findings**: What's already been discovered about other functions
  (builds up as probing progresses through the function list)
- **Sentinel table**: Named error codes from S-00 calibration
  (e.g., 0xFFFFFFFE = "null/invalid argument", 0xFFFFFFFC = "account locked")
- **Vocabulary**: ID formats, value semantics, domain terminology from S-00
- **Static analysis hints**: Embedded strings, IAT imports, binary metadata from S-00
- **Write-unlock block**: If S-01 succeeded, a note saying write mode is active
  with the sequence that was used
- **User hints/use cases**: Whatever the user provided at job creation

### Per-Function Context (unique to each function being probed)
- **Function signature**: Name, param types, return type, calling convention
- **Decompiled C code**: Full Ghidra decompilation from `doc_comment` field
- **Parameter descriptions**: Semantic names and type info from static analysis
- **Criticality classification**: read/write/utility/required_first
- **Dependencies**: Which functions must run first (e.g., depends_on: [CS_Initialize])
- **Deterministic fallback args**: Pre-computed probe arguments from heuristic analysis

### What the model does NOT see
- Raw DLL binary bytes
- Other functions' decompiled code (only the target function's)
- Previous probe attempt details for OTHER functions (only current function's history
  within the same agent loop)
- Bridge/executor implementation details
- How the checkpoint system works

## Checkpoint artifacts
- Per-function entries: `{fn_name: {status, working_call, finding, probe_count, ...}}`
- A function is checkpoint-eligible when status="success" AND working_call is non-null
- Checkpointed functions are SKIPPED in subsequent focused runs
