# TF1 Synthetic Demo Fixtures

Status: Synthetic v1, demo/smoke only  
Owner: TF1 AI team  
Last updated: 2026-06-23

This folder contains controlled synthetic fixtures for the TF1 Triage Hub. These files are useful for API smoke tests, deterministic demo flows, and Jira/Slack payload examples.

Synthetic fixtures are **not** the primary evidence dataset for RCA quality. The preferred external dataset direction is RCAEval, documented in `datapack/external/README.md` and `docs/public-dataset-review.md`.

## Flow

```text
synthetic telemetry fixture
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

The validator checks required fixture metadata, calls the local FastAPI app through `TestClient`, and verifies status/classification/confidence against expectations.
