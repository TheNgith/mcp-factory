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

## 13) Front-and-Center Metadata: What should be easiest for AI to read first?

Why this question matters:
- Distributed metadata increases analysis time and creates avoidable ambiguity.

Suggested answer direction:
- Standardize a compact run-manifest block for every saved session and A/B compare.
- Put these fields at top-level in one JSON object:
  1. run identity: `job_id`, `component`, `saved_at`, `commit`, `commit_msg`,
  2. runtime controls: `mode`, `model`, `max_rounds`, `max_tool_calls`,
     `gap_resolution_enabled`, `clarification_questions_enabled`,
  3. input identity: `hints_sha256`, `use_cases_sha256` plus short previews,
  4. contract gate: `contract_valid`, `hard_fail`, `capture_quality`,
  5. cohesion totals: stage/transition pass-fail-warn-partial counts,
  6. outcome totals: `functions_success`, `functions_error`, unresolved function names,
  7. determinism signals for A/B: `deterministic`, `differences`.

Implementation note:
- Keep full detailed artifacts for deep diagnosis, but require this compact
  manifest to be present for every run so models can triage in one read.

## 14) Tournament Strategy: Should a production run select the best evidence per stage across parallel sessions?

Why this question matters:
- A real SaaS product for SMBs cannot ship a single-run result when any individual
  run may miss functions due to budget exhaustion, model variance, or context-window
  pressure. Running N parallel sessions with different profiles (hint variants, model
  temperatures, tool-budget settings) and then selecting the strongest evidence at each
  stage before synthesizing the final output is the most natural path to a reliable,
  shippable wrapper — but only if "best" can be defined precisely enough to automate
  the selection. Without a concrete scoring rubric, tournament selection just becomes
  manual cherry-picking, which does not scale as a product.

Suggested answer direction:
- Yes: run N parallel sessions and apply a tournament selection pass before synthesis.
  Define "best" at four granularities:

  1. Per-function winner (which session's probe result ships for this function):
     - Prefer `status=success` with `has_return=true` over success without a return.
     - Among multiple successes, rank by `confidence` score descending.
     - If no session succeeded, prefer the session with the richest failure evidence
       (most unique sentinel codes observed, most probe variant attempts).

  2. Per-stage artifact winner (which session's stage artifact becomes authoritative):
     - Prefer the session whose stage-level `capture_quality` is highest.
     - As a tiebreaker, prefer the session with the fewest `warn`/`partial` cohesion
       transitions (i.e. the session that moved most stage gates to `pass`).

  3. Per-probe argument quality (which session's argument selection is most credible):
     - Rank argument source quality in descending order:
         a. `vocab.id_formats` (real extracted string IDs — highest signal),
         b. `static_analysis.binary_strings`,
         c. `heuristic.*` sources,
         d. `default.*` / fallback sources (lowest signal, use only if no other
            session has higher-quality coverage for this function).
     - This ensures the final wrapper is backed by the strongest available evidence
       rather than whichever run happened to finish first.

  4. Determinism as a tiebreaker:
     - When two sessions produce otherwise equal evidence, prefer the session where
       A/B legs of the same call agree (`deterministic: true`).
     - Deterministic results indicate the system is reliable enough to trust without
       a clarification tier.

- Implementation sketch for MVP:
  1. After all N parallel sessions complete, build a per-function evidence table
     indexed by `function_name × session_id`.
  2. Apply the four-tier ranking above to assign one authoritative session per
     function.
  3. Re-run the synthesis and schema-generation step using only the winning evidence
     rows — the output `client.py` and `enriched_schema.json` are then composites
     drawn from the best-performing session per element, not a single session's output.
  4. Record provenance: every synthesized field should carry a `source_run_id` and
     `selection_reason` so the customer (or a support engineer) can audit why any
     particular probe result was chosen.

- Connection to existing questions:
  - Q10 ("Multi-model Parallelism") asked about parallelizing runs and merging
    artifacts. Q14 extends that into a full selection architecture: it answers *what*
    to merge, *how* to rank candidates, and *what provenance trails* the product must
    emit. Q10 is a prerequisite; Q14 is the product-level answer.
  - Q12 ("Success Criteria") KPIs should be measured against the *tournament output*,
    not individual session outputs, to reflect real product quality.
  - Q13 ("Front-and-Center Metadata") compact manifest fields should be emitted per
    session so that the tournament pass can compare sessions in a single structured
    read without re-parsing full artifact trees.
