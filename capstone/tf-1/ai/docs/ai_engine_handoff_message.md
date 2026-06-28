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

```text
AIOPS_PERSISTENCE_BACKEND=dynamodb
AIOPS_DYNAMODB_TABLE=<table name>
AWS_REGION=us-east-1
AGENTCORE_RUNTIME_ARN=<runtime arn>
ENABLE_AGENTCORE_LLM=true
ENABLE_AGENTCORE_LLM_TOOLS=true
AIOPS_INVESTIGATION_MODE=auto
```

DynamoDB table:

```text
partition key string: PK
sort key string: SK
TTL attribute: expires_at
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

Optional nếu bật Slack/Jira SQS handoff:

```text
TRIAGE_HUB_NOTIFY_SQS_URL=<queue url>
TRIAGE_HUB_NOTIFY_SQS_DRY_RUN=false
```

Nếu bật path này thì worker role cần thêm:

```text
sqs:SendMessage on notify queue
```
