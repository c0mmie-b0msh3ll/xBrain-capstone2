# TF1 DevOps - Ghi Chú Bổ Sung Khi Deploy

File này ghi thêm các điểm deploy/setup sau khi AI side thêm cơ chế Jira history lookup dạng hybrid.

## Mình Đã Update Gì Và Vì Sao

### 1. Jira history lookup giờ ưu tiên DynamoDB

Đã update:

```text
Khi AIOPS_PERSISTENCE_BACKEND=dynamodb, AI có thể đọc Jira assignee suggestion từ DynamoDB.
```

Lý do:

```text
Trong EKS/prod-like deploy, API có thể chạy nhiều replica. Nếu chỉ dùng JSON file local thì không ổn định giữa các pod hoặc sau restart, trừ khi DevOps mount và quản lý file riêng. DynamoDB đã được dùng cho audit/idempotency, nên Jira history suggestion dùng chung table này sẽ hợp lý hơn cho deploy thật.
```

### 2. JSON mapping vẫn được giữ để backward-compatible

Đã update:

```text
JIRA_HISTORY_PATH vẫn dùng được.
Nếu DynamoDB không có mapping phù hợp hoặc lookup lỗi, AI sẽ fallback về JSON file nếu có cấu hình.
```

Lý do:

```text
Local demo và quick smoke test cần đơn giản. Team vẫn có thể dùng một file jira-history.json nhỏ mà không cần seed DynamoDB. Cách này cũng tránh block DevOps/CDO nếu bước seed DynamoDB chưa sẵn sàng.
```

### 3. AI vẫn không mutate Jira

Đã giữ nguyên boundary:

```text
AI chỉ trả suggested_assignee_account_id và suggestion_reason.
AI không create/update Jira issue.
```

Lý do:

```text
Boundary đã thống nhất là CDO owns Slack/Jira mutation thông qua Triage-Hub dispatcher. AI owns diagnosis, confidence, recommended actions, audit metadata và handoff payload.
```

### 4. Đã thêm tests cho cả DynamoDB và JSON fallback

Đã update:

```text
Tests cover:
- DynamoDB có Jira history mapping
- DynamoDB không có mapping
- DynamoDB backend bật nhưng fallback được về JIRA_HISTORY_PATH JSON
```

Lý do:

```text
Để tránh regression khi deploy EKS/prod bị mất assignee suggestion hoặc local JSON demo bị hỏng.
```

## Thứ Tự Lookup Jira History

AI hiện lookup theo thứ tự:

```text
DynamoDB Jira history
-> fallback JIRA_HISTORY_PATH JSON nếu DynamoDB không có mapping hoặc lookup lỗi
-> route về owner team nếu vẫn không có mapping
```

## DynamoDB Jira History Item

Dùng chung table với audit/idempotency:

```text
Table: tf1-aiops-audit-demo
```

Item shape:

```text
PK = JIRA_HISTORY#<tenant_id>#<environment>#<service>
SK = SUGGESTION
record.suggested_assignee_account_id = <Jira accountId>
record.suggestion_reason = <lý do gợi ý>
expires_at = <TTL epoch seconds>
```

Ví dụ:

```text
PK = JIRA_HISTORY#tenant-a#sandbox#checkout-api
SK = SUGGESTION
record.suggested_assignee_account_id = 712020:abc123
record.suggestion_reason = SME for checkout-api
```

Ví dụ AWS CLI:

```bash
aws dynamodb put-item \
  --region us-east-1 \
  --table-name tf1-aiops-audit-demo \
  --item '{
    "PK": {"S": "JIRA_HISTORY#tenant-a#sandbox#checkout-api"},
    "SK": {"S": "SUGGESTION"},
    "record": {"M": {
      "suggested_assignee_account_id": {"S": "712020:abc123"},
      "suggestion_reason": {"S": "SME for checkout-api"}
    }},
    "expires_at": {"N": "1790000000"}
  }'
```

## JSON Fallback

Cho local demo hoặc smoke test nhanh, có thể set:

```text
JIRA_HISTORY_PATH=/path/to/jira-history.json
```

Ví dụ JSON:

```json
[
  {
    "tenant_id": "tenant-a",
    "environment": "sandbox",
    "service": "checkout-api",
    "account_id": "712020:abc123",
    "suggestion_reason": "SME for checkout-api"
  }
]
```

Các field accountId được hỗ trợ:

```text
account_id
assignee_account_id
suggested_assignee_account_id
```

## Environment Cần Có

Khi dùng DynamoDB-backed persistence:

```text
AIOPS_PERSISTENCE_BACKEND=dynamodb
AIOPS_DYNAMODB_TABLE=tf1-aiops-audit-demo
AWS_REGION=us-east-1
```

Nếu muốn bật JSON fallback:

```text
JIRA_HISTORY_PATH=/path/to/jira-history.json
```

## IAM Notes

API role cần quyền DynamoDB trên cùng table:

```text
dynamodb:GetItem
dynamodb:PutItem
dynamodb:UpdateItem
dynamodb:Query
```

Nếu policy hiện tại đã scope theo table `tf1-aiops-audit-demo`, item prefix mới `JIRA_HISTORY#...` không cần tạo thêm table riêng.

## Smoke Test Nên Check Gì

Sau khi gọi `/v1/triage`, check các field:

```text
suggested_assignee_account_id
suggestion_reason
ticket_payload.fields.suggested_assignee_account_id
```

Expected behavior:

```text
Nếu DynamoDB có JIRA_HISTORY item phù hợp, response dùng accountId từ DynamoDB.
Nếu DynamoDB không có mapping nhưng JIRA_HISTORY_PATH có mapping, response dùng JSON mapping.
Nếu cả hai đều không có, suggested_assignee_account_id = null và suggestion_reason route về owner team.
```

## Setup Cũ Vẫn Cần Giữ

Các phần deploy cũ vẫn cần:

```text
API và worker dùng cùng SERVICE_AUTH_TOKEN
API có AIOPS_AGENTCORE_RUNTIME_ARN
API có DynamoDB table env vars
worker có TRIAGE_HUB_NOTIFY_SQS_URL
worker role có sqs:SendMessage
API role có bedrock-agentcore:InvokeAgentRuntime
```
