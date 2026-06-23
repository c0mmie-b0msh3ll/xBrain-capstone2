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
