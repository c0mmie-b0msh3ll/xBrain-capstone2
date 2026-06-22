# TF1 AI Triage Engine Skeleton

Small HTTP service that implements the TF1 AI API contract before optional Bedrock synthesis is integrated.

The service is event-driven. The broader AIOps app continuously ingests telemetry and detects alert/anomaly candidates, then calls this service with a bounded context bundle. The skeleton performs compute-first validation, scenario classification, confidence gating, and payload generation.

## Endpoints

- `GET /healthz`
- `POST /v1/triage`

## Run Locally

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

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
