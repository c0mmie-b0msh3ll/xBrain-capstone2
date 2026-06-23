# Datapack Mapping

Status: Synthetic fixtures complete, RCAEval external adapter scaffolded  
Owner: TF1 AI team  
Last updated: 2026-06-23

## Dataset Strategy

- **Primary RCA validation direction**: RCAEval public dataset.
- **Demo/smoke fixtures**: `datapack/scenarios/*` synthetic fixtures.

Synthetic fixtures should be used to prove API shape and Jira/Slack payload wiring. RCAEval should be used for credible RCA evaluation once the external data is downloaded.

## Workflow

1. Generate synthetic observability records for each required incident scenario.
2. Validate raw records against `contracts/observability-data-contract.md`.
3. Convert bounded observability data into `contracts/telemetry-contract.md`.
4. Call `POST /v1/triage` with each scenario request.
5. Verify expected status/classification/confidence band.

## Scenario Inventory

| Scenario | Raw service | Expected detection | Expected triage |
|---|---|---|---|
| `critical-service-down` | `checkout-api` | incident candidate fires | `DIAGNOSED / critical_service_down` |
| `latency-degradation` | `payment-api` | incident candidate fires | `DIAGNOSED / latency_degradation` |
| `noisy-false-alert` | `search-api` | weak/noisy candidate | `INVESTIGATE / noisy_or_ambiguous_alert` |

## Raw To Observability Contract

| Raw datapack field | Observability contract field | Mapping type | Notes |
|---|---|---|---|
| `tenant_id` | `tenant_id` | direct | Tenant isolation key. |
| `service` | `service` | direct | Service under observation. |
| `environment` | `environment` | direct | `sandbox` in synthetic v1. |
| `region` | `region` | direct | `us-east-1` in synthetic v1. |
| `timestamp` | `timestamp` | direct | RFC3339 event timestamp. |
| `source` | `source` | direct | `prometheus`, `loki`, or `github-actions`. |
| `labels` | `labels` | direct | Endpoint, pod, dependency, version, detector. |
| `trace_id` | `trace_id` | direct | Present on logs when available. |
| `version` | `version` / `deploy_id` | direct | Deploy correlation field. |
| `metric_name` + `value` + `unit` | metric family record | direct | Used by AIOps windowing/baseline logic. |
| `level` + `message` | log record | direct | Bounded log snippets, no PII. |

## Observability To Triage Context

| Observability data | Normalized triage field | Mapping type | Notes |
|---|---|---|---|
| scenario id | `correlation_id`, `incident_id`, `alert.alert_id` | derived | Stable synthetic IDs per scenario. |
| `tenant_id` | `tenant_id` | direct | Must match request header. |
| `environment` | `environment` | direct | Must be contract enum. |
| detector timestamp | `received_at`, `alert.started_at` | derived | Synthetic alert timing from scenario window. |
| detector result | `alert.title`, `alert.description`, `alert.severity`, `alert.labels.detector` | derived | Encodes why incident candidate exists. |
| metric records | `metrics[]` | derived | Grouped by `metric_name` into time-series points. |
| log records | `logs[]` | derived | Sampled relevant snippets around incident window. |
| deploy records | `recent_deploys[]` | direct | Empty array when no deploy is relevant. |
| `ownership.json` + `runbooks.json` | `ownership` | derived | Combines owner routing and runbook snippets. |

## Missing Or Defaulted Fields

| Field | Mapping type | Handling |
|---|---|---|
| `authorization` | missing | Not in datapack; supplied by caller only if `SERVICE_AUTH_TOKEN` is enabled. |
| Persistent audit record | missing | Skeleton returns deterministic `audit_id`; storage is future work. |
| Real production PII redaction evidence | defaulted | Synthetic datapack contains no PII by construction. |
| Real tenant catalog | defaulted | Synthetic v1 uses `tenant-a`. |

## Adapter Acceptance Criteria

- Raw records include required metadata from `observability-data-contract.md`.
- Triage request passes `/v1/triage` validation.
- Missing optional context is represented as empty arrays or nullable fields.
- Contract changes are proposed only for missing concepts, not raw field-name differences.
- Run `python scripts/validate_datapack.py` from `engine-skeleton` before demo.
