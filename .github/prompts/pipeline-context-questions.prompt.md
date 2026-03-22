---
agent: ask
description: Use the pipeline operating-model questions as a stage-by-stage execution rubric and output actionable decisions with artifact evidence.
---

Use this prompt when you need to decide what the AI should do at each pipeline stage and how to prove those decisions from machine artifacts.

Primary reference:

#file:../../docs/architecture/PIPELINE-CONTEXT-ENGINEERING-OPERATING-MODEL.md

## Objective

Drive the run toward deterministic, evidence-backed outcomes by answering stage questions with artifact-first reasoning.

## Working Method

1. Read machine artifacts first, transcript text second.
2. For each stage S-00 to S-07, answer the stage's primary questions.
3. For each answer, cite concrete artifact files that prove it.
4. Call out any missing artifacts as explicit blockers.
5. Classify each stage as `pass`, `warn`, `partial`, or `blocked`.

## Required Focus Transitions (MVP)

Always evaluate and report these transitions with explicit evidence:

1. `T-04` static_analysis_to_probe_prompt
2. `T-05` static_ids_to_fallback_args
3. `T-14` findings_to_chat_prompt
4. `T-15` vocab_to_chat_prompt

If any of these are not `pass`, provide:

1. the exact missing or weak artifact,
2. the smallest instrumentation or logic fix,
3. the next verification command.

## Output Format

Return markdown with these sections in order:

1. `Global Questions`
2. `Stage Decisions`
3. `Transition Verdicts (T-04/T-05/T-14/T-15)`
4. `Readiness Summary`
5. `Next Minimal Changes`

Use this template:

```markdown
## Global Questions
- known_machine_verifiable: ...
- unknown_probeable: ...
- structural_blocks: ...
- new_evidence_this_round: yes|no
- machine_consumable_outputs: yes|no

## Stage Decisions
### S-00
- verdict: pass|warn|partial|blocked
- answers:
  - Q1: ...
  - Q2: ...
- evidence:
  - path/to/artifact
- blockers:
  - ...

### S-01
...

## Transition Verdicts (T-04/T-05/T-14/T-15)
- T-04: pass|warn|partial|blocked
  - reason: ...
  - evidence: ...
  - smallest_fix: ...
- T-05: ...
- T-14: ...
- T-15: ...

## Readiness Summary
- overall: pass|fail
- mvp_risk: low|medium|high
- stop_or_continue: stop|continue

## Next Minimal Changes
1. ...
2. ...
3. ...
```

Rules:

1. Do not claim pass without machine artifact evidence.
2. Prefer smallest safe diffs over broad refactors.
3. If evidence is missing, mark `warn` or `partial`, never silent pass.
4. Keep recommendations bounded to one verification loop where possible.
