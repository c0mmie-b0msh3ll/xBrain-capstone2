# MTTA / MTTR Before-After Evidence - 2026-07-01

Scope: TF1 AIOps demo evidence for mentor rubric. These numbers use measured engine latency plus a conservative operational proxy for manual triage time. Full production MTTR needs real incident timestamps after deployment.

## Before / After

| Metric | Before: manual on-call triage | After: deterministic AI engine | After: AgentCore-assisted path | Evidence / rationale |
| --- | ---: | ---: | ---: | --- |
| MTTA proxy: time to actionable diagnosis/ticket payload | 15-30 minutes | ~0.45-0.47 ms engine classification on 735 RCAEval cases | ~2.1 seconds average on 15 AgentCore sample cases | Manual baseline from TF1 discovery docs: engineers dig across logs, metrics, deploy history, Jira, and Slack. Engine latency from benchmark matrix. |
| MTTR proxy: time to routed owner action | 60-120 minutes | Reduced by immediate Jira payload + Slack/SQS handoff payload | Reduced by same handoff plus bounded reasoning/audit trail | Production MTTR is not directly measurable in capstone; use time-to-actionable-ticket and owner routing as proxy. |
| Handoff consistency | Manual, engineer-dependent | Structured `ticket_payload`, `audit_id`, RCA candidates, recommended actions | Same, with AgentCore metadata/fallback audit | `/v1/triage` response contract and E2E worker flow. |
| Fallback behavior | Human retry/escalation | Deterministic fallback for missing context or LLM disabled | AgentCore bounded by max iterations/tool calls with deterministic fallback | Guardrails in `investigation_router.py`, `agent_runtime.py`, and benchmark configs. |

## Demo-Safe Claim

Use this wording in slides/Q&A:

> We do not claim measured production MTTR yet. For the capstone demo, we measure a proxy: time from alert/evidence bundle to actionable diagnosis, Jira-ready payload, and Slack/SQS handoff. Deterministic triage is sub-millisecond in the benchmark; AgentCore adds about 2.1 seconds in the sampled run but provides bounded reasoning and auditability. This directly reduces manual investigation start time and handoff delay, which are the largest contributors to current MTTA.

## Benchmark References

- `reports-e2e/rcaeval-benchmark-matrix-2026-07-01.md`
- `reports-e2e/rcaeval-hidden-metadata-deterministic-full.json`
- `reports-e2e/rcaeval-mapped-metadata-deterministic-full.json`
- `reports-e2e/rcaeval-hidden-metadata-agentcore-sample15.json`
- `reports-e2e/rcaeval-mapped-metadata-agentcore-sample15.json`

## Required Follow-Up For Production

- Capture real incident timestamps: alert fired, AI response created, Jira ticket created, Slack message posted, first human acknowledgement, resolution.
- Compute production MTTA as alert fired -> first human acknowledgement with AI context.
- Compute production MTTR as alert fired -> incident resolved/mitigated.
- Keep deterministic and AgentCore paths separated in dashboards because their latency/cost profiles differ.
