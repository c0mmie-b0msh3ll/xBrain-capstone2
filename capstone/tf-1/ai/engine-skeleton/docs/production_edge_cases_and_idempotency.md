# Production Edge Cases And Idempotency Notes

## How Idempotency Works With Agentic Flow

The engine does not make the AgentCore or LLM path deterministic. Agentic execution can still vary because model output, tool-request ordering, provider retries, and context backend timing may vary.

Instead, idempotency is achieved at the API processing boundary:

1. The engine computes the deterministic `audit_id` from `tenant_id:correlation_id:incident_id`.
2. It computes a `request_hash` from the request body plus selected runtime-affecting settings.
3. On first processing, it writes `audit/idempotency/{audit_id}.json` as `in_progress`.
4. On successful completion, it stores `completed` with the exact response payload and response hash.
5. If CDO retries the same `audit_id` with the same `request_hash`, the engine returns the stored response immediately and does not call AgentCore again.

So the guarantee is retry idempotency: duplicate delivery of the same incident request does not trigger duplicate expensive agentic work after a completed first run. It is not a guarantee that two fresh executions of the same prompt would produce the same result.

If the first pod/process dies before completion, the record remains `in_progress`. A retry before the stale timeout receives `409`; a retry after `AIOPS_IDEMPOTENCY_STALE_SECONDS` reprocesses. That retry may produce a different result because the original agentic run never completed and no response artifact exists to replay.

## Remaining Edge Cases

### Idempotency Race

Two identical requests can arrive at the same time before either writes `in_progress`. The JSON-file store uses atomic replacement for writes, but it is not a distributed conditional lock. This is acceptable for the W11 demo, but production should use DynamoDB conditional writes, Postgres row locks, S3 conditional writes, or another durable compare-and-set primitive.

### Completed Response Staleness

A completed idempotency replay returns the original stored response for the same request hash. This is correct for retry safety, but it means retries do not re-read newer observability context, ownership data, or Jira history. CDO should treat retries as delivery retries, not re-evaluation requests.

### Request Hash Scope

The request hash includes the request body and selected runtime-affecting settings, but not every possible environment variable or the contents of every configured data file. If ownership or Jira files change without the path changing, a same-hash retry can replay the previous response. Production can address this with versioned context snapshots, context version fields, or explicit force-reprocess semantics.

### Local File Durability

The idempotency store is only durable if the audit base path is mounted to durable storage. If the pod uses ephemeral storage and dies, replay protection is lost. CDO owns durable volume or object-store provisioning.

### Partial Audit Or Idempotency Write Failure

If audit write fails but idempotency completion succeeds, `/v1/audit/{audit_id}` may not expose the final decision even though retry replay works. If idempotency completion fails after audit succeeds, a retry may duplicate work. Production should alert on audit/idempotency write failures.

### Large Non-Evidence Fields

Metrics, logs, and traces are bounded. Other fields can still grow if upstream sends very large `anomaly_evidence`, `rca_candidates`, `causal_hints`, `service_topology`, ownership metadata, runbook excerpts, or alert labels. LLM prompt builders slice many of these fields, but memory and response size could still benefit from explicit caps.

### Malformed But Schema-Valid Evidence

Schema-valid evidence can still be operationally poor: duplicate timestamps, strange timestamp ordering, huge floats, `NaN`-like values, excessive label cardinality, or misleading metric names. Current compaction limits size, but semantic validation is still limited.

### Sensitive Data In Returned Response

Audit records and structured logs avoid raw evidence, but the public triage response can include representative log messages in root-cause evidence or ticket descriptions. If logs can contain secrets or PII, a redaction layer should run before response assembly.

### Per-Process Semaphore Scope

`AIOPS_MAX_CONCURRENT_TRIAGE_REQUESTS` is local to one process. With multiple replicas, total concurrency is roughly `replica_count * cap`. CDO still owns queueing, retry, autoscaling, and fleet-level backpressure.

### Prompt Budget Pressure From Non-Evidence Fields

Evidence compaction reduces metrics/logs/traces, and token budgets fail closed before provider calls. However, large non-evidence fields can still push prompts over budget and force deterministic fallback more often than expected.

### Clock Skew

Stale `in_progress` detection uses local wall-clock time. If idempotency files are shared across nodes with skewed clocks, retries could reprocess early or wait longer than intended. Production stores should prefer server-side timestamps.

## Recommended Next Hardening

1. Add caps for all user-controlled non-evidence structures before RCA and response assembly.
2. Add secret/PII redaction before response evidence and ticket description generation.
3. Replace the file idempotency store with a conditional-write durable store for production.
4. Add tests for oversized labels, topology, anomaly evidence, runbook excerpts, and redaction.
