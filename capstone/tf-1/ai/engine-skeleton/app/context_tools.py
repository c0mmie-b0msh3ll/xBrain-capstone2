from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import requests

from app.observability import CIRCUIT_BREAKER_OPEN, CONTEXT_TOOL_CALLS_TOTAL, CONTEXT_TOOL_DURATION_SECONDS, span, timed
from app import rca


READ_ONLY_TOOL_NAMES = {
    "get_metrics",
    "get_logs",
    "get_traces",
    "get_recent_deploys",
    "get_ownership",
    "detect_metric_anomalies",
    "detect_log_anomalies",
    "infer_topology",
    "infer_causal_hints",
    "rank_rca_candidates",
    "get_jira_history",
    "search_runbooks",
    "search_known_errors",
}


@dataclass(frozen=True)
class ToolScope:
    tenant_id: str
    environment: str
    service: str
    started_at: str
    received_at: str
    max_window_minutes: int = 60
    log_limit: int = 50


class ToolScopeError(ValueError):
    pass


class ToolRegistry:
    def __init__(self, client: "ContextClient | None" = None) -> None:
        self.client = client or ContextClient()
        self._tools: dict[str, Callable[..., Any]] = {
            "get_metrics": self._get_metrics,
            "get_logs": self._get_logs,
            "get_traces": self._get_traces,
            "get_recent_deploys": self._get_recent_deploys,
            "get_ownership": self._get_ownership,
            "detect_metric_anomalies": self._detect_metric_anomalies,
            "detect_log_anomalies": self._detect_log_anomalies,
            "infer_topology": self._infer_topology,
            "infer_causal_hints": self._infer_causal_hints,
            "rank_rca_candidates": self._rank_rca_candidates,
            "get_jira_history": self._get_jira_history,
            "search_runbooks": self._search_runbooks,
            "search_known_errors": self._search_known_errors,
        }

    @property
    def names(self) -> set[str]:
        return set(self._tools)

    def execute(self, name: str, args: dict[str, Any] | None, scope: ToolScope, request: Any | None = None) -> dict[str, Any]:
        if name not in self._tools:
            CONTEXT_TOOL_CALLS_TOTAL.labels(tool="unknown", status="blocked").inc()
            raise ToolScopeError(f"Unknown tool: {name}")
        args = args or {}
        with span("context_tool_call", tool=name, tenant_id=scope.tenant_id, environment=scope.environment, service=scope.service):
            with timed() as timer:
                try:
                    bounded_scope = validate_and_build_scope(args, scope)
                    result = self._tools[name](args, scope, request)
                    CONTEXT_TOOL_CALLS_TOTAL.labels(tool=name, status="ok").inc()
                    CIRCUIT_BREAKER_OPEN.labels(dependency=name).set(0)
                    return {
                        "name": name,
                        "status": "ok",
                        "bounded_scope": bounded_scope,
                        "result": to_jsonable(result),
                    }
                except Exception:
                    CONTEXT_TOOL_CALLS_TOTAL.labels(tool=name, status="error").inc()
                    raise
                finally:
                    CONTEXT_TOOL_DURATION_SECONDS.labels(tool=name).observe(timer["duration"])

    def _get_metrics(self, args: dict[str, Any], scope: ToolScope, request: Any | None) -> list[dict[str, Any]]:
        window = bounded_window(args, scope)
        return self.client.get_metrics(scope.service, scope.environment, scope.tenant_id, window)

    def _get_logs(self, args: dict[str, Any], scope: ToolScope, request: Any | None) -> list[dict[str, Any]]:
        window = bounded_window(args, scope)
        limit = min(int(args.get("limit") or scope.log_limit), scope.log_limit)
        return self.client.get_logs(scope.service, scope.environment, scope.tenant_id, window, limit)

    def _get_traces(self, args: dict[str, Any], scope: ToolScope, request: Any | None) -> list[dict[str, Any]]:
        window = bounded_window(args, scope)
        limit = min(int(args.get("limit") or scope.log_limit), scope.log_limit)
        if not hasattr(self.client, "get_traces"):
            return []
        return self.client.get_traces(scope.service, scope.environment, scope.tenant_id, window, limit)

    def _get_recent_deploys(self, args: dict[str, Any], scope: ToolScope, request: Any | None) -> list[dict[str, Any]]:
        window = bounded_window(args, scope)
        return self.client.get_recent_deploys(scope.service, scope.environment, window)

    def _get_ownership(self, args: dict[str, Any], scope: ToolScope, request: Any | None) -> dict[str, Any]:
        return self.client.get_ownership(scope.service)

    def _detect_metric_anomalies(self, args: dict[str, Any], scope: ToolScope, request: Any | None) -> list[dict[str, Any]]:
        metrics = args.get("metrics")
        if metrics is None and request is not None:
            metrics = request.metrics
        return rca.detect_metric_anomalies(metrics or [])

    def _detect_log_anomalies(self, args: dict[str, Any], scope: ToolScope, request: Any | None) -> list[dict[str, Any]]:
        logs = args.get("logs")
        if logs is None and request is not None:
            logs = request.logs
        return rca.detect_log_anomalies(logs or [])

    def _infer_topology(self, args: dict[str, Any], scope: ToolScope, request: Any | None) -> dict[str, Any]:
        if request is None:
            raise ToolScopeError("infer_topology requires a triage request")
        return rca.infer_topology(request)

    def _infer_causal_hints(self, args: dict[str, Any], scope: ToolScope, request: Any | None) -> list[dict[str, Any]]:
        metrics = args.get("metrics")
        if metrics is None and request is not None:
            metrics = request.metrics
        return rca.infer_causal_hints(metrics or [])

    def _rank_rca_candidates(self, args: dict[str, Any], scope: ToolScope, request: Any | None) -> list[dict[str, Any]]:
        if request is None:
            raise ToolScopeError("rank_rca_candidates requires a triage request")
        evidence = args.get("evidence") or request.anomaly_evidence or rca.detect_metric_anomalies(request.metrics) + rca.detect_log_anomalies(request.logs)
        topology = args.get("topology") or request.service_topology or rca.infer_topology(request)
        causal_hints = args.get("causal_hints") or request.causal_hints or rca.infer_causal_hints(request.metrics)
        return rca.rank_rca_candidates(request, evidence, topology, causal_hints)

    def _get_jira_history(self, args: dict[str, Any], scope: ToolScope, request: Any | None) -> dict[str, Any]:
        return self.client.get_jira_history(scope.service, scope.environment, scope.tenant_id)

    def _search_runbooks(self, args: dict[str, Any], scope: ToolScope, request: Any | None) -> list[dict[str, Any]]:
        query = str(args.get("query") or "")
        return self.client.search_runbooks(scope.service, query)

    def _search_known_errors(self, args: dict[str, Any], scope: ToolScope, request: Any | None) -> list[dict[str, Any]]:
        query = str(args.get("query") or "")
        return self.client.search_known_errors(scope.service, scope.environment, scope.tenant_id, query)


class ContextClient:
    def __init__(
        self,
        prometheus_url: str | None = None,
        loki_url: str | None = None,
        jaeger_url: str | None = None,
        deploy_metadata_path: str | None = None,
        ownership_path: str | None = None,
        evidence_bundle_base_path: str | None = None,
        jira_history_path: str | None = None,
        known_errors_path: str | None = None,
    ) -> None:
        self.prometheus_url = prometheus_url or os.getenv("PROMETHEUS_URL")
        self.loki_url = loki_url or os.getenv("LOKI_URL")
        self.jaeger_url = jaeger_url or os.getenv("JAEGER_URL")
        self.deploy_metadata_path = deploy_metadata_path or os.getenv("DEPLOY_METADATA_PATH")
        self.ownership_path = ownership_path or os.getenv("OWNERSHIP_PATH")
        self.evidence_bundle_base_path = evidence_bundle_base_path or os.getenv("EVIDENCE_BUNDLE_BASE_PATH")
        self.jira_history_path = jira_history_path or os.getenv("JIRA_HISTORY_PATH")
        self.known_errors_path = known_errors_path or os.getenv("KNOWN_ERRORS_PATH")
        self.timeout_seconds = int(os.getenv("AIOPS_CONTEXT_TOOL_TIMEOUT_SECONDS", os.getenv("LLM_TOOL_TIMEOUT_SECONDS", "3")))

    @property
    def metrics_access_configured(self) -> bool:
        return bool(self.prometheus_url)

    @property
    def logs_access_configured(self) -> bool:
        return bool(self.loki_url)

    @property
    def traces_access_configured(self) -> bool:
        return bool(self.jaeger_url)

    def get_metrics(self, service: str, environment: str, tenant_id: str, window: tuple[str, str]) -> list[dict[str, Any]]:
        if not self.prometheus_url:
            return []
        query = (
            'aiops_scenario_metric_value{'
            f'tenant_id="{tenant_id}",environment="{environment}",service="{service}"'
            "}"
        )
        response = requests.get(
            f"{self.prometheus_url.rstrip('/')}/api/v1/query",
            params={"query": query},
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("status") != "success":
            raise RuntimeError(f"Prometheus query failed: {payload}")
        metrics: list[dict[str, Any]] = []
        for result in payload.get("data", {}).get("result", []):
            labels = result.get("metric", {})
            metrics.append(
                {
                    "metric_name": labels.get("metric_name", "unknown_metric"),
                    "service": labels.get("service", service),
                    "unit": labels.get("unit"),
                    "points": [{"ts": window[1], "value": float(result.get("value", [None, 0])[1])}],
                    "labels": {"region": labels.get("region"), "source": "prometheus"},
                }
            )
        return metrics

    def get_logs(self, service: str, environment: str, tenant_id: str, window: tuple[str, str], limit: int) -> list[dict[str, Any]]:
        if not self.loki_url:
            return []
        query = f'{{tenant_id="{tenant_id}",environment="{environment}",service="{service}"}} |~ "(?i)(error|timeout|failed|refused|exhausted|down)"'
        response = requests.get(
            f"{self.loki_url.rstrip('/')}/loki/api/v1/query_range",
            params={"query": query, "limit": str(limit), "direction": "backward"},
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("status") != "success":
            raise RuntimeError(f"Loki query failed: {payload}")
        logs: list[dict[str, Any]] = []
        for stream in payload.get("data", {}).get("result", []):
            labels = stream.get("stream", {})
            for _, line in stream.get("values", []):
                parsed = parse_log_line(line)
                logs.append(
                    {
                        "service": labels.get("service", service),
                        "ts": parsed.get("timestamp") or window[1],
                        "level": parsed.get("level") or labels.get("level", "error"),
                        "message": parsed.get("message", ""),
                        "trace_id": parsed.get("trace_id"),
                        "labels": parsed.get("labels", {}),
                    }
                )
        return logs[:limit]

    def get_traces(self, service: str, environment: str, tenant_id: str, window: tuple[str, str], limit: int) -> list[dict[str, Any]]:
        if not self.jaeger_url:
            return []
        response = requests.get(
            f"{self.jaeger_url.rstrip('/')}/api/traces",
            params={"service": service, "lookback": "1h", "limit": str(limit)},
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        traces: list[dict[str, Any]] = []
        for trace in payload.get("data", []):
            if not isinstance(trace, dict):
                continue
            trace_id = str(trace.get("traceID") or trace.get("trace_id") or trace.get("id") or "unknown-trace")
            spans = trace.get("spans") if isinstance(trace.get("spans"), list) else []
            root_span = spans[0] if spans and isinstance(spans[0], dict) else {}
            process = {}
            processes = trace.get("processes")
            if isinstance(processes, dict) and root_span.get("processID") in processes:
                process = processes[root_span.get("processID")] or {}
            service_name = service_from_trace_process(process) or service
            start_time = root_span.get("startTime")
            ts = format_iso(datetime.fromtimestamp(start_time / 1_000_000, timezone.utc)) if isinstance(start_time, (int, float)) else window[1]
            if not iso_in_window(ts, window):
                continue
            traces.append(
                {
                    "trace_id": trace_id,
                    "span_id": root_span.get("spanID"),
                    "parent_span_id": first_parent_span_id(root_span),
                    "service": service_name,
                    "operation": root_span.get("operationName"),
                    "ts": ts,
                    "duration_ms": float(root_span.get("duration", 0)) / 1000 if root_span.get("duration") is not None else None,
                    "status": trace_status(root_span),
                    "labels": {"source": "jaeger", "environment": environment, "tenant_id": tenant_id},
                }
            )
        return traces[:limit]

    def get_recent_deploys(self, service: str, environment: str, window: tuple[str, str]) -> list[dict[str, Any]]:
        if not self.deploy_metadata_path:
            return []
        records = load_json_file(self.deploy_metadata_path)
        deploys: list[dict[str, Any]] = []
        for deploy in records if isinstance(records, list) else []:
            if deploy.get("service") != service:
                continue
            deployed_at = deploy.get("deployed_at") or deploy.get("timestamp")
            if deployed_at and not iso_in_window(deployed_at, window):
                continue
            deploys.append(
                {
                    "service": service,
                    "version": deploy.get("version", deploy.get("deploy_id", "unknown")),
                    "deployed_at": deployed_at or window[1],
                    "deployed_by": deploy.get("deployed_by", "ci"),
                    "change_summary": deploy.get("change_summary"),
                    "rollback_ref": deploy.get("rollback_ref"),
                }
            )
        return deploys[:3]

    def get_ownership(self, service: str) -> dict[str, Any]:
        if self.ownership_path:
            records = load_json_file(self.ownership_path)
            if isinstance(records, dict) and records.get("service") == service:
                return records
            if isinstance(records, list):
                for record in records:
                    if record.get("service") == service:
                        return record
        return {
            "service": service,
            "owner_team": None,
            "slack_channel": None,
            "jira_project": None,
            "runbooks": [],
        }

    def get_evidence_bundle(self, evidence_uri: str, scope: ToolScope) -> dict[str, Any] | None:
        path = resolve_local_bundle_path(evidence_uri, self.evidence_bundle_base_path)
        if path is None or not path.exists() or not path.is_file():
            return None
        bundle = load_json_file(str(path))
        if not isinstance(bundle, dict):
            return None
        if not bundle_matches_scope(bundle, scope):
            return None
        return bundle

    def get_jira_history(self, service: str, environment: str, tenant_id: str) -> dict[str, Any]:
        if not self.jira_history_path:
            return {
                "suggested_assignee_account_id": None,
                "suggestion_reason": "No Jira history mapping file is configured for read-only assignee suggestion.",
            }
        records = load_json_file(self.jira_history_path)
        match = find_jira_history_match(records, service, environment, tenant_id)
        if not match:
            return {
                "suggested_assignee_account_id": None,
                "suggestion_reason": f"No Jira accountId history mapping matched {service}; route to owner team for human confirmation.",
            }
        account_id = match.get("account_id") or match.get("assignee_account_id") or match.get("suggested_assignee_account_id")
        if not isinstance(account_id, str) or not account_id:
            return {
                "suggested_assignee_account_id": None,
                "suggestion_reason": f"Jira history matched {service}, but no accountId mapping is available; route to owner team for human confirmation.",
            }
        return {
            "suggested_assignee_account_id": account_id,
            "suggestion_reason": str(match.get("suggestion_reason") or f"Suggested from read-only Jira history for {service}."),
            "source": "jira_history",
        }

    def search_runbooks(self, service: str, query: str = "") -> list[dict[str, Any]]:
        ownership = self.get_ownership(service)
        runbooks = ownership.get("runbooks") if isinstance(ownership, dict) else []
        if not isinstance(runbooks, list):
            return []
        return filter_docs(runbooks, query, default_source="ownership_runbook")[:5]

    def search_known_errors(self, service: str, environment: str, tenant_id: str, query: str = "") -> list[dict[str, Any]]:
        if not self.known_errors_path:
            return []
        records = load_json_file(self.known_errors_path)
        candidates = records.get("known_errors") if isinstance(records, dict) and isinstance(records.get("known_errors"), list) else records
        if not isinstance(candidates, list):
            return []
        scoped: list[dict[str, Any]] = []
        for record in candidates:
            if not isinstance(record, dict):
                continue
            if record.get("service") not in (None, service):
                continue
            if record.get("environment") not in (None, environment):
                continue
            if record.get("tenant_id") not in (None, tenant_id):
                continue
            scoped.append(record)
        return filter_docs(scoped, query, default_source="known_errors")[:5]


def scope_from_request(request: Any, max_window_minutes: int | None = None, log_limit: int | None = None) -> ToolScope:
    return ToolScope(
        tenant_id=request.tenant_id,
        environment=request.environment,
        service=request.alert.service,
        started_at=request.alert.started_at,
        received_at=request.received_at,
        max_window_minutes=max_window_minutes or int(os.getenv("LLM_TOOL_MAX_WINDOW_MINUTES", "60")),
        log_limit=log_limit or int(os.getenv("LLM_TOOL_LOG_LIMIT", "50")),
    )


def validate_and_build_scope(args: dict[str, Any], scope: ToolScope) -> dict[str, Any]:
    if args.get("tenant_id", scope.tenant_id) != scope.tenant_id:
        raise ToolScopeError("Tool call tenant_id is outside incident scope")
    if args.get("environment", scope.environment) != scope.environment:
        raise ToolScopeError("Tool call environment is outside incident scope")
    if args.get("service", scope.service) != scope.service:
        raise ToolScopeError("Tool call service is outside incident scope")
    window = bounded_window(args, scope)
    return {
        "tenant_id": scope.tenant_id,
        "environment": scope.environment,
        "service": scope.service,
        "window": f"{window[0]}/{window[1]}",
    }


def bounded_window(args: dict[str, Any], scope: ToolScope) -> tuple[str, str]:
    default_start = parse_iso(scope.started_at)
    default_end = parse_iso(scope.received_at)
    if default_start > default_end:
        default_start = default_end - timedelta(minutes=min(scope.max_window_minutes, 20))
    start = parse_iso(args.get("window_start")) if args.get("window_start") else default_start
    end = parse_iso(args.get("window_end")) if args.get("window_end") else default_end
    if start > end:
        raise ToolScopeError("Tool call window_start must be before window_end")
    if end - start > timedelta(minutes=scope.max_window_minutes):
        raise ToolScopeError("Tool call window exceeds configured maximum")
    outer_start = default_end - timedelta(minutes=scope.max_window_minutes)
    if start < outer_start or end > default_end + timedelta(seconds=1):
        raise ToolScopeError("Tool call window is outside incident bounds")
    return format_iso(start), format_iso(end)


def merge_tool_result_into_request(request: Any, tool_call: dict[str, Any]) -> Any:
    updated = request.model_dump(mode="json")
    name = tool_call["name"]
    result = tool_call.get("result")
    if name == "get_metrics" and isinstance(result, list):
        updated["metrics"].extend(result)
    elif name == "get_logs" and isinstance(result, list):
        updated["logs"].extend(result)
    elif name == "get_traces" and isinstance(result, list):
        updated["traces"].extend(result)
    elif name == "get_recent_deploys" and isinstance(result, list):
        updated["recent_deploys"].extend(result)
    elif name == "get_ownership" and isinstance(result, dict):
        updated["ownership"] = result
    elif name == "detect_metric_anomalies" and isinstance(result, list):
        updated["anomaly_evidence"].extend(result)
    elif name == "detect_log_anomalies" and isinstance(result, list):
        updated["anomaly_evidence"].extend(result)
    elif name == "infer_topology" and isinstance(result, dict):
        updated["service_topology"] = result
    elif name == "infer_causal_hints" and isinstance(result, list):
        updated["causal_hints"] = result
    elif name == "rank_rca_candidates" and isinstance(result, list):
        updated["rca_candidates"] = result
    return request.__class__.model_validate(updated)


def to_jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [to_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    return value


def parse_iso(value: str) -> datetime:
    text = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def format_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def iso_in_window(value: str, window: tuple[str, str]) -> bool:
    parsed = parse_iso(value)
    return parse_iso(window[0]) <= parsed <= parse_iso(window[1])


def parse_log_line(line: str) -> dict[str, Any]:
    try:
        parsed = json.loads(line)
        return parsed if isinstance(parsed, dict) else {"message": line}
    except json.JSONDecodeError:
        return {"message": line}


def load_json_file(path: str) -> Any:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def resolve_local_bundle_path(evidence_uri: str, base_path: str | None) -> Path | None:
    if not evidence_uri:
        return None
    if evidence_uri.startswith(("s3://", "http://", "https://")):
        return None
    raw_path = evidence_uri.removeprefix("file://")
    path = Path(raw_path)
    if path.is_absolute():
        return path
    if base_path:
        return Path(base_path) / path
    return path


def bundle_matches_scope(bundle: dict[str, Any], scope: ToolScope) -> bool:
    for key, expected in (
        ("tenant_id", scope.tenant_id),
        ("environment", scope.environment),
        ("service", scope.service),
    ):
        value = bundle.get(key)
        if value is not None and value != expected:
            return False
    for key in ("started_at", "received_at", "window_start", "window_end"):
        value = bundle.get(key)
        if isinstance(value, str) and not iso_in_window(value, (scope.started_at, scope.received_at)):
            return False
    return True


def find_jira_history_match(records: Any, service: str, environment: str, tenant_id: str) -> dict[str, Any] | None:
    candidates = records.get("mappings") if isinstance(records, dict) and isinstance(records.get("mappings"), list) else records
    if isinstance(records, dict) and records.get("service") == service:
        candidates = [records]
    if not isinstance(candidates, list):
        return None
    for record in candidates:
        if not isinstance(record, dict):
            continue
        if record.get("service") != service:
            continue
        if record.get("tenant_id") not in (None, tenant_id):
            continue
        if record.get("environment") not in (None, environment):
            continue
        return record
    return None


def filter_docs(records: list[Any], query: str, default_source: str) -> list[dict[str, Any]]:
    terms = [term for term in query.lower().split() if len(term) >= 3]
    results: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        title = str(record.get("title") or record.get("summary") or record.get("name") or "")
        excerpt = str(record.get("excerpt") or record.get("description") or record.get("notes") or "")
        text = f"{title} {excerpt} {record.get('tags', '')}".lower()
        if terms and not any(term in text for term in terms):
            continue
        results.append(
            {
                "title": title or "Untitled internal document",
                "url": record.get("url") or record.get("ref"),
                "excerpt": excerpt[:500],
                "source": record.get("source") or default_source,
            }
        )
    return results


def service_from_trace_process(process: dict[str, Any]) -> str | None:
    service_name = process.get("serviceName") or process.get("service_name")
    return str(service_name) if service_name else None


def first_parent_span_id(span: dict[str, Any]) -> str | None:
    references = span.get("references")
    if isinstance(references, list) and references:
        ref = references[0]
        if isinstance(ref, dict) and ref.get("spanID"):
            return str(ref["spanID"])
    return None


def trace_status(span: dict[str, Any]) -> str | None:
    tags = span.get("tags")
    if not isinstance(tags, list):
        return None
    for tag in tags:
        if isinstance(tag, dict) and tag.get("key") in {"error", "otel.status_code"}:
            return str(tag.get("value"))
    return None
