# TF1 AI Ops EKS Demo

This package provisions a scoped EKS demo in AWS account `589077667575`, region `us-east-1`, with cost guardrails and R&D cost tracking.

The default demo now exercises AgentCore live calls with a constrained runtime ARN:

```text
telemetry simulator -> Prometheus/Loki/Jaeger -> aiops-worker -> tf1-ai-triage-engine -> DynamoDB audit/idempotency
                                                                  -> Bedrock AgentCore Runtime
```

## Cost Guardrails

- Monthly AWS Budget: `25 USD` by default, scoped to tag `Project=tf1-aiops-demo`.
- Budget alerts: 50%, 80%, 100% actual spend and 100% forecasted spend.
- No NAT Gateway.
- No public Kubernetes `LoadBalancer`; use `kubectl port-forward`.
- Small managed node group: default desired size `2`, max size `2`.
- No customer-managed EKS KMS key and no module-created CloudWatch log group in the default config.
- AgentCore calls enabled only for the configured `agentcore_runtime_arn`.
- Optional QA LLM judge is disabled by default with `ENABLE_QA_LLM=false`; when enabled it uses direct Bedrock invoke from the engine role, not a second AgentCore runtime.
- All Terraform-created AWS resources are tagged:
  - `Project=tf1-aiops-demo`
  - `CostCenter=RnD`
  - `Owner=AI`
  - `Environment=demo`
  - `ExpiresAt=<YYYY-MM-DD>`

## Provision

```powershell
cd capstone\tf-1\ai\engine-skeleton\deploy\eks-demo\terraform
Copy-Item terraform.tfvars.example terraform.tfvars
# Edit expires_at and budget_alert_email.
terraform init
terraform plan
terraform apply
terraform output kubeconfig_command
```

For a one-off e2e run without writing local `terraform.tfvars`, pass the expiry date inline:

```powershell
terraform init
terraform plan `
  -var "expires_at=2026-07-01" `
  -var "agentcore_runtime_arn=arn:aws:bedrock-agentcore:us-east-1:589077667575:runtime/tf1_ai_investigator-D48STMEUHo" `
  -out tf1-aiops-demo.tfplan
terraform apply -auto-approve tf1-aiops-demo.tfplan
```

Run the printed kubeconfig command:

```powershell
aws eks update-kubeconfig --region us-east-1 --name tf1-aiops-demo
```

Apply the manifests from the `k8s` directory:

```powershell
cd capstone\tf-1\ai\engine-skeleton\deploy\eks-demo\k8s
kubectl apply -k .
```

Then inject the Terraform-created IRSA role ARN and the AgentCore runtime ARN. This keeps account-specific ARNs out of committed Kubernetes YAML:

```powershell
cd ..\terraform
$roleArn = terraform output -raw triage_engine_role_arn
$agentcoreArn = terraform output -raw agentcore_runtime_arn

kubectl annotate serviceaccount tf1-ai-triage-engine `
  -n tf1-ai-demo `
  "eks.amazonaws.com/role-arn=$roleArn" `
  --overwrite

kubectl set env deployment/tf1-ai-triage-engine `
  -n tf1-ai-demo `
  "AGENTCORE_RUNTIME_ARN=$agentcoreArn" `
  ENABLE_AGENTCORE_LLM=true `
  ENABLE_AGENTCORE_LLM_TOOLS=true
```

The engine image is pinned to:

```text
589077667575.dkr.ecr.us-east-1.amazonaws.com/tf1-ai-triage-engine@sha256:3b60233dbd9fe042bff8eefa5c7b3924cc168bdb7b386e37f3fe938756a7dc95
```

The AgentCore runtime is deployed separately and currently points to the rebuilt ARM64 investigator image:

```text
589077667575.dkr.ecr.us-east-1.amazonaws.com/tf1-agentcore-investigator@sha256:63cf6cd30c9f8b6178ff95ce5ec8d5f2d5a1a3f51d970759a3e0927464d811b9
```

AgentCore live calls require a boto3/botocore version that includes the `bedrock-agentcore` data-plane service. The engine image above was rebuilt with `boto3==1.43.2`, so no startup patch is required.

The QA LLM judge is configured but disabled by default:

```text
ENABLE_QA_LLM=false
AIOPS_QA_PROVIDER=bedrock
AIOPS_QA_MODEL_ID=us.amazon.nova-micro-v1:0
AIOPS_QA_MAX_TOKENS_PER_INCIDENT=1200
AIOPS_QA_CONFIDENCE_PENALTY=-0.1
AIOPS_QA_FAIL_CLOSED=false
```

When `ENABLE_QA_LLM=true`, the triage engine calls Bedrock Runtime directly for a bounded read-only QA verdict. The Terraform IRSA policy grants `bedrock:InvokeModel` and `bedrock:InvokeModelWithResponseStream` for foundation models and account inference profiles in `us-east-1`.

## Smoke Test

```powershell
kubectl get nodes
kubectl get pods -n tf1-ai-demo
kubectl rollout status deployment/tf1-ai-triage-engine -n tf1-ai-demo
kubectl rollout status deployment/aiops-worker -n tf1-ai-demo
```

Verify cost/network guardrails:

```powershell
kubectl get svc -n tf1-ai-demo
aws dynamodb describe-time-to-live --region us-east-1 --table-name tf1-aiops-audit-demo
aws budgets describe-budget --account-id 589077667575 --budget-name tf1-aiops-demo-monthly-tagged-budget
aws ec2 describe-nat-gateways --region us-east-1 --filter Name=tag:Project,Values=tf1-aiops-demo
```

Port-forward services only when needed:

```powershell
kubectl port-forward -n tf1-ai-demo svc/tf1-ai-triage-engine 8080:8080
kubectl port-forward -n tf1-ai-demo svc/prometheus 9090:9090
kubectl port-forward -n tf1-ai-demo svc/loki 3100:3100
kubectl port-forward -n tf1-ai-demo svc/jaeger 16686:16686
```

Then verify:

```powershell
curl http://localhost:8080/healthz
curl "http://localhost:9090/api/v1/query?query=aiops_scenario_metric_value"
curl "http://localhost:3100/loki/api/v1/query_range?query={job=`"tf1-telemetry-simulator`"}&limit=5"
```

Check worker output for a triage response:

```powershell
kubectl logs -n tf1-ai-demo deployment/aiops-worker --tail=200
```

Expected `/v1/triage` fields include `audit_id`, `classification`, `status`, `ticket_payload`, and `llm_metadata`.

The worker prints Slack legacy and Triage-Hub SQS dry-run payloads by default. To enable the CDO Slack/Jira dispatcher flow for a controlled smoke test, configure the notify queue URL and IAM permission for `sqs:SendMessage`:

```powershell
kubectl set env deployment/aiops-worker -n tf1-ai-demo `
  TRIAGE_HUB_NOTIFY_SQS_URL="https://sqs.us-east-1.amazonaws.com/<account>/<queue-name>" `
  TRIAGE_HUB_NOTIFY_SQS_DRY_RUN=false
```

Live Slack/Jira mutations remain CDO-owned. AI publishes the unified event body described in `capstone/tf-1/ai/docs/slack_integration_migration.md`; CDO consumes the queue and creates or updates Slack/Jira artifacts.

Expected live AgentCore result for the included latency scenario:

```text
classification: latency_degradation
status: DIAGNOSED
llm_metadata.mode_selection.agentcore_enabled: true
llm_metadata.provider: agentcore
llm_metadata.cost_estimate.calls: tools, summary, actions
llm_metadata.tool_investigation.fallback: false
llm_metadata.action_wording.fallback: false
```

If QA LLM is enabled, expected QA metadata includes:

```text
llm_metadata.qa.provider: bedrock
llm_metadata.qa.verdict: pass|fail|uncertain
llm_metadata.qa.required_human_review: true on fail/uncertain
llm_metadata.qa.prompt_tokens and completion_tokens
```

## DynamoDB Verification

```powershell
aws dynamodb describe-table --region us-east-1 --table-name tf1-aiops-audit-demo
aws dynamodb describe-time-to-live --region us-east-1 --table-name tf1-aiops-audit-demo
```

After a worker run, use an `audit_id` from logs:

```powershell
curl -H "X-Tenant-Id: tenant-a" http://localhost:8080/v1/audit/<audit_id>
curl -i -H "X-Tenant-Id: wrong-tenant" http://localhost:8080/v1/audit/<audit_id>
```

The wrong tenant request should return `404`.

To test DynamoDB idempotency replay, restart the engine deployment and submit the same triage request body and headers again. The response should replay from the DynamoDB idempotency record.

Example DynamoDB idempotency item check:

```powershell
@'
import boto3, json

client = boto3.client("dynamodb", region_name="us-east-1")
resp = client.get_item(
    TableName="tf1-aiops-audit-demo",
    Key={
        "PK": {"S": "IDEMPOTENCY#<audit_id>"},
        "SK": {"S": "STATE"},
    },
)
item = resp["Item"]
print(json.dumps({
    "status": item["record"]["M"]["status"]["S"],
    "audit_id": item["record"]["M"]["audit_id"]["S"],
    "response_status": item["record"]["M"]["response"]["M"]["status"]["S"],
    "classification": item["record"]["M"]["response"]["M"]["classification"]["S"],
}, indent=2))
'@ | python -
```

Expected status:

```text
record.status: completed
```

## R&D Cost Tracking

Use Cost Explorer after the demo window starts reporting tagged cost:

```powershell
aws ce get-cost-and-usage `
  --region us-east-1 `
  --time-period Start=YYYY-MM-DD,End=YYYY-MM-DD `
  --granularity DAILY `
  --metrics UnblendedCost `
  --filter '{\"Tags\":{\"Key\":\"Project\",\"Values\":[\"tf1-aiops-demo\"]}}' `
  --group-by Type=DIMENSION,Key=SERVICE
```

Capture the result in `docs/tf1-aiops-demo-cost-summary.md` with:

- EKS/EC2 cost.
- DynamoDB cost.
- ECR/data transfer cost if present.
- CloudWatch/logging cost.
- LoadBalancer/NAT cost if any; expected value is zero for the default setup.

## Cleanup

Do cleanup immediately after the demo window:

```powershell
kubectl delete namespace tf1-ai-demo
cd ..\terraform
terraform destroy
```

Verify there are no accidental leftovers:

```powershell
aws eks list-clusters --region us-east-1
aws ec2 describe-instances --region us-east-1 --filters "Name=tag:Project,Values=tf1-aiops-demo" "Name=instance-state-name,Values=pending,running,stopping,stopped"
aws elbv2 describe-load-balancers --region us-east-1
aws dynamodb describe-table --region us-east-1 --table-name tf1-aiops-audit-demo
```

`describe-table` should fail after `terraform destroy` unless the table was intentionally retained.

## E2E Evidence From AI Account

Validated on `2026-06-29` in account `589077667575`, region `us-east-1`.

Infra:

- `terraform apply` completed: 49 resources added.
- EKS nodes: 2 Ready nodes, Kubernetes `v1.30.14-eks-93b80c6`.
- DynamoDB TTL: `ENABLED`, attribute `expires_at`.
- Budget: `tf1-aiops-demo-monthly-tagged-budget`, `25 USD`, filter `user:Project$tf1-aiops-demo`, health `HEALTHY`.

Kubernetes:

- Namespace: `tf1-ai-demo`.
- All workloads reached ready/running.
- Services were all `ClusterIP`; no public `LoadBalancer`.
- Tagged NAT Gateway lookup returned no NAT gateways.

Flow:

- Simulator emitted `payment-api` latency, error rate, Redis timeout metrics, logs, and traces.
- Prometheus query `aiops_scenario_metric_value` returned 3 series.
- Loki query for `{job="tf1-telemetry-simulator"}` returned 1 stream.
- Jaeger services included `tf1-telemetry-simulator` and `tf1-ai-triage-engine`.
- Worker called `/v1/triage` and returned `audit-3ca526f76c74`, `latency_degradation`, `DIAGNOSED`.
- `GET /v1/audit/audit-3ca526f76c74` with `X-Tenant-Id: tenant-a` returned the record.
- Same audit lookup with `X-Tenant-Id: wrong-tenant` returned `404`.
- Restarted `tf1-ai-triage-engine`, retried the same sample request, and got the same audit id `audit-bd3ad67392c2`.
- DynamoDB idempotency item `IDEMPOTENCY#audit-bd3ad67392c2 / STATE` had `record.status=completed`.

AgentCore rebuilt validation:

- Rebuilt and pushed engine image `tf1-ai-triage-engine@sha256:7f1c47894170b27a5c040565e56ab372250248294b34f89bb9cdd5abea2d09f7`.
- Rebuilt and pushed AgentCore investigator ARM64 image `tf1-agentcore-investigator@sha256:63cf6cd30c9f8b6178ff95ce5ec8d5f2d5a1a3f51d970759a3e0927464d811b9`.
- Updated AgentCore runtime `tf1_ai_investigator-D48STMEUHo` to version `2`, status `READY`, using the rebuilt investigator image.
- Added the investigator ECR repository to the AgentCore execution role pull policy.
- Direct AgentCore data-plane smoke test returned valid task-specific schemas: `tool_calls` for `tool_planning`, `actions` for `action_wording`, and `summary` for `investigation_summary`.
- Re-created the EKS demo with `agentcore_runtime_arn=arn:aws:bedrock-agentcore:us-east-1:589077667575:runtime/tf1_ai_investigator-D48STMEUHo`.
- Forced `AIOPS_INVESTIGATION_MODE=agent_assisted` and `LLM_TOOL_FORCE=true`, then submitted the latency scenario.
- Response `audit-8181fa5d4971` returned `classification=latency_degradation`, `status=DIAGNOSED`, `llm_metadata.provider=agentcore`, and `llm_metadata.mode_selection.agentcore_enabled=true`.
- AgentCore call metadata recorded 3 live calls: `tools`, `summary`, and `actions`, with total `2865` tokens.
- Tool planning and action wording completed through AgentCore with `fallback=false` and no errors.
- Tenant isolation remained enforced: `GET /v1/audit/audit-8181fa5d4971` with `X-Tenant-Id: wrong-tenant` returned `404`.
- DynamoDB idempotency item `IDEMPOTENCY#audit-8181fa5d4971 / STATE` had `record.status=completed`.
