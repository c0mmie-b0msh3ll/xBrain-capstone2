# Solution Design - TF1 Triage Hub

Owner: AI team TF1  
Status: Draft for CDO review  
Last updated: 2026-06-22

## 1. High-Level Architecture

TF1 uses an event-driven triage design. Production-grade ownership is split between platform observability and AIOps reasoning. Platform/DevOps makes telemetry observable, queryable, secure, and bounded. The AIOps app consumes bounded telemetry windows, then performs normalization, aggregation, baseline comparison, anomaly detection, context packaging, RCA, and optional LLM synthesis.

```mermaid
graph LR
    A[Services emit metrics logs traces deploy events] --> B[Platform observability stack: OTel Prometheus Loki CloudWatch]
    B -->|bounded query/export by tenant service env window| C[AIOps ingestion and context service]
    C --> D[Normalize window baseline trend]
    D --> E[Lightweight anomaly detection]
    E -->|Incident candidate| F[Context package]
    F -->|POST /v1/triage| G[Compute-first RCA and confidence gate]
    G -->|Optional grounded synthesis| H[Bedrock LLM]
    G --> I[Jira Slack audit payload]
    H --> I
```

The triage engine is not a direct Bedrock wrapper. It is a Dockerized compute service that receives bounded context, performs schema validation, feature extraction, deterministic RCA scoring, confidence gating, safety checks, and optional LLM synthesis.

## 2. Component Breakdown

| Component | Owner | Responsibility | Tech choice | Reason |
|---|---|---|---|---|
| Observability stack | Platform/DevOps | Collect, store, retain, secure, and expose metrics/logs/traces/deploy events. | OpenTelemetry, Prometheus/Grafana, Loki, CloudWatch, or capstone simulator | Platform ensures data is observable and accessible safely. |
| AIOps ingestion/context | AIOps app | Query bounded telemetry windows, normalize schema, aggregate windows, compute baseline/trend. | Python/FastAPI worker or service | This is product logic, not platform plumbing. |
| Lightweight detection | AIOps app | Detect threshold breaches, anomaly candidates, SLO burn rate, and alert grouping. | Rules/statistics initially; ML/anomaly model later if needed | Runs continuously and cheaply before expensive RCA or LLM synthesis. |
| Context aggregation | AIOps app | Build bounded incident context windows for triage. | Internal service/workflow | Converts raw telemetry into a normalized incident context bundle. |
| AI triage engine | AIOps app | Validate request, extract features, run RCA scoring, confidence gate, and produce response payloads. | Dockerized FastAPI service on ECS/Fargate | Gives the team full control of diagnosis behavior and API contract. |
| Optional LLM synthesis | AIOps app | Turn grounded RCA evidence into concise Jira/Slack wording and runbook-aware recommendations. | Bedrock via AI engine | LLM is used after compute evidence exists, not as the first decision-maker. |
| Ticket/notification integration | AIOps app, with platform credentials/config | Create Jira issue and send Slack notification using AI response payloads. | Jira/Slack APIs or mocks | Required for E2E demo flow. |
| Audit | AIOps app | Persist traceable AI decisions and link them to ticket/notification artifacts. | DynamoDB/S3/CloudWatch, or local store for demo | Required for confidence behavior and demo evidence. |

## 3. Data Flow

1. Services continuously emit telemetry: metrics, logs, traces, deploy events, and alert-source events.
2. Platform observability stack collects/stores telemetry and exposes bounded query/export paths by tenant, service, environment, and time window.
3. AIOps ingestion/context service queries bounded slices, normalizes schema, aggregates windows, and computes baseline/trend.
4. AIOps detector runs lightweight anomaly/alert logic continuously over bounded summaries.
5. When an alert/anomaly/incident candidate is detected, the AIOps app creates a bounded context bundle around the event window.
6. The detector/context layer invokes the triage engine, either through an internal event or `POST /v1/triage`, with normalized alert metadata, metrics, logs, recent deploys, ownership, and runbook/docs context.
7. The AI engine validates tenant/correlation headers, validates schema, extracts features, and runs compute-first RCA rules/scoring.
8. The AI engine applies confidence gates:
   - high enough signal: `DIAGNOSED`
   - weak or conflicting signal: `INVESTIGATE`
   - missing supporting context: `INSUFFICIENT_CONTEXT`
9. If enabled, the AI engine calls Bedrock only to synthesize grounded human-readable diagnosis, recommendations, Jira description, and Slack text.
10. The AIOps integration layer uses the response to create Jira/Slack artifacts and persists or links the audit ID.

## 4. Key Design Decisions

### 4.1 Continuous Triage vs Event-Driven Triage

- Option A: Run full AI triage continuously on all telemetry.
  - Pros: could detect subtle patterns earlier.
  - Cons: expensive, noisy, difficult to scale, and overuses LLM/compute for non-incidents.
- Option B: Run lightweight detection continuously inside the AIOps app, invoke AI triage only on incident candidates.
  - Pros: lower cost, clearer detector/triage boundary, easier to test and defend.
  - Cons: depends on detection quality and context aggregation.

Chosen: Option B. TF1 AIOps continuously detects, then invokes triage event-by-event.

### 4.2 LLM-First vs Compute-First RCA

- Option A: Send raw incident context directly to Bedrock and ask for diagnosis.
  - Pros: faster to prototype.
  - Cons: weaker evidence control, harder confidence calibration, higher hallucination risk.
- Option B: Run deterministic RCA/scoring first, then optionally call Bedrock for synthesis.
  - Pros: more explainable, safer, cheaper, and easier to evaluate.
  - Cons: requires more explicit scenario logic.

Chosen: Option B. Bedrock is optional synthesis after grounded compute evidence.

### 4.3 Triage Pulls Raw Telemetry vs Internal Context Aggregation

- Option A: The triage/RCA function pulls directly from every raw telemetry store at request time.
  - Pros: triage has direct retrieval control.
  - Cons: tighter coupling, higher latency, broader runtime permissions, and harder testing.
- Option B: Platform observability exposes bounded telemetry, then AIOps context logic builds a normalized context bundle before triage.
  - Pros: clearer platform/AIOps separation, cheaper triage calls, easier replay/eval, and safer LLM prompting.
  - Cons: observability data contract must preserve enough evidence for RCA.

Chosen: Option B. Platform owns observability plumbing and bounded access. AIOps owns normalization, detection, context aggregation, and incident-level RCA.

## 5. Risk And Mitigation

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Context bundle misses important telemetry | Medium | High | Return `INSUFFICIENT_CONTEXT`, document missing fields, and add datapack mapping checks. |
| Detection layer sends noisy incident candidates | Medium | Medium | Confidence gate returns `INVESTIGATE` for weak/conflicting signals and tune detector thresholds. |
| LLM hallucinates root cause | Medium | High | Compute-first evidence, schema validation, grounding checks, and no direct auto-remediation. |
| Bedrock throttling or outage | Medium | Medium | Keep rule-based path available; fallback to deterministic response without LLM. |
| Tenant data leak | Low | High | Enforce header/body tenant match and avoid cross-request context persistence. |
| Team conflates continuous detection with full continuous LLM triage | Medium | Medium | Document two-stage design: continuous detector, event-driven triage/RCA. |

## 6. Open Design Questions

- [ ] Final auth mechanism if detector and triage are deployed as separate services.
- [ ] Persistent audit store implementation for demo.
- [ ] Exact telemetry sources for the capstone demo: simulator, Prometheus, CloudWatch, or mentor datapack replay.
- [ ] Mentor datapack schema and whether it includes runbooks/docs.

## Related Documents

- [`03_ai_engine_spec.md`](03_ai_engine_spec.md) - AI engine architecture detail, governance, and security.
- [`../contracts/observability-data-contract.md`](../contracts/observability-data-contract.md) - platform observability handoff.
- [`../contracts/telemetry-contract.md`](../contracts/telemetry-contract.md) - normalized context bundle contract.
- [`../contracts/ai-api-contract.md`](../contracts/ai-api-contract.md) - API consumed by the detector/context layer.
- [`../contracts/deployment-contract.md`](../contracts/deployment-contract.md) - deployment topology.
- [`05_adrs.md`](05_adrs.md) - architecture decision records.
