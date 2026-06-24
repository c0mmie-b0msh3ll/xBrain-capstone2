# xBrain Capstone 2

Capstone workspace for AI/CDO task-force deliverables. The current implemented focus is **Task Force 1 - Triage Hub**, an AI-assisted incident triage service that receives normalized incident context and returns diagnosis, confidence, evidence, Jira payload, Slack payload, and an audit ID.

## Architecture Summary

TF1 uses an **event-driven, compute-first triage architecture**:

```text
Continuous telemetry
  -> Platform observability stack
  -> bounded query/export by tenant/service/env/window
  -> AIOps normalize/window/baseline/detection
  -> Alert/anomaly/incident candidate
  -> AIOps context aggregation
  -> TF1 AI compute service
  -> topology-aware RCA + deterministic investigator summary
  -> report JSON artifact
  -> Slack summary + React report UI + Jira payload
```

This is not a direct Bedrock workflow and not an unbounded raw-data dump into AI. Platform/DevOps owns observability plumbing: metrics/logs/traces collection, retention, secure access, and bounded query/export. The AIOps app owns data interpretation: normalize, window, baseline, detect, context package, RCA, confidence, and optional Bedrock synthesis.

The local demo implements that boundary with a Docker Compose observability stack:

```text
sanitized RCAEval-style scenarios
  -> telemetry simulator
  -> OpenTelemetry Collector
  -> Prometheus / Loki / Jaeger
  -> Grafana
  -> AIOps query worker
  -> /v1/triage
  -> reports/{incident_id}.json
  -> Slack dry-run summary
  -> React report UI
```

## Project Layout

```text
.
|-- capstone/
|   `-- tf-1/
|       `-- ai/
|           |-- contracts/
|           |   |-- ai-api-contract.md
|           |   |-- deployment-contract.md
|           |   |-- observability-data-contract.md
|           |   `-- telemetry-contract.md
|           |-- docs/
|           |   |-- 01_requirements.md
|           |   |-- 02_solution_design.md
|           |   |-- 03_ai_engine_spec.md
|           |   |-- 04_eval_report.md
|           |   |-- 05_adrs.md
|           |   |-- 06_cdo_evidence_handoff.md
|           |   `-- 07_w11_readiness_checklist.md
|           `-- engine-skeleton/
|               |-- app/
|               |   |-- aiops_worker.py
|               |   |-- main.py
|               |   |-- rca.py
|               |   |-- report_store.py
|               |   `-- simulator.py
|               |-- observability/
|               |-- report-ui/
|               |-- samples/
|               |-- Dockerfile
|               |-- docker-compose.observability.yml
|               |-- README.md
|               |-- datapack-mapping.md
|               `-- requirements.txt
`-- docs/
    |-- reference/
    `-- templates/
```

`capstone/tf-1/ai/PLAN.md` is intentionally ignored by Git because it is a local working tracker.

## Ownership Boundary

TF1 AIOps app owns:

- telemetry interpretation after bounded platform access,
- normalization/windowing/baseline,
- lightweight alert/anomaly detection,
- context aggregation before calling `/v1/triage`,
- triage API contract,
- telemetry schema,
- incident-level diagnosis logic,
- confidence behavior,
- safety boundaries,
- optional LLM synthesis behavior,
- AI evaluation docs,
- Jira and Slack integration.

Platform/deployment owners provide observability plumbing and infrastructure concerns: Prometheus/Grafana/Loki/CloudWatch/OTel setup, bounded telemetry access, networking, secrets, monitoring, and deployment pipelines.

The AI service does **not** auto-remediate. It only produces human-reviewed recommendations.

## Contracts

The three W11 signed/frozen AI-CDO contracts are under `capstone/tf-1/ai/contracts/`:

- `telemetry-contract.md`: normalized incident context that the AIOps detector/context layer sends to triage after detection.
- `ai-api-contract.md`: HTTP API shape for `/healthz` and `/v1/triage`.
- `deployment-contract.md`: TF1-specific deployment handoff for platform owners.

There is one additional supporting data-availability contract/handoff in the same folder:

- `observability-data-contract.md`: CDO/platform handoff for bounded metrics/logs/traces/deploy/ownership evidence. It explains extra evidence hosting and access, but it is not counted as a fourth W11 signed/frozen contract.

- `docs/06_cdo_evidence_handoff.md`: CDO-facing guide for hosting/exposing extra evidence based on current app state.
- `docs/07_w11_readiness_checklist.md`: W11 onsite review checklist, verification commands, and AWS endpoint evidence placeholder.
- `docs/public-dataset-review.md`: why RCAEval is selected as the primary external RCA dataset.

RCAEval subset files under `capstone/tf-1/ai/engine-skeleton/datapack/external/` are treated as the primary scenario input. They are mapped into the telemetry contract through adapters and CDO-hostable evidence bundles.

## Engine Skeleton

The skeleton service is a small FastAPI app in `capstone/tf-1/ai/engine-skeleton/`.

Implemented endpoints:

- `GET /healthz`
- `POST /v1/triage`
- `GET /v1/reports`
- `GET /v1/reports/{incident_id}`
- `GET /v1/reports/{incident_id}/raw`

The current triage logic is deterministic and rule-based so the detector/context, report UI, and Jira/Slack integration layers can integrate before the final hybrid AI mode is ready. The RCA pipeline adds threshold/log detection, 3-sigma evidence, EWMA drift evidence, Isolation Forest evidence, topology-aware candidate ranking, bounded causal hints, and a deterministic investigator summary.

Response behavior:

| Input pattern | Status | Classification |
|---|---|---|
| Required alert exists but no useful context | `INSUFFICIENT_CONTEXT` | `insufficient_context` |
| Critical service-down signal | `DIAGNOSED` | `critical_service_down` |
| Latency, p95, timeout, or deploy-related degradation | `DIAGNOSED` | `latency_degradation` |
| Noisy, flapping, low-severity, or ambiguous alert | `INVESTIGATE` | `noisy_or_ambiguous_alert` |

## Run Locally

From `capstone/tf-1/ai/engine-skeleton`:

```powershell
pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8080
```

If port `8080` is busy, use another port such as `8081`.

Health check:

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8080/healthz" -Method Get
```

Sample triage call:

```powershell
$body = Get-Content -Raw samples\latency-degradation.request.json
Invoke-RestMethod `
  -Uri "http://127.0.0.1:8080/v1/triage" `
  -Method Post `
  -ContentType "application/json" `
  -Headers @{
    "X-Tenant-Id" = "tenant-a"
    "X-Correlation-Id" = "corr-latency-001"
  } `
  -Body $body
```

## Docker

Build and run from `capstone/tf-1/ai/engine-skeleton`:

```powershell
docker build -t tf1-ai-triage-engine .
docker run --rm -p 8080:8080 tf1-ai-triage-engine
```

Full local observability demo:

```powershell
docker compose -f docker-compose.observability.yml up --build
```

Open Grafana at `http://localhost:3000` with `admin` / `admin`, then view the `TF1 AIOps Observability Demo` dashboard. Open the report UI at `http://localhost:5173`.

Slack is the notification surface, Grafana is the raw telemetry surface, and the React report UI is the RCA explanation and audit surface.

The default scenario is `latency-degradation`; use `SIM_SCENARIO=critical-service-down` and `AIOPS_SERVICE=checkout-api` to switch scenarios.

Offline detector-to-triage smoke path without Docker:

```powershell
python -m uvicorn app.main:app --host 127.0.0.1 --port 8081
python -m app.aiops_worker --offline-scenario --scenario latency-degradation --service payment-api --triage-url http://127.0.0.1:8081/v1/triage --report-dir reports
cd report-ui
npm install
npm run dev
```

Reports are created by the AIOps worker after it detects an incident and receives a `/v1/triage` response. The UI only reads report artifacts from `GET /v1/reports`; opening the UI does not create new reports.

Additional offline report triggers:

```powershell
python -m app.aiops_worker --offline-scenario --scenario critical-service-down --service checkout-api --triage-url http://127.0.0.1:8081/v1/triage --report-dir reports
python -m app.aiops_worker --offline-scenario --scenario noisy-false-alert --service notification-worker --triage-url http://127.0.0.1:8081/v1/triage --report-dir reports
```

## Sample Fixtures

Request/response samples are under `capstone/tf-1/ai/engine-skeleton/samples/`.

- `critical-service-down.request.json`
- `critical-service-down.response.json`
- `latency-degradation.request.json`
- `latency-degradation.response.json`
- `noisy-alert.request.json`
- `noisy-alert.response.json`
- `insufficient-context.request.json`
- `insufficient-context.response.json`

These fixtures prove the API shape and provide integration-ready examples for Jira/Slack integration.

## Verification

From `capstone/tf-1/ai/engine-skeleton`, run:

```powershell
python -m compileall app
```

Contract smoke check:

```powershell
@'
import json
from pathlib import Path
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)
health = client.get('/healthz')
assert health.status_code == 200
assert health.json()['status'] == 'ok'

for request_path in sorted(Path('samples').glob('*.request.json')):
    body = json.loads(request_path.read_text())
    response = client.post('/v1/triage', json=body, headers={
        'X-Tenant-Id': body['tenant_id'],
        'X-Correlation-Id': body['correlation_id'],
    })
    assert response.status_code == 200
    expected_path = request_path.with_name(request_path.name.replace('.request.json', '.response.json'))
    assert response.json() == json.loads(expected_path.read_text())

body = json.loads(Path('samples/latency-degradation.request.json').read_text())
bad = client.post('/v1/triage', json=body, headers={
    'X-Tenant-Id': 'wrong-tenant',
    'X-Correlation-Id': body['correlation_id'],
})
assert bad.status_code == 400
print('contract checks passed')
'@ | python -
```

## Datapack Adaptation

`capstone/tf-1/ai/engine-skeleton/datapack-mapping.md` is the placeholder for mentor datapack mapping. Once the datapack arrives, map raw fields into the normalized telemetry contract using:

- `direct`
- `derived`
- `defaulted`
- `missing`

The adapter should emit at least three valid `/v1/triage` request fixtures: critical down, latency degradation, and noisy alert.

## Current Status

Completed:

- Deployment contract customized for TF1.
- Telemetry and API contracts refined.
- Event-driven compute-first architecture documented.
- FastAPI skeleton implemented.
- Sample request/response JSON added.
- Deterministic scenario behavior implemented.
- Eval report updated with skeleton verification results.
- Telemetry simulator added for sanitized RCAEval-style scenario replay.
- Local Prometheus/Loki/Jaeger/Grafana/OTel Compose stack added.
- AIOps query worker added for bounded observability queries, statistical detection, context building, triage invocation, report JSON persistence, and Slack dry-run output.
- Report APIs and React/Vite triage report UI added.
- Topology-aware RCA candidates, causal hints, and deterministic investigator summaries added.
- Optional Bedrock investigator synthesis added with fallback order: Claude Opus 4.8, Claude Opus 4.6, then Amazon Nova 2 Lite.
- CDO-facing evidence handoff added for bounded extra data hosting/querying.
- RCAEval-derived evidence bundles generated for 3 scenario categories, 9 bundles total.
- W11 readiness checklist and standup notes added.

Pending:

- Record real AWS endpoint evidence after deployment smoke tests pass.
- Have CDO choose the hosted evidence bundle location.
- Final eval report with precision, recall, F1, latency, and cost.
