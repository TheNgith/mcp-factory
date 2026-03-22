# MVP Transition Automation Execution Plan

Date: 2026-03-21
Owner: Copilot implementation pass
Status: Active

Companion findings log:
- docs/architecture/MVP-TRANSITION-AUTOMATION-FINDINGS.md

## What You Are Asking Me To Do

You want an implementation-focused push to prove MVP readiness through repeatable, auditable transition checks, not only manual spot checks.

Primary objective:
- Automate verification for transition contracts T-04, T-05, T-14, T-15.
- Run A/B evaluation in a repeatable way.
- Emit one compact readiness result (pass or fail).
- Wire CI in non-blocking mode first so signal is visible before enforcing hard gates.
- Document evidence and outcomes each run.

## MVP Problems To Solve

The current strict A/B artifacts show stable parity, but readiness proof is still weak in specific transitions.

Target transitions:
1. T-04 static_analysis_to_probe_prompt
- Current issue: warn due to insufficient prompt instrumentation evidence.

2. T-05 static_ids_to_fallback_args
- Current issue: warn due to inability to prove static IDs reached fallback args.

3. T-14 findings_to_chat_prompt
- Current issue: partial because chat context artifacts are often missing.

4. T-15 vocab_to_chat_prompt
- Current issue: partial because chat context artifacts are often missing.

Why this matters for MVP:
- MVP is not only behavior output quality; it is also reproducibility and evidence quality.
- These four transitions are the shortest path from "works" to "provably works".

## Scope Split (Now vs Manual)

Implement now:
- Instrumentation and artifact chain for T-04/T-05/T-14/T-15.
- Automated tests that fail on regression with actionable messages.
- Single-command A/B plus evaluation workflow.
- Machine-readable and human-readable readiness summaries.
- CI non-blocking wiring.

Keep manual for now:
- Human semantic quality checks for T-12/T-13.
- Exploratory provider drift investigations.

## Delivery Plan

Phase 1: Transition gate evaluator and tests
1. Add a transition gate test module under tests.
2. Validate required evidence artifact presence.
3. Validate transition statuses for T-04/T-05/T-14/T-15 against expected contract.
4. Fail with explicit missing-artifact or bad-status messages.

Phase 2: Runner plus evaluator integration
1. Extend existing runner flow instead of duplicating logic.
2. Execute A/B runs.
3. Evaluate transition outcomes for both legs.
4. Write compact readiness JSON and markdown.
5. Print one concise pass/fail line for operator use.

Phase 3: CI non-blocking integration
1. Add workflow step to execute transition readiness check.
2. Mark as non-blocking initially (warning/report mode).
3. Preserve artifacts so failures are diagnosable from CI output.
4. Include clear toggle to move to blocking mode after stability window.

Phase 4: Run report
1. Add short run report doc in repo with:
- date and time
- exact commands used
- pass/fail per transition
- artifact locations
- recommended next action

## Expected Artifacts

Per run:
- batch or ab compare output
- transition readiness summary JSON
- transition readiness summary markdown
- evidence pointers to transition-index and supporting artifacts

Likely storage locations:
- sessions/_runs/<run-folder>/
- sessions/index.json append row for compact traceability

## Acceptance Criteria Mapping

1. One command runs full A/B plus evaluation
- Delivered via runner integration command surface.

2. Tests include transition contract checks
- Delivered via tests module for T-04/T-05/T-14/T-15.

3. CI non-blocking check visible
- Delivered via workflow step in report-only mode.

4. Compact readiness summary generated and saved
- Delivered via JSON plus markdown outputs.

5. Final report quality
- Changed files list
- Rerun commands
- Current pass/fail and known risks

## Execution Defaults

Use these unless unavailable:
- api_url: https://mcp-factory-pipeline.icycoast-8ddfa278.eastus.azurecontainerapps.io
- api_key: from environment or placeholder
- mode: dev
- model: gpt-4o
- max_rounds: 2
- max_tool_calls: 5
- gap_resolution_enabled: true
- append_index: true

## Risks and Controls

Risk 1: Over-bundling changes reduces diagnosis clarity
- Control: keep checks focused on four transitions and emit per-transition verdicts.

Risk 2: Provider variability introduces noise
- Control: A/B parity and deterministic-vs-baseline reporting.

Risk 3: CI spam without action path
- Control: compact summary format and explicit toggle path from warn to block.

## Immediate Next Steps When Iteration Continues

1. Implement transition gate test module.
2. Add evaluator layer to runner path.
3. Add CI workflow report step.
4. Execute one real run and publish run report doc.

This plan is intended as the implementation contract for the next execution pass.
