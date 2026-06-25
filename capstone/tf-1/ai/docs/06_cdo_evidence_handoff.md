# CDO Evidence Handoff - TF1 Triage Hub

Owner: AI team TF1
Status: Draft for CDO integration handoff
Last updated: 2026-06-25

## Purpose

This handoff explains how CDO teams get the extra evidence needed by TF1 AI Ops and how they can host or expose that data based on the current contracts and app implementation.

For case-by-case meaning, incident times, expected AI interpretation, and current RCAEval E2E output, also read `10_datapack_insights_for_cdo.md`.

The key boundary is:

```text
CDO/platform detects alert and pushes incident seed/context
  -> TF1 AIOps validates context
  -> TF1 AIOps gathers bounded context from the observability layer only if needed
  -> TF1 AIOps cleans, redacts, normalizes, and curates evidence
  -> POST /v1/triage
  -> report + raw Slack-renderable fields + Jira ticket_payload + optional assignee suggestion
```

TF1 AI Ops does not call customer applications directly for logs or metrics. Customer applications push telemetry into the customer's observability layer; CDO/platform detects alerts and exposes only bounded read-only access to that observability/evidence layer; AI Ops queries that bounded access path only after an alert exists.

AI Ops also does not own continuous infrastructure metric collection or real-time platform health evaluation. That remains CDO/platform scope. AI Ops consumes incident-scoped evidence after an alert exists, then cleans/normalizes/curates it for RCA.

## What TF1 AI Provides

TF1 AI will provide the seed data and guidance needed for CDO teams to stand up integration paths.

Provided data assets:

| Asset | Path | Purpose |
|---|---|---|
| RCAEval raw subset | `capstone/tf-1/ai/engine-skeleton/datapack/external/rcaeval-subsets/` | Selected RCAEval metrics and injection timestamps used as primary scenario evidence. Full raw logs/traces are downloaded or hosted outside Git when large. |
| RCAEval adapted subset | `capstone/tf-1/ai/engine-skeleton/datapack/external/adapted/` | Primary eval requests mapped into TF1 request shape. Logs/traces are present only when the exact selected RCAEval case provides them. |
| RCAEval evidence bundles | `capstone/tf-1/ai/engine-skeleton/datapack/external/evidence-bundles/` | Primary CDO-hostable evidence bundles for the three TF1 scenarios, including metrics, logs, traces, query hints, routing metadata, and lineage. |
| Direct API samples | `capstone/tf-1/ai/engine-skeleton/samples/` | Requests and expected responses for `/v1/triage` contract testing. |
| Synthetic scenario datapack | `capstone/tf-1/ai/engine-skeleton/datapack/scenarios/` | Secondary demo/smoke fixtures and supplemental field examples. |
| Local observability demo | `capstone/tf-1/ai/engine-skeleton/docker-compose.observability.yml` | Reference stack for Prometheus, Loki, Jaeger, Grafana, simulator, worker, and API. |

Current built-in scenarios:

- `critical-service-down`
- `latency-degradation`
- `noisy-false-alert`

The RCAEval evidence bundles are the primary scenario datapacks for CDO handoff. Current bundles contain selected-case RCAEval metrics for all nine cases, exact selected-case RCAEval log snippets for two RE2SS cases, and exact selected-case RCAEval trace snippets for one RE2TT case. The synthetic scenario datapack is not the primary evidence source; it is a repeatable scaffold for smoke testing, dashboards, and supplemental routing/runbook/deploy examples.

## What CDO Needs To Host Or Expose

CDO teams should expose extra evidence using one of these supported patterns. In all patterns, alert delivery remains push-based from CDO/platform to AI Ops.

### Recommended Foundation - Bounded Evidence/Log Access

Mentor feedback aligns with a production pattern where there is an evidence layer between messy observability backends and AI RCA. For TF1, CDO/platform should expose bounded incident-scoped evidence access, while AI Ops owns the cleaning/curation logic before triage.

Ownership split:

| Area | Owner | Notes |
|---|---|---|
| Raw telemetry ingestion | CDO/platform | Customer telemetry, agents/exporters, backend storage. |
| Alert detection | CDO/platform | Alert rules, monitoring events, incident trigger to AI Ops. |
| Bounded evidence storage/API | CDO/platform | Storage, retention, auth, tenant isolation, query limits, and safe read-only access. |
| Evidence gathering orchestration | AI Ops | Uses allowlisted tools/LLM planning to request only incident-scoped metrics/logs/traces/deploy/Jira history. |
| Evidence cleaning/curation | AI Ops | Redaction before AI use if needed, useful RCA evidence selection, fields, examples, confidence impact. |
| RCA interpretation | AI Ops | Uses cleaned bounded evidence; does not own production raw telemetry store. |
| Slack Block Kit rendering | CDO/platform | Injects raw AI response fields into the CDO-owned Slack template and handles interactive buttons. |
| Jira personal assignment | CDO/platform | Shows AI suggestion and performs Jira assignment only after human confirmation. |
| Human feedback/audit | CDO + AI Ops | Confirmation/correction events are audit metadata. Retrain trigger is design-only until enough reviewed feedback exists. |

Minimal AI-cleaned/curated log fields:

```json
{
  "tenant_id": "tenant-a",
  "incident_id": "inc-001",
  "service": "checkout-api",
  "environment": "prod",
  "ts": "2026-06-24T09:03:00Z",
  "level": "error",
  "message": "database timeout after 3000ms",
  "trace_id": "trace-123",
  "curation_reason": "timeout during alert window",
  "labels": {"endpoint": "/v1/orders", "version": "sha-a1b2c3"}
}
```

### Option A - Precomputed Evidence Bundle

This is the recommended MVP path.

CDO runs a job or integration flow that collects relevant evidence from its observability stack, redacts it, normalizes it, and stores it as a bounded JSON bundle. TF1 receives either the bundle inline or an `evidence_uri`.

Recommended stores:

- S3 or MinIO for JSON evidence bundles by `tenant_id/incident_id`.
- Postgres or DynamoDB for incident metadata, evidence indexes, report metadata, and idempotency records.
- The customer's Prometheus/Loki/Jaeger or equivalent observability backends remain the source of truth for raw observability data.

Minimal bundle shape:

```json
{
  "schema_version": "tf1.evidence_bundle.v1",
  "tenant_id": "tenant-a",
  "incident_id": "inc-001",
  "correlation_id": "corr-001",
  "service": "checkout-api",
  "environment": "prod",
  "time_window": {
    "start": "2026-06-24T09:00:00Z",
    "end": "2026-06-24T09:20:00Z"
  },
  "metrics": [],
  "logs": [],
  "traces": [],
  "deploy_events": [],
  "ownership": {}
}
```

CDO can start by hosting the TF1-provided RCAEval evidence bundles and storing them in its chosen platform.

### Option B - Read-only Evidence Proxy

This is the more production-like path.

CDO hosts a read-only internal API in front of Prometheus, Loki, Jaeger, CloudWatch, OpenSearch, or equivalent systems. TF1 calls only approved operations with bounded incident scope.

Recommended endpoint shape:

```text
GET  /v1/evidence/incidents/{incident_id}
POST /v1/evidence/query
```

`GET /v1/evidence/incidents/{incident_id}` returns a precomputed evidence bundle when CDO has already prepared one.

`POST /v1/evidence/query` is used only after an alert exists and the initial context is insufficient. Request scope must include:

```json
{
  "tenant_id": "tenant-a",
  "incident_id": "inc-001",
  "service": "checkout-api",
  "environment": "prod",
  "start_time": "2026-06-24T09:00:00Z",
  "end_time": "2026-06-24T09:20:00Z",
  "include": ["metrics", "logs", "traces", "deploy_events", "ownership"],
  "limits": {"logs": 50, "traces": 20}
}
```

Response can be raw-derived but must be bounded. AI Ops will clean/normalize/curate it into the same normalized evidence shape as the evidence bundles:

```json
{
  "tenant_id": "tenant-a",
  "incident_id": "inc-001",
  "metrics": [],
  "logs": [],
  "traces": [],
  "deploy_events": [],
  "ownership": {},
  "data_lineage": {}
}
```

Required operations for v1:

| Operation | Input | Output |
|---|---|---|
| `get_metric_window` | `tenant_id`, `service`, `environment`, `start_time`, `end_time`, metric names | bounded metric series or summaries |
| `get_log_snippets` | `tenant_id`, `service`, `environment`, `start_time`, `end_time`, filters, limit | redacted log snippets |
| `get_trace_summary` | `tenant_id`, `trace_id` or `correlation_id` | trace/span summary and error/latency highlights |
| `get_deploy_events` | `tenant_id`, `service`, `environment`, `start_time`, `end_time` | recent deploy/change records |
| `get_ownership` | `service`, optional `tenant_id` | owner team, Slack channel, Jira routing, runbook refs |
| `get_jira_history` | service/component, lookback limit, status filters | recent issue owners, component leads, accountId candidates, suggestion evidence |

The proxy owns PromQL/LogQL/index details, auth, timeout, retry, rate limit, and audit logging. TF1 should not receive arbitrary Prometheus/Loki/Jaeger credentials or arbitrary query access. AI Ops owns allowlisted evidence gathering, cleaning/redaction/normalization/curation after the bounded response is returned.

## Current TF1 App State

Current app root:

```text
capstone/tf-1/ai/engine-skeleton
```

Relevant implementation paths:

| Path | Role |
|---|---|
| `app/aiops_worker.py` | Reads incident seed events or local scenarios, queries evidence, calls `/v1/triage`, writes reports. |
| `app/incident_seed.py` | Defines `tf1.incident_seed.v1` and converts seed events into triage requests. |
| `app/context_tools.py` | Bounded read-only tool registry and current Prometheus/Loki/file-backed context client. |
| `app/main.py` | FastAPI `/v1/triage` and report APIs. |
| `app/rca.py` | Deterministic RCA, anomaly evidence, topology, and causal hints. |
| `app/action_catalog.py` | Catalog-gated human-reviewed action suggestions. |

Current worker supports:

- Offline scenario mode from `datapack/scenarios`.
- Incident seed event mode via the deployment platform's chosen transport.
- Prometheus metrics query via `PROMETHEUS_URL` for local/demo mode.
- Loki log query via `LOKI_URL` for local/demo mode.
- Deploy metadata file via `DEPLOY_METADATA_PATH`.
- Ownership mapping file via `OWNERSHIP_PATH`.
- Jaeger query in the worker path via `JAEGER_URL`, currently used for trace count/logging rather than full trace enrichment.

For production, those local/demo query paths should be fronted or replaced by a CDO/platform-approved bounded access layer over the customer's observability/evidence sources. AI Ops should not receive broad raw backend credentials.

Current env expected by the app:

```text
INCIDENT_EVENT_SOURCE=...
PROMETHEUS_URL=...
LOKI_URL=...
JAEGER_URL=...
DEPLOY_METADATA_PATH=...
OWNERSHIP_PATH=...
TRIAGE_URL=...
REPORTS_DIR=...
REPORT_BASE_URL=...
SLACK_WEBHOOK_URL=...
SERVICE_AUTH_TOKEN=...
```

## Recommended CDO MVP Integration

For the current app state, the lowest-risk CDO integration is:

1. Host TF1 API and worker on ECS Fargate or equivalent container platform.
2. Provide push-based incident delivery to AI Ops for `tf1.incident_seed.v1` messages or direct `/v1/triage` calls.
3. Store RCAEval evidence bundles from `datapack/external/evidence-bundles/` in S3, MinIO, Postgres, or DynamoDB.
4. Optionally add a bounded evidence/log table or object path keyed by `tenant_id/incident_id`.
5. If live follow-up is needed, expose `GET /v1/evidence/incidents/{incident_id}` and/or `POST /v1/evidence/query`.
6. Optionally expose bounded Jira history/accountId mapping for AI assignee suggestion.
7. Generate deploy metadata and ownership JSON files or expose equivalent read-only endpoints.
8. Expose Prometheus/Loki/Jaeger-compatible read-only URLs only behind CDO-controlled evidence proxy if running the live observability path.
9. Configure worker env vars so TF1 can read only the tenant/service/environment/time-window data.
10. Verify three flows: latency, critical service-down, and noisy alert.

For MVP, CDO does not need to build a full production evidence proxy on day one. They can host precomputed evidence bundles first, then add live proxy operations if time permits.

## Incident Seed From CDO To TF1

CDO sends this seed through the agreed integration layer:

```json
{
  "schema_version": "tf1.incident_seed.v1",
  "tenant_id": "tenant-a",
  "correlation_id": "corr-001",
  "incident_id": "inc-001",
  "environment": "prod",
  "service": "checkout-api",
  "severity": "high",
  "title": "High p95 latency on checkout-api",
  "description": "p95 latency above threshold for 5 minutes",
  "started_at": "2026-06-24T09:00:00Z",
  "received_at": "2026-06-24T09:05:00Z",
  "labels": {
    "region": "us-east-1",
    "cluster": "prod-eks-1",
    "namespace": "payments",
    "alert_id": "alert-001",
    "source": "cdo-detector",
    "metric_names": ["http_latency_p95_ms", "http_5xx_rate"],
    "trace_id": "trace-123",
    "suspected_dependency": "postgres",
    "evidence_uri": "s3://tf1-evidence/tenant-a/inc-001/evidence.json"
  }
}
```

Minimum required seed fields are `tenant_id`, `correlation_id`, `incident_id`, `environment`, `service`, `severity`, `title`, `started_at`, and `received_at`. Recommended labels such as `region`, `cluster`, `namespace`, `metric_names`, `trace_id`, and `suspected_dependency` help AI Ops query the right bounded evidence before cleaning and triage.

In the current code, `evidence_uri` is documented as handoff metadata but is not yet implemented as a bundle reader. Until that reader is added, CDO should either:

- expose Prometheus/Loki/deploy/ownership sources through existing env vars, or
- send full normalized context directly to `/v1/triage` using `telemetry-contract.md`.

## Data Bounds And Security Requirements

CDO-hosted evidence must enforce:

- read-only access
- tenant isolation
- service/environment/time-window scoping
- default window of 15 minutes before alert and 5 minutes after alert
- maximum extended window of 60 minutes unless agreed
- log snippet limit, default 50 lines per service per incident
- no PII or secrets in snippets
- query timeout and retry policy
- audit log for evidence access
- no remediation/write/restart/rollback/scale permissions
- no direct Jira personal assignment without human confirmation
- no automatic retraining or production behavior change from a single feedback event

## Contract References

- `capstone/tf-1/ai/contracts/observability-data-contract.md`
- `capstone/tf-1/ai/contracts/telemetry-contract.md`
- `capstone/tf-1/ai/contracts/ai-api-contract.md`
- `capstone/tf-1/ai/contracts/deployment-contract.md`

## CDO Acceptance Checks

- CDO can send a latency incident seed and TF1 produces a latency report.
- CDO can send a critical service-down seed and TF1 produces a service-down report.
- CDO can send a noisy alert seed and TF1 returns `INVESTIGATE` or observe/human-review behavior.
- CDO can show where extra evidence is hosted.
- CDO can explain whether bounded logs/evidence are precomputed, proxied live, or not included for MVP.
- CDO can explain whether AI follow-up evidence uses precomputed bundles, `GET /v1/evidence/incidents/{incident_id}`, or `POST /v1/evidence/query`.
- CDO can show how TF1 is scoped to tenant/service/environment/time window.
- CDO can show that logs are redacted and bounded.
- CDO can show report URL, Slack Block Kit rendered from raw AI response fields, Jira `ticket_payload`, and optional assignee suggestion.
- CDO can explain that engineer feedback is recorded for audit/future retrain design, not used to auto-change W11 behavior.
- CDO can explain whether it chose evidence bundle, read-only proxy, or both.
