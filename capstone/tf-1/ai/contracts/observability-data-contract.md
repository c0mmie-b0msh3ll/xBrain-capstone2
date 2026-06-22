# Observability Data Contract - TF1 Triage Hub

Owner: AI team TF1  
Status: Draft for CDO/platform review  
Freeze target: 2026-06-25

## Purpose

Define what the platform/DevOps side must make available from the observability stack so the AIOps app can process telemetry professionally. This contract is about **data availability, quality, access, and bounds**. It is not the RCA contract.

Production boundary:

```text
Services
  -> OpenTelemetry/exporters
  -> Prometheus/Loki/CloudWatch/Grafana stack
  -> bounded query/export by tenant + service + environment + time window
  -> AIOps ingestion/context service
  -> normalize/window/baseline/anomaly/RCA logic
```

Platform/DevOps ensures telemetry is observable, queryable, secure, and bounded. AIOps owns interpretation: normalization, windowing, baseline/trend, anomaly detection, incident context packaging, RCA scoring, confidence gating, and optional Bedrock synthesis.

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
| Retention | At least enough for demo scenarios and baseline comparison. |
| Auth | IAM/SigV4, service token, or platform-approved service auth. |
| Isolation | A tenant-scoped query must not return other tenants' data. |

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
  -> AIOps normalization/windowing/baseline/detection
  -> telemetry-contract.md incident context
  -> POST /v1/triage
```

## Open Questions

- [ ] Final observability stack: Prometheus/Grafana/Loki/CloudWatch/OpenTelemetry mix.
- [ ] Exact source of deploy events.
- [ ] Whether runbooks/ownership come from config, Jira, repo files, or synthetic fixtures.
- [ ] Final freshness and retention targets for demo.
