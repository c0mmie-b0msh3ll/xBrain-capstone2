# TF1 Individual Pitches

Owner: TF1 members
Status: Draft template for W12 panel prep

Each member should fill one section before W12 dry run. Keep each pitch short enough for individual defense.

## Template

```text
Name:
Role:
Jira tasks owned:
Key commits/PRs:
Decision I made:
Trade-off:
Verification evidence:
What I would improve next:
```

## AI Ops Pitch Points

- Event-driven triage instead of continuous full AI triage.
- Compute-first RCA before optional AgentCore/LLM synthesis.
- RCAEval subset as primary scenario data.
- CDO-hosted bounded evidence bundles for extra data.
- No direct customer app access from AI.
- No auto-remediation.
- Jira payload and Slack payload generated with audit metadata.
- Tenant/correlation validation enforced in `/v1/triage`.

## CDO Pitch Points To Coordinate

- How evidence bundles are hosted and exposed.
- How AI endpoint is deployed and secured.
- How observability data remains tenant/service/time-window scoped.
- How failure modes are handled for AI unavailable, Slack unavailable, and Jira unavailable.
