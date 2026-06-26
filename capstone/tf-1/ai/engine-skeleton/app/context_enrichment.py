from __future__ import annotations

from typing import Any

from app.context_tools import ContextClient, ToolRegistry, ToolScope, ToolScopeError
from app.observability import (
    CONTEXT_ENRICHMENT_MISSING_FIELDS_TOTAL,
    CONTEXT_ENRICHMENT_RESULT_TOTAL,
    DEGRADED_MODE_TOTAL,
    span,
)


EVIDENCE_FIELDS = ("metrics", "logs", "traces", "recent_deploys")


def enrich_triage_context(
    body: dict[str, Any],
    registry: ToolRegistry | None = None,
) -> dict[str, Any]:
    with span("context_enrichment"):
        registry = registry or ToolRegistry()
        enriched = clone_request_body(body)
        alert = enriched.setdefault("alert", {})
        labels = dict(alert.get("labels", {})) if isinstance(alert.get("labels"), dict) else {}
        scope = scope_from_body(enriched)

        evidence_uri = labels.get("evidence_uri")
        if isinstance(evidence_uri, str) and evidence_uri:
            with span("evidence_bundle_load", service=scope.service, tenant_id=scope.tenant_id, environment=scope.environment):
                bundle = load_evidence_bundle(registry, evidence_uri, scope)
            if bundle is not None:
                apply_evidence_bundle(enriched, bundle)
                labels.update(evidence_lineage_labels(bundle, evidence_uri, "loaded"))
            else:
                labels["evidence_uri_status"] = "missing_or_out_of_scope"
                DEGRADED_MODE_TOTAL.labels(reason="evidence_bundle_unavailable").inc()

        apply_tool_fallbacks(enriched, registry, scope)
        alert["labels"] = labels
        record_enrichment_result(enriched)
        return enriched


def clone_request_body(body: dict[str, Any]) -> dict[str, Any]:
    cloned = dict(body)
    cloned["alert"] = dict(body.get("alert", {}))
    for field in EVIDENCE_FIELDS:
        value = body.get(field, [])
        cloned[field] = list(value) if isinstance(value, list) else []
    ownership = body.get("ownership")
    cloned["ownership"] = dict(ownership) if isinstance(ownership, dict) else ownership
    return cloned


def scope_from_body(body: dict[str, Any]) -> ToolScope:
    alert = body.get("alert", {})
    return ToolScope(
        tenant_id=str(body["tenant_id"]),
        environment=str(body["environment"]),
        service=str(alert["service"]),
        started_at=str(alert["started_at"]),
        received_at=str(body["received_at"]),
    )


def load_evidence_bundle(registry: ToolRegistry, evidence_uri: str, scope: ToolScope) -> dict[str, Any] | None:
    try:
        bundle = registry.client.get_evidence_bundle(evidence_uri, scope)
    except (AttributeError, OSError, ValueError, RuntimeError, ToolScopeError):
        return None
    return bundle if isinstance(bundle, dict) else None


def apply_evidence_bundle(body: dict[str, Any], bundle: dict[str, Any]) -> None:
    if is_missing_list(body.get("metrics")):
        body["metrics"] = list(bundle.get("metrics", [])) if isinstance(bundle.get("metrics"), list) else []
    if is_missing_list(body.get("logs")):
        body["logs"] = list(bundle.get("logs", [])) if isinstance(bundle.get("logs"), list) else []
    if is_missing_list(body.get("traces")):
        body["traces"] = normalize_bundle_traces(bundle.get("traces", []))
    if is_missing_list(body.get("recent_deploys")):
        deploys = bundle.get("recent_deploys", bundle.get("deploy_events", []))
        body["recent_deploys"] = list(deploys) if isinstance(deploys, list) else []
    if not body.get("ownership"):
        body["ownership"] = normalize_bundle_ownership(bundle)


def apply_tool_fallbacks(body: dict[str, Any], registry: ToolRegistry, scope: ToolScope) -> None:
    tool_by_field = {
        "metrics": "get_metrics",
        "logs": "get_logs",
        "traces": "get_traces",
        "recent_deploys": "get_recent_deploys",
    }
    for field, tool_name in tool_by_field.items():
        if not is_missing_list(body.get(field)):
            continue
        if not context_access_configured(registry, tool_name):
            continue
        result = execute_context_tool(registry, tool_name, scope)
        if isinstance(result, list):
            body[field] = result

    if not body.get("ownership") and context_access_configured(registry, "get_ownership"):
        result = execute_context_tool(registry, "get_ownership", scope)
        if isinstance(result, dict):
            body["ownership"] = result


def execute_context_tool(registry: ToolRegistry, tool_name: str, scope: ToolScope) -> Any:
    try:
        return registry.execute(tool_name, {}, scope).get("result")
    except (ToolScopeError, OSError, ValueError, RuntimeError):
        DEGRADED_MODE_TOTAL.labels(reason="context_tool_failure").inc()
        return None


def context_access_configured(registry: ToolRegistry, tool_name: str) -> bool:
    client = registry.client
    if not isinstance(client, ContextClient):
        return tool_name in registry.names
    if tool_name == "get_metrics":
        return client.metrics_access_configured
    if tool_name == "get_logs":
        return client.logs_access_configured
    if tool_name == "get_traces":
        return client.traces_access_configured
    if tool_name == "get_recent_deploys":
        return bool(client.deploy_metadata_path)
    if tool_name == "get_ownership":
        return bool(client.ownership_path)
    return tool_name in registry.names


def is_missing_list(value: Any) -> bool:
    return not isinstance(value, list) or len(value) == 0


def normalize_bundle_traces(traces: Any) -> list[dict[str, Any]]:
    if not isinstance(traces, list):
        return []
    normalized: list[dict[str, Any]] = []
    for trace in traces:
        if not isinstance(trace, dict):
            continue
        labels = dict(trace.get("labels", {})) if isinstance(trace.get("labels"), dict) else {}
        for key in ("root_span", "bottleneck_service", "bottleneck_reason"):
            if key in trace:
                labels[key] = trace[key]
        normalized.append(
            {
                "trace_id": str(trace.get("trace_id") or trace.get("id") or "unknown-trace"),
                "span_id": trace.get("span_id"),
                "parent_span_id": trace.get("parent_span_id"),
                "service": str(trace.get("service") or "unknown"),
                "operation": trace.get("operation") or trace.get("root_span"),
                "ts": trace.get("ts") or trace.get("timestamp"),
                "duration_ms": trace.get("duration_ms"),
                "status": trace.get("status"),
                "labels": labels,
            }
        )
    return normalized


def normalize_bundle_ownership(bundle: dict[str, Any]) -> dict[str, Any] | None:
    ownership = bundle.get("ownership")
    if not isinstance(ownership, dict):
        return None
    normalized = dict(ownership)
    if "runbooks" not in normalized and isinstance(bundle.get("runbooks"), list):
        normalized["runbooks"] = bundle["runbooks"]
    return normalized


def evidence_lineage_labels(bundle: dict[str, Any], evidence_uri: str, status: str) -> dict[str, Any]:
    labels: dict[str, Any] = {
        "evidence_uri": evidence_uri,
        "evidence_uri_status": status,
    }
    for key in ("schema_version", "source_case", "scenario"):
        value = bundle.get(key)
        if value is not None:
            labels[f"evidence_{key}"] = value
    if isinstance(bundle.get("data_lineage"), dict):
        labels["evidence_data_lineage"] = bundle["data_lineage"]
    return labels


def record_enrichment_result(body: dict[str, Any]) -> None:
    missing_fields: list[str] = []
    for field in EVIDENCE_FIELDS:
        if is_missing_list(body.get(field)):
            missing_fields.append(field)
    if not body.get("ownership"):
        missing_fields.append("ownership")
    for field in missing_fields:
        CONTEXT_ENRICHMENT_MISSING_FIELDS_TOTAL.labels(field=field).inc()
    CONTEXT_ENRICHMENT_RESULT_TOTAL.labels(result="complete" if not missing_fields else "partial").inc()
