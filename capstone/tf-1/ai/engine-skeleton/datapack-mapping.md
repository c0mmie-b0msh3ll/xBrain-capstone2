# Mentor Datapack Mapping

Status: Waiting for mentor datapack  
Owner: TF1 AI team

## Workflow

1. Inventory all datapack files and identify record types.
2. Map raw datapack fields into `contracts/telemetry-contract.md`.
3. Mark each mapping as `direct`, `derived`, `defaulted`, or `missing`.
4. Build an adapter that emits valid `/v1/triage` request JSON.
5. Produce at least three transformed fixtures: critical down, latency degradation, noisy alert.

## Mapping Table

| Raw datapack field | Normalized field | Mapping type | Notes |
|---|---|---|---|
| TBD | `correlation_id` | TBD | Stable workflow trace id. |
| TBD | `tenant_id` | TBD | Required for isolation. |
| TBD | `incident_id` | TBD | Alert grouping id. |
| TBD | `environment` | TBD | `prod`, `staging`, or `sandbox`. |
| TBD | `received_at` | TBD | RFC3339 timestamp. |
| TBD | `alert.alert_id` | TBD | Original alert id. |
| TBD | `alert.source` | TBD | Alert source system. |
| TBD | `alert.service` | TBD | Affected service. |
| TBD | `alert.severity` | TBD | Contract enum. |
| TBD | `alert.title` | TBD | Human-readable title. |
| TBD | `alert.description` | TBD | Raw alert description. |
| TBD | `alert.started_at` | TBD | Alert start timestamp. |
| TBD | `metrics[]` | TBD | Time-series metrics window. |
| TBD | `logs[]` | TBD | Sampled log snippets. |
| TBD | `recent_deploys[]` | TBD | Recent deploy context. |
| TBD | `ownership` | TBD | Owner team, Slack channel, Jira project, runbooks. |

## Adapter Acceptance Criteria

- Adapter output passes `/v1/triage` validation.
- Missing optional context is represented as empty arrays or nullable fields, not omitted ambiguous data.
- Missing required context is documented and either derived/defaulted safely or rejected before API call.
- Contract changes are proposed only for missing concepts, not raw field-name differences.
