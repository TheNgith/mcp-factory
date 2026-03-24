# S-01: Write-Unlock Probe — Model Context Description

## What the model sees

### Phase 1: Code Reasoning + Write-Unlock Probe
- **Decompiled C code**: Full Ghidra decompilation from `doc_comment` for each write function
- **Function signatures**: Parameter types, names, calling convention
- **Sentinel table**: Named error codes from S-00 (e.g., 0xFFFFFFFE = "null/invalid argument")
- **Vocabulary**: Known ID formats (CUST-###, ORD-###), value semantics
- **Static analysis strings**: Embedded string evidence from binary
- **Prior winning init sequence**: If warm/hot start, the exact sequence that worked before
- LLM task: "Analyze this decompiled C code. What unlock mechanism does it use?
  XOR checksums? Magic values? Required call sequences? Generate specific inputs."

## What the model does NOT see
- Other stages' outputs (synthesis, gap resolution, MC decisions)
- Previous probe attempt results for individual functions
- The bridge's DLL caching behavior
- How ctypes encodes strings (the model proposes string values, the pipeline handles encoding)

## Key insight
The decompiled C in `doc_comment` is the PRIMARY source for autonomous unlock discovery.
For CS_UnlockAccount, it contains `if (bVar5 == 0xa5)` — the XOR checksum target.
The LLM must identify this pattern and propose appropriate input strings.

## Checkpoint artifacts
- `write_unlock_probe.json` — full probe sequence and outcomes
- `winning_init_sequence.json` — the initialization sequence that activates write mode
- `code_reasoning_analysis.json` — LLM's analysis of decompiled code
