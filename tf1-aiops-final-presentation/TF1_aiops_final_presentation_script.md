# TF1 AIOps Final Presentation Script

## 0. Opening

Chào thầy/cô và các anh chị. Phần TF1 của tụi em là Triage Hub AI Engine: nhận incident/evidence từ CDO, chạy RCA, trả lại diagnosis, confidence, recommended actions, audit id, và payload đủ để CDO tạo Jira/Slack handoff.

Thông điệp chính: tụi em không build chatbot. Tụi em build một diagnostic pipeline có guardrail, có audit, có fallback, và có boundary rõ giữa AI Ops và CDO.

## 1. Problem

Client không thiếu alert. Vấn đề là mỗi alert thiếu context để engineer hành động nhanh.

Current flow thường là: alert bắn ra, engineer mở dashboard, query logs, xem recent deploy, hỏi owner, rồi mới tạo Jira hoặc ping Slack. Với 50 microservices, phần manual digging này làm MTTA tăng và tạo on-call burnout.

Mục tiêu của TF1 là biến alert thành một triage packet có cấu trúc: root-cause hypothesis, evidence, confidence, owner, action, audit trail, và ticket payload.

## 2. AI Engine Story

AI engine chạy theo hướng compute-first:

- normalize evidence
- detect anomaly trên metrics/logs/traces
- rank RCA candidates
- classify incident
- confidence gate
- optional AgentCore investigator
- QA check
- action catalog
- response assembly cho Jira/Slack/SQS

Điểm quan trọng: deterministic RCA luôn chạy trước. AgentCore không đứng đầu pipeline và không được tự ý mutate Jira/Slack hay chạy command production.

## 3. Algorithms

Phần RCA không phải một rule đơn giản.

Các signal chính:

- z-score để bắt spike
- EWMA/window shift để bắt thay đổi theo thời gian
- IsolationForest/BARO-lite style để bắt anomaly đa biến
- service profile để phân biệt latency, traffic loss, resource pressure
- topology/causal hints để rank service candidate
- confidence gate để tránh fake RCA khi evidence yếu

Khi ranking RCA, tụi em dùng evidence score chứ không chỉ keyword. Lag correlation chỉ là supporting evidence, không claim causality tuyệt đối.

## 4. Testing Story

Tụi em có một giai đoạn test khá quan trọng.

Ban đầu khi chạy hidden-metadata stress test, accuracy chỉ khoảng 18%. Lý do là classifier cũ gom alert title, logs, và cả metric names vào một text blob rồi scan keyword. Vì dataset luôn có metric như latency, hệ thống bị bias và đoán quá nhiều case thành latency degradation.

Sau đó tụi em fix logic: không dùng raw metric name để keyword-match nữa, chỉ score trên anomaly evidence thật. Kết quả tăng lên khoảng 38%.

Tiếp theo tụi em thêm detector/feature mạnh hơn, gồm BARO-lite/multivariate window shift/service profile. Final benchmark:

- Hidden metadata, deterministic full 735 cases: 62.45% accuracy, 48.11% macro F1
- Production-like metadata, deterministic full 735 cases: 89.66% accuracy, 81.03% macro F1
- AgentCore sample 15 cases: 93.33% accuracy, 93.27% macro F1, khoảng 2.1s latency

Hidden metadata là lower-bound stress test vì alert title/severity/context bị ẩn. Production-like metadata là path thực tế hơn vì CDO contract sẽ gửi service, severity, alert context, ownership, và evidence bundle.

## 5. MTTA / MTTR

Tụi em chưa claim production MTTR thật, vì cần real production timestamps sau khi deploy.

Trong demo, tụi em đo proxy:

- Before manual triage: 15-30 phút để có actionable diagnosis/ticket context
- After deterministic AI path: khoảng 0.45-0.47 ms engine classification trên 735 RCAEval cases
- After AgentCore path: khoảng 2.1 giây average trên sample 15 cases

MTTR proxy: hệ thống giảm delay từ diagnosis đến routed owner action bằng ticket_payload, Slack/SQS handoff, audit_id, recommended action. Full MTTR thật phải đo từ alert fired đến resolved sau production deployment.

Demo-safe wording: tụi em đo time-to-actionable-triage và handoff consistency, không claim production MTTR khi chưa có production incident timeline.

## 6. Router + AgentCore

Router chọn mode dựa trên complexity score:

- đủ context, confidence ổn -> deterministic_only
- confidence thấp, RCA ambiguous, thiếu evidence nhẹ -> agent_assisted
- thiếu context nặng hoặc cần investigator platform -> agent_platform

Nếu AgentCore chưa bật hoặc fail, system fallback về deterministic thay vì fail request.

AgentCore được dùng như investigator layer:

- bounded iteration
- bounded tool call
- strict JSON
- read-only evidence tools
- no shell
- no Jira mutation
- no Slack mutation
- no remediation command

Trade-off: deterministic path cực nhanh; AgentCore chậm hơn vài giây nhưng có reasoning/audit cho incident phức tạp.

## 7. Evidence Request Flow

Khi thiếu context, engine không đoán bừa.

Response có:

- `missing_context`
- `next_evidence_request`

Ví dụ missing metrics/logs/traces/deploy_events/ownership thì engine trả request rõ evidence type, reason, scope, time window, limits. CDO Evidence Builder có thể dùng field này cho next iteration mà không cần đổi input contract hiện tại.

## 8. AI/CDO Boundary

AI Ops owns:

- diagnosis
- RCA candidates
- confidence/status
- suggested actions
- audit/idempotency
- ticket payload contract

CDO owns:

- platform deployment
- Slack dispatcher
- Jira mutation
- SQS handoff
- real EKS/IRSA/networking

Boundary này giúp demo an toàn: AI không tự execute remediation và không trực tiếp mutate collaboration tools nếu production flow đi qua CDO dispatcher.

## 9. E2E Demo Talk Track

Demo flow:

1. CDO/worker sends incident/evidence to `/v1/triage`.
2. AI engine returns classification, status, confidence, root cause, actions, audit_id.
3. Audit/idempotency persists in DynamoDB.
4. Payload is ready for Jira ticket and Slack/SQS notification.
5. If evidence is missing, response includes `missing_context` and `next_evidence_request`.

Say this line clearly:

The deliverable is not only a classification label. The deliverable is an auditable, owner-routable triage packet.

## 10. Q&A Defense

### Q: Why not just use LLM/AgentCore for everything?

Because high-volume triage needs low latency and predictable cost. Deterministic path is the hot path. AgentCore is routed only for ambiguous, missing-context, or high-value incidents.

### Q: Why is hidden telemetry-only accuracy lower?

Because that test intentionally removes alert title, description, and severity hints. It is a lower-bound stress test. Production contract includes metadata and evidence bundle, where full-run accuracy is 89.66%.

### Q: Does AgentCore improve accuracy?

On the controlled 15-case sample it reached 93.33%, but we do not position AgentCore as the main classifier. We position it as bounded investigator/reasoning/audit layer with latency trade-off.

### Q: What about MTTA/MTTR?

We measured proxy MTTA: alert/evidence to actionable diagnosis and ticket payload. Deterministic path is sub-millisecond; AgentCore path is around 2.1s average. Real production MTTA/MTTR requires timestamps after CDO deployment.

### Q: Can the AI take unsafe action?

No. Recommended actions come from a catalog and require human review for risky steps. AI returns payload; CDO/on-call owns execution.

### Q: What if context is missing?

Engine returns `INSUFFICIENT_CONTEXT` or `INVESTIGATE`, plus `missing_context` and `next_evidence_request`. It does not force a high-confidence diagnosis from weak evidence.

## 11. Closing

The final story:

We built a bounded AI triage engine. It reduces manual digging, produces evidence-grounded RCA, keeps confidence honest, writes audit/idempotency, and hands CDO a structured payload for Jira/Slack/SQS. The system is designed for safe demo now and measurable MTTA/MTTR improvement after production deployment.
