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
- AgentCore/LLM integration with deterministic fallback for failure handling.
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
- Durable audit/idempotency storage mount or production replacement store. This is required for production/EKS because retry safety depends on persisted state.
- Slack rendering and Jira issue creation from AI response fields.
- Platform metrics/logs/traces collection for `/metrics` and structured logs.
- Rollout/rollback/canary process for the AI image.

## Deployable Artifacts

Production deploy has two AI-owned runtime artifacts:

1. AI triage engine API
   - Source: `capstone/tf-1/ai/engine-skeleton`
   - Dockerfile: `capstone/tf-1/ai/engine-skeleton/Dockerfile`
   - Runtime port: `8080`
   - Main endpoints: `/healthz`, `/readyz`, `/metrics`, `/v1/triage`, `/v1/audit/{audit_id}`

2. AgentCore investigator runtime
   - Source: `capstone/tf-1/ai/engine-skeleton/agentcore_investigator`
   - Dockerfile: `capstone/tf-1/ai/engine-skeleton/agentcore_investigator/Dockerfile`
   - Entrypoint: `agentcore_investigator/main.py`
   - Purpose: Bedrock AgentCore runtime invoked by the triage engine through `AGENTCORE_RUNTIME_ARN`
   - Model env: `BEDROCK_MODEL_ID`, default `us.amazon.nova-micro-v1:0`

CDO CI/CD should build, scan, sign, and push both artifacts if CDO owns production image promotion. If AIO supplies images directly, AIO must provide both image references and the AgentCore runtime ARN.

Build commands from repo root:

```bash
docker build -t tf1-ai-triage-engine:v1.0.0 capstone/tf-1/ai/engine-skeleton
docker build -t tf1-agentcore-investigator:v1.0.0 capstone/tf-1/ai/engine-skeleton/agentcore_investigator
```

## Production Deploy Order

1. Build/push the AgentCore investigator artifact.
2. Create or update the Bedrock AgentCore runtime from `agentcore_investigator`.
3. Grant the AgentCore runtime execution role permission to call the configured Bedrock model.
4. Capture the created `AGENTCORE_RUNTIME_ARN`.
5. Build/push the AI triage engine artifact.
6. Create durable audit/idempotency storage and mount it into the AI engine pod.
7. Deploy the AI triage engine with `AGENTCORE_RUNTIME_ARN`, storage env vars, evidence access env vars, and auth secrets.
8. Smoke test `/healthz`, `/readyz`, `/metrics`, and `POST /v1/triage`.

## AgentCore Runtime Requirement

Production/EKS handoff should deploy the full AI app path, including AgentCore runtime access and durable audit/idempotency storage. The deterministic path remains in the code as a fail-closed fallback when AgentCore/tool calls fail, but it is not the intended production deployment mode.

### Required AgentCore Configuration

CDO can wire AgentCore in one of two production-supported ways:

### Path A - CDO Calls AIO-Owned AgentCore Runtime

This is possible only if AIO and CDO configure cross-account AWS permissions.

Required from AIO:

- provide `AGENTCORE_RUNTIME_ARN`;
- allow the CDO EKS workload role/account to invoke that runtime;
- confirm region and model/runtime availability.
- provide source/image for `capstone/tf-1/ai/engine-skeleton/agentcore_investigator` if CDO builds the AgentCore runtime in its own pipeline.

Required from CDO:

- configure the EKS pod IAM role/IRSA with permission to invoke the runtime;
- set env vars:

```text
AGENTCORE_RUNTIME_ARN=<AIO provided runtime arn>
ENABLE_AGENTCORE_LLM=true
ENABLE_AGENTCORE_LLM_TOOLS=true   # only if assisted tool proposals are needed
AIOPS_INVESTIGATION_MODE=auto     # or agent_assisted / agent_platform for explicit tests
AWS_REGION=us-east-1
```

If these permissions are not configured, the agentic runtime path is not fully deployed. The engine may still fail closed through deterministic fallback, but CDO should treat that as degraded mode, not the target production setup.

### Path B - CDO-Owned AgentCore Runtime

CDO can deploy/configure its own AgentCore runtime and pass its ARN through `AGENTCORE_RUNTIME_ARN`. The same safety rules still apply: AgentCore never receives raw backend credentials and can only request allowlisted read-only tools through the AI engine gateway.

### Degraded/Local Mode

For local smoke tests only, the app can run without `AGENTCORE_RUNTIME_ARN`:

```text
AIOPS_INVESTIGATION_MODE=auto
```

In that mode, `/v1/triage` still works using deterministic RCA, bounded context enrichment, action catalog, audit, idempotency, and metrics. This is useful for development and emergency fallback, but it is not the full production deployment target.

### Not Implemented In v1.0.0

There is no separate AIO-hosted HTTP proxy endpoint for AgentCore calls in this release. CDO EKS pods call AWS AgentCore Runtime directly with IAM permissions.

## Required IAM

AI engine EKS workload role needs permission to invoke the AgentCore runtime:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": "bedrock-agentcore:InvokeAgentRuntime",
    "Resource": "<AGENTCORE_RUNTIME_ARN>"
  }]
}
```

AgentCore investigator runtime execution role needs permission to call the selected Bedrock model:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": [
      "bedrock:Converse",
      "bedrock:InvokeModel"
    ],
    "Resource": "*"
  }]
}
```

Scope the model resource tighter if the target AWS account uses model-specific ARNs/policies.

## Required Persistent Storage

Current `v1.0.0` stores audit, reports, and idempotency as local files. In EKS, this must be backed by durable storage.

Recommended for current file-based implementation:

- Use EFS when running more than one replica, because all pods must see the same idempotency records.
- Use EBS only for a single-replica deployment, because normal EBS PVCs are ReadWriteOnce and do not give shared idempotency across replicas.
- For production hardening, CDO can replace the file store with DynamoDB/Postgres later, but the current image expects filesystem paths.

Mount one durable volume at:

```text
/var/lib/tf1-ai
```

Set:

```text
AIOPS_AUDIT_LOG_PATH=/var/lib/tf1-ai/audit/audit-log.jsonl
AIOPS_IDEMPOTENCY_DIR=/var/lib/tf1-ai/audit/idempotency
REPORTS_DIR=/var/lib/tf1-ai/reports
```

Minimum directories created by the app:

```text
/var/lib/tf1-ai/audit
/var/lib/tf1-ai/audit/idempotency
/var/lib/tf1-ai/reports
```

Example EFS-backed PVC reference:

```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: tf1-ai-engine-data
spec:
  accessModes:
    - ReadWriteMany
  storageClassName: efs-sc
  resources:
    requests:
      storage: 5Gi
```

Example deployment volume/env wiring:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: tf1-ai-triage-engine
spec:
  replicas: 2
  selector:
    matchLabels:
      app: tf1-ai-triage-engine
  template:
    metadata:
      labels:
        app: tf1-ai-triage-engine
    spec:
      serviceAccountName: tf1-ai-engine
      containers:
        - name: engine
          image: <cdo-ecr>/tf1-ai-triage-engine:v1.0.0
          ports:
            - containerPort: 8080
          env:
            - name: APP_ENV
              value: prod
            - name: AWS_REGION
              value: us-east-1
            - name: AIOPS_INVESTIGATION_MODE
              value: auto
            - name: AGENTCORE_RUNTIME_ARN
              valueFrom:
                secretKeyRef:
                  name: tf1-ai-engine-secrets
                  key: agentcore-runtime-arn
            - name: ENABLE_AGENTCORE_LLM
              value: "true"
            - name: ENABLE_AGENTCORE_LLM_TOOLS
              value: "true"
            - name: AIOPS_AUDIT_LOG_PATH
              value: /var/lib/tf1-ai/audit/audit-log.jsonl
            - name: AIOPS_IDEMPOTENCY_DIR
              value: /var/lib/tf1-ai/audit/idempotency
            - name: REPORTS_DIR
              value: /var/lib/tf1-ai/reports
          volumeMounts:
            - name: ai-data
              mountPath: /var/lib/tf1-ai
          livenessProbe:
            httpGet:
              path: /healthz
              port: 8080
          readinessProbe:
            httpGet:
              path: /readyz
              port: 8080
      volumes:
        - name: ai-data
          persistentVolumeClaim:
            claimName: tf1-ai-engine-data
```

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
AGENTCORE_RUNTIME_ARN=<required for production AgentCore path>
ENABLE_AGENTCORE_LLM=true
ENABLE_AGENTCORE_LLM_TOOLS=true
BEDROCK_MODEL_ID=<optional>
BEDROCK_MODEL_IDS=<optional csv>
AIOPS_LLM_MAX_TOKENS_PER_INCIDENT=0
AIOPS_LLM_INPUT_COST_PER_1K=0
AIOPS_LLM_OUTPUT_COST_PER_1K=0
```

Audit/idempotency:

```text
AIOPS_AUDIT_LOG_PATH=<durable mounted path>/audit-log.jsonl
AIOPS_AUDIT_RETENTION_DAYS=90
AIOPS_IDEMPOTENCY_DIR=<durable mounted path>/idempotency
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

## Production Acceptance Checks

CDO should capture evidence for these checks after EKS deployment:

- `GET /healthz` returns `status=ok` and `version=v1.0.0`.
- `GET /readyz` returns successful readiness from the deployed pod.
- `POST /v1/triage` returns a valid triage response with `audit_id`, `ticket_payload`, `llm_metadata`, and `llm_metadata.cost_estimate`.
- `llm_metadata.mode_selection.agentcore_enabled=true` for the production AgentCore path.
- AgentCore metadata does not show `runtime_arn_configured=false`.
- `GET /metrics` exposes triage, LLM token/cost, idempotency, and evidence truncation metrics.
- Restart one AI engine pod and retry the same request; the same `audit_id` should replay from persisted idempotency state instead of calling AgentCore again.
- `GET /v1/audit/{audit_id}` still works after pod restart, proving audit storage is durable.
- The mounted storage contains metadata-only audit/idempotency/report artifacts under `/var/lib/tf1-ai`; raw logs/metrics/traces are not persisted there.

## Handoff Boundary

AI complete:

- Engine code, Dockerfile/artifact source, contracts, runtime docs, tests, validation, and smoke-test support.

CDO remaining:

- Deploy the image/artifact on the CDO platform.
- Configure auth, secrets, network, ingress, scaling, and rollback.
- Configure bounded evidence access or host evidence bundles.
- Configure durable audit/idempotency storage, for example EBS/EFS or a production replacement such as DynamoDB/Postgres.
- Configure AgentCore runtime access for the production agentic path.
- Render Slack and create Jira from AI response fields.
- Capture final deployed endpoint evidence, E2E screenshots/video, and platform observability evidence.

## Known Production Notes

- File-based idempotency requires a durable mount. Production can either mount durable storage for the file store or replace it with a conditional-write durable store such as DynamoDB, Postgres, or an equivalent CDO-managed service.
- The app returns `429` for local per-tenant rate limiting, but CDO should still enforce fleet-level rate limits at ingress/API Gateway.
- The app returns `413` for request payloads over `AIOPS_MAX_REQUEST_BYTES`; CDO ingress should enforce the same or stricter cap.
- Audit/log storage is metadata-only, but public triage response evidence can include curated snippets. If production evidence can contain secrets or PII, CDO/AI should add an approved redaction policy before external ticket rendering.
