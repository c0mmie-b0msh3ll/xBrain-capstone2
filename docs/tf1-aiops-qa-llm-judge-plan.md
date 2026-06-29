# TF1 AIOps QA LLM Judge Plan

Implementation status: implemented in `app/qa_judge.py` and wired into `/v1/triage`. The EKS demo keeps `ENABLE_QA_LLM=false` by default, while Terraform grants the engine role direct Bedrock invoke permission for the QA model path.

## Goal

Add a small, low-cost QA judge after the main triage/agent result. The judge reviews whether the final diagnosis is grounded in the supplied evidence before the response is returned and audited.

This does not require a new Bedrock AgentCore runtime. The recommended implementation is a direct Bedrock model call from the triage engine using a smaller model than the main reasoning path.

## Recommended Architecture

```text
telemetry/evidence
  -> deterministic RCA and classification
  -> optional main LLM/AgentCore investigation
  -> response assembly draft
  -> deterministic QA checks
  -> small Bedrock QA judge
  -> confidence adjustment + llm_metadata.qa
  -> DynamoDB audit/idempotency
```

The QA judge should be read-only. It must not call tools, create tickets, post Slack messages, run commands, or recommend direct remediation. It only returns a verdict and evidence-grounding issues.

## Why Bedrock Direct Instead Of AgentCore

Use Bedrock direct for this phase because the QA task is a single bounded model call:

- Lower implementation complexity.
- No additional AgentCore runtime, image, endpoint, or lifecycle management.
- Easier IAM boundary: `bedrock:InvokeModel` for the engine role.
- Lower cost: one small prompt per incident with a strict token budget.
- Easier fallback: if QA model fails, keep deterministic QA and mark degraded metadata.

Create a separate AgentCore QA runtime only if CDO later needs the QA judge to have its own runtime lifecycle, memory, tool orchestration, versioned agent policy, or independent ownership boundary.

## Proposed Config

Environment variables:

```text
ENABLE_QA_LLM=false
AIOPS_QA_PROVIDER=bedrock
AIOPS_QA_MODEL_ID=us.amazon.nova-micro-v1:0
AIOPS_QA_MAX_TOKENS_PER_INCIDENT=1200
AIOPS_QA_CONFIDENCE_PENALTY=-0.1
AIOPS_QA_FAIL_CLOSED=false
```

Recommended defaults:

- Keep `ENABLE_QA_LLM=false` until CDO explicitly enables the feature.
- Use a small/cheap model for QA.
- Keep `AIOPS_QA_FAIL_CLOSED=false` for demo and early production, so model failure degrades metadata instead of blocking triage.
- Keep deterministic QA enabled regardless of LLM judge status.

## IAM

The engine IAM role needs direct Bedrock invocation permission for the selected QA model or inference profile:

```json
{
  "Effect": "Allow",
  "Action": [
    "bedrock:InvokeModel",
    "bedrock:InvokeModelWithResponseStream"
  ],
  "Resource": [
    "arn:aws:bedrock:us-east-1::foundation-model/*",
    "arn:aws:bedrock:us-east-1:589077667575:inference-profile/*"
  ]
}
```

For CDO production, scope `Resource` down to the exact approved model or inference profile where possible.

## QA Input

Send only bounded evidence and the draft result:

```json
{
  "task": "qa_judge",
  "incident": {
    "tenant_id": "tenant-a",
    "incident_id": "inc-123",
    "service": "checkout-api",
    "severity": "high",
    "title": "checkout-api p95 latency high"
  },
  "draft_result": {
    "classification": "latency_degradation",
    "status": "DIAGNOSED",
    "confidence": 0.82,
    "summary": "...",
    "evidence": ["..."],
    "recommended_action_ids": ["dependency_timeout_triage"]
  },
  "bounded_evidence": {
    "metrics": ["names and compacted anomaly summaries"],
    "logs": ["short log samples"],
    "deploys": ["recent deploy metadata"],
    "runbooks": ["titles/excerpts only"],
    "rca_candidates": ["top candidates only"]
  }
}
```

Do not include secrets, raw long logs, full trace payloads, credentials, or customer PII.

## QA Output Schema

The model must return strict JSON:

```json
{
  "verdict": "pass",
  "issues": [],
  "confidence_delta": 0,
  "rationale": "The diagnosis is supported by latency metric and timeout log evidence.",
  "required_human_review": false
}
```

Allowed values:

- `verdict`: `pass`, `fail`, or `uncertain`.
- `issues`: list of short machine-readable strings.
- `confidence_delta`: number from `-0.2` to `0`.
- `rationale`: short explanation grounded in supplied evidence.
- `required_human_review`: boolean.

Suggested issue codes:

```text
missing_evidence
unsupported_classification
unsupported_root_cause
unsafe_action
invented_service_or_signal
insufficient_context
schema_mismatch
```

## Engine Behavior

1. Run the existing deterministic QA checks first.
2. If `ENABLE_QA_LLM=true`, call Bedrock direct with the compact QA payload.
3. Parse and validate strict JSON.
4. Clamp `confidence_delta` to the configured range.
5. Apply negative confidence delta to the final response.
6. If verdict is `fail` or `uncertain`, set `required_human_review=true` in QA metadata and avoid increasing automation confidence.
7. If the QA call fails, keep deterministic QA result and record:

```json
{
  "enabled": true,
  "provider": "bedrock",
  "result": "degraded",
  "error": "..."
}
```

## Response Metadata

Extend `llm_metadata.qa`:

```json
{
  "enabled": true,
  "provider": "bedrock",
  "deterministic_result": "passed",
  "llm_result": "passed",
  "verdict": "pass",
  "issues": [],
  "confidence_delta": 0,
  "required_human_review": false,
  "model": "us.amazon.nova-micro-v1:0",
  "prompt_tokens": 420,
  "completion_tokens": 80,
  "estimated_cost_usd": 0.0001
}
```

## Implementation Steps

1. Done: added `app/qa_judge.py`.
2. Done: moved/wrapped deterministic QA helpers without changing disabled behavior.
3. Done: added Bedrock direct call using `boto3.client("bedrock-runtime").converse(...)`.
4. Done: added strict output parser and schema validation.
5. Done: wired `run_qa()` to call deterministic QA first, then optional LLM judge.
6. Done: added env vars to EKS config and README.
7. Done: added IAM permission to Terraform for direct Bedrock invocation.
8. Done: added tests for:
   - LLM QA disabled keeps current behavior.
   - pass verdict does not reduce confidence.
   - fail verdict reduces confidence and records issues.
   - malformed model output degrades safely.
   - token budget exceeded skips LLM QA and records budget metadata.

## E2E Test Plan

Run these cases:

1. Clean latency scenario:
   - Expected: QA `pass`, no confidence penalty.
2. Unsupported classification fixture:
   - Expected: QA `fail`, issue `unsupported_classification`, confidence penalty.
3. Missing evidence fixture:
   - Expected: QA `fail` or `uncertain`, issue `missing_evidence`.
4. Model failure simulation:
   - Expected: response still returns, QA metadata `degraded`.
5. Budget cap simulation:
   - Expected: LLM QA skipped, deterministic QA still runs.

Required verification:

```text
llm_metadata.qa.provider=bedrock
llm_metadata.qa.verdict is present when ENABLE_QA_LLM=true
llm_metadata.qa.required_human_review=true on fail/uncertain
DynamoDB audit stores QA metadata
Idempotency replay returns the same QA metadata for the same request
```

## Cost Guardrails

- One QA call per incident.
- Use a small model.
- Compact evidence before QA.
- Enforce `AIOPS_QA_MAX_TOKENS_PER_INCIDENT`.
- Track prompt/completion tokens in `llm_metadata.qa`.
- Include QA calls in the existing `llm_metadata.cost_estimate` or a QA-specific cost field.
- Keep QA disabled by default in shared demo unless the test specifically validates it.

## CDO Handoff Statement

```text
The QA judge does not require a new AgentCore runtime. It is implemented as a direct Bedrock call from the triage engine using a small model. The main agent/triage result is reviewed against bounded evidence, and the verdict is stored in llm_metadata.qa. CDO only needs to approve the QA model and grant the engine role bedrock:InvokeModel for that model or inference profile.
```
