# Telemetry Contract - TF1 Triage Hub

Owner: AI team TF1  
Status: Draft for CDO review  
Freeze target: 2026-06-25

## Purpose

Define the data CDO must provide to the AI triage engine. For TF1, telemetry is a context bundle for one alert/incident, not a generic metrics stream.

The current assumption is:

```text
Continuous telemetry or demo workload
  -> CDO ingestion and lightweight detection
  -> Alert/anomaly/incident candidate
  -> CDO context aggregation
  -> AI API request with normalized context bundle
  -> AI compute-first RCA + optional LLM synthesis
  -> AI diagnosis + ticket/slack payload
```

The AI triage engine is event-driven. It is invoked when CDO has detected an alert/anomaly/incident candidate and assembled bounded context around that event.

## Contract Boundary

CDO continuously ingests telemetry, runs lightweight detection, and sends normalized incident context to `POST /v1/triage` only after an alert/anomaly/incident candidate exists. Mentor datapack files are treated as raw source material and must be adapted into this contract before calling the AI engine.

Field-name differences in the datapack should be handled in an adapter. The contract changes only when the datapack exposes a missing concept that cannot be represented by existing fields.

The AI team does not own direct pull connectors to logs, metrics, traces, deploy stores, Jira, or Slack in this contract.

## Required Envelope

Every request to AI must include:

| Field | Type | Required | Notes |
|---|---|---:|---|
| `correlation_id` | string | yes | Stable trace id for the full workflow. |
| `tenant_id` | string | yes | Required for isolation. Missing tenant is rejected. |
| `incident_id` | string | yes | Unique incident/alert grouping id. |
| `environment` | enum | yes | `prod`, `staging`, `sandbox`. |
| `received_at` | RFC3339 | yes | When CDO received or generated the alert. |

Validation rules:

- `tenant_id` must be non-empty and must match the `X-Tenant-Id` request header.
- `correlation_id` must be non-empty and must match the `X-Correlation-Id` request header.
- `environment` must be one of `prod`, `staging`, or `sandbox`.
- Missing required envelope or alert fields returns `400`.

## Alert Metadata

| Field | Type | Required | Notes |
|---|---|---:|---|
| `alert.alert_id` | string | yes | Original alert source id. |
| `alert.source` | string | yes | Example: `cloudwatch`, `prometheus`, `datadog`, `synthetic-pack`. |
| `alert.service` | string | yes | Affected service name. |
| `alert.severity` | enum | yes | `critical`, `high`, `medium`, `low`, `unknown`. |
| `alert.title` | string | yes | Human-readable alert title. |
| `alert.description` | string | optional | Raw alert description. |
| `alert.started_at` | RFC3339 | yes | Alert start time. |
| `alert.labels` | object | optional | Source-specific labels. |

## Metrics Window

Metrics should cover at least 15 minutes before alert start and, when available, 5 minutes after alert start.

```json
{
  "metric_name": "http_latency_p95_ms",
  "service": "checkout-api",
  "unit": "ms",
  "points": [
    {"ts": "2026-06-22T08:00:00Z", "value": 220},
    {"ts": "2026-06-22T08:01:00Z", "value": 950}
  ],
  "labels": {"endpoint": "/v1/orders", "region": "us-east-1"}
}
```

Minimum useful metric types for TF1:

- Latency: p50/p95/p99 if available.
- Error rate or 5xx count.
- Request rate.
- Saturation signals: CPU, memory, queue depth, DB connection count, or equivalent.

## Logs Window

Logs should be sampled, not dumped raw. CDO should provide relevant snippets around the alert window.

```json
{
  "service": "checkout-api",
  "ts": "2026-06-22T08:03:00Z",
  "level": "error",
  "message": "database timeout after 3000ms",
  "trace_id": "trace-123",
  "labels": {"pod": "checkout-api-7d9f", "version": "sha-a1b2c3"}
}
```

Rules:

- No PII in log snippets.
- Maximum 50 log lines per service per incident unless otherwise agreed.
- Preserve timestamp, service, level, and correlation/trace id when available.

## Recent Deploys

```json
{
  "service": "checkout-api",
  "version": "sha-a1b2c3",
  "deployed_at": "2026-06-22T07:50:00Z",
  "deployed_by": "ci",
  "change_summary": "changed database query path",
  "rollback_ref": "sha-prev"
}
```

Required for deploy-related diagnosis. If not available, CDO must pass an empty array and AI will lower confidence.

## Ownership And Runbook Docs

```json
{
  "service": "checkout-api",
  "owner_team": "payments-platform",
  "slack_channel": "#oncall-payments",
  "jira_project": "PAY",
  "runbooks": [
    {
      "title": "Database timeout triage",
      "url": "runbook://db-timeout",
      "excerpt": "Check DB connections, recent deploys, and slow query logs before rollback."
    }
  ]
}
```

Runbook/docs are preferred for AI suggestion quality. If the mentor data pack does not include runbooks, AI team may create minimal scenario runbooks and mark them as synthetic.

## Context Sufficiency

The AI engine can accept empty arrays for `metrics`, `logs`, and `recent_deploys`, but response status changes based on available context:

| Context state | Expected AI behavior |
|---|---|
| Missing required envelope or alert fields | Reject with `400`. |
| Alert exists but no metrics, logs, deploys, or ownership | Return `INSUFFICIENT_CONTEXT`. |
| Signals conflict or indicate a noisy/non-impacting alert | Return `INVESTIGATE`. |
| Logs/metrics/deploys support a scenario diagnosis | Return `DIAGNOSED`. |

## Datapack Mapping Table Template

When the mentor datapack arrives, create a mapping table using this format:

| Raw datapack field | Normalized field | Mapping type | Notes |
|---|---|---|---|
| `<source field>` | `alert.alert_id` | `direct` | Same meaning. |
| `<source field>` | `alert.severity` | `derived` | Map source priority to contract enum. |
| `<missing>` | `ownership.runbooks[].excerpt` | `defaulted` | Synthetic runbook if mentor pack lacks docs. |
| `<missing>` | `recent_deploys[].rollback_ref` | `missing` | Lowers confidence if unavailable. |

Mapping type must be one of:

- `direct`: same value and meaning.
- `derived`: transformed or inferred from one or more raw fields.
- `defaulted`: supplied by TF1/CDO when source lacks the field.
- `missing`: unavailable and not safely defaulted.

## Delivery And Quality

- Delivery mode: request payload from CDO to AI API.
- Invocation mode: event-driven after CDO detection, not continuous full triage over all telemetry.
- Detection ownership: CDO/observability continuously detects candidate alerts/anomalies; AI performs incident-level RCA after invocation.
- Duplicate handling: CDO must provide `correlation_id`; AI responses must be idempotent for the same `correlation_id`.
- Missing data behavior: AI returns lower confidence or `INSUFFICIENT_CONTEXT`.
- Malformed data behavior: AI returns `400` with validation errors.
- Safety behavior: AI must never return an executable auto-remediation action.

## Open Questions

- [ ] Exact data pack format from mentor.
- [ ] Whether runbook/docs are provided or AI-authored.
- [ ] Target alert burst volume for CDO load test.
