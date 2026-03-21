# Alignment Notes

Use this file only when implementation is knowingly out of alignment with docs/architecture so we can return and close the gap.

## 2026-03-21

### Pending: Real-run blocker validation remains open
- Scope: `B1 — Real-run contract verification` from `docs/architecture/CAUSALITY-ARTIFACT-LAYER-PLAN.md`.
- Status: Executed with real captures, but acceptance still not met due live deployment artifact gap.
- Evidence captured:
  1. Explore-only real captures (repeat pair):
     - `job_id=44fb7051`
     - `sessions/2026-03-21-48db9b7-b1-live-explore-only-a-2`
     - `sessions/2026-03-21-48db9b7-b1-live-explore-only-b`
  2. Answer-gaps-triggered real captures (repeat pair):
     - `job_id=cfd26cf5`
     - `sessions/2026-03-21-48db9b7-b1-live-answer-gaps-a`
     - `sessions/2026-03-21-48db9b7-b1-live-answer-gaps-b`
  3. Determinism result on unchanged inputs:
     - Explore-only pair: deterministic (`valid=false`, `hard_fail=false`, identical missing set)
     - Answer-gaps pair: deterministic (`valid=false`, `hard_fail=false`, identical missing set)
  4. Missing required contract files in all four captures:
     - `stage-index.json`
     - `transition-index.json`
     - `cohesion-report.json`

- Why blocker remains open:
  - Blocker 1 acceptance requires all four contract files present + parseable and T-01..T-16 verification from `transition-index.json`.
  - Live snapshots currently provide `session-meta.json` but not the other three contract files.

- Required follow-up:
  1. Deploy runtime/API contract-artifact emitter and snapshot packaging changes to the live environment.
  2. Re-run one explore-only + one answer-gaps capture in strict mode.
  3. Verify `transition-index.json` includes `T-01..T-16` with `status` and `severity`.
  4. Re-run unchanged capture and confirm deterministic `cohesion-report.json.gates.hard_fail`.

- Close condition: Strict-mode captures from live deployment contain all required contract artifacts and pass Blocker 1 acceptance checks.
