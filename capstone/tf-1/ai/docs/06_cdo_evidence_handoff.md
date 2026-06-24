# CDO Evidence Handoff - TF1 Triage Hub

Owner: AI team TF1
Status: Draft for CDO integration handoff
Last updated: 2026-06-24

## Purpose

This handoff explains how CDO teams get the extra evidence needed by TF1 AI Ops and how they can host or expose that data based on the current contracts and app implementation.

The key boundary is:

```text
CDO/platform hosts or exposes evidence data
  -> TF1 AIOps reads bounded evidence by tenant/service/environment/time window
  -> TF1 builds normalized triage context
  -> POST /v1/triage
  -> report + Slack payload + Jira ticket_payload
```

TF1 AI Ops does not call customer applications directly for logs or metrics. Extra evidence comes from controlled datasets, observability backends, or a CDO-hosted evidence layer.

## What TF1 AI Provides

TF1 AI will provide the seed data and guidance needed for CDO teams to stand up integration paths.

Provided data assets:

| Asset | Path | Purpose |
|---|---|---|
| RCAEval raw subset | `capstone/tf-1/ai/engine-skeleton/datapack/external/rcaeval-subsets/` | Selected raw metrics used to produce adapted evaluation requests. |
| RCAEval adapted subset | `capstone/tf-1/ai/engine-skeleton/datapack/external/adapted/` | Primary eval requests mapped into TF1 request shape. |
| RCAEval evidence bundles | `capstone/tf-1/ai/engine-skeleton/datapack/external/evidence-bundles/` | Primary CDO-hostable evidence bundles for the three TF1 scenarios. |
| Direct API samples | `capstone/tf-1/ai/engine-skeleton/samples/` | Requests and expected responses for `/v1/triage` contract testing. |
| Synthetic scenario datapack | `capstone/tf-1/ai/engine-skeleton/datapack/scenarios/` | Secondary demo/smoke fixtures and supplemental field examples. |
| Local observability demo | `capstone/tf-1/ai/engine-skeleton/docker-compose.observability.yml` | Reference stack for Prometheus, Loki, Jaeger, Grafana, simulator, worker, and API. |

Current built-in scenarios:

- `critical-service-down`
- `latency-degradation`
- `noisy-false-alert`

The RCAEval evidence bundles are the primary scenario datapacks for CDO handoff. The synthetic scenario datapack is not the primary evidence source; it is a repeatable scaffold for smoke testing, dashboards, and supplemental routing/runbook/deploy examples.

## What CDO Needs To Host Or Expose

CDO teams should expose extra evidence using one of two supported patterns.

### Option A - Precomputed Evidence Bundle

This is the recommended MVP path.

CDO runs a job or integration flow that collects relevant evidence from its observability stack, redacts it, normalizes it, and stores it as a bounded JSON bundle. TF1 receives either the bundle inline or an `evidence_uri`.

Recommended stores:

- S3 or MinIO for JSON evidence bundles by `tenant_id/incident_id`.
- Postgres or DynamoDB for incident metadata, evidence indexes, report metadata, and idempotency records.
- Prometheus/Loki/Jaeger remain the source of truth for raw observability data.

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

Required operations for v1:

| Operation | Input | Output |
|---|---|---|
| `get_metric_window` | `tenant_id`, `service`, `environment`, `start_time`, `end_time`, metric names | bounded metric series or summaries |
| `get_log_snippets` | `tenant_id`, `service`, `environment`, `start_time`, `end_time`, filters, limit | redacted log snippets |
| `get_trace_summary` | `tenant_id`, `trace_id` or `correlation_id` | trace/span summary and error/latency highlights |
| `get_deploy_events` | `tenant_id`, `service`, `environment`, `start_time`, `end_time` | recent deploy/change records |
| `get_ownership` | `service`, optional `tenant_id` | owner team, Slack channel, Jira routing, runbook refs |

The proxy owns PromQL/LogQL/index details, auth, masking, timeout, retry, rate limit, and audit logging. TF1 should not receive arbitrary Prometheus/Loki credentials or arbitrary query access.

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
- Prometheus metrics query via `PROMETHEUS_URL`.
- Loki log query via `LOKI_URL`.
- Deploy metadata file via `DEPLOY_METADATA_PATH`.
- Ownership mapping file via `OWNERSHIP_PATH`.
- Jaeger query in the worker path via `JAEGER_URL`, currently used for trace count/logging rather than full trace enrichment.

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
2. Provide an incident event ingestion path for `tf1.incident_seed.v1` messages using the CDO team's chosen transport.
3. Store RCAEval evidence bundles from `datapack/external/evidence-bundles/` in S3, MinIO, Postgres, or DynamoDB.
4. Generate deploy metadata and ownership JSON files or expose equivalent read-only endpoints.
5. Expose Prometheus-compatible and Loki-compatible read-only URLs if running the live observability path.
6. Configure worker env vars so TF1 can read only the tenant/service/environment/time-window data.
7. Verify three flows: latency, critical service-down, and noisy alert.

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
    "alert_id": "alert-001",
    "source": "cdo-detector",
    "evidence_uri": "s3://tf1-evidence/tenant-a/inc-001/evidence.json"
  }
}
```

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
- CDO can show how TF1 is scoped to tenant/service/environment/time window.
- CDO can show that logs are redacted and bounded.
- CDO can show report URL, Slack payload, and Jira `ticket_payload`.
- CDO can explain whether it chose evidence bundle, read-only proxy, or both.
