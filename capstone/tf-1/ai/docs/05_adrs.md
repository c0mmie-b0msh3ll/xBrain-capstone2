# Architecture Decision Records - TF1 AI

## ADR-001 - Context Layer Aggregates Bundle Before Calling Triage

- **Status**: Proposed
- **Date**: 2026-06-22
- **Context**: TF1 requires logs, metrics, recent deploys, ownership, and runbook/docs context. The broader AIOps app owns observability ingestion and detection, but the triage/RCA function should not pull from every raw telemetry store at request time.
- **Decision**: The AIOps detector/context layer aggregates a normalized context bundle and calls `POST /v1/triage`.
- **Consequence**: The AIOps app owns ingestion, detection, context aggregation, triage, and output integration. The triage engine owns schema validation, diagnosis, confidence, and response payload. This keeps the internal detector/triage boundary clear.
- **Alternatives considered**:
  - Triage pulls directly from observability stores at request time: richer control, but higher coupling and latency.
  - Detector sends only alert metadata: simpler, but AI suggestions become too generic.

## ADR-002 - Use Runbook/Docs-Backed Suggestions

- **Status**: Proposed
- **Date**: 2026-06-22
- **Context**: Mentor feedback said AI suggestions are stronger if backed by runbooks, and docs are important for evaluation.
- **Decision**: AI response will include recommended actions linked to runbook/docs references when available.
- **Consequence**: Suggestions become more defensible and less generic. If the mentor data pack lacks runbooks, the team will author minimal synthetic runbook snippets for the 3 E2E scenarios and label them clearly.
- **Alternatives considered**:
  - Free-form AI suggestions only: faster, but harder to defend and more hallucination-prone.
  - Full runbook management system: out of scope for capstone.

## ADR-003 - Conservative Confidence Gate

- **Status**: Proposed
- **Date**: 2026-06-22
- **Context**: TF1 explicitly forbids auto-remediation and requires confidence to correlate with accuracy.
- **Decision**: Low or ambiguous confidence returns `INVESTIGATE` or `INSUFFICIENT_CONTEXT` instead of a strong root-cause claim.
- **Consequence**: The system may be less assertive, but safer and easier to defend during Q&A.
- **Alternatives considered**:
  - Always produce a best-effort root cause: more impressive demo, but unsafe when data is noisy.
  - Refuse all ambiguous alerts: safe, but poor utility for the noisy-alert scenario.

## ADR-004 - Event-Driven Compute-First Triage

- **Status**: Proposed
- **Date**: 2026-06-22
- **Context**: AIOps telemetry is continuous, but running full triage and LLM synthesis over every metric/log event would be expensive, noisy, and difficult to defend. TF1 also needs RCA decisions to be explainable and confidence-gated.
- **Decision**: The TF1 AIOps app continuously ingests telemetry and runs lightweight alert/anomaly detection. The incident-level triage engine is invoked only after an alert/anomaly/incident candidate exists. Inside the triage engine, deterministic compute logic performs validation, feature extraction, RCA scoring, confidence gating, and safety checks before optional Bedrock synthesis.
- **Consequence**: Bedrock is not the engine of record for RCA. It is used only for grounded summarization and human-readable Jira/Slack output when enabled. This reduces cost and hallucination risk while keeping a clear internal boundary between continuous detection and event-driven triage.
- **Alternatives considered**:
  - Continuous full AI triage over all telemetry: richer detection potential, but too expensive and noisy for capstone scope.
  - Detector calls Bedrock directly: faster demo path, but loses schema validation, RCA scoring, confidence behavior, and safety controls.
  - Triage directly pulls all telemetry stores per incident: more control in one function, but broader permissions and weaker replayability.
