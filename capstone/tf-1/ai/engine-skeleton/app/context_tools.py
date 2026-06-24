from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import requests

from app import rca


READ_ONLY_TOOL_NAMES = {
    "get_metrics",
    "get_logs",
    "get_recent_deploys",
    "get_ownership",
    "detect_metric_anomalies",
    "detect_log_anomalies",
    "infer_topology",
    "infer_causal_hints",
    "rank_rca_candidates",
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
            "get_recent_deploys": self._get_recent_deploys,
            "get_ownership": self._get_ownership,
            "detect_metric_anomalies": self._detect_metric_anomalies,
            "detect_log_anomalies": self._detect_log_anomalies,
            "infer_topology": self._infer_topology,
            "infer_causal_hints": self._infer_causal_hints,
            "rank_rca_candidates": self._rank_rca_candidates,
        }

    @property
    def names(self) -> set[str]:
        return set(self._tools)

    def execute(self, name: str, args: dict[str, Any] | None, scope: ToolScope, request: Any | None = None) -> dict[str, Any]:
        if name not in self._tools:
            raise ToolScopeError(f"Unknown tool: {name}")
        args = args or {}
        bounded_scope = validate_and_build_scope(args, scope)
        result = self._tools[name](args, scope, request)
        return {
            "name": name,
            "status": "ok",
            "bounded_scope": bounded_scope,
            "result": to_jsonable(result),
        }

    def _get_metrics(self, args: dict[str, Any], scope: ToolScope, request: Any | None) -> list[dict[str, Any]]:
        window = bounded_window(args, scope)
        return self.client.get_metrics(scope.service, scope.environment, scope.tenant_id, window)

    def _get_logs(self, args: dict[str, Any], scope: ToolScope, request: Any | None) -> list[dict[str, Any]]:
        window = bounded_window(args, scope)
        limit = min(int(args.get("limit") or scope.log_limit), scope.log_limit)
        return self.client.get_logs(scope.service, scope.environment, scope.tenant_id, window, limit)

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


class ContextClient:
    def __init__(
        self,
        prometheus_url: str | None = None,
        loki_url: str | None = None,
        deploy_metadata_path: str | None = None,
        ownership_path: str | None = None,
    ) -> None:
        self.prometheus_url = prometheus_url or os.getenv("PROMETHEUS_URL", "http://localhost:9090")
        self.loki_url = loki_url or os.getenv("LOKI_URL", "http://localhost:3100")
        self.deploy_metadata_path = deploy_metadata_path or os.getenv("DEPLOY_METADATA_PATH")
        self.ownership_path = ownership_path or os.getenv("OWNERSHIP_PATH")
        self.timeout_seconds = int(os.getenv("LLM_TOOL_TIMEOUT_SECONDS", "10"))

    def get_metrics(self, service: str, environment: str, tenant_id: str, window: tuple[str, str]) -> list[dict[str, Any]]:
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
