# TF1 AI Triage Engine Skeleton

Small HTTP service that implements the TF1 AI API contract before full LLM integration.

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
