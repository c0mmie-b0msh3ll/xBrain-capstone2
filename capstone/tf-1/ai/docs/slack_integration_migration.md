# Triage-Hub Slack/Jira SQS Handoff

Status: AI publisher implemented for CDO integration  
Owner boundary: AI publishes triage events; CDO owns Slack/Jira mutation and presentation.

## Purpose

The AI worker can publish one normalized event to the CDO Triage-Hub notify SQS queue after it receives a `/v1/triage` response. CDO dispatchers consume that event and remain responsible for Jira creation/update, Slack Block Kit rendering, buttons, assignment changes, and any follow-up workflow state.

Backward compatibility: the existing `SLACK_WEBHOOK_URL` publisher remains available for local demos and existing users. Triage-Hub SQS is additive and optional. The old Slack webhook path can be retired later only after CDO confirms the SQS dispatcher path is deployed and accepted.

This keeps the public `/v1/triage` request/response contract unchanged. The SQS message is a delivery handoff for CDO notification automation, not a new AI API response shape.

## Unified Payload Contract

The AI worker sends this JSON object as the SQS `MessageBody`:

```json
{
  "incident_id": "inc-checkout-api-001",
  "tenant_id": "tenant-a",
  "alert": {
    "alert_id": "alert-checkout-api-001",
    "source": "cdo-detector",
    "service": "checkout-api",
    "severity": "high",
    "title": "High p95 latency on checkout-api",
    "description": "p95 latency above threshold",
    "started_at": "2026-06-24T08:45:00Z",
    "labels": {
      "environment": "prod"
    }
  },
  "ownership": {
    "service": "checkout-api",
    "owner_team": "payments-platform",
    "slack_channel": "#oncall-payments",
    "jira_project": "PAY"
  },
  "classification": "latency_degradation",
  "confidence": 0.82,
  "status": "DIAGNOSED",
  "suspected_root_cause": {
    "summary": "checkout-api latency is most consistent with dependency timeout signals.",
    "evidence": [
      "database timeout after 3000ms",
      "http_latency_p95_ms breached threshold"
    ]
  },
  "recommended_actions": [
    {
      "id": "dependency_timeout_triage",
      "summary": "Review dependency timeout signals and connection pool saturation.",
      "risk": "low",
      "requires_human_approval": false
    }
  ],
  "suggested_assignee_account_id": "712020:abc123",
  "suggestion_reason": "Most recent checkout-api incidents were handled by the primary on-call."
}
```

Field ownership:

| Field | Source |
|---|---|
| `incident_id`, `tenant_id`, `alert`, `ownership` | Original incident/request context sent to AI. |
| `classification`, `confidence`, `status`, `suspected_root_cause`, `recommended_actions`, `suggested_assignee_account_id`, `suggestion_reason` | AI `/v1/triage` response fields. |

## AI-Side Publisher Behavior

Implementation: `capstone/tf-1/ai/engine-skeleton/app/aiops_worker.py`

- `build_triage_hub_notify_payload(response, request_context)` maps the request context and triage response into the CDO payload.
- `publish_to_triage_hub_sqs(response, request_context, dry_run)` sends the JSON body to SQS when configured.
- Existing `publish_slack(response, dry_run, report_url)` behavior is preserved for backward compatibility.
- Missing queue URL or explicit dry-run prints `triage_hub_sqs_dry_run` and does not call AWS.
- Live publish calls `sqs:SendMessage` with the configured queue URL.
- Existing inbound SQS incident-seed processing is unchanged; this notify queue is a separate outbound queue.

Required environment:

```text
TRIAGE_HUB_NOTIFY_SQS_URL=<CDO notify queue URL>
AWS_REGION=<queue region>
# or AWS_DEFAULT_REGION=<queue region>
```

Optional dry-run:

```text
TRIAGE_HUB_NOTIFY_SQS_DRY_RUN=true
```

The CLI also supports:

```text
--dry-run-triage-hub-sqs
```

## Required IAM

The AI worker runtime role needs permission to send messages to the CDO notify queue:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "sqs:SendMessage",
      "Resource": "<TRIAGE_HUB_NOTIFY_QUEUE_ARN>"
    }
  ]
}
```

## Ownership Boundary

AI owns:

- RCA/classification/confidence/recommended action fields.
- Mapping to the agreed SQS payload.
- Publishing to the queue when CDO provides URL and IAM.
- Dry-run output for local smoke tests.

CDO owns:

- Notify queue provisioning, queue URL, queue ARN, IAM trust/permissions, and DLQ policy.
- Slack Block Kit layout, buttons, interaction handlers, and message updates.
- Jira issue creation/update, assignee mutation, transitions, and comments.
- Any dispatcher idempotency, retry, and audit implementation on the CDO side.

## CDO Message Template

AI will publish a Triage-Hub notify event after `/v1/triage` completes. The event body contains incident/request context plus AI fields: classification, confidence, status, suspected root cause, recommended actions, suggested assignee accountId, and suggestion reason.

Minimal smoke-test JSON accepted by the current mapper:

```json
{
  "incident_id": "inc-test-001",
  "tenant_id": "tenant-a",
  "alert": {
    "service": "checkout-api",
    "severity": "high",
    "title": "High p95 latency on checkout-api",
    "description": "p95 latency above threshold"
  },
  "ownership": {
    "jira_project": "TRIAGE",
    "slack_channel": "#oncall-alerts"
  },
  "classification": "latency_degradation",
  "confidence": 0.82,
  "status": "DIAGNOSED",
  "suspected_root_cause": {
    "summary": "Database connection pool exhausted",
    "evidence": [
      "p95 latency 950ms",
      "DB timeout logs"
    ]
  },
  "recommended_actions": [
    {
      "type": "HUMAN_REVIEW",
      "summary": "Check DB connection saturation"
    }
  ],
  "suggested_assignee_account_id": "712020:abc123",
  "suggestion_reason": "SME for checkout-api"
}
```

CDO should provide:

- `TRIAGE_HUB_NOTIFY_SQS_URL`
- queue ARN for IAM policy
- target AWS region
- confirmation that the JSON fields above match the notify dispatcher schema
- one smoke-test incident where CDO confirms the SQS message is consumed and Slack/Jira output is created or updated as expected

Smoke-test expectation: AI dry-run first prints `triage_hub_sqs_dry_run`; live mode then sends one SQS message and CDO confirms dispatcher receipt from queue logs or workflow output.
