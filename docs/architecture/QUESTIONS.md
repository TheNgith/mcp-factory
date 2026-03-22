# QUESTIONS

Purpose: open-ended questions that should drive the next architecture and
implementation decisions. Each question includes a suggested answer direction
so planning stays actionable.

## 1) Coverage Ceiling: What is the real blocker class for each unresolved function?

Why this question matters:
- "8/13" is only useful if unresolved functions are classified by root cause,
  not just counted as failures.

Suggested answer direction:
- Build a per-function blocker taxonomy:
  1. state-dependent handle chain,
  2. missing domain value constraints,
  3. argument-shape ambiguity,
  4. structural ceiling (likely unprobeable in MVP).
- Define one primary blocker class per unresolved function.

## 2) Progression: What exact changes should move dev runs from 8/13 to 10+?

Why this question matters:
- Without explicit progression targets, retries become random tuning.

Suggested answer direction:
- Define a 3-step progression plan with measurable milestones:
  1. S-02 argument quality improvements,
  2. S-06 structural classifier improvements,
  3. clarification-tier enablement.
- Set expected gain per step and rollback criteria.

## 3) S-02 Arguments: Should we keep a generic DLL argument list?

Why this question matters:
- Generic lists provide coverage, but uncontrolled use causes repetition.

Suggested answer direction:
- Yes: maintain a generic argument catalog.
- Add adaptive ranking so model picks top-N candidates by evidence score.
- Block repeated calls unless one mutation dimension changes.

## 4) Sentinel Discovery: How should unknown return codes become durable knowledge?

Why this question matters:
- The long-term goal requires autonomous sentinel interpretation.

Suggested answer direction:
- Add confidence-scored sentinel lifecycle:
  1. observed,
  2. correlated with failure class,
  3. behavior-validated,
  4. promoted to stable vocabulary.
- Require at least two independent observations before promotion.

## 5) Clarification Tier: When should operator Q&A be enabled?

Why this question matters:
- Clarification should be strategic, not a fallback every time probes fail.

Suggested answer direction:
- Enable only after bounded retries show no progress and stable failure pattern.
- Ask one constrained question per function with direct mapping to retry logic.
- Record accepted answers in machine-readable format for future runs.

## 6) Clarification Quality: What makes a clarification question valid?

Why this question matters:
- Poorly formed questions produce vague answers that cannot improve probes.

Suggested answer direction:
- Require each question to include:
  1. target function,
  2. observed failure evidence,
  3. one missing fact,
  4. expected answer format,
  5. retry policy that uses the answer.

## 7) Instrumentation: Which warn-tier transitions should be closed first?

Why this question matters:
- Not all instrumentation has equal ROI.

Suggested answer direction:
- Prioritize T-04 and T-05 first because they govern probe quality confidence.
- Emit explicit artifacts:
  1. probe_user_message_sample.txt,
  2. argument-selection reason fields,
  3. static-hints digest matching evidence.

## 8) Documentation Scope: How much thought should be documented?

Why this question matters:
- Over-documenting slows shipping; under-documenting repeats mistakes.

Suggested answer direction:
- Document only decisions that change behavior, gates, or evaluation semantics.
- Keep this rule:
  1. if it affects code path, promotion gate, or KPI target, document it,
  2. if it is temporary brainstorming without a decision, keep it in session notes.

## 9) Beyond MVP: What does 0/13 -> 13/13 without hints require?

Why this question matters:
- This is the north-star system, not current MVP scope.

Suggested answer direction:
- Define a post-MVP architecture track with explicit capabilities:
  1. dynamic state graph reconstruction,
  2. typed memory/struct inference,
  3. active experiment planner across model ensembles,
  4. autonomous sentinel semantics induction.
- Track this as a separate roadmap lane so MVP delivery stays on schedule.

## 10) Multi-model Parallelism: What should be parallelized now vs later?

Why this question matters:
- Parallel runs can improve evidence diversity but may create merge noise.

Suggested answer direction:
- Near-term: run independent model/profile sweeps in parallel and compare scores.
- Mid-term: merge only machine-truth artifacts with confidence weighting.
- Long-term: use cross-model consensus as a proposal, never automatic truth.

## 11) Natural Next Step: Code now, or questions first?

Why this question matters:
- The repo is stable enough that one targeted code step has high value.

Suggested answer direction:
- Do both, in order:
  1. finalize acceptance criteria in docs (done in this file + architecture docs),
  2. implement one concrete code increment: S-02 argument-candidate ranking and
     selection-reason logging,
  3. re-run A/B strict captures and measure uplift.

## 12) Success Criteria: What should prove we are moving toward the north star?

Why this question matters:
- Progress must be measured by outcomes, not effort.

Suggested answer direction:
- Track a compact KPI set:
  1. verified coverage delta by profile,
  2. unresolved-to-clarified conversion rate,
  3. warn-tier transition count trend,
  4. deterministic hard-fail consistency across repeats.
- Require KPI improvement before expanding system complexity.
