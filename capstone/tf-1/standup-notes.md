# TF1 AI Ops Standup Notes

## 2026-06-24 - W11 Contract Freeze Prep

### Done

- Created AI Ops Jira backlog for the next implementation phase in project A0X.
- Clarified the shared CDO handoff boundary: CDO provides bounded evidence access; AIOps owns normalization, RCA, confidence, and output payloads.
- Updated contracts so the shared handoff is transport-neutral and does not require a specific team implementation detail.
- Added `docs/06_cdo_evidence_handoff.md` to explain where extra evidence comes from, where CDO can host it, and how AIOps consumes it.
- Confirmed the primary scenario data is the RCAEval subset, not synthetic-only data.
- Generated 9 RCAEval-derived evidence bundles across:
  - `critical-service-down`
  - `latency-degradation`
  - `noisy-false-alert`
- Documented that the local RCAEval subset contains metrics and injection time only; logs/traces/deploy/ownership/runbooks are supplemental records marked in `data_lineage`.
- Updated requirements, solution design, AI engine spec, eval report, ADRs, and contracts for W11 review.
- Deployed a W11 demo endpoint on AWS App Runner: `https://snpmtcwpys.us-east-1.awsapprunner.com`.
- Smoke-tested `GET /healthz` and `POST /v1/triage` against the deployed endpoint.

### Verification

- `python -m compileall app scripts`
- `python -m pytest tests -q`
- `python scripts/validate_datapack.py`
- `docker compose -f docker-compose.observability.yml config --quiet`
- `npm run build` in `report-ui`

### Blockers / Needs

- CDO needs to choose where to host the precomputed evidence bundles.
- Team members still need to pick Jira issues, move them through status, and comment commit/PR links daily.

## 2026-06-25 - Onsite Review Prep

### Bring To Review

- `capstone/tf-1/ai/docs/01_requirements.md`
- `capstone/tf-1/ai/docs/02_solution_design.md`
- `capstone/tf-1/ai/docs/03_ai_engine_spec.md`
- `capstone/tf-1/ai/docs/04_eval_report.md`
- `capstone/tf-1/ai/docs/05_adrs.md`
- `capstone/tf-1/ai/docs/06_cdo_evidence_handoff.md`
- `capstone/tf-1/ai/docs/07_w11_readiness_checklist.md`
- `capstone/tf-1/ai/contracts/telemetry-contract.md`
- `capstone/tf-1/ai/contracts/ai-api-contract.md`
- `capstone/tf-1/ai/contracts/deployment-contract.md`
- `capstone/tf-1/ai/contracts/observability-data-contract.md`

### Talking Points

- AIOps does not call customer applications directly for logs or metrics.
- Extra data comes from CDO-hosted observability/evidence paths.
- W11 MVP is precomputed evidence bundles; live read-only proxy can come after bundle hosting works.
- RCAEval subset is the primary data source; supplemental records fill fields the local subset does not contain.
- The triage engine remains event-driven and compute-first; Bedrock is optional grounded synthesis, not the first decision-maker.

## 2026-06-26 - Jira Evidence Update

### Done

- Added paste-ready Jira evidence comments and status recommendations in `capstone/tf-1/ai/docs/jira_task_evidence_updates.md`.
- Mapped A0X-19 through A0X-35 to concrete repo evidence, commits, tests, screenshots, contracts, and report artifacts.
- Refreshed `capstone/tf-1/ai/docs/07_w11_readiness_checklist.md` so Jira evidence hygiene points to the new update doc.
- Refreshed `personal-handoff-qna.txt` with the latest pushed commit and Jira evidence update location.

### Jira Actions Needed

- Paste evidence comments from `docs/jira_task_evidence_updates.md` into the matching A0X issues.
- Move A0X-19 through A0X-30 to Done where the implementation evidence is accepted.
- Mark A0X-31 as duplicate of A0X-27.
- Move A0X-32 through A0X-34 to Done after reviewer acceptance.
- Keep A0X-35 In Progress until CDO confirms their deployed engine passes `/healthz` and `/v1/triage`, and their Slack/Jira/evidence bundle integration is wired.

## 2026-06-26 - CDO Contract Acceptance / Responsibility Boundary

### Confirmed

- CDO accepts the current contract direction.
- CDO will deploy the AI engine and own infrastructure concerns.
- AI team responsibility is the AI engine artifact, runtime behavior, contracts, environment/config documentation, and smoke-test support.
- CDO responsibility is hosting platform, network/auth, observability plumbing, scaling, rollout/rollback, Slack rendering, Jira creation, and evidence bundle hosting.
