# Discovery Debrief - TF1 Triage Hub

Owner: AI team TF1
Date: 2026-06-24
Status: Ready for W11 onsite review

## Purpose

Record the Phase 1 discovery understanding before W11 contract sign-off. This is the AI team's "I understand that..." debrief for mentor/client confirmation.

## Client Understanding

TF1 client is the CTO of a B2B SaaS startup with about 20,000 active users and about 50 production microservices. The on-call team has 8 engineers and receives about 50+ alerts per week. Each alert currently takes about 30-60 minutes from Slack ping to root-cause understanding because engineers manually dig through logs, metrics, deploy history, Jira, and ownership channels.

The client wants Triage Hub to reduce the investigation start time by automatically assembling bounded context, diagnosing likely root cause, creating a structured Jira payload, and notifying the responsible owner in Slack. Engineers still confirm and act. Auto-remediation is out of scope.

## Confirmed Scope For W11

| Area | Understanding |
|---|---|
| Scenarios | Critical service down, latency degradation, noisy/false-positive alert. |
| Data | RCAEval subset is the primary scenario source. Synthetic/demo fixtures are supplemental only. |
| Extra evidence | CDO hosts/exposes bounded evidence bundles first; read-only evidence proxy can follow. |
| AI boundary | AIOps does not call customer apps directly; it consumes CDO-hosted evidence. |
| Slack | One-way notification/payload for W11; two-way read-only Slack Q&A is future MVP work. |
| Jira | TF1 produces `ticket_payload`; live publisher is feature-gated/future work until Jira config and accountId mapping exist. |
| Safety | No auto-remediation; rollback/restart/scale/DB actions are advisory only and require human approval. |
| Tenant isolation | Every request must include tenant context and must reject tenant/header mismatch. |
| Audit | Every triage response must include `audit_id` and report metadata where available. |

## Discovery Questions Prepared

1. Which alert source should be considered canonical for incident seed events?
2. What are the exact service names and tenant/environment labels CDO will preserve?
3. Which observability stores are available for metrics, logs, and traces?
4. Can CDO host precomputed evidence bundles in object storage or metadata storage for W11?
5. If live queries are needed, which team owns the read-only evidence proxy?
6. What is the maximum allowed default time window for incident evidence?
7. Which log fields may contain PII and must be redacted before AIOps/LLM use?
8. Which system is the source of truth for deploy events?
9. Which system is the source of truth for ownership/team routing?
10. Which Slack channels or user groups are allowed to receive incident notifications?
11. Which Jira project, issue type, and components should be used for TF1 demo tickets?
12. Is personal Jira assignment required, or is team/queue assignment sufficient for W11?
13. What fallback should happen if Slack is unavailable?
14. What fallback should happen if Jira is unavailable?
15. What manual MTTA/MTTR baseline should be used for the three demo scenarios?

## Decisions After Discovery

| Decision | Rationale |
|---|---|
| Use event-driven triage, not continuous full AI triage | Keeps cost and noise bounded while CDO/platform owns alert detection and AI Ops handles incident-level RCA. |
| Use compute-first RCA with optional AgentCore/LLM synthesis | Gives explainable evidence and confidence behavior before natural-language output. |
| Use RCAEval subset as primary scenario data | More defensible than generated-only fixtures for RCA discussion. |
| Use precomputed evidence bundles as CDO MVP | Gives CDO a concrete data artifact to host and avoids direct AI credentials into observability backends. |
| Keep shared CDO handoff transport-neutral | Allows both CDO teams to implement their own platform details while preserving one AI-facing contract. |
| Keep Jira personal assignment out of W11 | Jira Cloud requires accountId mapping; queue/team routing is safer until mapping exists. |

## Remaining External Items

| Item | Owner | Status |
|---|---|---|
| CDO-hosted evidence bundle location | CDO | Pending |
| Final Slack channel/user group allowlist | CDO + AI | Pending |
| Final Jira project/component/accountId mapping | CDO + AI | Pending |
| Signed contract names and timestamps | AI + CDO + mentor | Pending onsite |
