# Architecture Decision Records - TF1 AI

## ADR-001 - CDO Pushes Incident Context Before AI Triage

- **Status**: Accepted
- **Date**: 2026-06-22
- **Context**: TF1 requires logs, metrics, recent deploys, ownership, and runbook/docs context. Platform/DevOps owns observability plumbing and alert detection, while the triage/RCA function should not poll every raw telemetry store to discover incidents.
- **Decision**: CDO/platform detects alerts and pushes incident seed/context to AI Ops. AI Ops may pull bounded evidence from the customer's observability/evidence layer through CDO/platform-approved access after alert delivery if the initial context is insufficient, then calls `POST /v1/triage`.
- **Consequence**: Platform owns data availability, alert detection, quality, access, retention, and security. AIOps owns context validation/enrichment, triage, confidence, and output integration. This keeps the platform/AIOps boundary clear and avoids polling delay.
- **Alternatives considered**:
  - Triage pulls directly from observability stores at request time: richer control, but higher coupling and latency.
  - AI Ops continuously polls CDO/customer telemetry for alerts: flexible, but duplicates monitoring responsibility and introduces polling delay.
  - Detector sends only alert metadata: simpler, but AI suggestions become too generic.

## ADR-002 - Use Runbook/Docs-Backed Suggestions

- **Status**: Accepted
- **Date**: 2026-06-22
- **Context**: Mentor feedback said AI suggestions are stronger if backed by runbooks, and docs are important for evaluation.
- **Decision**: AI response will include recommended actions linked to runbook/docs references when available.
- **Consequence**: Suggestions become more defensible and less generic. If the mentor data pack lacks runbooks, the team will author minimal synthetic runbook snippets for the 3 E2E scenarios and label them clearly.
- **Alternatives considered**:
  - Free-form AI suggestions only: faster, but harder to defend and more hallucination-prone.
  - Full runbook management system: out of scope for capstone.

## ADR-003 - Conservative Confidence Gate

- **Status**: Accepted
- **Date**: 2026-06-22
- **Context**: TF1 explicitly forbids auto-remediation and requires confidence to correlate with accuracy.
- **Decision**: Low or ambiguous confidence returns `INVESTIGATE` or `INSUFFICIENT_CONTEXT` instead of a strong root-cause claim.
- **Consequence**: The system may be less assertive, but safer and easier to defend during Q&A.
- **Alternatives considered**:
  - Always produce a best-effort root cause: more impressive demo, but unsafe when data is noisy.
  - Refuse all ambiguous alerts: safe, but poor utility for the noisy-alert scenario.

## ADR-004 - Event-Driven Compute-First Triage

- **Status**: Accepted
- **Date**: 2026-06-22
- **Context**: Observability telemetry is continuous, but running full triage and LLM synthesis over every metric/log event would be expensive, noisy, and difficult to defend. TF1 also needs RCA decisions to be explainable and confidence-gated.
- **Decision**: Platform/DevOps continuously collects telemetry and owns alert/anomaly detection. The incident-level triage engine is invoked only after CDO/platform pushes an alert/anomaly/incident candidate to AI Ops. Inside the triage engine, deterministic compute logic performs validation, feature extraction, RCA scoring, confidence gating, and safety checks before optional Bedrock synthesis.
- **Consequence**: Bedrock is not the engine of record for RCA. It is used only for grounded summarization and human-readable Jira/Slack output when enabled. This reduces cost and hallucination risk while keeping a clear boundary between platform detection and AI triage.
- **Alternatives considered**:
  - Continuous full AI triage over all telemetry: richer detection potential, but too expensive and noisy for capstone scope.
  - Detector calls Bedrock directly: faster demo path, but loses schema validation, RCA scoring, confidence behavior, and safety controls.
  - AI Ops continuously polls for alerts: gives AI retrieval control, but adds latency and turns AI Ops into a monitoring/data platform.
  - Triage directly pulls all telemetry stores per incident: more control in one function, but broader permissions and weaker replayability.

## ADR-005 - Add Supporting Observability Data Contract Beside The 3 Signed Contracts

- **Status**: Accepted
- **Date**: 2026-06-23
- **Context**: The W11 announcement requires exactly three signed AI-CDO contracts: Telemetry, AI API, and Deployment. TF1 also needs a concrete explanation of how CDO hosts or exposes extra metrics/logs/traces/deploy/ownership evidence. Putting all data availability detail into the signed telemetry contract made that file too broad and risked confusing raw observability access with the normalized triage payload.
- **Decision**: Keep the three signed/frozen W11 contracts as `telemetry-contract.md`, `ai-api-contract.md`, and `deployment-contract.md`. Add `observability-data-contract.md` as a supporting data-availability contract/handoff, not counted as a fourth signed contract.
- **Consequence**: CDO/platform can review concrete data availability and quality expectations without being asked to implement RCA logic. AIOps can own context validation/enrichment, evidence sufficiency, and triage logic. The repo can explain extra evidence clearly while still satisfying the "3 contracts" requirement.
- **Alternatives considered**:
  - One large telemetry contract: simpler file count, but unclear ownership and easier to misread as "platform does RCA".
  - Raw data directly into triage API: faster prototype, but unsafe, noisy, and hard to evaluate.

## ADR-006 - Use RCAEval Subset As Primary W11 Scenario Data

- **Status**: Accepted
- **Date**: 2026-06-24
- **Context**: The team needs defensible scenario data for W11 and CDO handoff. Synthetic fixtures are useful for smoke tests, but they are not enough to claim credible RCA evaluation.
- **Decision**: Use the checked-in RCAEval subset under `engine-skeleton/datapack/external/` as the primary scenario source. Generate CDO-hostable evidence bundles under `engine-skeleton/datapack/external/evidence-bundles/`.
- **Consequence**: Scenario evidence is grounded in the RCAEval subset. RE2/RE3 logs and traces are used when present in the official dataset. Where a selected case lacks logs/traces, or where RCAEval lacks deploy metadata, ownership, or runbooks, TF1 may add supplemental records and mark them in `data_lineage`.
- **Alternatives considered**:
  - Use generated demo fixtures only: easier, but weak for CDO/mentor defense.
  - Wait for richer external data before handoff: safer academically, but blocks W11 contract review.

## ADR-007 - Keep Shared CDO Handoff Transport-Neutral

- **Status**: Accepted
- **Date**: 2026-06-24
- **Context**: The AI handoff is shared across CDO teams, and each CDO team may choose different infrastructure integration details.
- **Decision**: Shared contracts describe incident seed events, bounded evidence bundles, read-only evidence proxy operations, and deployment/API boundaries without requiring a specific transport implementation.
- **Consequence**: CDO teams can implement the integration with their chosen platform mechanism while preserving the same AI-facing contracts. Transport-specific notes must stay in team-private implementation docs, not in shared CDO handoff artifacts.
- **Alternatives considered**:
  - Put transport-specific details into shared contracts: concrete, but leaks team-specific design and couples both CDO teams unnecessarily.
  - Leave integration unspecified: flexible, but CDO would not know what data to host or expose.
