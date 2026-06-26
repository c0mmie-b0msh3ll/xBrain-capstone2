# W11 Readiness Checklist - TF1 AI Ops

Owner: AI team TF1
Review date: 2026-06-25 onsite
Status: Ready for design/contract review, live demo endpoint smoke-tested

## Approval Package

| Artifact | Status | Notes |
|---|---|---|
| `docs/01_requirements.md` | Ready | Includes W11 decisions and remaining external dependencies. |
| `docs/02_solution_design.md` | Ready | Event-driven, compute-first RCA design; RCAEval evidence bundle handoff included. |
| `docs/03_ai_engine_spec.md` | Ready | Defines compute-first RCA, confidence gates, safety, and evaluation method. |
| `docs/04_eval_report.md` | Ready | Skeleton tests verified; RCAEval subset evidence bundle readiness documented. |
| `docs/05_adrs.md` | Ready | ADRs accepted for W11, including RCAEval primary data and transport-neutral handoff. |
| `docs/06_cdo_evidence_handoff.md` | Ready | Explains extra evidence source, hosting model, and CDO responsibilities. |
| `docs/ai_engine_detailed_report_vi.md` | Ready | Vietnamese AI engine flow, algorithms, thresholds, modes, guardrails, fallback, and observability report. |
| `docs/jira_task_evidence_updates.md` | Ready | Paste-ready Jira evidence comments/status suggestions for A0X tasks. |
| `contracts/telemetry-contract.md` | Ready | Normalized incident context contract. |
| `contracts/ai-api-contract.md` | Ready | `/healthz` and `/v1/triage` contract plus Jira ticket fields, Slack Block Kit rendering boundary, and advisory assignee suggestion. |
| `contracts/deployment-contract.md` | Ready | AI artifact handoff, per-CDO deployment expectation, auth fallback, scaling, rollout, and smoke test requirements. |
| `contracts/observability-data-contract.md` | Ready | Supporting data-availability contract/handoff; not one of the 3 signed W11 contracts. |

## Data Readiness

| Data asset | Status | Notes |
|---|---|---|
| RCAEval raw subset | Ready | Stored under `engine-skeleton/datapack/external/rcaeval-subsets/`. |
| RCAEval adapted triage requests | Ready | Stored under `engine-skeleton/datapack/external/adapted/`. |
| RCAEval evidence bundles | Ready | 9 bundles under `engine-skeleton/datapack/external/evidence-bundles/`. |
| Synthetic scenario datapack | Supplemental | Kept for smoke tests, observability demos, and dashboard wiring only. |
| Logs/traces/deploy/ownership/runbook extras | Ready with lineage | RCAEval logs/traces are used when available in the selected RE2/RE3 cases. Deploy events, ownership, and runbooks remain TF1 supplemental because RCAEval does not provide those operational records. |

## CDO Handoff Decision

For W11, CDO should host **precomputed evidence bundles** first:

```text
RCAEval subset metrics/logs/traces where available
  + TF1 supplemental deploy/ownership/runbook records where RCAEval has no equivalent
  -> evidence-bundle.json
  -> CDO-hosted object storage or metadata store
  -> AIOps context layer
  -> POST /v1/triage
```

If CDO wants live follow-up queries, add a read-only evidence proxy later with approved operations only:

- `get_metric_window`
- `get_log_snippets`
- `get_trace_summary`
- `get_deploy_events`
- `get_ownership`
- `get_runbook_excerpt`

The shared handoff intentionally stays transport-neutral. CDO teams can choose their own integration mechanism as long as they satisfy the same evidence, auth, isolation, and bounds.

## Verification Commands

Run from `capstone/tf-1/ai/engine-skeleton`:

```powershell
python -m compileall app scripts
python -m pytest tests -q
python scripts/validate_datapack.py
docker compose -f docker-compose.observability.yml config --quiet
```

Run from `capstone/tf-1/ai/engine-skeleton/report-ui`:

```powershell
npm run build
```

## Bootstrap AWS Endpoint Evidence

This endpoint is the W11 bootstrap/demo endpoint for early CDO integration and mentor smoke tests. It is not the final W12 hosting target; each CDO team is expected to deploy its own AI engine instance from the AI-provided artifact according to `contracts/deployment-contract.md`.

| Item | Value |
|---|---|
| Endpoint URL | `https://snpmtcwpys.us-east-1.awsapprunner.com` |
| Runtime | AWS App Runner demo service |
| Service ARN | `arn:aws:apprunner:us-east-1:589077667575:service/tf1-ai-triage-engine/540fcd194a144db09c63786d3d28c8f9` |
| `/healthz` result | Passed, returned `{"status":"ok","service":"tf1-ai-triage-engine","version":"v1"}` |
| `/v1/triage` sample result | Passed with `latency-degradation.request.json`, returned `DIAGNOSED / latency_degradation` |
| Image tag | `589077667575.dkr.ecr.us-east-1.amazonaws.com/tf1-ai-triage-engine:latest` |
| Image digest | `sha256:db688c5ed3ebed46beb50690df396bb8174752601015071c6094505e489c4909` |
| Pushed at | `2026-06-24T17:39:26.858000+07:00` |
| Rollback target | Previous App Runner image version or previous ECR digest after the next release; no prior production digest exists for this first demo deploy. |

Minimum smoke test:

```powershell
Invoke-RestMethod -Uri "$env:TF1_AI_ENDPOINT/healthz" -Method Get
Invoke-RestMethod -Uri "$env:TF1_AI_ENDPOINT/v1/triage" -Method Post `
  -Headers @{
    "X-Tenant-Id" = "tenant-a"
    "X-Correlation-Id" = "corr-smoke-001"
    "Authorization" = "Bearer $env:SERVICE_AUTH_TOKEN"
  } `
  -ContentType "application/json" `
  -Body (Get-Content -Raw .\samples\latency-degradation.request.json)
```

## Jira Hygiene For W11

Each team member should:

- Pick an AIOps task before implementation work.
- Move Jira status from To Do to In Progress before coding.
- Comment the related commit or PR link daily.
- Move the issue to Done only after verification evidence is available.

Evidence update source:

- Use `docs/jira_task_evidence_updates.md` for paste-ready comments and evidence links.
- A0X-19 through A0X-30 have implementation evidence mapped to code/docs/tests.
- A0X-31 should be marked duplicate of A0X-27.
- A0X-32 through A0X-34 can be moved to Done after reviewer acceptance.
- A0X-35 should remain In Progress until CDO confirms their deployed engine can pass `/healthz` and `/v1/triage`, and that Slack/Jira/evidence bundle integration is wired on the CDO side.

## Remaining External Dependencies

| Dependency | Owner | Status |
|---|---|---|
| Contract acceptance | CDO | Accepted |
| Bootstrap AWS endpoint | AI | Complete for demo endpoint; final W12 engine hosting remains per CDO deployment contract |
| AI engine artifact/config support | AI | Complete for handoff; AI owns engine behavior, contracts, env/config docs, and smoke-test support |
| Per-CDO engine deployment and infrastructure | CDO | CDO owns hosting platform, network, auth, observability plumbing, scaling, and rollout/rollback |
| CDO-hosted evidence bundle location | CDO | Pending CDO implementation |
| Final auth mechanism beyond capstone token fallback | CDO + AI | Deferred after W11 sign-off |
| Regenerate selected subset after RCAEval utility download succeeds | AI | Active follow-up; current scripts support copying RE2 logs/traces from official utility output |
