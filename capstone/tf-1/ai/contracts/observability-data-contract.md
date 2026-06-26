# Supporting Observability Data Contract - TF1 Triage Hub

Owner: AI team TF1
Status: Supporting CDO/platform data handoff; not one of the 3 W11 signed/frozen contracts
Freeze target: 2026-06-25

## Contract Classification

The W11 announcement requires exactly three AI-CDO contracts to be signed and frozen:

1. `telemetry-contract.md`
2. `ai-api-contract.md`
3. `deployment-contract.md`

This file is an additional supporting data-availability contract/handoff. It exists to make the CDO evidence boundary concrete: where metrics/logs/traces/deploy/ownership data comes from, how it is bounded, and how AIOps can consume it safely. It informs the three signed contracts, but it is not counted as a fourth signed contract.

## Purpose

Define what the platform/DevOps side must make available from the observability stack so the AIOps app can process telemetry professionally. This contract is about **data availability, quality, access, and bounds**. It is not the RCA contract.

Production boundary:

```text
Customer applications
  -> OpenTelemetry/exporters
  -> customer-owned observability layer such as Prometheus/Loki/Jaeger/Grafana/OpenTelemetry
  -> CDO/platform alert detection
  -> CDO pushes incident seed/context to AI Ops
  -> AI Ops requests bounded evidence if needed through allowlisted tools
  -> AI Ops cleans/normalizes/curates evidence
  -> AI Ops RCA logic
```

Platform/DevOps ensures telemetry is observable, queryable, secure, and bounded. Platform/DevOps also owns alert detection and production evidence storage/access. AIOps owns interpretation after invocation: context validation, bounded evidence query orchestration, cleaning/redaction/normalization, evidence sufficiency checks, RCA scoring, confidence gating, and optional Bedrock synthesis.

## Required Metadata

Every metric/log/trace/deploy event exposed to AIOps should preserve:

| Field | Required | Notes |
|---|---:|---|
| `tenant_id` | yes | Required for isolation and query scoping. |
| `service` | yes | Service name, e.g. `payment-api`. |
| `environment` | yes | `prod`, `staging`, or `sandbox`. |
| `region` | preferred | Required if multi-region data exists. |
| `timestamp` | yes | RFC3339 preferred. |
| `source` | yes | `prometheus`, `loki`, `cloudwatch`, `otel`, etc. |
| `labels` | preferred | Original source labels/tags. |
| `trace_id` / `correlation_id` | preferred | Needed for cross-signal linking when available. |
| `version` / `deploy_id` | preferred | Needed for deploy correlation when available. |

## Metric Families

Platform should expose these metric families where the workload supports them:

| Metric family | Examples |
|---|---|
| Traffic | request count, RPS, throughput |
| Latency | p50, p95, p99 latency |
| Errors | error rate, 4xx/5xx count, timeout count |
| Saturation | CPU, memory, queue depth, DB connections |
| Dependencies | Redis/DB latency, dependency error count, connection pool errors |
| Availability | health check status, success rate |

## Log Requirements

Logs exposed to AIOps should include:

| Field | Required | Notes |
|---|---:|---|
| `timestamp` | yes | Event time. |
| `tenant_id` | yes | Required for isolation if logs are multi-tenant. |
| `service` | yes | Emitting service. |
| `level` | yes | `error`, `warning`, `info`, etc. |
| `message` | yes | Redacted if sensitive. |
| `trace_id` | preferred | For correlation with traces/metrics. |
| `labels` | preferred | Pod, version, endpoint, dependency, region. |

Rules:

- PII must be absent or redacted before AIOps/LLM use.
- Logs should be queryable by tenant, service, environment, and time window.
- Raw log dumps should be bounded; do not send unbounded logs into RCA/LLM.

## Query And Access Boundary

AIOps needs bounded query/export capability:

| Capability | Requirement |
|---|---|
| Query scope | `tenant_id + service + environment + time_window`. |
| Max default window | 15 minutes before alert and 5 minutes after alert. |
| Max extended window | 60 minutes unless explicitly agreed. |
| Max log snippets | Default 50 relevant lines per service per incident. |
| Retention | Use the CDO platform's configured retention. For the demo, metrics/logs/traces/deploy/ownership data must remain available long enough to replay the selected incident scenarios. |
| Auth | IAM/SigV4, service token, or platform-approved service auth. |
| Isolation | A tenant-scoped query must not return other tenants' data. |

This contract does not require AI Ops to collect infrastructure metrics continuously or evaluate real-time platform health. Real-time observability collection, alert rules, platform SLOs, and monitoring dashboards remain CDO/platform responsibilities. AI Ops consumes incident-scoped evidence after an alert exists, then cleans and normalizes that evidence for RCA.

## Extra Evidence Hosting Model

When the initial incident package is not enough for confident RCA, AIOps may request additional evidence. That evidence must come from a controlled platform-owned data path, not from direct AI access to customer applications. The LLM may help decide which approved context tool to invoke, but it must not receive direct backend credentials or arbitrary PromQL/LogQL/query privileges.

Alert delivery and evidence retrieval are intentionally separate:

```text
Alert delivery: CDO/platform pushes incident to AI Ops immediately.
Evidence retrieval: AI Ops pulls bounded evidence only after the alert exists, then cleans/curates it before triage.
```

AIOps should not continuously poll CDO/customer telemetry to discover incidents.

### Source Of Extra Evidence

CDO/platform should treat the customer's existing observability stack as the source of truth:

| Data type | Preferred source systems | Notes |
|---|---|---|
| Metrics | Prometheus, Thanos, Mimir, or equivalent metrics backend | Query by tenant, service, environment, and bounded time window. |
| Logs | Loki, Elasticsearch, OpenSearch, or equivalent log backend | Return redacted snippets, not unbounded raw dumps. |
| Traces | Jaeger, Tempo, X-Ray, OpenTelemetry backend | Query by `trace_id`, `correlation_id`, service, or time window where supported. |
| Alerts/incidents | Alertmanager, EventBridge, event pipeline, webhook/API integration, monitoring system | Provides incident id, severity, labels, and start time. |
| Deploy metadata | CI/CD system, deployment event table, GitOps controller, CloudTrail/EventBridge | Used for deploy correlation and rollback references. |
| Ownership/runbooks | Repo config, service catalog, Jira/Confluence, static config | Used for routing, recommendations, and Slack/Jira payloads. |
| Jira history/accountId map | Jira issue history, component ownership, service catalog | Used only for advisory `suggested_assignee_account_id` and `suggestion_reason`; CDO performs human-confirmed assignment. |

### Hosting Options For CDO

CDO can implement either of these patterns, as long as the same query bounds and redaction rules are enforced.

### Required Alert Seed Context

CDO should send enough metadata for AI Ops to query the right evidence without guessing:

| Field | Required | Why AI Ops needs it |
|---|---:|---|
| `tenant_id` | yes | Tenant isolation and evidence scope. |
| `incident_id` | yes | Idempotency, report, and evidence lookup key. |
| `correlation_id` | yes | End-to-end audit and traceability. |
| `service` | yes | Primary service scope for metrics/logs/traces. |
| `environment` | yes | `prod`, `staging`, or `sandbox` evidence scope. |
| `severity` | yes | Triage urgency and confidence behavior. |
| `title` / `description` | yes | Initial triage direction such as latency, outage, error, or noisy alert. |
| `started_at` / `received_at` | yes | Query window construction. |
| `region`, `cluster`, `namespace` | recommended | Multi-region or Kubernetes evidence scoping. |
| `trace_id` / `correlation_id` label | recommended | Log/trace correlation. |
| `metric_names` | recommended | Narrows metric query to alert-related signals. |
| `suspected_dependency` | optional | Helps query dependency logs/traces and rank RCA candidates. |
| `evidence_uri` | optional | Points to precomputed evidence bundle when available. |

**Recommended foundation - Bounded evidence access**

In production, CDO should expose bounded incident-scoped evidence access in front of Loki/Prometheus/Jaeger/OpenTelemetry/OpenSearch or equivalent systems. This is not a replacement for raw observability backends; it is a safe access layer that lets AI Ops retrieve only the tenant/service/environment/time-window evidence needed for RCA.

CDO/platform responsibilities:

- continuously ingest or query raw observability backends,
- store or expose bounded records with tenant/service/environment/time indexes,
- enforce retention, auth, rate limits, and audit logging,
- expose read-only bounded query operations to AI Ops.

AI Ops responsibilities:

- query bounded evidence only after an alert exists,
- clean, redact if needed, normalize, and curate RCA-useful logs/metrics/traces,
- provide sample processors or mapping logic for datapacks,
- define how cleaned/curated evidence affects RCA confidence and evidence sufficiency,
- avoid storing or operating production raw telemetry as the system of record.

Minimal cleaned/curated log record produced by AI Ops:

```json
{
  "tenant_id": "tenant-a",
  "incident_id": "inc-123",
  "service": "checkout-api",
  "environment": "prod",
  "ts": "2026-06-22T08:03:00Z",
  "level": "error",
  "message": "database timeout after 3000ms",
  "trace_id": "trace-123",
  "curation_reason": "timeout during alert window",
  "source_ref": "cdo-evidence://redacted-query-id",
  "labels": {"endpoint": "/v1/orders", "version": "sha-a1b2c3"}
}
```

### Evidence Access Interface

This handoff does not require CDO to implement a new evidence API endpoint. CDO may provide the bounded evidence through any approved mechanism that satisfies the scope and safety rules, for example:

- inline fields in the `/v1/triage` request,
- a precomputed evidence bundle referenced by `alert.labels.evidence_uri`,
- a CDO-owned read-only evidence proxy,
- local demo files or object storage for replayable capstone scenarios.

Regardless of transport, the evidence access path must enforce tenant/service/environment/time-window bounds, return bounded logs and trace summaries, and avoid exposing raw backend credentials to AI Ops or AgentCore.

**Option A - Read-only evidence proxy**

CDO hosts an internal read-only API in front of Prometheus/Loki/Jaeger or equivalent backends. AIOps calls this proxy with incident-scoped inputs such as:

- `tenant_id`
- `incident_id`
- `service`
- `environment`
- `start_time` / `end_time`
- `trace_id` / `correlation_id`
- alert labels or metric/log filter keys

The proxy owns backend-specific details such as PromQL, LogQL, index names, auth, timeouts, retries, rate limits, secret masking, and audit logging. AIOps receives normalized evidence responses and should not need direct Prometheus/Loki/Jaeger credentials.

Example proxy operations:

| Operation | Input | Output |
|---|---|---|
| `get_metric_window` | tenant, service, environment, time window, metric names | bounded metric series or summary |
| `get_log_snippets` | tenant, service, environment, time window, filters, limit | redacted relevant log lines |
| `get_trace_summary` | tenant, trace id or correlation id | span summary and error/latency highlights |
| `get_deploy_events` | tenant, service, environment, time window | bounded deploy/change records |
| `get_jira_history` | tenant/service/component, lookback limit, issue status filters | recent issue owners, component leads, and accountId candidates for advisory assignment |

**Option B - Precomputed evidence bundle**

CDO or an upstream job queries the observability stack, redacts and normalizes relevant evidence, then stores a bounded evidence bundle. AIOps receives either the bundle inline or an `evidence_uri` in the incident package.

Suitable storage:

- S3/MinIO/object storage for JSON evidence bundles by `tenant_id/incident_id`.
- Postgres/DynamoDB for incident metadata, evidence indexes, and report metadata.
- Existing customer Prometheus/Loki/Jaeger backends remain the source of truth for raw observability data.
- Vector DB is optional for runbooks/docs search only; it should not be the primary store for raw logs or metrics.
- Jira history storage or indexing is optional for W11. If not available, AI returns no personal accountId suggestion and routes to the owner queue/team.

Expected bundle shape:

```json
{
  "tenant_id": "tenant-a",
  "incident_id": "inc-123",
  "service": "checkout-api",
  "environment": "prod",
  "time_window": {
    "start": "2026-06-22T07:45:00Z",
    "end": "2026-06-22T08:10:00Z"
  },
  "metrics": [],
  "logs": [],
  "traces": [],
  "deploy_events": [],
  "ownership": {}
}
```

### MVP Requirement

For the capstone MVP, CDO and AI Ops need at least one replayable bounded evidence path. The current preferred path is a precomputed evidence bundle or equivalent local/object-storage artifact.

### Non-Goals

- AIOps does not call customer applications directly for logs or metrics.
- AIOps does not continuously poll CDO/customer systems for alert discovery.
- AIOps does not receive unbounded raw telemetry dumps.
- The LLM does not receive direct credentials or arbitrary query access.
- The evidence path must not expose remediation, write, restart, rollback, or scale permissions.
- AIOps does not personally assign Jira tickets; it may only return an advisory assignee suggestion when accountId evidence exists.

## Quality SLA Targets

Draft targets for capstone review:

| Quality item | Target |
|---|---:|
| Metric freshness | < 60 seconds delay for demo. |
| Log freshness | < 120 seconds delay for demo. |
| Required metadata completeness | >= 99% for demo fixtures/workloads. |
| Query p95 latency | < 2 seconds for bounded incident windows. |
| Cross-tenant leakage | 0 tolerated. |

## Handoff To Triage Context

The AIOps app converts bounded observability data into the triage context defined in `telemetry-contract.md`.

```text
observability data contract
  -> CDO/platform alert push + optional bounded evidence lookup
  -> telemetry-contract.md incident context
  -> POST /v1/triage
```

## W11 Implementation Scope

| Item | Requirement |
|---|---|
| Primary extra-data artifact | Precomputed evidence bundles are the W11 MVP because they are easy for CDO to host, validate, and replay. |
| Alert delivery | Push-based from CDO/platform to AI Ops. Poll-based alert discovery by AI Ops is out of scope. |
| Evidence cleaning | AI Ops owns cleaning/normalization/curation after bounded evidence retrieval. Customer observability remains the source of truth; CDO/platform owns hosting/access/query bounds for the integration path exposed to AI Ops. |
| Observability stack | CDO's planned Prometheus/Loki/Jaeger/Grafana/OpenTelemetry stack is acceptable as long as CDO exposes bounded metrics/logs/traces/deploy/ownership evidence through this contract. |
| Jira assignee suggestion | AI may return an advisory assignee suggestion only when bounded Jira history/accountId mapping is configured. CDO must require human confirmation before assignment. |
| Deploy events | For W11, deploy metadata may come from repo fixtures, CI/CD export, or CDO-provided deployment event tables. Missing deploys lower confidence instead of blocking triage. |
| Ownership/runbooks | RCAEval telemetry is primary for scenario evidence; ownership/runbooks may be TF1 supplemental records until a CDO service catalog or Jira/Confluence source is available. |
| Freshness and retention | Demo evidence must be fresh enough and retained long enough to replay the selected scenarios. |
