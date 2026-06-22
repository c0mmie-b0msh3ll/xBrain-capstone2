# Requirements - TF1 Triage Hub

## 1. Client Context

Client is the CTO of a B2B SaaS startup with about 20,000 active users and about 50 production microservices. The on-call team has 8 engineers and is experiencing burnout because every alert currently requires manual digging across logs, metrics, recent deploys, Jira, and Slack ownership channels.

The product goal is Triage Hub: when an alert fires, the system gathers context, asks the AI engine to diagnose likely root cause and suggest next steps, creates a structured Jira ticket, and notifies the responsible team owner in Slack. The engineer confirms and acts. The system must not perform auto-remediation.

## 2. Outcomes

- Reduce time spent starting incident investigation by giving engineers a ready context bundle instead of making them search from zero.
- Produce actionable AI suggestions with evidence, confidence, and concrete remediation steps or runbook references.
- Create consistent Jira tickets and Slack summaries so incident handling is traceable.
- Preserve tenant isolation and audit every AI decision.

## 3. Debrief Confirmations

- Mentor/client will provide a data pack for triage input.
- AI suggestions can rely on runbooks or operational documents; having runbook-backed suggestions is preferred.
- The main artifact is not only model output. The docs/contracts must clearly explain assumptions, data schema, safety boundaries, and evaluation.

## 4. Success Criteria

| Metric | Target | How to measure |
|---|---:|---|
| MTTA reduction | >= 50% | Compare manual baseline steps vs Triage Hub workflow on the same scenarios. |
| MTTR reduction | >= 30% | Simulated before/after on incident scenarios, using time-to-actionable-ticket as proxy if full fix time is not measurable. |
| Scenario coverage | 3 E2E scenarios | Critical service down, latency degradation, ambiguous/noisy alert. |
| Suggestion actionability | Pass for each scenario | Suggestion includes suspected cause, evidence, confidence, owner, and concrete next steps or runbook reference. |
| Confidence behavior | Low confidence does not guess | Low-confidence cases return `INVESTIGATE` or `INSUFFICIENT_CONTEXT`. |
| Auditability | 100% AI decisions logged | Every AI response has an `audit_id` linked to ticket/notification. |

## 5. In Scope

- AI engine endpoint for triage diagnosis.
- Input schema for alert, logs, metrics, recent deploys, service ownership, and runbook/docs snippets.
- Output schema for diagnosis, severity, confidence, recommendation, ticket payload, Slack summary, and audit reference.
- Runbook/doc-aware suggestion logic.
- Evaluation set using the mentor-provided data pack plus derived test cases.
- Engine skeleton endpoint with dummy response before full AI logic.

## 6. Out of Scope

- Auto-remediation.
- Custom dashboard.
- PagerDuty integration.
- ServiceNow implementation.
- Historical incident migration or backfill.
- Real production data ingestion unless explicitly provided in the data pack.
- Auto-retrain pipeline.
- GDPR erasure API implementation.

## 7. Non-Functional Requirements

- API availability target for demo: >= 99.5%.
- P99 AI API latency target: < 2 seconds for capstone demo, unless model/tooling requires a documented exception.
- Tenant isolation: every request must include `tenant_id`; AI must reject missing tenant context.
- Security: no PII in prompts or telemetry unless explicitly anonymized.
- Audit retention target: >= 90 days in design.
- Failure fallback: if AI cannot diagnose safely, return `INSUFFICIENT_CONTEXT` or `INVESTIGATE`, not a confident guess.

## 8. Open Questions

- [ ] What exact format will the mentor-provided data pack use: JSON events, CSV, log files, traces, or mixed files?
- [ ] Does the data pack include runbooks/docs, or should the AI team author minimal runbook snippets for the 3 scenarios?
- [ ] Which AIOps component owns each context source: metrics, logs, deploys, ownership, and runbooks?
- [ ] What Jira and Slack integrations are expected to be live vs mocked?
- [ ] What manual baseline should be used for MTTA/MTTR comparison?
