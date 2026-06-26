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

W11 contract assumption:

- The CDO/platform incident integration layer calls AI over private network or protected API Gateway.
- Each request includes `X-Tenant-Id` and `X-Correlation-Id`.
- Production design should use IAM SigV4 or service-to-service JWT.
- Capstone demo may use a scoped bearer token stored in platform secret management if IAM/JWT is not ready by deployment freeze.

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
|---|---:|---|
| `X-Tenant-Id` | yes | Must match request body `tenant_id`. |
| `X-Correlation-Id` | yes | End-to-end workflow trace id. |
| `Authorization` | yes | Auth scheme finalized in Deployment Contract. |

### Request Body

```json
{
  "correlation_id": "corr-001",
  "tenant_id": "tenant-a",
  "incident_id": "inc-001",
  "environment": "sandbox",
  "received_at": "2026-06-22T08:05:00Z",
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

Recommended action objects should be actionable enough for Slack/Jira rendering. When available, each action includes:

- `id`: stable catalog action id.
- `priority`: display/execution order for human responders.
- `summary`: concise next step.
- `why`: why this action matches the evidence.
- `risk`: `low`, `medium`, or `high`.
- `evidence_refs`: references back to response evidence fields.
- `runbook_ref`: runbook URL/reference when available.
- `requires_human_approval`: whether CDO must force explicit approval before action.
- `approval_reason`: reason approval is required.

Current action coverage includes missing context, noisy alerts, service down, latency/dependency timeout, recent deploy rollback consideration, resource saturation, disk pressure, queue/Kafka lag, auth failures, DNS/TLS/network errors, Kubernetes crash loops, rate-limit/throttling, and internal runbook review. All actions remain recommendations only.

### Jira And Slack Integration Requirements

Jira and Slack are core consumers of this contract. The AI engine must return stable raw fields that CDO can use across the project for ticket creation and notification rendering.

AI engine responsibilities:

- Return `ticket_payload` for Jira issue creation.
- Return `recommended_actions` as advisory action objects.
- Return `suggested_assignee_account_id` and `suggestion_reason` when bounded Jira history/accountId evidence is configured.
- Return `audit_id` so Jira/Slack artifacts can reference the AI decision.
- Return evidence and confidence fields so Slack/Jira messages remain explainable.
- Never return executable remediation actions.
- Never perform Jira mutation or Slack posting directly.

CDO/platform responsibilities:

- Render Slack Block Kit from the raw AI response fields.
- Create Jira issues from `ticket_payload`.
- Attach `audit_id`, confidence, evidence, and recommended action summaries to Slack/Jira surfaces.
- Require human confirmation before assigning Jira to a person from `suggested_assignee_account_id`.
- Own Slack/Jira credentials, retries, UI actions, and message formatting.

The response intentionally does not include a rendered `slack_payload`; this keeps Slack presentation owned by CDO while preserving a stable AI data contract.

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

## Error Codes

| Code | Meaning | Integration action |
|---:|---|---|
| 400 | Invalid schema or tenant mismatch | Do not retry until request fixed. |
| 401 | Authentication failed | Refresh credentials and retry once. |
| 429 | Rate limited after 60 requests/minute/tenant for W11 demo capacity | Exponential backoff and queue. |
| 500 | Unexpected AI error | Create fallback ticket with raw alert context. |
| 503 | AI unavailable | Use rule-based fallback or queue retry. |

## SLA Targets

| Metric | Target | Measurement |
|---|---:|---|
| P99 latency | < 2 seconds for demo | Measured over 5-minute windows at the API ingress or service metrics endpoint. |
| Availability | >= 99.5% for demo target | Measured from 5-minute request/error-rate metrics: successful `/healthz` plus non-5xx `/v1/triage` responses divided by total eligible requests. |
| Rate limit | 60 requests/minute/tenant for W11 demo capacity | Measured by tenant-scoped ingress or app counter; excess returns `429`. |
| Max payload size | 512 KB unless changed by platform constraints | Enforced before RCA/LLM processing. |

## Safety Rules

- AI must not auto-remediate.
- AI may suggest human-reviewed commands or runbook steps.
- AI should not recommend destructive actions on databases or production infrastructure unless phrased as human-reviewed escalation and backed by runbook/docs.
- Low confidence must return `INVESTIGATE` or `INSUFFICIENT_CONTEXT`, not a strong root cause.

## W11 Implementation Scope

| Item | Contract requirement |
|---|---|
| Auth for W11 demo | Private network or protected gateway plus scoped bearer token fallback. IAM SigV4 or service-to-service JWT remains the production-preferred mechanism. |
| Slack/Jira ownership | AI response includes raw diagnosis fields, `ticket_payload`, and optional assignee suggestion fields. CDO owns Slack Block Kit rendering, Jira issue creation, and human-confirmed personal assignment. |
| Payload limit | Keep request and response payloads at 512 KB for W11. Larger logs/traces are hosted as bounded evidence bundles or evidence URIs, not inlined into `/v1/triage`. |
| Endpoint behavior | `/v1/triage` must not query customer applications directly. Extra data retrieval happens in the AIOps context layer through the observability contract and approved Jira history access. |
| Alert delivery | CDO/platform pushes alerts/incidents to `/v1/triage`; AI Ops does not poll CDO/customer systems continuously for alert discovery. |
| Evidence retrieval | After alert delivery, AI Ops may pull bounded evidence from the customer observability/evidence layer through CDO/platform-approved access when the initial request has insufficient context. |
| Evidence cleaning | AI Ops owns cleaning, normalization, curation criteria, sample processors, and how cleaned evidence affects RCA confidence. |
| Trace input | `traces` is an optional non-breaking field. RCAEval `traces.csv` and platform trace exports must be normalized into bounded span summaries before calling `/v1/triage`. |
| Slack rendering | CDO renders Slack Block Kit from raw response fields; AI does not return pre-rendered Slack text in the contract response. |
| Jira assignment | AI may suggest `suggested_assignee_account_id` and `suggestion_reason` from configured Jira history/accountId mappings. CDO must require human confirmation before assigning Jira to a person. If no mapping exists, suggestion fields may be `null` with a queue/team reason. |

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
