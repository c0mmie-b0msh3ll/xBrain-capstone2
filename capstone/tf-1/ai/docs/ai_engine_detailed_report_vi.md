# Báo Cáo Chi Tiết AI Engine - TF1 AIOps Triage

Owner: AI team TF1  
Scope: W11/W12 demo implementation hiện tại  
API chính: `POST /v1/triage`

## 1. Mục Tiêu Của AI Engine

AI engine là dịch vụ triage sự cố theo hướng **compute-first**. Engine không phải là wrapper gọi LLM trực tiếp. Engine nhận một incident context đã được chuẩn hóa, kiểm tra tenant/correlation, làm giàu evidence trong giới hạn an toàn, chạy RCA deterministic, chọn investigation mode, sau đó mới dùng AgentCore nếu mode yêu cầu.

Public contract hiện tại chỉ yêu cầu:

- `GET /healthz`
- `POST /v1/triage`

Slack rendering, Jira creation, Jira assignment thật, remediation, rollback, restart, scale, hoặc shell command đều không thuộc AI engine. AI chỉ trả raw diagnosis fields, `ticket_payload`, recommended actions dạng advisory, và optional assignee suggestion.

## 2. High-Level Flow

Flow runtime chính:

```text
POST /v1/triage
-> validate headers/body tenant/correlation
-> enrich context bằng bounded read-only tools nếu cần
-> chạy deterministic RCA baseline
-> deterministic classification
-> chọn investigation mode
-> dispatch theo mode:
   - deterministic_only
   - agent_assisted
   - agent_platform
-> enrich Jira history read-only nếu cấu hình có
-> reclassify hoặc validate final answer từ agent
-> QA checks và confidence adjustment
-> select action từ action catalog
-> assemble TriageResponse
```

Response contract không đổi. Các thông tin về mode, reason, agent trace, fallback, QA, tool calls được ghi trong `llm_metadata`.

## 3. Investigation Modes

AI engine có 3 mode investigation:

| Mode | Vai trò | Khi dùng |
|---|---|---|
| `deterministic_only` | Chỉ dùng deterministic RCA, QA, action catalog. Không gọi AgentCore summary/action wording. | Incident đơn giản, đủ context, confidence cao, hoặc AgentCore disabled. |
| `agent_assisted` | Deterministic RCA là chính. AgentCore chỉ đề xuất tool calls để bổ sung evidence. Engine validate tool và re-run deterministic RCA. | Incident vừa phức tạp, thiếu một phần context, hoặc confidence chưa đủ. |
| `agent_platform` | AgentCore Runtime là investigator chính cho demo agent platform. Engine giữ vai trò policy/tool gateway/fallback. | Incident phức tạp, thiếu nhiều context, confidence thấp, ambiguous RCA. |

Mode được force bằng env:

```text
AIOPS_INVESTIGATION_MODE=auto|deterministic_only|agent_assisted|agent_platform
```

Default là `auto`.

## 4. Mode Selection Và Decision Thresholds

Mode router chạy sau context enrichment và deterministic baseline RCA.

Env thresholds:

```text
AIOPS_ASSISTED_COMPLEXITY_THRESHOLD=3
AIOPS_AGENT_COMPLEXITY_THRESHOLD=6
```

Complexity score:

| Điều kiện | Điểm | Reason metadata |
|---|---:|---|
| Thiếu metrics | +2 | `missing_metrics` |
| Thiếu logs | +2 | `missing_logs` |
| Thiếu traces | +1 | `missing_traces` |
| Thiếu recent deploys | +1 | `missing_recent_deploys` |
| Thiếu ownership/runbook context | +1 | `missing_ownership` |
| Deterministic confidence `< 0.7` | +2 | `low_confidence` |
| Deterministic confidence `< 0.5` | +1 thêm | `very_low_confidence` |
| Status `INSUFFICIENT_CONTEXT` | +3 | `insufficient_context_status` |
| Status `INVESTIGATE` | +2 | `investigate_status` |
| Top 2 RCA candidate confidence delta `<= 0.15` | +2 | `ambiguous_rca_candidates` |
| Có topology/causal hints nhưng confidence thấp | +1 | `dependency_or_causal_hints_low_confidence` |
| Severity `critical/high` và thiếu context | +1 | `high_severity_missing_context` |
| Evidence URI missing/out of scope | +1 | `evidence_uri_missing_or_out_of_scope` |

Routing:

```text
score >= 6 -> agent_platform
score >= 3 -> agent_assisted
score < 3  -> deterministic_only
```

Nếu `AIOPS_INVESTIGATION_MODE` khác `auto`, env override thắng auto router.

Nếu AgentCore disabled nhưng auto đáng ra chọn agent mode, engine vẫn trả `deterministic_only` và ghi planned mode:

```json
{
  "llm_metadata": {
    "investigation_mode": "deterministic_only",
    "mode_selection": {
      "source": "auto",
      "complexity_score": 8,
      "planned_mode": "agent_platform",
      "selected_mode": "deterministic_only",
      "agentcore_enabled": false
    }
  }
}
```

## 5. Context Enrichment

Context enrichment chạy trước deterministic RCA.

Input ban đầu có thể thiếu metrics/logs/traces/deploys/ownership. Nếu có configured source, engine dùng bounded read-only context tools để bổ sung:

- metrics
- logs
- traces
- recent deploys
- ownership/runbooks
- evidence bundle từ `alert.labels.evidence_uri`

Evidence lookup phải luôn nằm trong scope:

- `tenant_id`
- `service`
- `environment`
- incident time window
- max window configured
- max log limit configured

Nếu evidence bundle missing hoặc out of scope, engine không mở rộng scope. Nó fallback sang scoped tools nếu có, hoặc giữ context sparse và giảm confidence/status.

## 6. Deterministic RCA Algorithms

Deterministic RCA nằm ở `app/rca.py`. Các thuật toán chính:

### 6.1 Threshold Detection

Engine phát hiện anomaly bằng threshold đơn giản:

| Metric pattern | Threshold |
|---|---:|
| latency | `>= 1000ms` |
| error / 5xx | `>= 5` |
| availability | `< 95` |
| timeout | `>= 10` |
| CPU / memory | `>= 85` |

Output là `anomaly_evidence` với detector `threshold`.

### 6.2 Rolling Z-Score

Nếu metric series có ít nhất 2 điểm:

```text
z_score = (current - baseline_mean) / baseline_std
```

Nếu `abs(z_score) >= 3.0`, engine tạo anomaly evidence `rolling_zscore_3sigma`.

### 6.3 EWMA Drift

EWMA dùng alpha mặc định:

```text
alpha = 0.35
```

Nếu latest value drift đủ xa khỏi EWMA expected value, engine tạo evidence `ewma_drift`.

### 6.4 Isolation Forest

Nếu metric series có đủ dữ liệu:

- ít nhất 8 điểm
- ít nhất 3 giá trị khác nhau

Engine dùng `sklearn.ensemble.IsolationForest` với:

```text
contamination=0.15
random_state=7
```

Nếu latest point bị model xem là outlier, engine tạo evidence `isolation_forest`.

### 6.5 Log Keyword Detection

Engine scan log level + message với các token:

```text
error
timeout
failed
refused
exhausted
down
deadline
```

Evidence type là `log_keyword`.

### 6.6 Topology Inference

Engine infer service topology từ:

- `metric.labels.dependency`
- `log.labels.dependency`
- dependency token trong log message như `redis`, `postgres`, `mysql`, `kafka`, `s3`, `checkout`, `payment`, `inventory`

Output:

```json
{
  "root_service": "...",
  "nodes": [],
  "edges": [
    {"source": "...", "target": "...", "evidence": "..."}
  ]
}
```

### 6.7 Causal Hints

Engine tính lag correlation khi có đủ metric points theo service.

Nếu absolute correlation `>= 0.7`, engine tạo causal hint:

```text
type = lag_correlation
direction = experimental
```

Causal hints là supporting evidence, không phải proof.

### 6.8 RCA Candidate Ranking

Engine rank RCA candidates bằng score tổng hợp:

- anomaly evidence score theo service
- dependency topology edge
- recent deploy correlation
- causal hints

Nếu không có signal, alerted service được gán anchor score `0.25`.

Output gồm:

- `rank`
- `service`
- `score`
- `confidence`
- `reasons`

## 7. Deterministic Classification

Classifier chạy sau deterministic RCA.

Status hợp lệ:

```text
DIAGNOSED
INVESTIGATE
INSUFFICIENT_CONTEXT
UNSAFE_SUGGESTION_BLOCKED
```

Classification chính:

```text
insufficient_context
noisy_or_ambiguous_alert
critical_service_down
latency_degradation
general_investigation
```

Rules:

| Điều kiện | Status | Classification | Confidence |
|---|---|---|---:|
| Không có metrics/logs/deploys/ownership | `INSUFFICIENT_CONTEXT` | `insufficient_context` | `0.25` |
| Low/unknown severity, noisy/flapping/false alarm/ambiguous | `INVESTIGATE` | `noisy_or_ambiguous_alert` | `0.45` |
| Critical/down/unavailable/connection refused | `DIAGNOSED` | `critical_service_down` | `0.86` |
| latency/p95/timeout/latency anomaly | `DIAGNOSED` | `latency_degradation` | `0.82` |
| Có context nhưng không match rule mạnh | `INVESTIGATE` | `general_investigation` | `0.55` |

## 8. Agent-Assisted Flow

`agent_assisted` dùng existing tool investigation path.

Flow:

```text
deterministic RCA
-> AgentCore proposes tool_calls
-> engine validates tool name + args + scope
-> engine executes read-only tool via ToolRegistry
-> merge result into request
-> rerun deterministic RCA
-> deterministic reclassification
```

Agent chỉ được đề xuất tool. Engine quyết định có execute không.

Nếu AgentCore lỗi, engine fallback deterministic và ghi metadata:

```json
{
  "tool_investigation": {
    "fallback": true,
    "error": "..."
  }
}
```

## 9. Agent Platform Flow

`agent_platform` dùng AgentCore Runtime làm investigator chính.

Flow:

```text
baseline deterministic RCA
-> build AgentCore investigator packet
-> invoke AgentCore Runtime
-> agent returns tool_requests hoặc final_diagnosis
-> engine validates tool requests
-> engine executes allowed read-only tools
-> engine sends observations back next iteration
-> repeat until final_diagnosis hoặc budget hit
-> engine validates final diagnosis
-> assemble response
```

AgentCore owns:

- planning
- synthesis
- final diagnosis proposal

AI engine owns:

- tool allowlist
- tenant/environment/service/window validation
- read-only tool execution
- result merge
- policy validation
- deterministic fallback
- action catalog filtering

## 10. AgentCore Response Protocol

Tool request:

```json
{
  "type": "tool_requests",
  "thought_summary": "Need logs and deploy correlation.",
  "tool_calls": [
    {"name": "get_logs", "args": {"limit": 10}},
    {"name": "get_recent_deploys", "args": {}}
  ]
}
```

Final diagnosis:

```json
{
  "type": "final_diagnosis",
  "classification": "latency_degradation",
  "status": "DIAGNOSED",
  "confidence": 0.78,
  "summary": "payment-api latency is likely tied to dependency timeout signals.",
  "evidence": ["Representative log: database timeout after 3000ms"],
  "recommended_action_ids": ["dependency_timeout_triage"],
  "qa": {"passed": true, "gaps": []}
}
```

Malformed JSON, unknown `type`, invalid final answer, hoặc runtime error đều fallback deterministic.

## 11. Tool Registry Và Bounds

Allowed tools:

```text
get_metrics
get_logs
get_traces
get_recent_deploys
get_ownership
detect_metric_anomalies
detect_log_anomalies
infer_topology
infer_causal_hints
rank_rca_candidates
get_jira_history
```

Disallowed/unknown tool bị block.

Scope validation:

| Scope field | Rule |
|---|---|
| `tenant_id` | Phải match incident tenant |
| `environment` | Phải match incident environment |
| `service` | Phải match alerted service |
| `window_start/window_end` | Phải nằm trong incident bounds |
| max window | Không vượt `LLM_TOOL_MAX_WINDOW_MINUTES` |
| log limit | Clamp bởi `LLM_TOOL_LOG_LIMIT` |

Important bounds:

```text
AIOPS_AGENT_MAX_ITERATIONS=2
AIOPS_AGENT_MAX_TOOL_CALLS=5
AIOPS_TRIAGE_DEADLINE_SECONDS=30
AIOPS_LLM_MAX_TOKENS_PER_INCIDENT=0 by default, set to enforce budget
LLM_TOOL_MAX_WINDOW_MINUTES=60
LLM_TOOL_LOG_LIMIT=50
```

## 12. Model Runtime

AgentCore/Bedrock là optional.

Env chính:

```text
AGENTCORE_RUNTIME_ARN
AWS_REGION or AWS_DEFAULT_REGION
ENABLE_AGENTCORE_LLM
ENABLE_AGENTCORE_LLM_TOOLS
BEDROCK_MODEL_ID
BEDROCK_MODEL_IDS
```

Default model priority trong code:

```text
us.anthropic.claude-opus-4-8
us.anthropic.claude-opus-4-6-v1
us.amazon.nova-2-lite-v1:0
```

Nếu không có AgentCore config, engine vẫn trả deterministic response.

## 13. Guardrails

Guardrails chính:

- Không auto-remediate.
- Không execute rollback/restart/scale/delete.
- Không shell command.
- Không Jira mutation.
- Không Slack posting.
- Không PromQL/LogQL arbitrary query từ agent.
- Agent không nhận backend credentials.
- Agent không gọi observability backend trực tiếp.
- Unknown tool bị block.
- Tool args ngoài tenant/service/environment/time window bị block.
- Unknown action IDs bị ignore.
- Final diagnosis phải dùng classification/status hợp lệ.
- Confidence phải trong `0.0..1.0`.
- `DIAGNOSED` phải có evidence không rỗng.

Blocked final-answer text tokens gồm:

```text
kubectl
curl
rm
delete
restart
rollback
scale
promql
logql
jira create
slack post
shell
```

Nếu final answer chứa unsafe operational text, engine fallback deterministic.

## 14. Action Selection

Recommended actions không lấy trực tiếp từ agent free-form text. Engine chọn từ action catalog.

Catalog action examples:

```text
attach_telemetry_context
observe_user_impact
human_review_noisy_alert
service_down_runbook
page_service_owner
dependency_timeout_triage
latency_saturation_review
consider_recent_deploy_rollback
resource_saturation_triage
disk_pressure_triage
queue_backlog_triage
auth_failure_triage
network_dns_triage
kubernetes_crashloop_triage
rate_limit_throttling_triage
consult_internal_runbooks
```

Risk rules:

- `insufficient_context` và `noisy_or_ambiguous_alert`: chỉ low-risk actions.
- confidence `< 0.6`: chỉ low-risk actions.
- medium-risk rollback consideration yêu cầu human approval.

Trong `agent_platform`, `recommended_action_ids` từ agent chỉ là advisory. Engine vẫn gọi `select_actions()` và chỉ intersect với known catalog actions nếu phù hợp.

## 14.1 Action Coverage Mở Rộng

Current implementation đã mở rộng actionable suggestions để cover thêm các nhóm incident phổ biến:

| Signal family | Action ID |
|---|---|
| CPU/memory/connection-pool saturation | `resource_saturation_triage` |
| Disk/inode/filesystem pressure | `disk_pressure_triage` |
| Queue backlog / Kafka lag / consumer lag | `queue_backlog_triage` |
| Auth/OAuth/JWT/401/403 failures | `auth_failure_triage` |
| DNS/TLS/certificate/network errors | `network_dns_triage` |
| Kubernetes crash loop/readiness/liveness/pod restarts | `kubernetes_crashloop_triage` |
| 429/rate limit/throttling/quota | `rate_limit_throttling_triage` |
| Có internal runbook/known-error evidence | `consult_internal_runbooks` |

Các action này vẫn giữ nguyên guardrail:

- chỉ là recommendation,
- có `risk`,
- có `why`,
- có `evidence_refs`,
- dùng action type trong allowlist,
- không execute remediation.

## 14.2 Internal Documents Vs Internet Search

Runtime incident triage **không cho LLM search internet tự do**.

Lý do:

- public internet advice có thể stale hoặc generic,
- dễ prompt injection,
- khó audit,
- có thể mâu thuẫn internal runbook,
- có thể gợi ý command unsafe.

Thay vào đó, AgentCore chỉ được dùng bounded read-only internal tools:

```text
search_runbooks
search_known_errors
get_jira_history
get_ownership
```

`search_runbooks` đọc runbook từ ownership/service catalog đã cấu hình.  
`search_known_errors` đọc known-error/postmortem style records từ `KNOWN_ERRORS_PATH`, scoped theo tenant/service/environment.

Internet/public docs chỉ nên dùng offline để con người curate thêm action catalog hoặc runbook, sau đó review và commit vào repo/config.

## 15. QA Và Confidence Adjustment

QA chạy sau investigation.

Checks:

- `DIAGNOSED` phải có evidence.
- Không được diagnosis nếu không có supporting context.
- `latency_degradation` phải có latency-related evidence.

Nếu QA fail hoặc token budget exceeded:

- ghi `llm_metadata.qa`
- giảm confidence `-0.1`
- tăng degraded mode metric

QA metadata example:

```json
{
  "qa": {
    "enabled": true,
    "iterations": 1,
    "result": "passed"
  }
}
```

## 16. Fallback Behavior

Fallback principle: **triage không fail chỉ vì AgentCore fail**. Nếu agent unavailable/invalid/over-budget, API trả deterministic RCA với degraded metadata.

Fallback reasons:

```text
agentcore_disabled
unknown_agent_response_type
malformed_tool_requests
max_iterations
malformed_agent_json
invalid_final_diagnosis
agent_runtime_error
llm_tool_failure
qa_budget_exceeded
qa_failed
triage_exception
```

Fallback metadata example:

```json
{
  "agent_platform": {
    "enabled": true,
    "provider": "agentcore",
    "iterations": 1,
    "fallback": true,
    "fallback_reason": "invalid_final_diagnosis"
  }
}
```

## 17. Observability

Metrics chính:

```text
aiops_triage_requests_total
aiops_triage_request_duration_seconds
aiops_triage_inflight_requests
aiops_context_tool_calls_total
aiops_context_tool_duration_seconds
aiops_context_enrichment_missing_fields_total
aiops_context_enrichment_result_total
aiops_llm_calls_total
aiops_llm_tokens_total
aiops_llm_estimated_cost_usd_total
aiops_qa_iterations_total
aiops_budget_exceeded_total
aiops_degraded_mode_total
aiops_investigation_mode_selected_total
aiops_agent_iterations_total
aiops_agent_tool_requests_total
aiops_agent_fallback_total
```

Spans:

```text
request_validation
triage_request
context_enrichment
deterministic_rca
mode_selection
llm_investigation
agent_platform
agent_platform_iteration
agent_platform_tool_gateway
agent_platform_final_validation
deterministic_rca_reclassify
qa
response_assembly
```

Structured logs theo metadata-only policy. Logs include:

- `audit_id`
- `tenant_id`
- `correlation_id`
- `incident_id`
- `service`
- `environment`
- `stage`
- `status`
- `classification`
- `duration_ms`
- counts của metrics/logs/traces/deploys
- `investigation_mode`
- `complexity_score`
- `agent_iterations`
- `fallback_reason`

Raw customer evidence không được log.

## 18. Public Response Contract

Public `/v1/triage` response vẫn giữ shape hiện tại:

- `incident_id`
- `classification`
- `severity`
- `confidence`
- `status`
- `suspected_root_cause`
- `recommended_actions`
- `ticket_payload`
- optional `suggested_assignee_account_id`
- optional `suggestion_reason`
- `audit_id`
- optional RCA/evidence fields
- `llm_metadata`

Engine không trả `slack_payload`. CDO tự render Slack Block Kit từ raw fields.

## 19. Config Summary

Core mode config:

```text
AIOPS_INVESTIGATION_MODE=auto
AIOPS_ASSISTED_COMPLEXITY_THRESHOLD=3
AIOPS_AGENT_COMPLEXITY_THRESHOLD=6
AIOPS_AGENT_MAX_ITERATIONS=2
AIOPS_AGENT_MAX_TOOL_CALLS=5
AIOPS_TRIAGE_DEADLINE_SECONDS=30
```

Tool bounds:

```text
LLM_TOOL_MAX_WINDOW_MINUTES=60
LLM_TOOL_LOG_LIMIT=50
LLM_TOOL_MAX_CALLS=3
```

AgentCore:

```text
AGENTCORE_RUNTIME_ARN
ENABLE_AGENTCORE_LLM=true
ENABLE_AGENTCORE_LLM_TOOLS=true
AWS_REGION=us-east-1
BEDROCK_MODEL_ID or BEDROCK_MODEL_IDS
```

Context sources:

```text
PROMETHEUS_URL
LOKI_URL
JAEGER_URL
DEPLOY_METADATA_PATH
OWNERSHIP_PATH
EVIDENCE_BUNDLE_BASE_PATH
JIRA_HISTORY_PATH
```

## 20. Verification

Current validation commands:

```bash
python -m compileall app scripts
python -m pytest tests -q
python scripts/validate_datapack.py
docker compose -f docker-compose.observability.yml config --quiet
```

Latest known test status:

```text
51 passed
```

## 21. Implementation Files

Important files:

- `engine-skeleton/app/main.py`: API models, `/v1/triage`, pipeline orchestration, response assembly.
- `engine-skeleton/app/investigation_router.py`: complexity scoring and mode selection.
- `engine-skeleton/app/agent_runtime.py`: AgentCore platform loop, final validation, fallback.
- `engine-skeleton/app/context_tools.py`: read-only tool registry and scope validation.
- `engine-skeleton/app/context_enrichment.py`: bounded context enrichment before RCA.
- `engine-skeleton/app/rca.py`: deterministic anomaly detection, topology, causal hints, RCA ranking.
- `engine-skeleton/app/action_catalog.py`: catalog actions and risk gating.
- `engine-skeleton/app/llm.py`: AgentCore calls for summary/action wording/assisted tools.
- `engine-skeleton/app/observability.py`: Prometheus metrics, tracing, metadata-only logs.
