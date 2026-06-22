# AI API Contract - TF1 Triage Hub

Owner: AI team TF1  
Status: Draft for CDO review  
Freeze target: 2026-06-25

## Purpose

Define the API exposed by the AI triage engine and consumed by the AIOps detector/context layer or deployment platform. The API receives a normalized incident context bundle and returns a diagnosis, confidence, suggested next steps, and payloads that the integration layer can use for Jira and Slack.

The AIOps detector/context layer invokes this API after continuous telemetry ingestion and lightweight detection has produced an alert/anomaly/incident candidate. The API is not designed for streaming all metrics/logs directly into the triage engine.

## Versioning

- Current version: `v1.0`
- Base path: `/v1`
- Health path: `/healthz`
- Breaking changes require a new path such as `/v2`.
- Non-breaking additions must be optional fields only.

## Authentication

Draft assumption:

- The detector/context layer calls AI over private network or protected API Gateway.
- Each request includes `X-Tenant-Id` and `X-Correlation-Id`.
- Production design should use IAM SigV4 or service-to-service JWT. Capstone demo may use a scoped bearer token if agreed in Deployment Contract.

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

Diagnose an incident from alert metadata plus logs, metrics, recent deploys, ownership, and runbook/docs context.

The endpoint performs compute-first triage: validation, feature extraction, RCA scoring, confidence gating, and safety checks. Bedrock/LLM synthesis may be enabled later, but only after grounded evidence has been produced by the compute layer.

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
    "labels": {"region": "us-east-1"}
  },
  "metrics": [],
  "logs": [],
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

Field definitions are in `telemetry-contract.md`.

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
      "audit_id": "audit-001"
    }
  },
  "slack_payload": {
    "channel": "#oncall-payments",
    "text": "checkout-api high latency. Suspected deploy-related DB timeout. Confidence 0.82. Jira: pending."
  },
  "audit_id": "audit-001"
}
```

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
- `slack_payload`
- `audit_id`

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
| `DIAGNOSED` | AI has enough context to suggest next steps. | Create ticket and notify owner. |
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

This behavior exists so the detector/context and Jira/Slack integration layers can integrate against stable response shapes before the final AI logic is added.

## Error Codes

| Code | Meaning | Integration action |
|---:|---|---|
| 400 | Invalid schema or tenant mismatch | Do not retry until request fixed. |
| 401 | Authentication failed | Refresh credentials and retry once. |
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

## Open Questions

- [ ] Final auth mechanism if detector/context and triage are deployed as separate services.
- [ ] Whether Slack/Jira payload generation belongs in AI response or integration transforms from AI summary.
- [ ] Exact payload size limit after mentor data pack is reviewed.
