# TF1 AI Ops Demo Cost Summary

Demo scope:

- AWS account: `589077667575`
- Region: `us-east-1`
- Project tag: `Project=tf1-aiops-demo`
- Cost center tag: `CostCenter=RnD`
- Planned runtime: max 48 hours
- Budget guardrail: `25 USD` monthly tagged budget

## Demo Window

| Field | Value |
| --- | --- |
| Start | 2026-06-29 13:08 ICT |
| End | 2026-06-29 13:42 ICT |
| Cluster | `tf1-aiops-demo` |
| Namespace | `tf1-ai-demo` |
| DynamoDB table | `tf1-aiops-audit-demo` |

Second validation window:

| Field | Value |
| --- | --- |
| Start | 2026-06-29 15:14 ICT |
| End | 2026-06-29 15:51 ICT |
| Purpose | AgentCore live invocation validation |
| AgentCore runtime | `arn:aws:bedrock-agentcore:us-east-1:589077667575:runtime/tf1_ai_investigator-D48STMEUHo` |

Rebuilt AgentCore validation window:

| Field | Value |
| --- | --- |
| Start | 2026-06-29 18:31 ICT |
| End | 2026-06-29 19:14 ICT |
| Purpose | Rebuild engine image, rebuild AgentCore runtime image, rerun EKS e2e |
| Engine image | `589077667575.dkr.ecr.us-east-1.amazonaws.com/tf1-ai-triage-engine@sha256:7f1c47894170b27a5c040565e56ab372250248294b34f89bb9cdd5abea2d09f7` |
| AgentCore image | `589077667575.dkr.ecr.us-east-1.amazonaws.com/tf1-agentcore-investigator@sha256:63cf6cd30c9f8b6178ff95ce5ec8d5f2d5a1a3f51d970759a3e0927464d811b9` |
| AgentCore runtime version | `2`, status `READY` |

E2E run on `2026-06-29`:

| Field | Value |
| --- | --- |
| Account | `589077667575` |
| Region | `us-east-1` |
| Terraform apply | `49 added, 0 changed, 0 destroyed` |
| EKS nodes | 2 Ready `t3.small` managed nodes |
| Workloads | `tf1-ai-triage-engine`, `telemetry-simulator`, `aiops-worker`, Prometheus, Loki, Jaeger, OTel Collector |
| Public exposure | None; all demo Kubernetes services were `ClusterIP` |
| NAT Gateway | None found for tag `Project=tf1-aiops-demo` |
| Budget | `tf1-aiops-demo-monthly-tagged-budget`, `25 USD`, health `HEALTHY` |
| DynamoDB TTL | `ENABLED` on `expires_at` |

AgentCore live e2e run on `2026-06-29`:

| Field | Value |
| --- | --- |
| Terraform apply | `49 added, 0 changed, 0 destroyed`, followed by an in-place IAM policy update for AgentCore runtime endpoint ARN |
| Workloads | All demo workloads ready/running |
| AgentCore mode | `AIOPS_INVESTIGATION_MODE=agent_assisted`, `ENABLE_AGENTCORE_LLM=true`, `ENABLE_AGENTCORE_LLM_TOOLS=true` |
| Engine SDK | Source requirement updated to `boto3==1.43.2`; old pinned image smoke-tested with temporary startup patch |
| Triage audit | `audit-edd3e5c02569` |
| Triage result | `classification=latency_degradation`, `status=DIAGNOSED` |
| AgentCore evidence | 3 live calls recorded: `tools`, `summary`, `actions`; total tokens `2895` |
| AgentCore limitation | Summary succeeded through AgentCore; tools/actions fell back because current runtime returns final-diagnosis output for all tasks instead of task-specific JSON schemas |
| Tenant isolation | Wrong tenant audit lookup returned `404` |
| DynamoDB idempotency | `IDEMPOTENCY#audit-edd3e5c02569 / STATE` had `record.status=completed` |
| Budget | `tf1-aiops-demo-monthly-tagged-budget`, `25 USD`, health `HEALTHY`, immediate actual spend still `0.0 USD` due Cost Explorer lag |

Rebuilt AgentCore e2e run on `2026-06-29`:

| Field | Value |
| --- | --- |
| Build | Engine rebuilt for `linux/amd64`; AgentCore investigator rebuilt for `linux/arm64` |
| ECR push | Engine digest `sha256:7f1c47894170b27a5c040565e56ab372250248294b34f89bb9cdd5abea2d09f7`; investigator digest `sha256:63cf6cd30c9f8b6178ff95ce5ec8d5f2d5a1a3f51d970759a3e0927464d811b9` |
| Runtime update | `update-agent-runtime` accepted and runtime became `READY` on version `2` |
| Runtime direct smoke | `tool_planning` returned `tool_calls`; `action_wording` returned `actions`; `investigation_summary` returned `summary` |
| Terraform apply | `49 added, 0 changed, 0 destroyed` |
| Workloads | All demo workloads ready/running |
| Public exposure | None; all services were `ClusterIP` |
| Triage audit | `audit-8181fa5d4971` |
| Triage result | `classification=latency_degradation`, `status=DIAGNOSED` |
| AgentCore evidence | 3 live calls recorded: `tools`, `summary`, `actions`; total tokens `2865` |
| Tool planning | `provider=agentcore`, `fallback=false`, `tool_calls=3`, no error |
| Action wording | `provider=agentcore`, `fallback=false`, no error |
| Tenant isolation | Wrong tenant audit lookup returned `404` |
| DynamoDB idempotency | `IDEMPOTENCY#audit-8181fa5d4971 / STATE` had `record.status=completed` |
| Observability | Prometheus returned 3 series, Loki returned 1 stream, Jaeger listed `tf1-ai-triage-engine` and `tf1-telemetry-simulator` |
| Budget/NAT | Budget health `HEALTHY`, immediate actual spend `0.0 USD`; tagged NAT Gateway lookup returned none |

## Cost Explorer Export

Run after Cost Explorer has tagged spend for the demo window:

```powershell
aws ce get-cost-and-usage `
  --region us-east-1 `
  --time-period Start=YYYY-MM-DD,End=YYYY-MM-DD `
  --granularity DAILY `
  --metrics UnblendedCost `
  --filter '{\"Tags\":{\"Key\":\"Project\",\"Values\":[\"tf1-aiops-demo\"]}}' `
  --group-by Type=DIMENSION,Key=SERVICE
```

## Service Breakdown

| AWS service | Cost USD | Notes |
| --- | ---: | --- |
| EKS | TBD | Cluster hourly cost. |
| EC2 | TBD | Managed node group instances and EBS root volumes. |
| DynamoDB | TBD | PAY_PER_REQUEST audit/idempotency table. |
| ECR | TBD | Image pull/storage if reported in account costs. |
| CloudWatch | TBD | Logs/metrics generated by EKS and AWS services. |
| Data Transfer | TBD | Expected to be minimal. |
| Elastic Load Balancing | 0.00 expected | No public LoadBalancer in default manifests. |
| NAT Gateway | 0.00 expected | Terraform disables NAT Gateway. |

Cost Explorer usually lags behind resource creation. During the immediate e2e window, budget actual spend still reported `0.0 USD`. Re-run the Cost Explorer command after AWS cost data catches up.

## Cleanup Evidence

| Check | Result | Evidence |
| --- | --- | --- |
| `kubectl delete namespace tf1-ai-demo` | Pass | Namespace deleted. |
| `terraform destroy` completed | Pass | `49 destroyed` for each demo run. |
| No running EC2 instances tagged `Project=tf1-aiops-demo` | Pass | `describe-instances` returned empty `Reservations`. |
| No EKS cluster `tf1-aiops-demo` | Pass | `describe-cluster` returned `ResourceNotFoundException`. |
| No DynamoDB table `tf1-aiops-audit-demo` unless intentionally retained | Pass | `describe-table` returned `ResourceNotFoundException`. |
| No demo Budget after cleanup | Pass | `describe-budget` returned `NotFoundException`. |
| No LoadBalancer/NAT resources for demo | Pass | Services were `ClusterIP`; ELBv2 returned empty `LoadBalancers`; tagged NAT Gateway lookup returned none. |
| Terraform state empty | Pass | `terraform state list` returned no resources after destroy. |

Note: immediately after destroy, Resource Groups Tagging API briefly returned the old default security group ARN. Direct EC2 verification returned `InvalidGroup.NotFound`, and tagged VPC lookup returned an empty list, so this was tagging API lag rather than a live resource.
