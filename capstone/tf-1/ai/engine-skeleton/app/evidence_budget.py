from __future__ import annotations

import json
import os
from copy import deepcopy
from typing import Any

from app.observability import EVIDENCE_TRUNCATION_TOTAL


DEFAULT_MAX_EVIDENCE_BYTES = 262144
DEFAULT_MAX_METRIC_SERIES = 20
DEFAULT_MAX_METRIC_POINTS_PER_SERIES = 120
DEFAULT_MAX_LOG_RECORDS = 50
DEFAULT_MAX_TRACE_RECORDS = 20
DEFAULT_MAX_LOG_MESSAGE_CHARS = 500
DEFAULT_MAX_TRACE_LABEL_BYTES = 2048

ERROR_TOKENS = ("error", "timeout", "failed", "refused", "exhausted", "down", "deadline", "critical")


def compact_request_evidence(request: Any) -> tuple[Any, dict[str, Any]]:
    body = request.model_dump(mode="json")
    compacted, metadata = compact_evidence_body(body)
    if not metadata["truncated"]:
        return request, metadata
    return request.__class__.model_validate(compacted), metadata


def compact_evidence_body(body: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    compacted = deepcopy(body)
    before_counts = evidence_counts(compacted)
    before_bytes = evidence_bytes(compacted)
    reasons: list[str] = []

    metrics, reason = compact_metrics(compacted.get("metrics", []))
    compacted["metrics"] = metrics
    if reason:
        reasons.extend(reason)

    logs, reason = compact_logs(compacted.get("logs", []))
    compacted["logs"] = logs
    if reason:
        reasons.extend(reason)

    traces, reason = compact_traces(compacted.get("traces", []))
    compacted["traces"] = traces
    if reason:
        reasons.extend(reason)

    max_bytes = env_int("AIOPS_MAX_EVIDENCE_BYTES", DEFAULT_MAX_EVIDENCE_BYTES)
    while evidence_bytes(compacted) > max_bytes and (compacted["logs"] or compacted["traces"] or compacted["metrics"]):
        if compacted["logs"]:
            compacted["logs"] = compacted["logs"][:-1]
            reasons.append("max_evidence_bytes_logs")
        elif compacted["traces"]:
            compacted["traces"] = compacted["traces"][:-1]
            reasons.append("max_evidence_bytes_traces")
        elif compacted["metrics"]:
            last = compacted["metrics"][-1]
            points = last.get("points") if isinstance(last, dict) else None
            if isinstance(points, list) and len(points) > 1:
                last["points"] = points[len(points) // 2 :]
                reasons.append("max_evidence_bytes_metric_points")
            else:
                compacted["metrics"] = compacted["metrics"][:-1]
                reasons.append("max_evidence_bytes_metrics")

    after_counts = evidence_counts(compacted)
    after_bytes = evidence_bytes(compacted)
    truncated = bool(reasons) or after_bytes < before_bytes or after_counts != before_counts
    if truncated:
        for evidence_type, before in before_counts.items():
            if after_counts.get(evidence_type, 0) < before:
                EVIDENCE_TRUNCATION_TOTAL.labels(type=evidence_type, reason="count_cap").inc()
        if after_bytes < before_bytes:
            EVIDENCE_TRUNCATION_TOTAL.labels(type="all", reason="byte_cap").inc()

    return compacted, {
        "truncated": truncated,
        "counts_before": before_counts,
        "counts_after": after_counts,
        "bytes_before": before_bytes,
        "bytes_after": after_bytes,
        "reasons": sorted(set(reasons)),
    }


def compact_tool_result(tool_name: str, result: Any) -> tuple[Any, dict[str, Any]]:
    pseudo_body = {"metrics": [], "logs": [], "traces": []}
    field = tool_result_field(tool_name)
    if field and isinstance(result, list):
        pseudo_body[field] = result
        compacted, metadata = compact_evidence_body(pseudo_body)
        return compacted[field], metadata
    return result, {
        "truncated": False,
        "counts_before": {},
        "counts_after": {},
        "bytes_before": json_bytes(result),
        "bytes_after": json_bytes(result),
        "reasons": [],
    }


def compact_metrics(metrics: Any) -> tuple[list[dict[str, Any]], list[str]]:
    if not isinstance(metrics, list):
        return [], []
    reasons: list[str] = []
    max_series = env_int("AIOPS_MAX_METRIC_SERIES", DEFAULT_MAX_METRIC_SERIES)
    max_points = env_int("AIOPS_MAX_METRIC_POINTS_PER_SERIES", DEFAULT_MAX_METRIC_POINTS_PER_SERIES)
    ranked = sorted(
        [metric for metric in metrics if isinstance(metric, dict)],
        key=lambda item: (metric_signal_score(item), str(item.get("metric_name", ""))),
        reverse=True,
    )
    if len(ranked) > max_series:
        reasons.append("max_metric_series")
        ranked = ranked[:max_series]
    compacted: list[dict[str, Any]] = []
    for metric in ranked:
        item = dict(metric)
        points = item.get("points")
        if isinstance(points, list) and len(points) > max_points:
            item["points"] = sorted(points, key=lambda point: str(point.get("ts", "")) if isinstance(point, dict) else "")[-max_points:]
            reasons.append("max_metric_points_per_series")
        compacted.append(item)
    return compacted, reasons


def compact_logs(logs: Any) -> tuple[list[dict[str, Any]], list[str]]:
    if not isinstance(logs, list):
        return [], []
    reasons: list[str] = []
    max_records = env_int("AIOPS_MAX_LOG_RECORDS", DEFAULT_MAX_LOG_RECORDS)
    max_chars = env_int("AIOPS_MAX_LOG_MESSAGE_CHARS", DEFAULT_MAX_LOG_MESSAGE_CHARS)
    ranked = sorted(
        [log for log in logs if isinstance(log, dict)],
        key=lambda item: (log_signal_score(item), str(item.get("ts", ""))),
        reverse=True,
    )
    if len(ranked) > max_records:
        reasons.append("max_log_records")
        ranked = ranked[:max_records]
    compacted: list[dict[str, Any]] = []
    for log in ranked:
        item = dict(log)
        message = item.get("message")
        if isinstance(message, str) and len(message) > max_chars:
            item["message"] = message[:max_chars]
            reasons.append("max_log_message_chars")
        compacted.append(item)
    return compacted, reasons


def compact_traces(traces: Any) -> tuple[list[dict[str, Any]], list[str]]:
    if not isinstance(traces, list):
        return [], []
    reasons: list[str] = []
    max_records = env_int("AIOPS_MAX_TRACE_RECORDS", DEFAULT_MAX_TRACE_RECORDS)
    max_label_bytes = env_int("AIOPS_MAX_TRACE_LABEL_BYTES", DEFAULT_MAX_TRACE_LABEL_BYTES)
    ranked = sorted(
        [trace for trace in traces if isinstance(trace, dict)],
        key=lambda item: (trace_signal_score(item), str(item.get("ts", ""))),
        reverse=True,
    )
    if len(ranked) > max_records:
        reasons.append("max_trace_records")
        ranked = ranked[:max_records]
    compacted: list[dict[str, Any]] = []
    for trace in ranked:
        item = dict(trace)
        labels = item.get("labels")
        if isinstance(labels, dict) and json_bytes(labels) > max_label_bytes:
            item["labels"] = shrink_mapping(labels, max_label_bytes)
            reasons.append("max_trace_label_bytes")
        compacted.append(item)
    return compacted, reasons


def evidence_counts(body: dict[str, Any]) -> dict[str, int]:
    metrics = body.get("metrics", [])
    logs = body.get("logs", [])
    traces = body.get("traces", [])
    metric_points = 0
    if isinstance(metrics, list):
        for metric in metrics:
            points = metric.get("points") if isinstance(metric, dict) else None
            metric_points += len(points) if isinstance(points, list) else 0
    return {
        "metrics": len(metrics) if isinstance(metrics, list) else 0,
        "metric_points": metric_points,
        "logs": len(logs) if isinstance(logs, list) else 0,
        "traces": len(traces) if isinstance(traces, list) else 0,
    }


def evidence_bytes(body: dict[str, Any]) -> int:
    return json_bytes(
        {
            "metrics": body.get("metrics", []),
            "logs": body.get("logs", []),
            "traces": body.get("traces", []),
        }
    )


def json_bytes(value: Any) -> int:
    return len(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=True).encode("utf-8"))


def tool_result_field(tool_name: str) -> str | None:
    return {
        "get_metrics": "metrics",
        "get_logs": "logs",
        "get_traces": "traces",
    }.get(tool_name)


def metric_signal_score(metric: dict[str, Any]) -> int:
    name = str(metric.get("metric_name", "")).lower()
    score = sum(2 for token in ERROR_TOKENS if token in name)
    points = metric.get("points")
    if isinstance(points, list) and points:
        score += 1
    return score


def log_signal_score(log: dict[str, Any]) -> int:
    text = f"{log.get('level', '')} {log.get('message', '')}".lower()
    return sum(2 for token in ERROR_TOKENS if token in text)


def trace_signal_score(trace: dict[str, Any]) -> float:
    status = str(trace.get("status", "")).lower()
    duration = trace.get("duration_ms")
    score = 2.0 if any(token in status for token in ("error", "failed", "timeout")) else 0.0
    if isinstance(duration, (int, float)):
        score += min(float(duration) / 1000.0, 10.0)
    return score


def shrink_mapping(labels: dict[str, Any], max_bytes: int) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in sorted(labels):
        candidate = dict(result)
        candidate[str(key)] = labels[key]
        if json_bytes(candidate) > max_bytes:
            break
        result[str(key)] = labels[key]
    return result


def env_int(name: str, default: int) -> int:
    try:
        return max(0, int(os.getenv(name, str(default))))
    except ValueError:
        return default
