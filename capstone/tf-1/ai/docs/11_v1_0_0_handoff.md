# TF1 AI Triage Engine v1.0.0 Handoff

Owner: AI team TF1  
Release: `v1.0.0`  
Status: Ready for CDO deployment/integration handoff  
Prepared: 2026-06-28

## Release Scope

`v1.0.0` is the AI-owned implementation release for the TF1 Triage Hub engine. It preserves the frozen `/v1/triage` request/response contract and adds internal production guards, observability, and retry safety without requiring CDO schema changes.

Implemented API surface:

- `GET /healthz`
- `GET /readyz`
- `GET /metrics`
- `POST /v1/triage`
- `GET /v1/audit/{audit_id}`
- `GET /v1/reports`
- `GET /v1/reports/{incident_id}`
- `GET /v1/reports/{incident_id}/raw`

## AI-Owned Capabilities

- Deterministic RCA and scenario classification for service down, latency degradation, noisy/ambiguous alert, and insufficient context.
- Statistical metric detectors: threshold, rolling z-score, EWMA drift, and Isolation Forest.
- Log anomaly detection, topology inference, causal hints, RCA candidate ranking, and catalog action selection.
- Bounded context enrichment from inline evidence, `evidence_uri`, or configured read-only context tools.
- Evidence compaction before RCA/AgentCore: caps metrics, metric points, logs, log message chars, traces, trace label bytes, and total evidence bytes.
- AgentCore/LLM optional modes with deterministic fallback.
- Read-only tool gateway with tenant/service/environment/window validation.
- Advisory-only recommendations; no Jira mutation, Slack posting, shell, rollback, restart, scale, or database action execution.
- Metadata-only audit records and tenant-scoped audit lookup.
- Idempotency records keyed by deterministic `audit_id`.
- App-level request guardrails:
  - `AIOPS_RATE_LIMIT_PER_MINUTE=60`
  - `AIOPS_MAX_REQUEST_BYTES=524288`
  - `AIOPS_MAX_CONCURRENT_TRIAGE_REQUESTS=0` by default.
- Per-triage cost visibility:
  - `llm_metadata.cost_estimate`
  - structured completed log field `estimated_cost_usd`
  - audit metadata `cost_estimate`
  - Prometheus `aiops_llm_estimated_cost_usd_total`.

## CDO-Owned Integration Inputs

CDO/platform should provide the following for deployed environments:

- Private endpoint or protected gateway for the AI engine.
- Auth boundary:
  - preferred: IAM SigV4 or service-to-service JWT;
  - capstone fallback: `SERVICE_AUTH_TOKEN`.
- Bounded evidence path, using one or more:
  - inline normalized evidence in `/v1/triage`;
  - `alert.labels.evidence_uri` and configured evidence bundle base/access;
  - read-only bounded evidence proxy or backend env vars.
- Durable audit/idempotency storage mount or production replacement store.
- Slack rendering and Jira issue creation from AI response fields.
- Platform metrics/logs/traces collection for `/metrics` and structured logs.
- Rollout/rollback/canary process for the AI image.

## Runtime Configuration Summary

Core:

```text
APP_ENV=sandbox|staging|prod
SERVICE_AUTH_TOKEN=<optional capstone fallback token>
AIOPS_INVESTIGATION_MODE=auto|deterministic_only|agent_assisted|agent_platform
AIOPS_TRIAGE_DEADLINE_SECONDS=30
AIOPS_RATE_LIMIT_PER_MINUTE=60
AIOPS_MAX_REQUEST_BYTES=524288
AIOPS_MAX_CONCURRENT_TRIAGE_REQUESTS=0
```

Evidence access:

```text
PROMETHEUS_URL=<bounded metrics endpoint>
LOKI_URL=<bounded logs endpoint>
JAEGER_URL=<bounded traces endpoint>
DEPLOY_METADATA_PATH=<deploy metadata json path>
OWNERSHIP_PATH=<ownership/runbook json path>
EVIDENCE_BUNDLE_BASE_PATH=<bundle base path>
JIRA_HISTORY_PATH=<read-only jira history/accountId map>
KNOWN_ERRORS_PATH=<known errors json path>
AIOPS_CONTEXT_TOOL_TIMEOUT_SECONDS=3
```

Evidence budget:

```text
AIOPS_MAX_EVIDENCE_BYTES=262144
AIOPS_MAX_METRIC_SERIES=20
AIOPS_MAX_METRIC_POINTS_PER_SERIES=120
AIOPS_MAX_LOG_RECORDS=50
AIOPS_MAX_TRACE_RECORDS=20
AIOPS_MAX_LOG_MESSAGE_CHARS=500
AIOPS_MAX_TRACE_LABEL_BYTES=2048
```

AgentCore/cost:

```text
AGENTCORE_RUNTIME_ARN=<optional>
ENABLE_AGENTCORE_LLM=true|false
ENABLE_AGENTCORE_LLM_TOOLS=true|false
BEDROCK_MODEL_ID=<optional>
BEDROCK_MODEL_IDS=<optional csv>
AIOPS_LLM_MAX_TOKENS_PER_INCIDENT=0
AIOPS_LLM_INPUT_COST_PER_1K=0
AIOPS_LLM_OUTPUT_COST_PER_1K=0
```

Audit/idempotency:

```text
AIOPS_AUDIT_LOG_PATH=audit/audit-log.jsonl
AIOPS_AUDIT_RETENTION_DAYS=90
AIOPS_IDEMPOTENCY_DIR=<optional durable directory>
AIOPS_IDEMPOTENCY_STALE_SECONDS=120
```

## Verification

Validated from `capstone/tf-1/ai/engine-skeleton`:

```text
python -m compileall app scripts
python -m pytest tests -q
python scripts/validate_datapack.py
docker compose -f docker-compose.observability.yml config --quiet
```

Validated from `capstone/tf-1/ai/engine-skeleton/report-ui`:

```text
npm run build
```

Latest result:

```text
compileall -> passed
pytest -> 64 passed
datapack validation -> passed
docker compose config -> passed
report UI build -> passed
```

## Smoke Test

```powershell
$body = Get-Content -Raw .\samples\latency-degradation.request.json
Invoke-RestMethod -Uri "$env:TF1_AI_ENDPOINT/healthz" -Method Get
Invoke-RestMethod -Uri "$env:TF1_AI_ENDPOINT/v1/triage" -Method Post `
  -Headers @{
    "X-Tenant-Id" = "tenant-a"
    "X-Correlation-Id" = "corr-payment-latency-001"
    "Authorization" = "Bearer $env:SERVICE_AUTH_TOKEN"
  } `
  -ContentType "application/json" `
  -Body $body
```

Expected high-level result:

```text
status=DIAGNOSED
classification=latency_degradation
ticket_payload present
audit_id present
llm_metadata.cost_estimate present
slack_payload absent
```

## Handoff Boundary

AI complete:

- Engine code, Dockerfile/artifact source, contracts, runtime docs, tests, validation, and smoke-test support.

CDO remaining:

- Deploy the image/artifact on the CDO platform.
- Configure auth, secrets, network, ingress, scaling, and rollback.
- Configure bounded evidence access or host evidence bundles.
- Configure durable audit/idempotency storage.
- Render Slack and create Jira from AI response fields.
- Capture final deployed endpoint evidence, E2E screenshots/video, and platform observability evidence.

## Known Production Notes

- File-based idempotency is suitable for demo/local durable mounts. Production should replace it with a conditional-write durable store such as DynamoDB, Postgres, or an equivalent CDO-managed service.
- The app returns `429` for local per-tenant rate limiting, but CDO should still enforce fleet-level rate limits at ingress/API Gateway.
- The app returns `413` for request payloads over `AIOPS_MAX_REQUEST_BYTES`; CDO ingress should enforce the same or stricter cap.
- Audit/log storage is metadata-only, but public triage response evidence can include curated snippets. If production evidence can contain secrets or PII, CDO/AI should add an approved redaction policy before external ticket rendering.
