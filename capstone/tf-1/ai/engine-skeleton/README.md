# TF1 AI Triage Engine Skeleton

Small HTTP service that implements the TF1 AI API contract before optional Bedrock synthesis is integrated.

The service is event-driven. The broader AIOps app continuously ingests telemetry and detects alert/anomaly candidates, then calls this service with a bounded context bundle. The skeleton performs compute-first validation, scenario classification, confidence gating, and payload generation.

The demo includes a local observability-first pipeline:

```text
sanitized scenario datapack
  -> telemetry simulator
  -> OpenTelemetry Collector
  -> Prometheus / Loki / Jaeger
  -> Grafana dashboard
  -> AIOps query worker with threshold + statistical anomaly detection
  -> POST /v1/triage
  -> JSON triage report
  -> Slack dry-run summary + React report UI
```

## Endpoints

- `GET /healthz`
- `POST /v1/triage`
- `GET /v1/reports`
- `GET /v1/reports/{incident_id}`
- `GET /v1/reports/{incident_id}/raw`

## Run Locally

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

If port `8080` is already used by another local service, run the API on `8081` and point the worker/UI to that port:

```bash
python -m uvicorn app.main:app --host 127.0.0.1 --port 8081
```

## Observability Demo Stack

Start the full local stack from this directory:

```bash
docker compose -f docker-compose.observability.yml up --build
```

Local URLs:

- Triage API: `http://localhost:8080/healthz`
- Prometheus: `http://localhost:9090`
- Loki: `http://localhost:3100`
- Jaeger: `http://localhost:16686`
- Grafana: `http://localhost:3000` with `admin` / `admin`
- Triage report UI: `http://localhost:5173`

The default simulator scenario is `latency-degradation`. Override it with:

```bash
SIM_SCENARIO=critical-service-down AIOPS_SERVICE=checkout-api docker compose -f docker-compose.observability.yml up --build
```

The worker queries Prometheus/Loki/Jaeger with tenant, service, environment, and bounded-window filters. It runs threshold/log detection plus 3-sigma, EWMA, and Isolation Forest evidence before building a normalized triage request. `/v1/triage` adds topology-aware RCA candidates, experimental causal hints when enough metric points exist, and a deterministic investigator summary.

Slack is the alert surface: the worker sends or prints a concise summary with top evidence, confidence, and the report URL. Grafana remains the raw observability dashboard. The React report UI is the full investigation and audit surface, backed by JSON reports written under `reports/{incident_id}.json`.

## Connect Slack

Slack is dry-run by default. To send real messages, create a Slack Incoming Webhook for the target channel, then set `SLACK_WEBHOOK_URL` before running the worker:

```bash
export SLACK_WEBHOOK_URL="https://hooks.slack.com/services/..."
python -m app.aiops_worker --offline-scenario --scenario latency-degradation --service payment-api --triage-url http://127.0.0.1:8081/v1/triage --report-dir reports
```

PowerShell:

```powershell
$env:SLACK_WEBHOOK_URL = "https://hooks.slack.com/services/..."
python -m app.aiops_worker --offline-scenario --scenario latency-degradation --service payment-api --triage-url http://127.0.0.1:8081/v1/triage --report-dir reports
```

If `SLACK_WEBHOOK_URL` is not set, the worker prints `slack_dry_run` and does not send anything.

## Investigation Modes

`/v1/triage` keeps the same request and response contract, but the engine now records the selected investigation path in `llm_metadata.investigation_mode` and `llm_metadata.mode_selection`.

- `deterministic_only`: context enrichment, deterministic RCA/classification, Jira history read-only lookup, QA, and catalog actions. No AgentCore summary or action wording calls are made.
- `agent_assisted`: deterministic RCA remains primary, with the existing AgentCore bounded tool proposal loop used to enrich missing evidence before deterministic reclassification.
- `agent_platform`: AgentCore Runtime is the investigator for platform demos. The TF1 engine still owns tool allowlisting, tenant/service/environment/window validation, read-only tool execution, final policy validation, action catalog filtering, and deterministic fallback.

Mode selection defaults to `auto`:

```bash
AIOPS_INVESTIGATION_MODE=auto|deterministic_only|agent_assisted|agent_platform
AIOPS_ASSISTED_COMPLEXITY_THRESHOLD=3
AIOPS_AGENT_COMPLEXITY_THRESHOLD=6
AIOPS_AGENT_MAX_ITERATIONS=2
AIOPS_AGENT_MAX_TOOL_CALLS=5
```

Auto mode scores missing context, low deterministic confidence, ambiguous RCA candidates, insufficient-context/investigate status, dependency or causal hints, high-severity sparse incidents, and missing/out-of-scope evidence bundles. If AgentCore is disabled, auto mode returns `deterministic_only` and records the mode it would have planned in `mode_selection.planned_mode`.

## Connect AgentCore LLM

The triage API can optionally call Amazon Bedrock AgentCore Runtime for assisted summaries/action wording and for the full `agent_platform` investigator loop. It still runs deterministic RCA first, sends only bounded evidence to the agent, executes only TF1 allowlisted read-only tools, and falls back to deterministic RCA if AgentCore is disabled, slow, malformed, or policy-invalid.

Required env:

```bash
export AWS_REGION="us-east-1"
export AGENTCORE_RUNTIME_ARN="arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/tf1-investigator"
export ENABLE_AGENTCORE_LLM="true"
export ENABLE_AGENTCORE_LLM_TOOLS="true"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8081
```

PowerShell:

```powershell
$env:AWS_REGION = "us-east-1"
$env:AGENTCORE_RUNTIME_ARN = "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/tf1-investigator"
$env:ENABLE_AGENTCORE_LLM = "true"
$env:ENABLE_AGENTCORE_LLM_TOOLS = "true"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8081
```

The runtime IAM principal needs `bedrock-agentcore:InvokeAgentRuntime` on the configured AgentCore runtime. The local machine has AWS CLI access to the project AWS account, so local non-Docker runs can use that profile directly. Docker runs need credentials provided separately through your normal AWS credential mechanism; the Compose file only passes region/env values.

AgentCore summary/action payload contract expected by TF1:

```json
{
  "task": "investigation_summary | action_wording",
  "system_instructions": "bounded operating instructions",
  "input": {}
}
```

For tool investigation, the AgentCore agent must return strict JSON:

```json
{"tool_calls": [{"name": "get_logs", "args": {"limit": 10}}]}
```

For `agent_platform`, the AgentCore agent must return either tool requests or a final diagnosis:

```json
{
  "type": "tool_requests",
  "thought_summary": "Need logs and deploy correlation.",
  "tool_calls": [
    {"name": "get_logs", "args": {"limit": 10}},
    {"name": "get_recent_deploys", "args": {}}
  ]
}
```

```json
{
  "type": "final_diagnosis",
  "classification": "latency_degradation",
  "status": "DIAGNOSED",
  "confidence": 0.78,
  "summary": "payment-api latency is likely tied to dependency timeout signals.",
  "evidence": ["Representative log: database timeout after 3000ms"],
  "recommended_action_ids": ["dependency_timeout_triage"],
  "qa": {"passed": true, "gaps": []}
}
```

TF1 validates every returned tool name and tenant/service/environment/window scope before executing the local read-only tool registry. Agent final diagnoses must use existing classifications/statuses, confidence from `0.0` to `1.0`, non-empty evidence for `DIAGNOSED`, and known action IDs. Unknown action IDs are ignored; unsafe operational commands, Jira/Slack mutation, shell, PromQL, and LogQL are rejected and the response falls back to deterministic RCA.

Action recommendations are catalog-gated and evidence-linked. The catalog covers missing context, noisy alerts, service down, latency/dependency timeout, recent deploy rollback consideration, resource saturation, disk pressure, queue/Kafka lag, auth failures, DNS/TLS/network errors, Kubernetes crash loops, rate-limit/throttling, and internal runbook review. AgentCore may search configured internal runbooks/known-error records through read-only tools, but runtime internet search is intentionally not allowed for incident recommendations.

Mode and agent metrics exposed at `/metrics`:

- `aiops_investigation_mode_selected_total{mode,source}`
- `aiops_agent_iterations_total{result}`
- `aiops_agent_tool_requests_total{tool,status}`
- `aiops_agent_fallback_total{reason}`

Useful model discovery commands:

```bash
aws bedrock list-foundation-models --region us-east-1 --query "modelSummaries[?contains(modelId, 'claude')].[modelId,modelName]" --output table
aws bedrock list-foundation-models --region us-east-1 --query "modelSummaries[?contains(modelId, 'nova') || contains(modelName, 'Nova')].[modelId,modelName]" --output table
aws bedrock list-inference-profiles --region us-east-1 --type-equals SYSTEM_DEFINED --query "inferenceProfileSummaries[?contains(inferenceProfileId, 'opus') || contains(inferenceProfileId, 'nova-2-lite')].[inferenceProfileId,inferenceProfileName,status]" --output table
```

## Trigger Reports

Reports are created by the AIOps worker after it detects an incident candidate and receives a `/v1/triage` response. The React UI only lists and renders existing report files through `GET /v1/reports`; opening the UI does not create reports.

Offline report trigger:

```bash
python -m app.aiops_worker --offline-scenario --scenario latency-degradation --service payment-api --triage-url http://127.0.0.1:8081/v1/triage --report-dir reports
```

Additional scenarios:

```bash
python -m app.aiops_worker --offline-scenario --scenario critical-service-down --service checkout-api --triage-url http://127.0.0.1:8081/v1/triage --report-dir reports
python -m app.aiops_worker --offline-scenario --scenario noisy-false-alert --service notification-worker --triage-url http://127.0.0.1:8081/v1/triage --report-dir reports
```

Each detected incident writes `reports/{incident_id}.json`. Refresh `http://localhost:5173` after running a trigger. The worker prints a Slack dry-run payload containing the report link unless `SLACK_WEBHOOK_URL` is configured.

No-Docker smoke path:

```bash
python -m uvicorn app.main:app --host 127.0.0.1 --port 8081
python -m app.aiops_worker --offline-scenario --scenario latency-degradation --service payment-api --triage-url http://127.0.0.1:8081/v1/triage --report-dir reports
cd report-ui && npm install && npm run dev
```

Simulator-only dry run:

```bash
python -m app.simulator --scenario latency-degradation --dry-run
```

## Observability Files

- `docker-compose.observability.yml`: triage API, simulator, AIOps worker, OTel Collector, Prometheus, Loki, Jaeger, and Grafana.
- `report-ui/`: Vite React triage report viewer.
- `observability/otel/config.yml`: scrapes simulator metrics and forwards traces to Jaeger.
- `observability/prometheus/prometheus.yml`: scrapes the Collector Prometheus exporter.
- `observability/grafana/`: provisions Prometheus/Loki/Jaeger data sources and a TF1 demo dashboard.
- `app/simulator.py`: replays sanitized scenario metrics/logs/traces with tenant/service/environment overrides.
- `app/aiops_worker.py`: queries observability backends, detects anomalies, normalizes context, calls triage, writes report JSON, and publishes Slack dry-run output.
- `app/rca.py`: statistical anomaly evidence, topology scoring, causal hints, and deterministic investigator summary helpers.

## Smoke Test

```bash
curl http://localhost:8080/healthz

curl -X POST http://localhost:8080/v1/triage \
  -H "Content-Type: application/json" \
  -H "X-Tenant-Id: tenant-a" \
  -H "X-Correlation-Id: corr-critical-001" \
  -d @samples/critical-service-down.request.json
```

If `SERVICE_AUTH_TOKEN` is set, callers must include `Authorization: Bearer <token>`.

## Tests

```bash
python -m compileall app scripts
python -m pytest tests
cd report-ui && npm install && npm run build
docker compose -f docker-compose.observability.yml config --quiet
```
