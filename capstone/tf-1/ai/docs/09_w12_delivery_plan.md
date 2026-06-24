# W12 Delivery Plan - TF1 AI Ops

Owner: AI team TF1
Status: Planning artifact for W12 build and final evidence

## W12 Required Outputs

| Requirement | Current status | W12 action |
|---|---|---|
| Final AI engine deployed | Demo endpoint deployed on App Runner | Keep endpoint stable or redeploy final image behind CDO-approved platform. |
| Eval report with precision/recall/F1/latency | Skeleton eval and RCAEval bundle readiness documented | Run final labeled eval against RCAEval-derived cases and record metrics. |
| Three E2E scenarios | Sample requests, synthetic scenarios, and RCAEval bundles exist | Record alert -> triage -> report -> Slack/Jira payload flow for each scenario. |
| Jira ticket output | `ticket_payload` generated | Add live Jira publisher only after config/mapping is approved; otherwise demo payload handoff. |
| Slack notify | Payload/dry-run supported | Use webhook if approved; two-way Slack remains read-only future MVP unless scheduled. |
| MTTA/MTTR before-after evidence | Baseline plan documented | Measure manual baseline versus Triage Hub flow on same scenarios. |
| Curveball responses | Template created | Append all three curveball responses as they occur. |
| Slides/demo video | Not created | Produce after final E2E flow stabilizes. |
| Individual pitches | Template created | Each member fills owned tasks, commits, decisions, and verification. |

## Final Eval Metrics To Collect

| Metric | Target |
|---|---:|
| Precision | >= 0.80 |
| Recall | >= 0.70 |
| F1 | >= 0.75 |
| API p99 latency | < 2 seconds for bounded payloads |
| MTTA reduction | >= 50% |
| MTTR reduction | >= 30% or time-to-actionable-ticket proxy |
| Tenant isolation failures | 0 |
| Unsafe auto-remediation actions | 0 |

## E2E Scenarios

| Scenario | Data source | Expected result |
|---|---|---|
| Critical service down | RCAEval-derived bundle + supplemental records | `DIAGNOSED / critical_service_down`, owner escalation, no auto-remediation. |
| Latency degradation | RCAEval-derived bundle + supplemental records | `DIAGNOSED / latency_degradation`, dependency/deploy investigation actions. |
| Noisy false alert | RCAEval-derived bundle + supplemental records | `INVESTIGATE / noisy_or_ambiguous_alert`, observe/human-review action only. |

## Evidence Pack Checklist

- Final deployed endpoint URL and image digest.
- Test command output or screenshots for `/healthz` and `/v1/triage`.
- RCAEval adapted case list and evidence bundle count.
- Eval metric output.
- Report JSON artifacts.
- Slack notification evidence or dry-run payload.
- Jira ticket evidence or `ticket_payload`.
- Curveball responses.
- Individual pitch notes with Jira task and commit/PR links.
