# AI Engine Refactor Plan: Controlled Investigation, Observability, and Failure Modes

## Summary

Refactor the AI triage engine around a bounded investigation pipeline:

1. `/v1/triage` receives alert metadata plus optional evidence.
2. Deterministic context enrichment fills missing metrics, logs, traces, deploy, and ownership context through scoped tools.
3. RCA runs with deterministic analysis first and optional LLM investigation second.
4. An optional QA loop checks RCA quality within strict token and iteration budgets.
5. Output remains diagnosis data only; Jira and Slack execution stay with CDO.

CDO's Prometheus, Loki, and Jaeger stack is enough as the base observability stack, but the AI engine is not yet observable as its own service. Add engine metrics, traces, structured logs, dashboards, alerts, and self-protection controls.

## Key Changes

### Triage Flow

- Keep frozen `POST /v1/triage`; do not add a public seed endpoint or change contract files.
- Preserve the alert-metadata-first enrichment flow:
  - inline evidence wins;
  - `evidence_uri` fills missing fields;
  - scoped context tools fill remaining gaps;
  - failures degrade confidence instead of expanding scope.
- Keep LLM tools read-only and bounded:
  - allow context and RCA helper tools only;
  - forbid arbitrary PromQL, LogQL, shell commands, app mutation, Jira creation, or Slack posting.
- Add a bounded QA stage after RCA:
  - max 1 QA pass by default;
  - max 1 repair pass only if QA finds concrete missing evidence, contradiction, or unsupported conclusion;
  - hard token and latency budget;
  - if QA or repair fails, return the best available RCA with lower confidence and QA failure metadata.

### AI Engine Observability

- Add `/metrics` for the triage engine using `prometheus-client`.
- Add OpenTelemetry tracing for key spans:
  - request validation;
  - context enrichment;
  - evidence bundle load;
  - each context tool call;
  - deterministic RCA;
  - LLM investigation;
  - QA;
  - response assembly.
- Add structured JSON logs with metadata only by default:
  - include `audit_id`, `tenant_id`, `service`, `environment`, `stage`, `status`, durations, counts, model id, token usage, and error class;
  - exclude raw customer evidence from Loki by default.
- Update Docker Compose observability wiring:
  - expose triage-engine metrics;
  - scrape triage-engine from Prometheus or the OTel collector;
  - send engine traces to the OTel collector and then Jaeger;
  - keep simulator observability unchanged.

### Metrics

- Request metrics:
  - `aiops_triage_requests_total{status,classification}`
  - `aiops_triage_request_duration_seconds`
  - `aiops_triage_inflight_requests`
- Context metrics:
  - `aiops_context_tool_calls_total{tool,status}`
  - `aiops_context_tool_duration_seconds{tool}`
  - `aiops_context_enrichment_missing_fields_total{field}`
  - `aiops_context_enrichment_result_total{result}`
- LLM and QA metrics:
  - `aiops_llm_calls_total{stage,model,status}`
  - `aiops_llm_tokens_total{stage,model,type}`
  - `aiops_llm_estimated_cost_usd_total{stage,model}`
  - `aiops_qa_iterations_total{result}`
- Self-protection metrics:
  - `aiops_circuit_breaker_open{dependency}`
  - `aiops_budget_exceeded_total{budget_type}`
  - `aiops_degraded_mode_total{reason}`

Use low-cardinality labels only. Do not label metrics by alert title, trace id, incident id, or raw customer evidence. Treat service labels carefully; only use them when the service set is bounded for the demo.

## Failure Modes And Edge Cases

### Context Stack Failures

- Prometheus, Loki, or Jaeger is unavailable, slow, returns malformed data, returns stale data, returns empty results, or only partially works.
- Behavior: timeout each tool call, record the missing field, continue with available context, and lower confidence.

### Evidence Failures

- `evidence_uri` is missing, inaccessible, malformed, oversized, or out of scope for tenant, service, environment, or alert window.
- Behavior: reject out-of-scope data, truncate oversized bounded fields, and fall back to context tools when configured.

### LLM Failures

- LLM call times out, returns invalid JSON, hallucinates a tool name, proposes unsupported remediation, exceeds cost budget, or is throttled by the provider.
- Behavior: fall back to deterministic RCA, execute no tool outside the allowlist, and downgrade confidence when needed.

### QA Failures

- QA loop takes too long, contradicts itself, repair still fails, or token budget is exceeded.
- Behavior: stop after the configured limit and return the best available diagnosis with QA metadata.

### App Failures

- High concurrent incidents, slow context tools, report write failure, SQS duplicate, or repeated alert storms.
- Behavior: use request deadlines, context-tool circuit breakers, duplicate detection by alert fingerprint where possible, and degraded deterministic mode.

### Observability Stack Failures

- Metrics endpoint fails, OTel collector is down, Loki is down, or Jaeger is down.
- Behavior: observability must never fail triage. Log locally where possible and continue.

## Auto-Remediation Boundary

- Do not auto-remediate customer applications. That is out of scope.
- For the AI engine itself, implement self-protection only:
  - model fallback;
  - deterministic-only degraded mode;
  - context-tool circuit breakers;
  - request, LLM, and QA budget enforcement;
  - backpressure or `429` when overloaded;
  - health and readiness signals for orchestrator restart.
- Do not let the LLM execute remediation commands.
- Any customer-facing remediation remains a recommendation in `ticket_payload` and must be approved and executed by CDO, Jira, or Slack workflows.

## Interfaces And Config

- Public API:
  - keep `/v1/triage` unchanged;
  - add internal `/metrics`;
  - keep `/healthz`;
  - optionally add `/readyz` for dependency and circuit state.
- New config defaults:
  - `AIOPS_OBSERVABILITY_ENABLED=true`
  - `AIOPS_LOG_LEVEL=INFO`
  - `AIOPS_LOG_POLICY=metadata_only`
  - `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=http://otel-collector:4318/v1/traces`
  - `AIOPS_CONTEXT_TOOL_TIMEOUT_SECONDS=3`
  - `AIOPS_TRIAGE_DEADLINE_SECONDS=30`
  - `AIOPS_LLM_MAX_TOKENS_PER_INCIDENT`
  - `AIOPS_QA_MAX_ITERATIONS=1`
  - `AIOPS_QA_REPAIR_MAX_ITERATIONS=1`
  - `AIOPS_DEGRADED_MODE_ON_BUDGET_EXCEEDED=true`

## Test Plan

- Unit tests:
  - metrics counters and histograms increment for success, failure, and degraded mode;
  - structured logs omit raw evidence;
  - spans are created around enrichment, tools, RCA, LLM, and QA;
  - tool failures degrade context instead of failing the request;
  - QA loop stops at iteration and token limits.
- Endpoint tests:
  - alert metadata only with empty evidence still enriches and diagnoses when scoped tools return data;
  - Prometheus, Loki, and Jaeger failures return lower confidence or `INSUFFICIENT_CONTEXT`;
  - malformed LLM or QA output falls back to deterministic RCA;
  - out-of-scope evidence is rejected.
- Compose/manual verification:
  - `docker compose -f docker-compose.observability.yml up`;
  - Prometheus can scrape triage-engine metrics;
  - Jaeger shows triage spans;
  - Loki contains metadata-only engine logs;
  - Grafana dashboard shows request rate, latency, failures, tool latency, LLM cost, and degraded mode.
- Existing checks:
  - `python -m compileall app scripts`
  - `python -m pytest tests -q`
  - `python scripts/validate_datapack.py`
  - `npm run build` only if report UI is touched.

## Assumptions

- Optimize for Docker Compose demo first.
- Keep production compatibility through standard OTel and Prometheus environment names.
- Logs are metadata-only by default.
- CDO owns Jira, Slack, and customer-app remediation.
- The AI engine may protect itself, but it must not mutate customer systems.
