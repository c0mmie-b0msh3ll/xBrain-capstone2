# Architecture Decision Records - TF1 AI

## ADR-001 - CDO Aggregates Context Bundle Before Calling AI

- **Status**: Proposed
- **Date**: 2026-06-22
- **Context**: TF1 requires logs, metrics, recent deploys, ownership, and runbook/docs context. If AI pulls directly from each observability system, the AI team must own infra credentials, network paths, and data connectors, which increases W12 risk.
- **Decision**: Draft contracts assume CDO aggregates a normalized context bundle and calls `POST /v1/triage`.
- **Consequence**: CDO owns ingestion and integration reliability. AI owns schema validation, diagnosis, confidence, and response payload. This keeps the AI/CDO boundary clear.
- **Alternatives considered**:
  - AI pulls directly from observability stores: richer control, but higher integration and security risk.
  - CDO sends only alert metadata: simpler, but AI suggestions become too generic.

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
- **Decision**: CDO/observability continuously ingests telemetry and runs lightweight alert/anomaly detection. The TF1 AI engine is invoked only after an alert/anomaly/incident candidate exists. Inside the AI engine, deterministic compute logic performs validation, feature extraction, RCA scoring, confidence gating, and safety checks before optional Bedrock synthesis.
- **Consequence**: Bedrock is not the engine of record for RCA. It is used only for grounded summarization and human-readable Jira/Slack output when enabled. This reduces cost and hallucination risk while keeping the CDO/AI boundary clear.
- **Alternatives considered**:
  - Continuous full AI triage over all telemetry: richer detection potential, but too expensive and noisy for capstone scope.
  - CDO calls Bedrock directly: faster demo path, but loses AI-owned schema validation, RCA scoring, confidence behavior, and safety controls.
  - AI directly pulls all telemetry stores: more control for AI team, but requires broad credentials and connector ownership that belongs in CDO/platform.
