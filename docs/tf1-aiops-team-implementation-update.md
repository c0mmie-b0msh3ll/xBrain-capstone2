# TF1 AI Ops - Implementation Update

Latest pushed commit:

```text
db4c333 Enrich AIOps scenario runbooks and tests
```

## Runtime Flow

```text
Telemetry / Incident seed
-> aiops_worker
-> AI Triage API /v1/triage
-> AgentCore Investigator
-> AI Triage API finalize response + audit
-> aiops_worker
-> CDO Triage-Hub SQS
-> CDO Slack/Jira dispatcher
```

## Main Components

### `app/main.py`

AI Triage API owns:

- `/v1/triage`
- `/v1/audit/{audit_id}`
- `/metrics`
- tenant/correlation/auth validation
- deterministic RCA
- investigation mode selection
- AgentCore invocation
- final response assembly
- audit and idempotency persistence

Final response includes:

- `classification`
- `status`
- `confidence`
- `suspected_root_cause`
- `recommended_actions`
- `ticket_payload`
- `suggested_assignee_account_id`
- `suggestion_reason`
- `audit_id`
- `llm_metadata`

### `app/aiops_worker.py`

Worker owns:

- querying Prometheus/Loki/Jaeger or consuming incident seeds
- detecting incident candidates
- building bounded triage requests
- calling `/v1/triage`
- publishing the unified CDO handoff payload to `TRIAGE_HUB_NOTIFY_SQS_URL`

Recent auth fix:

```text
If SERVICE_AUTH_TOKEN is set, aiops_worker.call_triage() now sends:
Authorization: Bearer <SERVICE_AUTH_TOKEN>
```

### `agentcore_investigator/`

AgentCore Investigator is deployed as a custom container runtime through Amazon Bedrock AgentCore Runtime.

AgentCore handles:

- tool planning
- investigation summary
- action wording

AgentCore does not:

- mutate Slack/Jira
- run shell/remediation commands
- execute tools outside the TF1 allowlist

The API still owns:

- tool allowlisting
- tenant/service/environment/window scope validation
- deterministic fallback
- audit
- idempotency
- CDO handoff payload

AgentCore runtime ARN:

```text
arn:aws:bedrock-agentcore:us-east-1:589077667575:runtime/tf1_ai_investigator-D48STMEUHo
```

## DynamoDB Audit And Idempotency

EKS demo uses DynamoDB for shared audit/idempotency persistence:

```text
AIOPS_PERSISTENCE_BACKEND=dynamodb
AIOPS_DYNAMODB_TABLE=tf1-aiops-audit-demo
AWS_REGION=us-east-1
```

Table shape:

```text
PK: string
SK: string
TTL: expires_at
```

Record types:

```text
AUDIT#{audit_id}
IDEMPOTENCY#{audit_id}
JIRA_HISTORY#{tenant_id}#{environment}#{service}
```

Validated behavior:

- `/v1/triage` returns `audit_id`
- `/v1/audit/{audit_id}` returns record for the correct tenant
- wrong tenant lookup returns `404`
- retrying the same request after pod restart replays from DynamoDB idempotency state
- Jira history assignee suggestion reads from DynamoDB first when `AIOPS_PERSISTENCE_BACKEND=dynamodb`
- `JIRA_HISTORY_PATH` JSON remains backward-compatible for local/dev and as fallback when DynamoDB has no mapping

## CDO Slack/Jira Handoff

AI does not directly create Jira tickets or mutate Slack in the production flow.

AI publishes one unified event to CDO Triage-Hub SQS. CDO owns:

- Slack Block Kit rendering
- Jira create/update
- Jira assignment
- Jira comments/transitions
- dispatcher retry/idempotency

Payload fields:

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

Contract doc:

```text
capstone/tf-1/ai/docs/slack_integration_migration.md
```

## Enriched Scenarios And Runbooks

Three demo scenarios are enriched:

- `latency-degradation`
- `critical-service-down`
- `noisy-false-alert`

Each scenario now includes richer runbook context:

- steps
- validation signals
- safe actions
- avoid list
- escalation rule
- expected action IDs
- expected runbook refs

Tests now assert:

- classification
- status
- confidence range
- recommended action IDs
- runbook refs
- CDO handoff payload shape

## Validation

Latest local verification:

```text
pytest: 90 passed
datapack validation: passed
kubectl kustomize: passed
```

Current status:

```text
AI-side implementation is ready for CDO E2E smoke.
```
