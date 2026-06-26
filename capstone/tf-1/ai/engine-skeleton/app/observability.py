from __future__ import annotations

import json
import logging
import os
import time
from contextlib import contextmanager
from typing import Any, Iterator

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest
from prometheus_client.openmetrics.exposition import CONTENT_TYPE_LATEST

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor


SERVICE_NAME = "tf1-ai-triage-engine"
OBSERVABILITY_ENABLED = os.getenv("AIOPS_OBSERVABILITY_ENABLED", "true").lower() in {"1", "true", "yes"}
LOG_POLICY = os.getenv("AIOPS_LOG_POLICY", "metadata_only")

REGISTRY = CollectorRegistry()

TRIAGE_REQUESTS_TOTAL = Counter(
    "aiops_triage_requests_total",
    "Triage requests by terminal status and classification.",
    ["status", "classification"],
    registry=REGISTRY,
)
TRIAGE_REQUEST_DURATION_SECONDS = Histogram(
    "aiops_triage_request_duration_seconds",
    "Triage request duration in seconds.",
    registry=REGISTRY,
)
TRIAGE_INFLIGHT_REQUESTS = Gauge(
    "aiops_triage_inflight_requests",
    "In-flight triage requests.",
    registry=REGISTRY,
)
CONTEXT_TOOL_CALLS_TOTAL = Counter(
    "aiops_context_tool_calls_total",
    "Context tool calls by tool and status.",
    ["tool", "status"],
    registry=REGISTRY,
)
CONTEXT_TOOL_DURATION_SECONDS = Histogram(
    "aiops_context_tool_duration_seconds",
    "Context tool call duration in seconds.",
    ["tool"],
    registry=REGISTRY,
)
CONTEXT_ENRICHMENT_MISSING_FIELDS_TOTAL = Counter(
    "aiops_context_enrichment_missing_fields_total",
    "Context enrichment missing fields after bounded enrichment.",
    ["field"],
    registry=REGISTRY,
)
CONTEXT_ENRICHMENT_RESULT_TOTAL = Counter(
    "aiops_context_enrichment_result_total",
    "Context enrichment result by outcome.",
    ["result"],
    registry=REGISTRY,
)
LLM_CALLS_TOTAL = Counter(
    "aiops_llm_calls_total",
    "LLM calls by stage, model, and status.",
    ["stage", "model", "status"],
    registry=REGISTRY,
)
LLM_TOKENS_TOTAL = Counter(
    "aiops_llm_tokens_total",
    "Estimated LLM tokens by stage, model, and token type.",
    ["stage", "model", "type"],
    registry=REGISTRY,
)
LLM_ESTIMATED_COST_USD_TOTAL = Counter(
    "aiops_llm_estimated_cost_usd_total",
    "Estimated LLM cost in USD by stage and model.",
    ["stage", "model"],
    registry=REGISTRY,
)
QA_ITERATIONS_TOTAL = Counter(
    "aiops_qa_iterations_total",
    "QA iterations by result.",
    ["result"],
    registry=REGISTRY,
)
CIRCUIT_BREAKER_OPEN = Gauge(
    "aiops_circuit_breaker_open",
    "Circuit breaker open state by dependency.",
    ["dependency"],
    registry=REGISTRY,
)
BUDGET_EXCEEDED_TOTAL = Counter(
    "aiops_budget_exceeded_total",
    "Budget exceeded events by budget type.",
    ["budget_type"],
    registry=REGISTRY,
)
DEGRADED_MODE_TOTAL = Counter(
    "aiops_degraded_mode_total",
    "Degraded mode events by reason.",
    ["reason"],
    registry=REGISTRY,
)
INVESTIGATION_MODE_SELECTED_TOTAL = Counter(
    "aiops_investigation_mode_selected_total",
    "Investigation mode selections by mode and source.",
    ["mode", "source"],
    registry=REGISTRY,
)
AGENT_ITERATIONS_TOTAL = Counter(
    "aiops_agent_iterations_total",
    "Agent platform iterations by result.",
    ["result"],
    registry=REGISTRY,
)
AGENT_TOOL_REQUESTS_TOTAL = Counter(
    "aiops_agent_tool_requests_total",
    "Agent platform tool requests by tool and gateway status.",
    ["tool", "status"],
    registry=REGISTRY,
)
AGENT_FALLBACK_TOTAL = Counter(
    "aiops_agent_fallback_total",
    "Agent platform deterministic fallbacks by reason.",
    ["reason"],
    registry=REGISTRY,
)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%SZ"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "service": SERVICE_NAME,
        }
        extra = getattr(record, "aiops", None)
        if isinstance(extra, dict):
            payload.update(sanitize_log_fields(extra))
        if record.exc_info:
            payload["error_class"] = record.exc_info[0].__name__ if record.exc_info[0] else "Exception"
        return json.dumps(payload, ensure_ascii=True, default=str)


def configure_logging() -> None:
    logging.basicConfig(level=os.getenv("AIOPS_LOG_LEVEL", "INFO").upper(), force=True)
    formatter = JsonFormatter()
    for handler in logging.getLogger().handlers:
        handler.setFormatter(formatter)


def configure_tracing() -> None:
    if not OBSERVABILITY_ENABLED:
        return
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
    resource = Resource.create({"service.name": SERVICE_NAME})
    provider = TracerProvider(resource=resource)
    if endpoint:
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    trace.set_tracer_provider(provider)


def metrics_response() -> tuple[bytes, str]:
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST


def log_event(message: str, **fields: Any) -> None:
    logging.getLogger("aiops.engine").info(message, extra={"aiops": sanitize_log_fields(fields)})


def sanitize_log_fields(fields: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "audit_id",
        "tenant_id",
        "correlation_id",
        "incident_id",
        "service",
        "environment",
        "stage",
        "status",
        "classification",
        "duration_ms",
        "metrics_count",
        "logs_count",
        "traces_count",
        "deploys_count",
        "model",
        "tokens",
        "error_class",
        "reason",
        "qa_result",
        "investigation_mode",
        "complexity_score",
        "agent_iterations",
        "fallback_reason",
    }
    sanitized = {key: value for key, value in fields.items() if key in allowed}
    if LOG_POLICY != "metadata_only":
        return sanitized
    return sanitized


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[Any]:
    tracer = trace.get_tracer("aiops.engine")
    with tracer.start_as_current_span(name) as active_span:
        for key, value in attributes.items():
            if value is not None:
                active_span.set_attribute(key, value)
        yield active_span


@contextmanager
def timed() -> Iterator[dict[str, float]]:
    started = time.perf_counter()
    state = {"duration": 0.0}
    try:
        yield state
    finally:
        state["duration"] = time.perf_counter() - started


def estimate_tokens(value: Any) -> int:
    text = json.dumps(value, ensure_ascii=True, default=str) if not isinstance(value, str) else value
    return max(1, len(text) // 4)
