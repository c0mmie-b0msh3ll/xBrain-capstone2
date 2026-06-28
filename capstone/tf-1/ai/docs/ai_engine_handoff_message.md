# AI Engine Handoff Message

Anh em ơi, phần AI engine handoff đã ready nhé.

Source commit:

```text
ac21f60589752d977eff229745316bcf859f1396
```

GitHub source:

- Main handoff doc: https://github.com/c0mmie-b0msh3ll/xBrain-capstone2/blob/ac21f60589752d977eff229745316bcf859f1396/capstone/tf-1/ai/docs/11_v1_0_0_handoff.md
- Slack/Jira SQS handoff: https://github.com/c0mmie-b0msh3ll/xBrain-capstone2/blob/ac21f60589752d977eff229745316bcf859f1396/capstone/tf-1/ai/docs/slack_integration_migration.md
- Production idempotency / DynamoDB notes: https://github.com/c0mmie-b0msh3ll/xBrain-capstone2/blob/ac21f60589752d977eff229745316bcf859f1396/capstone/tf-1/ai/engine-skeleton/docs/production_edge_cases_and_idempotency.md
- AI triage engine API source: https://github.com/c0mmie-b0msh3ll/xBrain-capstone2/tree/ac21f60589752d977eff229745316bcf859f1396/capstone/tf-1/ai/engine-skeleton
- AgentCore investigator runtime source: https://github.com/c0mmie-b0msh3ll/xBrain-capstone2/tree/ac21f60589752d977eff229745316bcf859f1396/capstone/tf-1/ai/engine-skeleton/agentcore_investigator

Local paths nếu cần mở trực tiếp:

```text
E:\xBrain-capstone2\capstone\tf-1\ai\docs\11_v1_0_0_handoff.md
E:\xBrain-capstone2\capstone\tf-1\ai\docs\slack_integration_migration.md
E:\xBrain-capstone2\capstone\tf-1\ai\engine-skeleton\docs\production_edge_cases_and_idempotency.md
E:\xBrain-capstone2\capstone\tf-1\ai\engine-skeleton
E:\xBrain-capstone2\capstone\tf-1\ai\engine-skeleton\agentcore_investigator
```

## Cách 1 - Pull Image Build Sẵn Từ AI ECR

Khuyên dùng cách này nếu team muốn lấy artifact đã build sẵn.

AI ECR account:

```text
589077667575
```

CDO account:

```text
629149146950
```

Region:

```text
us-east-1
```

CDO account đã được cấp quyền pull cross-account cho cả 2 ECR repo.

### Image 1 - AI Triage Engine API

Repo:

```text
589077667575.dkr.ecr.us-east-1.amazonaws.com/tf1-ai-triage-engine
```

Tag:

```text
git-ac21f60
```

Digest:

```text
sha256:850c2f3b90edd2acb3958085d6b8be1228beabe675a6f5f97998481c9a993e23
```

### Image 2 - AgentCore Investigator Runtime

Repo:

```text
589077667575.dkr.ecr.us-east-1.amazonaws.com/tf1-agentcore-investigator
```

Tag:

```text
git-ac21f60
```

Digest:

```text
sha256:01072527feb5bb2f60657a332119f5c7517d93d35e9fa9972e17057e5252863c
```

### Pull Commands

```bash
aws ecr get-login-password --region us-east-1 \
  | docker login --username AWS --password-stdin 589077667575.dkr.ecr.us-east-1.amazonaws.com

docker pull 589077667575.dkr.ecr.us-east-1.amazonaws.com/tf1-ai-triage-engine@sha256:850c2f3b90edd2acb3958085d6b8be1228beabe675a6f5f97998481c9a993e23

docker pull 589077667575.dkr.ecr.us-east-1.amazonaws.com/tf1-agentcore-investigator@sha256:01072527feb5bb2f60657a332119f5c7517d93d35e9fa9972e17057e5252863c
```

Lưu ý: principal bên CDO dùng để pull vẫn cần identity permission:

```text
ecr:GetAuthorizationToken
```

## Cách 2 - Tự Build Từ Source

Checkout source ở commit:

```text
ac21f60589752d977eff229745316bcf859f1396
```

Build từ repo root:

```bash
docker build -t tf1-ai-triage-engine:ac21f60 capstone/tf-1/ai/engine-skeleton

docker build -t tf1-agentcore-investigator:ac21f60 capstone/tf-1/ai/engine-skeleton/agentcore_investigator
```

## Tóm Tắt Runtime

- `tf1-ai-triage-engine` là API service chính.
- `tf1-agentcore-investigator` là image cho Bedrock AgentCore runtime.
- Production path nên deploy cả 2.
- Engine gọi AgentCore qua `AGENTCORE_RUNTIME_ARN`.

## Config Production Cần Chuẩn Bị

Core runtime / auth / guardrails:

```text
APP_ENV=prod
SERVICE_AUTH_TOKEN=<optional fallback token nếu chưa dùng SigV4/JWT>
AWS_REGION=us-east-1
AWS_DEFAULT_REGION=us-east-1
AIOPS_INVESTIGATION_MODE=auto
AIOPS_TRIAGE_DEADLINE_SECONDS=30
AIOPS_RATE_LIMIT_PER_MINUTE=60
AIOPS_MAX_REQUEST_BYTES=524288
AIOPS_MAX_CONCURRENT_TRIAGE_REQUESTS=0
```

AgentCore / Bedrock path:

```text
AGENTCORE_RUNTIME_ARN=<runtime arn>
ENABLE_AGENTCORE_LLM=true
ENABLE_AGENTCORE_LLM_TOOLS=true
BEDROCK_MODEL_ID=<optional, default us.amazon.nova-micro-v1:0 cho AgentCore runtime>
BEDROCK_MODEL_IDS=<optional csv nếu muốn fallback model list>
BEDROCK_MAX_TOKENS=700
AIOPS_AGENT_MAX_ITERATIONS=2
AIOPS_AGENT_MAX_TOOL_CALLS=5
AIOPS_ASSISTED_COMPLEXITY_THRESHOLD=3
AIOPS_AGENT_COMPLEXITY_THRESHOLD=6
AIOPS_LLM_MAX_TOKENS_PER_INCIDENT=0
AIOPS_LLM_INPUT_COST_PER_1K=0
AIOPS_LLM_OUTPUT_COST_PER_1K=0
```

Audit / idempotency production backend:

```text
AIOPS_PERSISTENCE_BACKEND=dynamodb
AIOPS_DYNAMODB_TABLE=<table name>
AIOPS_AUDIT_RETENTION_DAYS=90
AIOPS_IDEMPOTENCY_STALE_SECONDS=120
```

DynamoDB table:

```text
partition key string: PK
sort key string: SK
TTL attribute: expires_at
```

Report JSON storage, nếu deploy report UI hoặc muốn giữ report local:

```text
REPORTS_DIR=/var/lib/tf1-ai/reports
```

File backend fallback, chỉ dùng nếu không dùng DynamoDB:

```text
AIOPS_PERSISTENCE_BACKEND=file
AIOPS_AUDIT_LOG_PATH=/var/lib/tf1-ai/audit/audit-log.jsonl
AIOPS_IDEMPOTENCY_DIR=/var/lib/tf1-ai/audit/idempotency
```

Evidence/context integrations:

```text
PROMETHEUS_URL=<bounded metrics endpoint>
LOKI_URL=<bounded logs endpoint>
JAEGER_URL=<bounded traces endpoint>
DEPLOY_METADATA_PATH=<deploy metadata json path>
OWNERSHIP_PATH=<ownership/runbook json path>
EVIDENCE_BUNDLE_BASE_PATH=<bundle base path>
JIRA_HISTORY_PATH=<read-only jira history/accountId map>
KNOWN_ERRORS_PATH=<known errors json path>
AIOPS_CONTEXT_TOOL_TIMEOUT_SECONDS=3
LLM_TOOL_MAX_WINDOW_MINUTES=60
LLM_TOOL_LOG_LIMIT=50
LLM_TOOL_MAX_CALLS=3
```

Evidence budget / payload caps:

```text
AIOPS_MAX_EVIDENCE_BYTES=262144
AIOPS_MAX_METRIC_SERIES=20
AIOPS_MAX_METRIC_POINTS_PER_SERIES=120
AIOPS_MAX_LOG_RECORDS=50
AIOPS_MAX_TRACE_RECORDS=20
AIOPS_MAX_LOG_MESSAGE_CHARS=500
AIOPS_MAX_TRACE_LABEL_BYTES=2048
```

QA / observability:

```text
AIOPS_QA_MAX_ITERATIONS=1
AIOPS_QA_REPAIR_MAX_ITERATIONS=1
AIOPS_OBSERVABILITY_ENABLED=true
AIOPS_LOG_POLICY=metadata_only
AIOPS_LOG_LEVEL=INFO
OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=<optional otel collector endpoint>
```

Worker/demo flow nếu CDO dùng `app.aiops_worker`:

```text
TRIAGE_URL=<AI engine /v1/triage URL>
REPORT_BASE_URL=<report UI base URL>
TENANT_ID=<tenant id>
SERVICE_NAME=<service name>
ENVIRONMENT=<environment>
SQS_QUEUE_URL=<inbound incident seed queue, nếu dùng>
SQS_WAIT_SECONDS=5
SQS_MAX_MESSAGES=1
SLACK_WEBHOOK_URL=<legacy demo Slack webhook, optional>
```

Optional nếu bật Slack/Jira SQS handoff:

```text
TRIAGE_HUB_NOTIFY_SQS_URL=<queue url>
TRIAGE_HUB_NOTIFY_SQS_DRY_RUN=false
```

IAM cho engine role:

```text
bedrock-agentcore:InvokeAgentRuntime on AGENTCORE_RUNTIME_ARN
dynamodb:GetItem
dynamodb:PutItem
dynamodb:UpdateItem
dynamodb:Query
```

IAM cho AgentCore runtime role:

```text
bedrock:Converse
bedrock:InvokeModel
```

Nếu bật path này thì worker role cần thêm:

```text
sqs:SendMessage on notify queue
```
