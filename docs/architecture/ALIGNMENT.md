# Alignment Notes

Use this file only when implementation is knowingly out of alignment with docs/architecture so we can return and close the gap.

## 2026-03-21

### CLOSED 2026-03-21: B1 — Real-run contract verification
- Scope: `B1 — Real-run contract verification` from `docs/architecture/CAUSALITY-ARTIFACT-LAYER-PLAN.md`.
- Status: **CLOSED — all acceptance criteria met.**

#### Previous blocker state (pre-deployment of commit 43de8df)
- Four earlier captures (`44fb7051`, `cfd26cf5` A/B pairs) all produced `contract_valid=false` — missing `stage-index.json`, `transition-index.json`, and `cohesion-report.json`.
- Explore-only pair was deterministic. Answer-gaps pair was deterministic. But none had the required contract artifacts.

#### Closure evidence (commit 43de8df, deployed 2026-03-21)
- Phase-8 gap-resolution A/B pair (mode=dev, gap_resolution_enabled=true, same hints/use-cases):
  - **Job A:** `a91aaf68` → `sessions/2026-03-21-43de8df-b1-live-answer-gaps-strict-a`
  - **Job B:** `ed79de1b` → `sessions/2026-03-21-43de8df-b1-live-answer-gaps-strict-b`
- Contract artifact status on both:
  - `session-meta.json` ✓, `stage-index.json` ✓, `transition-index.json` ✓, `cohesion-report.json` ✓
  - `contract_valid=true`, `hard_fail=false`, `capture_quality=complete`
- Transition summary (identical across A and B):
  - `transition_pass=9`, `transition_fail=0`, `transition_warn=2`, `transition_partial=2`, `transition_na=3`
  - `stage_pass=7`, `stage_fail=0`
  - `failed_transitions=[]`, `failed_stages=[]`
- Determinism: Both captures produce **bit-for-bit identical** `cohesion-report.json` totals.
- Gap resolution effectiveness: phase-8 retry improved 4→8 functions resolved (pre: 4 success / 9 error; post: 8 success / 5 error).
- T-12/T-13 `not_applicable` is correct: those transitions cover the user-domain-answers mini-session path, not the phase-8 automated retry path used here.

#### Notes on remaining warns/partials
- T-04 warn: static hints block present but `probe_user_message_sample.txt` not yet instrumented (low-severity observability gap, not a pipeline failure).
- T-05 warn: static IDs → fallback args propagation unverifiable from current artifacts.
- T-14/T-15 partial: chat-context diagnostic blobs (`chat_system_context_turn0.txt`) not present in these explore-only + gap-resolution runs (no chat stage triggered).

**B1 is closed. The strict answer-gaps path of the live pipeline is contract-cohesive.**
