# Triage Context Contract - TF1 Triage Hub

Owner: AI team TF1
Status: Final candidate for W11 CDO sign-off
Freeze target: 2026-06-25

## Purpose

Define the normalized incident context bundle passed from the AIOps context service to the AI triage engine. For TF1, the triage request is one bounded alert/incident package, not continuous raw metrics/logs.

The current production assumption is:

```text
Customer applications
  -> customer-owned observability layer exposed through CDO/platform access
  -> alert/incident detection
  -> push incident seed or normalized context to AI Ops
  -> AI Ops validates context and performs bounded evidence lookup if needed
  -> AI Ops cleans, redacts, normalizes, and curates evidence
  -> Triage API request with normalized context bundle
  -> AI compute-first RCA + optional LLM synthesis
  -> AI diagnosis + Jira ticket fields + Slack-renderable raw fields
```

The triage engine is event-driven. It is invoked when CDO/platform has detected an alert/anomaly/incident and sends a request to the AI Ops API. AI Ops may query additional bounded evidence only after an incident exists; it is not responsible for continuously polling CDO/customer telemetry to discover alerts.

The upstream platform/observability handoff is defined separately in `observability-data-contract.md`.

## Contract Boundary

The AIOps app consumes incident-scoped context from CDO/platform, runs validation, bounded evidence lookup, cleaning/redaction, normalization, evidence sufficiency checks, RCA scoring, and then sends normalized incident context to `POST /v1/triage`. Mentor datapack files are treated as raw source material and must be adapted into this contract before calling the triage engine.

Field-name differences in the datapack should be handled in an adapter. The contract changes only when the datapack exposes a missing concept that cannot be represented by existing fields.

The context layer may connect to bounded evidence APIs/storage for logs, metrics, traces, deploy stores, ownership catalogs, and Jira history as part of the broader AIOps app. The triage endpoint itself receives normalized bounded context instead of pulling unbounded raw telemetry during RCA. Slack presentation is owned by CDO and is rendered from raw response fields, not from pre-rendered AI text.

## Alert Delivery And Evidence Retrieval Model

Alert delivery is push-based:

```text
CDO detects alert -> CDO calls AI Ops endpoint -> AI Ops starts triage immediately
```

Evidence retrieval can be pull-based after the alert:

```text
AI Ops needs more context -> AI Ops calls bounded access to the customer's observability/evidence layer -> AI Ops cleans/curates evidence -> RCA/optional LLM synthesis starts
```

This split avoids polling delay for alert delivery while still allowing AI Ops to ask for more context when the initial datapack is insufficient.

The current app already has bounded read-only context tools for metrics, logs, deploy metadata, ownership, and internal RCA helpers. Production use should point those tools at the bounded access path that CDO/platform exposes for the customer's observability layer, not directly at customer applications or raw unbounded telemetry systems. AI Ops owns the cleaning/normalization/curation step before data enters the triage prompt or RCA logic. The LLM must use allowlisted tools and cleaned evidence only; it must not receive backend credentials or arbitrary query privileges.

## Required Envelope

Every request to AI must include:

| Field | Type | Required | Notes |
|---|---|---:|---|
| `correlation_id` | string | yes | Stable trace id for the full workflow. |
| `tenant_id` | string | yes | Required for isolation. Missing tenant is rejected. |
| `incident_id` | string | yes | Unique incident/alert grouping id. Used as primary idempotency key. |
| `environment` | enum | yes | `prod`, `staging`, `sandbox`. |
| `received_at` | RFC3339 | yes | When the AIOps app received or generated the alert. |
| `first_acknowledged_at` | RFC3339 | no | Optional. When the incident was first acknowledged by an on-call engineer. Used for MTTA calculation (Option b simulated in W11). |
| `resolved_at` | RFC3339 | no | Optional. When the incident was marked as resolved/mitigated. Used for MTTR calculation (Option b simulated in W11). |

Validation rules:

- `tenant_id` must be non-empty and must match the `X-Tenant-Id` request header.
- `correlation_id` must be non-empty and must match the `X-Correlation-Id` request header.
- `environment` must be one of `prod`, `staging`, or `sandbox`.
- `first_acknowledged_at` and `resolved_at` must be valid RFC3339 timestamps if provided.
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

Recommended optional alert labels for evidence lookup:

| Label | Notes |
|---|---|
| `evidence_uri` | Pointer to precomputed evidence bundle, e.g. S3/object key or bounded evidence API reference exposed by CDO/platform. |
| `trace_id` / `correlation_id` | Helps fetch trace summary and correlated logs. |
| `region` | Required when platform evidence is region-scoped. |
| `cluster` / `namespace` | Helps scope Kubernetes or multi-cluster evidence queries. |
| `metric_names` | Recommended list of metric names that triggered the alert, e.g. latency/error-rate metrics. |
| `suspected_dependency` | Optional dependency hint from alert labels, e.g. `postgres`, `redis`, or downstream service name. |
| `source` | Original monitoring source, e.g. `prometheus`, `cloudwatch`, `datadog`, or `cdo-detector`. |
| `jira_component` / `jira_project` | Optional routing hints if the alerting platform already knows the Jira target. |

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

Logs should be sampled, not dumped raw. The context layer should provide relevant snippets around the alert window.

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
- Prefer AI-curated logs: meaningful, redacted snippets selected from noisy bounded log results by AI Ops cleaning/curation logic.

## AI-Curated Log Records

Production deployments may include an AI Ops cleaning/curation step after bounded evidence lookup. CDO/platform exposes safe incident-scoped log access; AI Ops filters the bounded results into logs that are useful for RCA.

Minimal curated log fields:

| Field | Type | Required | Notes |
|---|---|---:|---|
| `service` | string | yes | Emitting or affected service. |
| `ts` | RFC3339 | yes | Event timestamp. |
| `level` | string | yes | `error`, `warning`, `info`, etc. |
| `message` | string | yes | Redacted useful message. |
| `trace_id` | string | optional | For trace/log correlation. |
| `curation_reason` | string | recommended | Why this line is relevant, e.g. `error_burst`, `timeout`, `dependency_failure`, `deploy_correlation`. |
| `source_ref` | string | optional | Pointer to original backend query/log stream without exposing credentials. |
| `labels` | object | optional | Pod, version, endpoint, dependency, region, dataset lineage. |

Ownership boundary:

- The customer's observability layer owns production telemetry storage and retention. CDO/platform owns the integration boundary, auth, tenant isolation, and bounded query access exposed to AI Ops.
- AI Ops owns cleaning, redaction before AI use when needed, curation criteria, sample processor logic, and how curated logs are consumed for RCA.
- AI Ops must not receive arbitrary raw log backend credentials or unbounded raw log dumps.

## Traces Window

Traces should be passed as bounded span summaries, not full raw trace exports. The context layer should include spans or trace summaries that are relevant to the incident service, dependencies, and alert window.

```json
{
  "trace_id": "trace-123",
  "span_id": "span-456",
  "parent_span_id": "span-root",
  "service": "checkout-api",
  "operation": "POST /v1/orders",
  "ts": "2026-06-22T08:03:01Z",
  "duration_ms": 1250,
  "status_code": "500",
  "labels": {"dependency": "postgres", "source_dataset": "RCAEval"}
}
```

Rules:

- `traces` is optional for `/v1/triage`; pass an empty array when trace data is unavailable.
- Preserve `trace_id`, `span_id`, service name, operation name, timestamp, duration, and response/status code when available.
- Keep only spans in the bounded incident window and relevant dependency path.
- Full trace dumps should be hosted as evidence bundles or evidence URIs, not inlined into `/v1/triage`.
- RCAEval `traces.csv` rows are adapted into this normalized span-summary shape.

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

Required for deploy-related diagnosis. If not available, the context layer must pass an empty array and AI will lower confidence.

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

The AI engine can accept empty arrays for `metrics`, `logs`, `traces`, and `recent_deploys`, but response status changes based on available context:

| Context state | Expected AI behavior |
|---|---|
| Missing required envelope or alert fields | Reject with `400`. |
| Alert exists but no metrics, logs, traces, deploys, or ownership | Return `INSUFFICIENT_CONTEXT`. |
| Signals conflict or indicate a noisy/non-impacting alert | Return `INVESTIGATE`. |
| Logs/metrics/traces/deploys support a scenario diagnosis | Return `DIAGNOSED`. |

When context is insufficient and an evidence URI/API is configured, AI Ops may perform a bounded follow-up lookup before returning the final response. If no evidence source is configured or the lookup fails, AI Ops returns `INSUFFICIENT_CONTEXT` or lower-confidence `INVESTIGATE` instead of expanding the query scope.

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
- `defaulted`: supplied by TF1/AIOps when source lacks the field.
- `missing`: unavailable and not safely defaulted.

## Delivery And Quality

- Upstream dependency: platform/DevOps provides bounded observability access as defined in `observability-data-contract.md`.
- Delivery mode: push request from CDO/platform to the AI Ops API after an alert exists.
- Invocation mode: event-driven after platform alert detection, not continuous full triage over all telemetry.
- Detection ownership: CDO/platform owns alert detection and incident triggering; AI Ops owns incident-level RCA after invocation.
- Extra context ownership: Customer observability is the source of truth. CDO/platform exposes bounded evidence access; AI Ops may pull bounded evidence after alert delivery, clean/curate it, and then triage.
- Duplicate handling: the caller must provide `incident_id`; AI responses must be idempotent for the same `incident_id`.
- Missing data behavior: AI returns lower confidence or `INSUFFICIENT_CONTEXT`.
- Malformed data behavior: AI returns `400` with validation errors.
- Safety behavior: AI must never return an executable auto-remediation action.
- Slack behavior: AI returns raw diagnosis fields only; CDO renders Slack Block Kit.
- Jira assignment behavior: AI may suggest a Jira `accountId` only when configured Jira history/accountId mapping supports it. Human confirmation is required before personal assignment.

## W11 Decisions And Deferred Items

| Item | W11 decision |
|---|---|
| Primary datapack format | Use the RCAEval subset under `../engine-skeleton/datapack/external/` as the primary scenario data. The CDO-hostable artifacts are normalized evidence bundles under `../engine-skeleton/datapack/external/evidence-bundles/`. Both CDO platforms must use the identical test fixture dataset to ensure E2E scenario validation alignment. |
| Triage request format | `POST /v1/triage` remains the normalized incident context contract. Raw dataset fields are adapted before calling the triage endpoint. |
| Runbooks/docs | If RCAEval does not provide runbooks or ownership, TF1 supplies minimal supplemental runbook/ownership records and marks them as supplemental in `data_lineage`. |
| Evidence follow-up | Optional bounded access to the customer's observability/evidence layer can be used after alert delivery for follow-up context. AI Ops owns cleaning/curation before triage. Required operations are described in the supporting `observability-data-contract.md`. |
| Jira history for suggestion | AI Ops may use bounded Jira history/accountId mapping to return an advisory assignee suggestion. CDO owns human confirmation and actual Jira assignment. |
| Load target for W11 skeleton | Initial capstone target is 30 triage requests/minute with API p99 under 2 seconds for bounded payloads. Higher platform load testing is deferred until CDO infrastructure is finalized. |
| Deferred mentor item | If the mentor provides a different official datapack shape, create an adapter and mapping table instead of changing the triage contract unless a required concept is missing. |

## W11 Sign-Off

This contract is the AI-owned draft for CDO review and onsite sign-off on 2026-06-25.

| Role | Name | Signature | Date | Status | Notes |
|---|---|---|---|---|---|
| AI lead | Đinh Danh Nam |  |  | Ready for signature | Owns triage context schema and adapter behavior. |
| CDO tech lead 1 | Nguyễn Đức Tiến |  |  | Ready for signature | Confirms platform can provide bounded evidence required by this contract. |
| CDO tech lead 2 | Nguyễn Đỗ Khánh Hưng |  |  | Ready for signature | Confirms platform can integrate with the same normalized context boundary. |
| Mentor witness | TBD |  |  | Pending onsite | Witnesses contract freeze. |

Signature may be handwritten on the printed contract or added as an approved electronic signature.

After sign-off, changes to required fields or response-breaking semantics require a formal ADR or curveball response.
