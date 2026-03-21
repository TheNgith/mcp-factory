# Explore Engine — Technical Deep Dive

The explore engine is the core differentiator — it's an LLM-driven autonomous agent that probes undocumented functions to learn their behavior without source code.

---

## How It Works (per function)

1. **Sentinel calibration** — calls the function with known-bad inputs to establish baseline error codes
2. **Boundary probing** — systematically varies parameters to find valid input patterns
3. **Vocabulary learning** — accumulates domain knowledge (ID formats, error codes, value semantics) across functions
4. **Finding recording** — saves successful call patterns with semantic descriptions
5. **Deterministic fallback** — for simple init/getter functions, tries a zero-arg call pattern
6. **Gap detection** — identifies functions that need clarification and generates targeted questions
7. **Schema synthesis** — rewrites raw Ghidra parameter names (`param_1`) to semantic names (`customer_id`) based on probe evidence

---

## Configuration Profiles

| Profile | Rounds | Tool Calls | Use Case |
|---------|--------|-----------|----------|
| dev | 3 | 5 | Fast iteration |
| stabilize | 5 | 10 | Quality runs |
| deploy | 8 | 15 | Production |

---

## Key Source Files

| File | Role |
|------|------|
| `api/explore.py` | Orchestrator — per-function probe loop, ground-truth overrides, checkpoint persistence |
| `api/explore_phases.py` | Sentinel calibration, boundary probing strategies |
| `api/explore_vocab.py` | Vocabulary accumulation (IDs, error codes, value semantics) |
| `api/explore_prompts.py` | LLM prompt construction for each probe phase |
| `api/explore_gap.py` | Gap question generation when probing is inconclusive |
| `api/explore_helpers.py` | Shared utilities (JSON parsing, finding merges) |

---

## Data Flow

See [WORKFLOW.md](WORKFLOW.md) for the full pipeline flowchart showing how explore feeds into schema synthesis and MCP generation.

See [PIPELINE-COHESION.md](PIPELINE-COHESION.md) for the inter-stage data flow analysis and the D-1 through D-11 bug resolution history.
