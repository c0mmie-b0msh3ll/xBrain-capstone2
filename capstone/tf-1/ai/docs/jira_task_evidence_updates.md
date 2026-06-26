# Jira Task Evidence Updates - TF1 AI Ops

Date: 2026-06-26  
Project: `A0X / AIO-01-XBrain`  
Purpose: paste-ready Jira task updates with evidence links for W11/W12 handoff.

## Current Repo Evidence

Latest pushed commits:

- `9ffc23e Clean contracts and add Vietnamese AI engine report`
- `d75fb8b Add investigation modes and AgentCore platform flow`

Primary evidence files:

- Contracts:
  - `capstone/tf-1/ai/contracts/telemetry-contract.md`
  - `capstone/tf-1/ai/contracts/ai-api-contract.md`
  - `capstone/tf-1/ai/contracts/deployment-contract.md`
- Supporting evidence handoff:
  - `capstone/tf-1/ai/contracts/observability-data-contract.md`
  - `capstone/tf-1/ai/docs/06_cdo_evidence_handoff.md`
- AI engine report:
  - `capstone/tf-1/ai/docs/ai_engine_detailed_report_vi.md`
- Engine implementation:
  - `capstone/tf-1/ai/engine-skeleton/app/main.py`
  - `capstone/tf-1/ai/engine-skeleton/app/investigation_router.py`
  - `capstone/tf-1/ai/engine-skeleton/app/agent_runtime.py`
  - `capstone/tf-1/ai/engine-skeleton/app/context_tools.py`
  - `capstone/tf-1/ai/engine-skeleton/app/rca.py`
  - `capstone/tf-1/ai/engine-skeleton/app/observability.py`
- Test/eval evidence:
  - `capstone/tf-1/ai/engine-skeleton/tests/test_aiops_pipeline.py`
  - `capstone/tf-1/ai/engine-skeleton/reports-e2e/rcaeval-e2e-results.json`
  - `capstone/tf-1/ai/engine-skeleton/reports-e2e/report-list-ui-rendered.png`
  - `capstone/tf-1/ai/engine-skeleton/reports-e2e/rcaeval-report-list-ui-rendered.png`
  - `capstone/tf-1/ai/engine-skeleton/reports-e2e/rcaeval-detail-ui-rendered.png`
- RCAEval evidence bundles:
  - `capstone/tf-1/ai/engine-skeleton/datapack/external/evidence-bundles/critical-service-down/`
  - `capstone/tf-1/ai/engine-skeleton/datapack/external/evidence-bundles/latency-degradation/`
  - `capstone/tf-1/ai/engine-skeleton/datapack/external/evidence-bundles/noisy-false-alert/`
- Dashboard/observability:
  - `capstone/tf-1/ai/engine-skeleton/observability/grafana/dashboards/tf1-aiops-demo.json`
  - `capstone/tf-1/ai/engine-skeleton/docker-compose.observability.yml`

## Paste-Ready Jira Updates

### A0X-19 - AI API Contract And Jira/Slack Output Boundary

Recommended status: `Done`

Comment:

```text
Completed API contract cleanup and Jira/Slack output boundary.

Evidence:
- capstone/tf-1/ai/contracts/ai-api-contract.md
- capstone/tf-1/ai/docs/ai_engine_detailed_report_vi.md
- Commit: 9ffc23e Clean contracts and add Vietnamese AI engine report

Notes:
- Contract now keeps only implementable/current API surface: GET /healthz and POST /v1/triage.
- Jira/Slack remain core consumers: AI returns ticket_payload, audit_id, confidence, evidence, recommended_actions, optional suggested_assignee_account_id/suggestion_reason.
- CDO owns Slack Block Kit rendering and Jira issue creation.
- Removed future AI-owned callback endpoints from the contract.
```

### A0X-20 - Triage Context / Telemetry Contract

Recommended status: `Done`

Comment:

```text
Updated and cleaned normalized telemetry/context contract.

Evidence:
- capstone/tf-1/ai/contracts/telemetry-contract.md
- capstone/tf-1/ai/contracts/observability-data-contract.md
- Commit: 9ffc23e Clean contracts and add Vietnamese AI engine report

Notes:
- Contract focuses on implementable incident context fields and evidence bounds.
- Removed planning/cost-only signal tables from contract scope.
- Kept required tenant/service/environment/time-window context and bounded metrics/logs/traces/deploy/ownership expectations.
```

### A0X-21 - Deployment Contract And Runtime Config

Recommended status: `Done`

Comment:

```text
Updated deployment contract to match implemented AI engine runtime.

Evidence:
- capstone/tf-1/ai/contracts/deployment-contract.md
- capstone/tf-1/ai/engine-skeleton/README.md
- Commit: 9ffc23e Clean contracts and add Vietnamese AI engine report
- Commit: d75fb8b Add investigation modes and AgentCore platform flow

Notes:
- Runtime surface is GET /healthz and POST /v1/triage.
- Added current env config for investigation modes, AgentCore, context tools, observability, and auth fallback.
- Removed Slack two-way endpoint secret from required deployment scope because AI does not host those endpoints.
```

### A0X-22 - Context Enrichment And Evidence Bundle Loading

Recommended status: `Done`

Comment:

```text
Implemented bounded context enrichment and evidence bundle handling.

Evidence:
- capstone/tf-1/ai/engine-skeleton/app/context_enrichment.py
- capstone/tf-1/ai/engine-skeleton/app/context_tools.py
- capstone/tf-1/ai/engine-skeleton/tests/test_aiops_pipeline.py
- capstone/tf-1/ai/engine-skeleton/datapack/external/evidence-bundles/
- Commit: d75fb8b Add investigation modes and AgentCore platform flow

Notes:
- Inline evidence is preserved.
- evidence_uri bundles are loaded only when tenant/environment/service/window scope matches.
- Missing or out-of-scope bundles fall back to scoped tools or sparse-context response.
- Tool calls are read-only and tenant/service/environment/time-window bounded.
```

### A0X-23 - Deterministic RCA Algorithms

Recommended status: `Done`

Comment:

```text
Deterministic RCA pipeline is implemented and documented.

Evidence:
- capstone/tf-1/ai/engine-skeleton/app/rca.py
- capstone/tf-1/ai/docs/ai_engine_detailed_report_vi.md
- capstone/tf-1/ai/engine-skeleton/tests/test_aiops_pipeline.py
- Commit: d75fb8b Add investigation modes and AgentCore platform flow

Implemented algorithms:
- Threshold detection
- Rolling z-score 3 sigma
- EWMA drift
- Isolation Forest
- Log keyword anomaly detection
- Topology inference
- Lag-correlation causal hints
- RCA candidate ranking
```

### A0X-24 - Investigation Mode Router

Recommended status: `Done`

Comment:

```text
Implemented investigation mode router for deterministic_only, agent_assisted, and agent_platform.

Evidence:
- capstone/tf-1/ai/engine-skeleton/app/investigation_router.py
- capstone/tf-1/ai/engine-skeleton/app/main.py
- capstone/tf-1/ai/docs/ai_engine_detailed_report_vi.md
- Commit: d75fb8b Add investigation modes and AgentCore platform flow

Notes:
- Auto routing uses complexity score.
- Default thresholds: assisted >=3, platform >=6.
- Forced env mode supported via AIOPS_INVESTIGATION_MODE.
- If AgentCore is disabled, planned mode is recorded but selected mode falls back to deterministic_only.
```

### A0X-25 - AgentCore Platform Runtime

Recommended status: `Done`

Comment:

```text
Implemented AgentCore platform investigator loop with deterministic fallback.

Evidence:
- capstone/tf-1/ai/engine-skeleton/app/agent_runtime.py
- capstone/tf-1/ai/engine-skeleton/app/main.py
- capstone/tf-1/ai/engine-skeleton/tests/test_aiops_pipeline.py
- capstone/tf-1/ai/docs/ai_engine_detailed_report_vi.md
- Commit: d75fb8b Add investigation modes and AgentCore platform flow

Notes:
- Agent can return tool_requests or final_diagnosis.
- Engine owns tool allowlist, scope validation, read-only execution, final validation, action filtering, and fallback.
- Unknown/disallowed/out-of-scope tools are blocked.
- Malformed/invalid agent output falls back to deterministic RCA.
```

### A0X-26 - Guardrails, Bounds, And Safety

Recommended status: `Done`

Comment:

```text
Implemented and documented guardrails/bounds.

Evidence:
- capstone/tf-1/ai/engine-skeleton/app/context_tools.py
- capstone/tf-1/ai/engine-skeleton/app/agent_runtime.py
- capstone/tf-1/ai/contracts/ai-api-contract.md
- capstone/tf-1/ai/docs/ai_engine_detailed_report_vi.md
- Commit: d75fb8b Add investigation modes and AgentCore platform flow

Guardrails:
- No auto-remediation.
- No shell, Jira mutation, Slack posting, arbitrary PromQL/LogQL.
- Tool calls are read-only and scope validated.
- Final diagnosis must use known status/classification and confidence 0.0..1.0.
- DIAGNOSED requires non-empty evidence.
```

### A0X-27 - Observability Metrics And Grafana Panels

Recommended status: `Done`

Comment:

```text
Added AI engine observability metrics and dashboard panels.

Evidence:
- capstone/tf-1/ai/engine-skeleton/app/observability.py
- capstone/tf-1/ai/engine-skeleton/observability/grafana/dashboards/tf1-aiops-demo.json
- capstone/tf-1/ai/engine-skeleton/reports-e2e/report-list-ui-rendered.png
- capstone/tf-1/ai/engine-skeleton/reports-e2e/rcaeval-report-list-ui-rendered.png
- Commit: d75fb8b Add investigation modes and AgentCore platform flow

Metrics added:
- aiops_investigation_mode_selected_total
- aiops_agent_iterations_total
- aiops_agent_tool_requests_total
- aiops_agent_fallback_total
```

### A0X-28 - Report UI / E2E Evidence

Recommended status: `Done`

Comment:

```text
Report UI and E2E evidence artifacts are available.

Evidence:
- capstone/tf-1/ai/engine-skeleton/report-ui/src/main.jsx
- capstone/tf-1/ai/engine-skeleton/reports-e2e/report-list-ui-rendered.png
- capstone/tf-1/ai/engine-skeleton/reports-e2e/rcaeval-report-list-ui-rendered.png
- capstone/tf-1/ai/engine-skeleton/reports-e2e/rcaeval-detail-ui-rendered.png
- capstone/tf-1/ai/engine-skeleton/reports-e2e/rcaeval-e2e-results.json
- Commit: d75fb8b Add investigation modes and AgentCore platform flow

Notes:
- Report artifacts include RCAEval-derived case outputs and screenshots.
- UI renders incident reports, RCA candidates, evidence, topology, causal hints, and metadata.
```

### A0X-29 - Tests And Validation

Recommended status: `Done`

Comment:

```text
Expanded tests for router, AgentCore platform runtime, tool gateway, fallback, metadata, and metrics.

Evidence:
- capstone/tf-1/ai/engine-skeleton/tests/test_aiops_pipeline.py
- capstone/tf-1/ai/engine-skeleton/scripts/validate_datapack.py
- Commit: d75fb8b Add investigation modes and AgentCore platform flow

Validation run:
- python -m compileall app scripts
- python -m pytest tests -q
- python scripts/validate_datapack.py
- docker compose -f docker-compose.observability.yml config --quiet

Latest known result:
- 51 passed
```

### A0X-30 - README / Handoff Documentation

Recommended status: `Done`

Comment:

```text
Updated README and handoff docs for CDO review.

Evidence:
- capstone/tf-1/ai/engine-skeleton/README.md
- capstone/tf-1/ai/docs/ai_engine_detailed_report_vi.md
- capstone/tf-1/ai/docs/jira_task_evidence_updates.md
- personal-handoff-qna.txt
- Commit: 9ffc23e Clean contracts and add Vietnamese AI engine report

Notes:
- Documented 3 modes, env config, routing thresholds, AgentCore runtime contract, safety boundary, and verification commands.
```

### A0X-31 - Duplicate Of A0X-27

Recommended status: `Duplicate` / link to A0X-27

Comment:

```text
This issue is a duplicate of A0X-27 for observability/dashboard work.

Primary evidence should be tracked on A0X-27:
- capstone/tf-1/ai/engine-skeleton/app/observability.py
- capstone/tf-1/ai/engine-skeleton/observability/grafana/dashboards/tf1-aiops-demo.json
- Commit: d75fb8b Add investigation modes and AgentCore platform flow
```

### A0X-32 - W11 Contracts Ready For CDO Sign-Off

Recommended status: `Done`

Comment:

```text
W11 contract package is ready for CDO review/sign-off.

Evidence:
- capstone/tf-1/ai/contracts/telemetry-contract.md
- capstone/tf-1/ai/contracts/ai-api-contract.md
- capstone/tf-1/ai/contracts/deployment-contract.md
- capstone/tf-1/ai/contracts/observability-data-contract.md
- Commit: 9ffc23e Clean contracts and add Vietnamese AI engine report

Notes:
- Main 3 contracts contain current implementable scope.
- observability-data-contract.md is supporting evidence handoff, not a fourth signed contract.
- Jira/Slack are preserved as core consumers through ticket_payload/raw response fields.
```

### A0X-33 - RCAEval Evidence Bundles

Recommended status: `Done`

Comment:

```text
RCAEval-derived evidence bundle package is ready.

Evidence:
- capstone/tf-1/ai/engine-skeleton/datapack/external/evidence-bundles/critical-service-down/
- capstone/tf-1/ai/engine-skeleton/datapack/external/evidence-bundles/latency-degradation/
- capstone/tf-1/ai/engine-skeleton/datapack/external/evidence-bundles/noisy-false-alert/
- capstone/tf-1/ai/engine-skeleton/datapack/external/adapted/
- capstone/tf-1/ai/engine-skeleton/datapack/external/rcaeval-subsets/
- capstone/tf-1/ai/engine-skeleton/reports-e2e/rcaeval-e2e-results.json
- Commit: d75fb8b Add investigation modes and AgentCore platform flow

Bundle count:
- critical-service-down: 3
- latency-degradation: 3
- noisy-false-alert: 3
- total: 9
```

### A0X-34 - Demo Endpoint / Runtime Evidence

Recommended status: `Done` for AI-owned W11 demo endpoint and artifact handoff; CDO-owned deployment is tracked by CDO

Comment:

```text
W11 bootstrap/demo endpoint evidence is documented.

Evidence:
- capstone/tf-1/ai/docs/07_w11_readiness_checklist.md
- capstone/tf-1/ai/engine-skeleton/README.md
- capstone/tf-1/ai/engine-skeleton/docker-compose.observability.yml

Demo endpoint:
- https://snpmtcwpys.us-east-1.awsapprunner.com

Documented smoke evidence:
- GET /healthz passed.
- POST /v1/triage with latency-degradation sample passed.
- Image digest recorded in readiness checklist.

Responsibility boundary:
- AI owns the engine artifact, runtime behavior, contracts, config documentation, and smoke-test support.
- CDO owns the deployment infrastructure, hosting platform, network, auth, observability plumbing, scaling, and rollout/rollback.
```

### A0X-35 - CDO Handoff / Remaining Integration Tasks

Recommended status: `In Progress` until CDO confirms their deployment smoke test, Slack/Jira mapping, and evidence hosting

Comment:

```text
CDO handoff package is prepared and contracts are accepted. Remaining work is CDO-owned deployment/infrastructure confirmation and integration smoke test.

Evidence:
- capstone/tf-1/ai/docs/06_cdo_evidence_handoff.md
- capstone/tf-1/ai/docs/07_w11_readiness_checklist.md
- capstone/tf-1/ai/docs/ai_engine_detailed_report_vi.md
- capstone/tf-1/ai/docs/jira_task_evidence_updates.md
- Commit: 9ffc23e Clean contracts and add Vietnamese AI engine report

Remaining CDO actions:
- Deploy the AI engine artifact on their platform.
- Confirm their deployed endpoint passes GET /healthz.
- Confirm their deployed endpoint passes POST /v1/triage with required headers.
- Confirm Slack mapping from raw AI fields.
- Confirm Jira issue creation from ticket_payload.
- Confirm where evidence bundles will be hosted or how inline evidence will be supplied.
```

## Issues That Need Human/Jira-Side Action

These cannot be completed from the repo alone:

| Issue | Required action |
|---|---|
| A0X-19..A0X-30 | Paste evidence comments and move completed implementation issues to Done. |
| A0X-31 | Mark duplicate/link to A0X-27. |
| A0X-32..A0X-34 | Paste evidence comments and move to Done if reviewer accepts. |
| A0X-35 | Keep In Progress until CDO runs their deployed-engine smoke test and confirms Slack/Jira/evidence hosting. |
