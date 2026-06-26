# AI API Contract - TF1 Triage Hub

Owner: AI team TF1
Status: Final candidate for W11 CDO sign-off
Freeze target: 2026-06-25

## Purpose

Define the API exposed by the AI triage engine and consumed by the CDO/platform incident integration layer. The API receives a normalized incident context bundle and returns raw diagnosis data, confidence, suggested next steps, Jira ticket fields, and optional Jira assignee suggestion fields that the integration layer can inject into Slack Block Kit or Jira workflows.

CDO/platform invokes this API after it detects an alert/anomaly/incident and has either assembled bounded context or provided references for AI Ops to fetch bounded context. The API is not designed for streaming all raw metrics/logs directly into the triage engine, and AI Ops is not expected to poll CDO continuously to discover alerts.

## Versioning

- Current version: `v1.0`
- Base path: `/v1`
- Health path: `/healthz`
- Breaking changes require a new path such as `/v2`.
- Non-breaking additions must be optional fields only.

## Authentication

The authentication mechanism for the TF1 Triage Hub is finalized in [Deployment Contract](file:///home/huyvu/test/deployment-contract.md).
- **Capstone Demo**: Scoped Bearer Token authentication via the `Authorization: Bearer <TOKEN>` header, with tokens mapped to tenants in AWS Secrets Manager.
- **Production**: Service-to-service JWT or IAM SigV4.
- Each request must also include `X-Tenant-Id` and `X-Correlation-Id` headers.

## Endpoint: `GET /healthz`

### Purpose

Allow load balancers, deployment checks, and smoke tests to verify the service is reachable.

### Response Body

```json
{
  "status": "ok",
  "service": "tf1-ai-triage-engine",
  "version": "v1"
}
```

## Endpoint: `POST /v1/triage`

### Purpose

Diagnose an incident from alert metadata plus logs, metrics, bounded trace summaries, recent deploys, ownership, and runbook/docs context.

The endpoint performs compute-first triage: validation, bounded evidence gathering through approved tools, cleaning/normalization/curation, feature extraction, RCA scoring, confidence gating, and safety checks. Bedrock/LLM synthesis may be enabled later, but only after grounded evidence has been gathered by the context layer and checked by the compute layer.

Alert delivery is push-based: customer systems publish telemetry into their observability layer, CDO/platform detects the alert, then CDO/platform calls this endpoint when an alert exists. If the initial request is missing useful evidence, AI Ops may use configured read-only evidence access to fetch bounded logs, metrics, traces, deploy metadata, ownership records, or Jira history before final RCA. Evidence lookup must stay tenant/service/environment/time-window scoped, and AI Ops cleans/normalizes/curates the fetched data before triage.

The AI API itself is not a raw telemetry API. CDO may pass evidence inline, pass an `evidence_uri` in `alert.labels`, or expose the evidence API defined in `observability-data-contract.md` for follow-up lookups. The LLM must not receive direct credentials or arbitrary query access; AI Ops uses bounded, allowlisted context tools and passes only cleaned evidence into RCA and synthesis.

### Request Headers

| Header | Required | Notes |
|---|---|---|
| `X-Tenant-Id` | yes | Tenant ID representing the owner of the incident scope. |
| `X-Correlation-Id` | yes | End-to-end workflow trace id. |
| `Authorization` | yes | Bearer Token: `Bearer <TOKEN>` (as defined in Deployment Contract). |

To protect tenant isolation and clarify client errors, the service checks request validation in the following strict order:
1. **Authentication Check (`401 Unauthorized`)**: Extract the Bearer Token from the `Authorization` header. Parse the token which must follow the format `<tenant_id>.<random_secret>`. Fetch the secret from Secrets Manager at `tf1/ai-engine/tenant-{tenant_id}/auth-token`. If the token format is invalid, the secret is not found, or the token does not match the retrieved secret, reject with `401 Unauthorized`.
2. **Tenant Scope Check (`403 Forbidden`)**: Compare the `<tenant_id>` parsed from the token with the `X-Tenant-Id` header. If they do not match, reject with `403 Forbidden`.
3. **Consistency Check (`400 Bad Request`)**: Compare `X-Tenant-Id` header with the request body `tenant_id`. If they do not match, or if the request body is missing required fields, reject with `400 Bad Request`.

### Request Body

```json
{
  "correlation_id": "corr-001",
  "tenant_id": "tenant-a",
  "incident_id": "inc-001",
  "environment": "sandbox",
  "received_at": "2026-06-22T08:05:00Z",
  "first_acknowledged_at": "2026-06-22T08:06:00Z",
  "resolved_at": "2026-06-22T08:15:00Z",
  "alert": {
    "alert_id": "alert-001",
    "source": "synthetic-pack",
    "service": "checkout-api",
    "severity": "high",
    "title": "High p95 latency on checkout-api",
    "description": "p95 latency above threshold for 5 minutes",
    "started_at": "2026-06-22T08:00:00Z",
    "labels": {
      "region": "us-east-1",
      "evidence_uri": "evidence://tenant-a/inc-001"
    }
  },
  "metrics": [],
  "logs": [
    {
      "service": "checkout-api",
      "ts": "2026-06-22T08:03:00Z",
      "level": "error",
      "message": "database timeout after 3000ms",
      "trace_id": "trace-123",
      "curation_reason": "timeout during alert window"
    }
  ],
  "traces": [],
  "recent_deploys": [],
  "ownership": {
    "service": "checkout-api",
    "owner_team": "payments-platform",
    "slack_channel": "#oncall-payments",
    "jira_project": "PAY",
    "runbooks": []
  }
}
```

Field definitions are in `telemetry-contract.md`. Upstream observability and bounded evidence access requirements are in `observability-data-contract.md`. `logs` should be AI-cleaned/curated snippets when possible. `traces` is optional and should contain bounded span summaries only; full trace exports should be hosted as evidence bundles or evidence URIs.

Sample request fixtures are stored in `../engine-skeleton/samples/`.

### Response Body

```json
{
  "incident_id": "inc-001",
  "classification": "latency_degradation",
  "severity": "high",
  "confidence": 0.82,
  "status": "DIAGNOSED",
  "suspected_root_cause": {
    "summary": "Recent checkout-api deploy likely introduced a slower DB query path.",
    "evidence": [
      "p95 latency increased from 220ms to 950ms after sha-a1b2c3",
      "error logs show database timeout after 3000ms",
      "runbook db-timeout matches observed symptoms"
    ]
  },
  "recommended_actions": [
    {
      "type": "HUMAN_REVIEW",
      "priority": 1,
      "summary": "Check DB connection saturation and slow query logs.",
      "runbook_ref": "runbook://db-timeout"
    },
    {
      "type": "ROLLBACK_CONSIDER",
      "priority": 2,
      "summary": "If DB timeout confirms deploy correlation, rollback checkout-api to sha-prev.",
      "runbook_ref": "runbook://rollback-service"
    }
  ],
  "ticket_payload": {
    "project": "PAY",
    "summary": "[high] checkout-api latency degradation",
    "description": "AI triage summary with evidence and next steps.",
    "labels": ["ai-triage", "tenant-a", "checkout-api"],
    "fields": {
      "confidence": 0.82,
      "owner_team": "payments-platform",
      "audit_id": "audit-001",
      "suggested_assignee_account_id": "712020:abc123",
      "suggestion_reason": "Based on recent Jira history, this account is the SME for checkout-api incidents."
    }
  },
  "suggested_assignee_account_id": "712020:abc123",
  "suggestion_reason": "Based on recent Jira history, this account is the SME for checkout-api incidents.",
  "audit_id": "audit-001"
}
```

The response intentionally does **not** include a rendered `slack_payload`. CDO owns Slack presentation and may inject these raw fields into its Block Kit template, including buttons such as acknowledge, view report, or confirm assignment.

Required successful response fields:

- `incident_id`
- `classification`
- `severity`
- `confidence`
- `status`
- `suspected_root_cause.summary`
- `suspected_root_cause.evidence`
- `recommended_actions`
- `ticket_payload`
- `audit_id`

Optional successful response fields:

- `suggested_assignee_account_id`
- `suggestion_reason`

Assignee suggestions are advisory only. CDO may show them in Slack, but a human must confirm before Jira is assigned to a person.

`recommended_actions[].type` must be advisory only. Allowed values for v1 are:

- `HUMAN_REVIEW`
- `RUNBOOK_CHECK`
- `ROLLBACK_CONSIDER`
- `ESCALATE_OWNER`
- `OBSERVE`

The API must not return auto-executing action types.

### Response Status Values

| Status | Meaning | Integration action |
|---|---|---|
| `DIAGNOSED` | AI has enough context to suggest next steps. | Create ticket, render Slack Block Kit from raw response fields, and notify owner. |
| `INVESTIGATE` | Weak or ambiguous signal; do not overstate cause. | Create ticket with investigation label. |
| `INSUFFICIENT_CONTEXT` | Required context missing or stale. | Create fallback ticket and include missing fields. |
| `UNSAFE_SUGGESTION_BLOCKED` | Candidate suggestion violated safety boundary. | Create ticket with no unsafe action. |

## Deterministic Skeleton Behavior

Before LLM integration, the skeleton service returns rule-based deterministic responses:

| Input pattern | Status | Classification |
|---|---|---|
| Required alert exists but all context arrays/ownership are empty | `INSUFFICIENT_CONTEXT` | `insufficient_context` |
| Critical service-down or availability title plus strong error signal | `DIAGNOSED` | `critical_service_down` |
| Latency title or latency metric with supporting deploy/log evidence | `DIAGNOSED` | `latency_degradation` |
| Low severity, noisy, flapping, or conflicting signals | `INVESTIGATE` | `noisy_or_ambiguous_alert` |

This behavior exists so the CDO/platform incident integration and Jira/Slack integration layers can integrate against stable raw response shapes before the final AI logic is added.

## Reserved W12 Optional Interfaces

These endpoints reserve the W12 interface surface so CDO and AI can build without changing the frozen `/v1` contract. They are not required for the W11 skeleton demo. If not implemented yet, they must return `404` or `501` consistently and must not change `/v1/triage` behavior.

### Slack Two-Way Read-Only API

Slack endpoints are for read-only follow-up questions and button actions against an existing incident/report. They must not expose remediation execution.

| Endpoint | Purpose | Required verification |
|---|---|---|
| `POST /v1/slack/events` | Receive Slack app event callbacks. | Verify Slack signing secret before processing. |
| `POST /v1/slack/commands` | Receive slash command payloads such as incident lookup or RCA explanation. | Verify Slack signing secret before processing. |
| `POST /v1/slack/actions` | Receive interactive button payloads such as Explain RCA, Show Evidence, Show Actions, or Show Jira Payload. | Verify Slack signing secret before processing. |

Slack request verification requirements:

- Use `SLACK_SIGNING_SECRET`.
- Validate `X-Slack-Signature`.
- Validate `X-Slack-Request-Timestamp`.
- Reject stale timestamps before parsing business payload.
- Reject invalid signatures with `401` or `403`.
- Process only authorized Slack channels/users configured for the incident tenant.

Slack responses must stay read-only and scoped to bounded report/evidence context. They may post a threaded reply, but must not trigger rollback, restart, scale, database, or shell actions.

### Jira Lifecycle Sync API

The W11 contract returns `ticket_payload` plus optional assignee suggestion fields only. W12 may add a callback/update endpoint for Jira lifecycle synchronization.

| Endpoint | Purpose | Notes |
|---|---|---|
| `PATCH /v1/incidents/{incident_id}/lifecycle` | Update incident lifecycle metadata when Jira status changes or a human closes the ticket. | Optional W12 extension. Body fields: `status` (enum: `OPEN`, `ACKNOWLEDGED`, `IN_REVIEW`, `RESOLVED`, `CLOSED`), `first_acknowledged_at` (RFC3339 timestamp), and `resolved_at` (RFC3339 timestamp). |

Accepted lifecycle states should be limited to non-destructive workflow metadata such as `OPEN`, `ACKNOWLEDGED`, `IN_REVIEW`, `RESOLVED`, and `CLOSED`. Jira remains the source of truth for Jira issue status; AI stores only audit/report metadata.

### Audit Query API

W11 audit data is available through report JSON. W12 may expose audit lookup by `audit_id` for external reviewers and integration flows.

| Endpoint | Purpose | Notes |
|---|---|---|
| `GET /v1/audit/{audit_id}` | Return the stored triage decision, evidence references, report link, and integration metadata for one audit id. | Must enforce tenant isolation and may return `404` when the audit record is not retained. |

The audit response must not expose raw unbounded logs, secrets, tokens, or cross-tenant data.

### Human Feedback And Retrain Trigger Design

Human feedback is a design target, not a W11 built endpoint. CDO may collect whether an engineer confirmed or corrected the RCA in Slack/Jira and store it in the audit trail. A future W12 API may accept feedback events without changing `/v1/triage`.

| Endpoint | Purpose | Notes |
|---|---|---|
| `POST /v1/incidents/{incident_id}/feedback` | Record human confirmation or correction for the RCA, owner suggestion, or recommended action. | Design-only for W11; must require `audit_id`, tenant scope, and authenticated caller. |

Allowed feedback values should be limited to non-executable audit metadata such as `RCA_CONFIRMED`, `RCA_CORRECTED`, `OWNER_ACCEPTED`, and `OWNER_REJECTED`. Retrain trigger remains offline/design-only until enough reviewed feedback exists; it must not change production model behavior automatically during W11/W12 demos.

## Error Codes

Every rejected or failed request (4xx and 5xx paths) MUST generate a unique `audit_id` and write an audit record to the log/audit trail detailing the cause of failure (e.g., validation failure details, authentication failure, rate limit breach, or stack trace for 500s).

The error response body schema must be:
```json
{
  "error_code": "STRING (e.g. AUTH_FAILED, TENANT_MISMATCH, VALIDATION_ERROR, RATE_LIMITED, AI_ERROR)",
  "message": "STRING (Human readable message)",
  "timestamp": "RFC3339 timestamp",
  "audit_id": "STRING (audit-err-XXX)"
}
```

| Code | Meaning | Integration action |
|---:|---|---|
| 400 | Invalid schema or tenant mismatch (body tenant_id != X-Tenant-Id header) | Do not retry until request fixed. |
| 401 | Authentication failed (token missing or invalid) | Refresh credentials and retry once. |
| 403 | Forbidden (token scope mismatch with X-Tenant-Id) | Do not retry. Contact administrator. |
| 429 | Rate limited | Exponential backoff and queue. |
| 500 | Unexpected AI error | Create fallback ticket with raw alert context. |
| 503 | AI unavailable | Use rule-based fallback or queue retry. |

## SLA Targets

| Metric | Target |
|---|---:|
| P99 latency | < 2 seconds for demo |
| Availability | >= 99.5% design target |
| Max payload size | 512 KB unless changed by platform constraints |

## Safety Rules

- AI must not auto-remediate.
- AI may suggest human-reviewed commands or runbook steps.
- AI should not recommend destructive actions on databases or production infrastructure unless phrased as human-reviewed escalation and backed by runbook/docs.
- Low confidence must return `INVESTIGATE` or `INSUFFICIENT_CONTEXT`, not a strong root cause.

## W11 Decisions And Deferred Items

| Item | W11 decision |
|---|---|
| Auth for W11 demo | Scoped Bearer Token authentication via the `Authorization: Bearer <tenant_id>.<random_secret>` header (finalized). Token secrets are stored at `tf1/ai-engine/tenant-{tenant_id}/auth-token` in Secrets Manager. |
| Slack/Jira ownership | AI response includes raw diagnosis fields, `ticket_payload`, and optional assignee suggestion fields. CDO owns Slack Block Kit rendering, Jira issue creation, and human-confirmed personal assignment. |
| Payload limit | Keep request and response payloads at 512 KB for W11. Larger logs/traces are hosted as bounded evidence bundles or evidence URIs, not inlined into `/v1/triage`. |
| Endpoint behavior | `/v1/triage` must not query customer applications directly. Extra data retrieval happens in the AIOps context layer through the observability contract and approved Jira history access. |
| Alert delivery | CDO/platform pushes alerts/incidents to `/v1/triage`; AI Ops does not poll CDO/customer systems continuously for alert discovery. |
| Evidence retrieval | After alert delivery, AI Ops may pull bounded evidence from the customer observability/evidence layer through CDO/platform-approved access when the initial request has insufficient context. |
| Evidence API | If live follow-up is enabled, CDO should expose `GET /v1/evidence/incidents/{incident_id}` and/or `POST /v1/evidence/query` as described in `observability-data-contract.md`. |
| Evidence cleaning | AI Ops owns cleaning, normalization, curation criteria, sample processors, and how cleaned evidence affects RCA confidence. |
| Trace input | `traces` is an optional non-breaking field. RCAEval `traces.csv` and platform trace exports must be normalized into bounded span summaries before calling `/v1/triage`. |
| Slack rendering | CDO renders Slack Block Kit from raw response fields; AI does not return pre-rendered Slack text in the contract response. |
| Jira assignment | AI may suggest `suggested_assignee_account_id` and `suggestion_reason` from configured Jira history/accountId mappings. CDO must require human confirmation before assigning Jira to a person. If no mapping exists, suggestion fields may be `null` with a queue/team reason. |
| Human feedback | Engineer confirm/correct feedback is audit-trail metadata only for W11. Retrain trigger is a design-only future hook, not an automatic production update. |

## W11 Sign-Off

This contract is the AI-owned draft for CDO review and onsite sign-off on 2026-06-25.

| Role | Name | Signature | Date | Status | Notes |
|---|---|---|---|---|---|
| AI lead | Đinh Danh Nam |  |  | Ready for signature | Owns API schema, validation, response behavior, and safety boundary. |
| CDO tech lead 1 | Nguyễn Đức Tiến |  |  | Ready for signature | Confirms platform can call `/healthz` and `/v1/triage` with required headers. |
| CDO tech lead 2 | Nguyễn Đỗ Khánh Hưng |  |  | Ready for signature | Confirms platform can handle response statuses, retries, and payload limits. |
| Mentor witness | TBD |  |  | Pending onsite | Witnesses contract freeze. |

Signature may be handwritten on the printed contract or added as an approved electronic signature.

After sign-off, changes to paths, required fields, status semantics, or error handling require a formal ADR or curveball response.
