# Eval Report - TF1 Triage Hub

Owner: AI team TF1
Status: Skeleton verified + RCAEval subset evidence bundles prepared
Last updated: 2026-06-24

## 1. Test Scenarios

The current eval set validates the deterministic skeleton contract. It is not the final model-quality eval.

| # | Fixture | Scenario | Expected status | Expected classification |
|---:|---|---|---|---|
| 1 | `critical-service-down.request.json` | High-severity critical service down | `DIAGNOSED` | `critical_service_down` |
| 2 | `latency-degradation.request.json` | High latency with timeout log and recent deploy | `DIAGNOSED` | `latency_degradation` |
| 3 | `noisy-alert.request.json` | Low-severity noisy/flapping alert | `INVESTIGATE` | `noisy_or_ambiguous_alert` |
| 4 | `insufficient-context.request.json` | Alert with no supporting context | `INSUFFICIENT_CONTEXT` | `insufficient_context` |

Fixtures live in `../engine-skeleton/samples/`.

RCAEval-derived evidence bundles live in `../engine-skeleton/datapack/external/evidence-bundles/` and are the primary W11 handoff datapacks.
Synthetic fixture scenarios live in `../engine-skeleton/datapack/scenarios/` and are retained for smoke tests, observability demos, and supplemental examples.
External dataset direction is documented in `public-dataset-review.md`.

## 2. Methodology

- Setup: local FastAPI test client.
- Test data: synthetic JSON fixtures aligned to `contracts/telemetry-contract.md`.
- Procedure:
  1. Call `GET /healthz`.
  2. Submit each `*.request.json` fixture to `POST /v1/triage`.
  3. Compare full response JSON with the matching `*.response.json` fixture.
  4. Submit a tenant mismatch request and confirm `400`.
- Metrics measured in this phase: contract conformance, status behavior, response completeness.

## 3. Results

| Check | Target | Actual | Pass/Fail |
|---|---|---|---|
| Health endpoint | `200` with `status=ok` | Passed | Pass |
| Critical down fixture | `DIAGNOSED` | `DIAGNOSED` | Pass |
| Latency degradation fixture | `DIAGNOSED` | `DIAGNOSED` | Pass |
| Noisy alert fixture | `INVESTIGATE` | `INVESTIGATE` | Pass |
| Insufficient context fixture | `INSUFFICIENT_CONTEXT` | `INSUFFICIENT_CONTEXT` | Pass |
| Tenant mismatch | `400` | `400` | Pass |
| Required output fields | diagnosis, confidence, evidence, recommendation, ticket payload, Slack-renderable raw fields, optional assignee suggestion, audit ID | Present | Pass |
| Auto-remediation boundary | No executable remediation actions | Advisory actions only | Pass |

### 3.1 Synthetic Fixture Results

| Scenario | Expected | Actual | Pass/Fail |
|---|---|---|---|
| `critical-service-down` | `DIAGNOSED / critical_service_down` | `DIAGNOSED / critical_service_down` | Pass |
| `latency-degradation` | `DIAGNOSED / latency_degradation` | `DIAGNOSED / latency_degradation` | Pass |
| `noisy-false-alert` | `INVESTIGATE / noisy_or_ambiguous_alert` | `INVESTIGATE / noisy_or_ambiguous_alert` | Pass |

Verification command used:

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

Synthetic fixture verification command:

```powershell
python scripts/validate_datapack.py
```

### 3.2 RCAEval Subset Evidence Bundle Readiness

The W11 handoff includes 9 RCAEval-derived evidence bundles across the three required scenario categories.

| Scenario category | Bundle count | Source |
|---|---:|---|
| `critical-service-down` | 3 | RCAEval subset telemetry as primary evidence |
| `latency-degradation` | 3 | RCAEval subset telemetry as primary evidence |
| `noisy-false-alert` | 3 | RCAEval subset telemetry as primary evidence |

RCAEval is the primary scenario source. For selected RE2/RE3 cases, logs and traces should come from official RCAEval `logs.csv` and `traces.csv` after the RCAEval utility download succeeds. For selected RE1 cases, or for operational records RCAEval does not provide, evidence bundles include `data_lineage` that marks deploy events, ownership, and runbooks as TF1 supplemental records.

Regeneration command:

```powershell
python scripts/build_rcaeval_evidence_bundles.py
```

## 4. Current Rule Logic

| Input pattern | Output behavior |
|---|---|
| Empty metrics/logs/deploys and no ownership context | `INSUFFICIENT_CONTEXT`, confidence `0.25` |
| Critical severity or service-down language | `DIAGNOSED`, `critical_service_down`, confidence `0.86` |
| Latency, p95, or timeout signals | `DIAGNOSED`, `latency_degradation`, confidence `0.82` |
| Low severity, noisy, flapping, false alarm, or ambiguous text | `INVESTIGATE`, confidence `0.45` |
| Context present but no known scenario match | `INVESTIGATE`, confidence `0.55` |

## 5. Gaps Before Final Eval

- RCAEval subset evidence bundles now cover the 3 required demo scenario categories, but final quality claims still need a larger labeled set.
- The checked-in RCAEval subset must be regenerated from official RCAEval utility output to include RE2/RE3 `logs.csv` and `traces.csv` when the network path to Zenodo is available.
- Precision, recall, F1, P50/P99 latency, and cost per call are not meaningful until RCAEval mapping and final AI logic exist.
- Current logic is deterministic scenario routing, not LLM reasoning.
- Persistent audit storage is not implemented; responses include deterministic `audit_id` only.

## 6. Final Eval Plan

| Metric | Target |
|---|---:|
| Precision | >= 0.80 |
| Recall | >= 0.70 |
| F1 | >= 0.75 |
| P99 latency | < 2 seconds for capstone demo |
| Scenario coverage | 3 E2E scenarios plus 5-10 additional test cases |
| Confidence behavior | Low confidence maps to `INVESTIGATE` or `INSUFFICIENT_CONTEXT` |

Final eval will extend the RCAEval mapping into the observability/triage contracts and use synthetic fixtures only for stable demo wiring.
