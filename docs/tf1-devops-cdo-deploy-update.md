# TF1 DevOps/CDO - Deploy And Integration Update

Latest pushed commit:

```text
db4c333 Enrich AIOps scenario runbooks and tests
```

## Scope For DevOps/CDO

AI-side implementation is ready for E2E smoke. DevOps/CDO mainly needs to validate deploy configuration, auth, DynamoDB persistence, AgentCore invocation, and SQS handoff.

Runtime pieces:

```text
1. AI Triage API
2. aiops_worker
3. AgentCore Investigator Runtime
4. DynamoDB audit/idempotency table
5. CDO Triage-Hub notify SQS queue
```

Runtime flow:

```text
aiops_worker
-> calls AI Triage API /v1/triage
-> API invokes AgentCore Runtime
-> API writes audit/idempotency to DynamoDB
-> worker publishes unified event to CDO SQS
-> CDO dispatcher creates/updates Slack/Jira
```

## Required API Environment

```text
SERVICE_AUTH_TOKEN=<shared token>
AIOPS_PERSISTENCE_BACKEND=dynamodb
AIOPS_DYNAMODB_TABLE=tf1-aiops-audit-demo
AWS_REGION=us-east-1
AIOPS_INVESTIGATION_MODE=auto
ENABLE_AGENTCORE_LLM=true
ENABLE_AGENTCORE_LLM_TOOLS=true
AIOPS_AGENTCORE_RUNTIME_ARN=arn:aws:bedrock-agentcore:us-east-1:589077667575:runtime/tf1_ai_investigator-D48STMEUHo
```

## Required Worker Environment

```text
SERVICE_AUTH_TOKEN=<same shared token as API>
TRIAGE_URL=http://<ai-api-service>/v1/triage
TRIAGE_HUB_NOTIFY_SQS_URL=<CDO notify queue URL>
AWS_REGION=us-east-1
PROMETHEUS_URL=<prometheus service URL>
LOKI_URL=<loki service URL>
JAEGER_URL=<jaeger service URL>
```

If live SQS is not configured, worker should print:

```text
triage_hub_sqs_dry_run
```

If live SQS is configured, worker should print:

```text
triage_hub_sqs_published=true
```

## Required IAM

API/engine role:

```text
bedrock-agentcore:InvokeAgentRuntime
dynamodb:GetItem
dynamodb:PutItem
dynamodb:UpdateItem
dynamodb:Query
```

Worker role:

```text
sqs:SendMessage
```

The `sqs:SendMessage` permission should be scoped to the CDO Triage-Hub notify queue ARN.

## AgentCore Runtime

AgentCore is deployed as an Amazon Bedrock AgentCore Runtime custom container.

Runtime ARN:

```text
arn:aws:bedrock-agentcore:us-east-1:589077667575:runtime/tf1_ai_investigator-D48STMEUHo
```

Expected behavior:

- API invokes AgentCore when investigation mode selects AgentCore path.
- AgentCore returns tool plan, summary, and action wording.
- API still owns guardrails, tool execution, audit, idempotency, and final response.
- AgentCore does not mutate Slack/Jira and does not run remediation commands.

## DynamoDB Persistence

Table:

```text
tf1-aiops-audit-demo
```

Shape:

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

Jira history suggestion item:

```text
PK = JIRA_HISTORY#<tenant_id>#<environment>#<service>
SK = SUGGESTION
record.suggested_assignee_account_id = <Jira accountId>
record.suggestion_reason = <human-readable reason>
```

Backward compatibility:

```text
If DynamoDB has no Jira history mapping, AI can still fall back to JIRA_HISTORY_PATH JSON.
This keeps local/dev demos simple while allowing EKS/prod to use the shared DynamoDB table.
```

Expected checks:

```text
/v1/triage returns audit_id
/v1/audit/{audit_id} works for the correct tenant
wrong tenant returns 404
same request replay after API pod restart returns idempotent response
Jira history suggestion is read from DynamoDB first when AIOPS_PERSISTENCE_BACKEND=dynamodb
JIRA_HISTORY_PATH JSON fallback still works when DynamoDB has no mapping
```

## Auth Bug Fix

Issue reported:

```text
API checks Authorization: Bearer <SERVICE_AUTH_TOKEN> when SERVICE_AUTH_TOKEN is enabled.
Worker previously called /v1/triage without Authorization header.
This caused 401 Unauthorized in real deploy.
```

Fix implemented:

```text
aiops_worker.call_triage() now reads SERVICE_AUTH_TOKEN.
If present, it sends Authorization: Bearer <token>.
```

Fix commit:

```text
4a6eb2e Send service auth token from AIOps worker
```

Required deploy note:

```text
API and worker must use the same SERVICE_AUTH_TOKEN.
```

## CDO Slack/Jira Boundary

AI does not directly create Jira tickets or mutate Slack in the production flow.

AI publishes one unified event to:

```text
TRIAGE_HUB_NOTIFY_SQS_URL
```

CDO owns:

```text
Slack Block Kit rendering
Jira create/update
Jira assignment
Jira comments/transitions
dispatcher retry/idempotency
```

## E2E Smoke Checklist

```text
1. Deploy AI API and worker with the same SERVICE_AUTH_TOKEN.
2. Confirm worker can call /v1/triage without 401.
3. Confirm API can invoke AgentCore Runtime.
4. Confirm API writes audit/idempotency to DynamoDB.
5. Configure TRIAGE_HUB_NOTIFY_SQS_URL for worker.
6. Confirm worker role has sqs:SendMessage to the notify queue.
7. Trigger one incident scenario.
8. Confirm worker logs contain triage_response.
9. Confirm worker logs contain triage_hub_sqs_published=true.
10. Confirm CDO dispatcher receives the message.
11. Confirm Slack/Jira output is created or updated by CDO dispatcher.
```

Suggested smoke scenarios:

```text
latency-degradation: dependency timeout / connection pool exhaustion
critical-service-down: health check + 5xx + dependency refused
noisy-false-alert: recovered synthetic probe / investigate only
```

## Current Status

```text
AI-side ready.
Remaining dependency: CDO queue URL, IAM sqs:SendMessage, and dispatcher confirmation.
```
