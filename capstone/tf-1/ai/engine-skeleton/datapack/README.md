# TF1 Synthetic Datapack

Status: Synthetic v1  
Owner: TF1 AI team  
Last updated: 2026-06-23

This datapack simulates incident telemetry for the TF1 Triage Hub. It is intentionally synthetic because the mentor confirmed the team should generate data to simulate incidents.

## Flow

```text
raw-ish synthetic telemetry
  -> observability data contract
  -> AIOps normalize/window/baseline/detect
  -> triage context contract
  -> POST /v1/triage
```

## Scenarios

| Scenario | Service | Expected detector result | Expected triage |
|---|---|---|---|
| `critical-service-down` | `checkout-api` | incident candidate fires | `DIAGNOSED / critical_service_down` |
| `latency-degradation` | `payment-api` | incident candidate fires | `DIAGNOSED / latency_degradation` |
| `noisy-false-alert` | `search-api` | weak candidate only | `INVESTIGATE / noisy_or_ambiguous_alert` |

## File Contract

Each scenario includes:

- `raw-metrics.json`: metric points and/or window summaries.
- `raw-logs.json`: bounded log snippets.
- `deploy-events.json`: recent deploy records.
- `ownership.json`: owner, Slack, Jira, runbook metadata.
- `runbooks.json`: synthetic runbook snippets.
- `expected-detection.json`: detector expectation.
- `triage-request.json`: normalized request body for `/v1/triage`.
- `expected-triage-summary.json`: expected status, classification, and confidence band.

## Validation

From `capstone/tf-1/ai/engine-skeleton`:

```powershell
python scripts/validate_datapack.py
```

The validator checks required raw metadata, calls the local FastAPI app through `TestClient`, and verifies status/classification/confidence against expectations.
