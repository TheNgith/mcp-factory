# MCP Factory — MVP Thesis & Business Plan

> Synthesized 2026-03-20 from conversations with Claude Opus and Sonnet.
> This is the definitive reference for what the MVP is, who it's for, and what it's worth.

---

## The One-Sentence Pitch

**"Upload your DLL, get a tested Python wrapper back."** Nobody sells this today.

---

## MVP Definition (per brother's framing)

The MVP is three deliverables generated entirely by the pipeline — zero human authoring:

### Component 1 — Enriched Schema JSON

- Every exported function has a corresponding entry
- Correct semantic parameter names (not `param_1` — actual names like `customer_id`, `amount_cents`)
- Correct parameter types (`int`, `char*`, `DWORD`, output buffer flag)
- Correct return value interpretation (0 = success, sentinel codes mapped)
- Correct calling convention (`stdcall` vs `cdecl`)
- Dependency initialization order (`CS_Initialize` before `CS_ProcessPayment`)
- Known working call example with proven arguments
- Coverage breakdown: `verified` / `inferred` / `unprobeable`

### Component 2 — Python Wrapper (`client.py`)

- Clean typed methods, one per exported function
- Docstrings derived from vocab
- Error code handling built in
- Initialization order enforced automatically

### Component 3 — Validation Report

- Function-by-function comparison: direct ctypes call vs wrapper output
- Every match = verified 1:1 with original DLL behavior
- Explicit list of unverified functions with explanation of why
- This is the commercial differentiator: "We don't ask you to trust us. We prove it."

---

## What "1:1 Schema" Means — And Its Limits

**1:1 means**: for every function the pipeline can verify, the wrapper produces identical output to a direct DLL call with the same inputs. Proven by running both paths and comparing.

**1:1 does NOT mean**: 100% coverage on every DLL. Some functions are genuinely unprobeable without additional context:

| Category | Why it's hard | Solvable? |
|----------|---------------|-----------|
| Struct pointers (`void*`, `MYSTRUCT*`) | Unknown memory layout | Partially — Ghidra recovers some layouts from memory access patterns |
| Callback function pointers | Need to know expected signature | Rarely without docs |
| Opaque handles / deep state chains | Handle from A feeds B feeds C | Partially — pipeline already handles init→use patterns |
| Output buffer pre-allocation | Need correct buffer size | Often — NULL-first pattern or probing increasing sizes |
| Large flag fields (`DWORD flags`) | 32-bit combinatorial space | Only for small enum-like subsets |

**The honest framing**: "1:1 for the functions we can verify, with an honest report showing which ones we couldn't."

---

## Coverage Expectations — Typical 1995-2015 Business DLLs

| Function type | Coverage | Est. share of typical business DLL |
|---------------|----------|------------------------------------|
| Simple scalar params (`int`, `DWORD`, `char*`) | ✅ Verified | 60-70% |
| Known handle chains (init → use) | ✅ Verified | 10-15% |
| Output buffers with NULL-first pattern | ✅ Verified | 5-10% |
| Simple structs Ghidra can recover | ⚠️ Inferred | ~5% |
| Small enum spaces (brute-forceable) | ⚠️ Inferred | ~3% |
| Complex struct pointers | ❌ Unprobeable | ~5% |
| Callback function pointers | ❌ Unprobeable | ~2% |
| Large flag fields | ❌ Unprobeable | ~2% |

**Baseline MVP coverage: 75-85% verified** before touching any hard cases.

Post-MVP improvements (struct recovery + buffer size probing) push to **88-90%**.

---

## Target Market

### Verticals with the most pain

- **Banks & insurance** — COBOL backends with C DLL bridges, regulatory compliance prevents rewrites
- **Manufacturing / SCADA** — equipment control DLLs from vendors that went bankrupt
- **Government / defense** — custom procurement systems built on expired contracts
- **Healthcare** — HL7 interface DLLs, lab equipment drivers

### The common scenario

DLLs from 1995-2015. Written in C/C++/Delphi/VB6. Original developers gone. Documentation incomplete or lost. The DLL works, the business depends on it, nobody fully understands it.

### Two tiers emerge

| Tier | Description | Typical coverage | Price range |
|------|-------------|------------------|-------------|
| **Grey box** | DLL has symbols but no documentation | 85-90% | $3-8k engagement |
| **True black box** | Stripped DLL, no symbols, Ghidra output mostly useless | 60-75% | $15-50k engagement |

The true black box tier is where the real money lives. A company stuck on a stripped DLL for five years isn't comparing you to a $5k option — they're comparing you to a $200k rewrite.

---

## Competition

**There are no direct product competitors.** The alternatives are:

| Alternative | What it does | Limitation |
|-------------|-------------|------------|
| Hire RE consultant ($200-400/hr) | Manual reverse engineering | Takes weeks, scales linearly with cost |
| IDA Pro / Ghidra | Disassembly | Shows assembly, doesn't generate callable schemas or test params |
| Binary Ninja | Same category | Same limitation |
| API doc generators (Doxygen, Swagger) | Auto-document source code | Requires source code — useless for legacy binaries |
| ctypes/cffi | Build Python wrappers | Requires you to already know the signatures |

The pipeline's value is 100% in the agentic enrichment loop: picking up where Ghidra gives up.

---

## The True Moat

> "Give me a DLL where Ghidra outputs almost nothing useful and I'll still tell you what it does."

That is the unsolved problem. That is the novel system architecture. The value is in:

- Sentinel calibration building error vocabulary from scratch
- Synthetic probing discovering parameter types empirically
- Vocab accumulation inferring semantic meaning from behavior
- Cross-function learning getting smarter over time

A competitor can't replicate this by reading the GitHub. They'd have to rebuild the entire enrichment methodology.

---

## Pricing Models

| Model | Price | When to use |
|-------|-------|-------------|
| Per-analysis report | $2,000-10,000 | Client uploads DLL, gets schema + wrapper + report |
| Platform subscription | $500-2,000/month | Self-service access for companies with ongoing needs |
| Enterprise contract | $50,000-200,000/year | Companies with dozens of legacy DLLs, includes support |

First customer: per-analysis at $3-5k. You're saving 40-80 hours of human RE work ($8-16k value).

---

## Sequencing to MVP

1. ✅ Pipeline: discovery + enrichment + probing loop — **working**
2. ✅ Pipeline: semantic param naming, sentinel interpretation, known working calls — **working**
3. ✅ Pipeline: dependency initialization order — **partially working**
4. 🔲 Finish contoso_cs demo with 100% coverage (realistic for this DLL)
5. 🔲 Wrapper generator (`client.py`) — P3-2 in ROADMAP
6. 🔲 Backwards compatibility validator (direct ctypes vs wrapper comparison)
7. 🔲 Coverage report with verified / inferred / unprobeable breakdown
8. 🔲 Run one non-authored DLL cold (the credibility demo)
9. 🔲 Find one pilot customer for paid proof-of-concept

### The credibility test

contoso_cs proves the pipeline works end-to-end. But the demo that closes pilots is running a DLL you've never seen before — a true black box with stripped symbols — and showing 60-75% verified coverage where every other tool failed.

---

## What This Is Not

- Not 100% coverage on every DLL. Honest about what it can't do.
- Not replacing reverse engineers. Doing 70-90% of their work in minutes so they focus on the hard 10-30%.
- Not competing with Ghidra. Picking up where Ghidra gives up.
