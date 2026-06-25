# TF1 Synthetic Demo Fixtures

Status: Synthetic v1, demo/smoke only  
Owner: TF1 AI team  
Last updated: 2026-06-23

This folder contains controlled synthetic fixtures for the TF1 Triage Hub. These files are useful for API smoke tests, deterministic demo flows, Jira ticket fields, and Slack-renderable raw response examples.

Synthetic fixtures are **not** the primary evidence dataset. The primary dataset for TF1 evaluation and CDO evidence handoff is the RCAEval subset under `datapack/external/`, documented in `datapack/external/README.md` and `docs/public-dataset-review.md`.

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
- `raw-traces.json`: bounded trace/span summaries for correlation and dependency evidence.
- `deploy-events.json`: recent deploy records.
- `ownership.json`: owner, Slack, Jira, runbook metadata.
- `runbooks.json`: synthetic runbook snippets.
- `evidence-bundle.json`: CDO-hostable bundle that combines scoped metrics, logs, traces, deploys, ownership, runbooks, query windows, and query hints.
- `expected-detection.json`: detector expectation.
- `triage-request.json`: normalized request body for `/v1/triage`.
- `expected-triage-summary.json`: expected status, classification, and confidence band.

## Extra Evidence For AI Queries

The AI should not query customer applications directly. If it needs more evidence than the initial incident seed provides, it should query a CDO-hosted evidence bundle or a read-only observability proxy.

For primary RCAEval-based handoff, use:

```text
datapack/external/evidence-bundles/<scenario>/<case>.evidence-bundle.json
```

The scenario-level `evidence-bundle.json` files in this synthetic fixture folder are only examples of the same hostable shape.

Useful query dimensions:

- `tenant_id`, `service`, `environment`, and `region`.
- Baseline window before the incident.
- Incident window around the alert start.
- Short post-incident window for recovery or continued impact.
- Metric names and dependency names in `query_hints.metrics` and `query_hints.dependencies`.
- Error/retry/timeout filters in `query_hints.log_filters`.
- Trace ids in `query_hints.trace_ids`.

For demo purposes, the scenario bundles include:

- Metrics: latency, availability, error rate, dependency timeout, or stable/noisy indicators.
- Logs: bounded redacted snippets linked by trace id.
- Traces: summarized spans that show dependency latency, dependency failure, or lack of user impact.
- Deploy events: recent change records when relevant.
- Ownership/runbooks: routing and human-review context.

The local RCAEval subset under `datapack/external/rcaeval-subsets/` is generated from official RCAEval utility output. Selected RE2/RE3 cases should include RCAEval `logs.csv` and `traces.csv` when the utility download is available. Evidence bundles use RCAEval telemetry as primary data where present and clearly mark deploy/ownership/runbook records, plus any missing RE1 logs/traces, as TF1 supplemental records in `data_lineage`.

## Validation

From `capstone/tf-1/ai/engine-skeleton`:

```powershell
python scripts/validate_datapack.py
```

The validator checks required fixture metadata, calls the local FastAPI app through `TestClient`, and verifies status/classification/confidence against expectations.
