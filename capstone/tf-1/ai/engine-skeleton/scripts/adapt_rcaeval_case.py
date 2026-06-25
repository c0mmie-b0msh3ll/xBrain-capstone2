from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any
from datetime import datetime, timezone


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def read_inject_time(case_dir: Path) -> str:
    inject_file = case_dir / "inject_time.txt"
    if not inject_file.exists():
        return "unknown"
    raw_value = inject_file.read_text(encoding="utf-8").strip()
    if raw_value.isdigit():
        return datetime.fromtimestamp(int(raw_value), tz=timezone.utc).isoformat().replace("+00:00", "Z")
    return raw_value


def infer_service_fault(case_dir: Path) -> tuple[str, str]:
    parts = case_dir.name.split("_")
    if len(parts) >= 3:
        return parts[1], parts[2]
    return case_dir.name, "unknown"


def metric_points_from_series(series: Any, inject_epoch: int | None = None) -> list[dict[str, float | str]]:
    points: list[dict[str, float | str]] = []
    if isinstance(series, list):
        selected = select_series_window(series, inject_epoch)
        for index, item in enumerate(selected):
            if isinstance(item, dict):
                ts = item.get("time") or item.get("timestamp") or str(index)
                value = item.get("value")
                if isinstance(value, int | float):
                    points.append({"ts": str(ts), "value": float(value)})
            elif isinstance(item, list | tuple) and len(item) >= 2 and isinstance(item[1], int | float):
                points.append({"ts": str(item[0]), "value": float(item[1])})
            elif isinstance(item, int | float):
                points.append({"ts": str(index), "value": float(item)})
    return points


def select_series_window(series: list[Any], inject_epoch: int | None) -> list[Any]:
    if inject_epoch is None:
        return series[-40:]

    before: list[Any] = []
    after: list[Any] = []
    for item in series:
        timestamp = None
        if isinstance(item, dict):
            timestamp = item.get("time") or item.get("timestamp")
        elif isinstance(item, list | tuple) and item:
            timestamp = item[0]
        try:
            ts = int(float(timestamp))
        except (TypeError, ValueError):
            continue
        if ts < inject_epoch:
            before.append(item)
        elif ts <= inject_epoch + 30:
            after.append(item)
    if len(before) >= 8 and after:
        return before[-30:] + after[:10]
    return series[-40:]


def convert_metrics(metrics: Any, default_service: str, fault: str, inject_epoch: int | None) -> list[dict[str, Any]]:
    converted: list[dict[str, Any]] = []
    if isinstance(metrics, dict):
        selected_items = sorted(
            metrics.items(),
            key=lambda item: metric_priority(item[0], default_service, fault),
        )[:12]
        for metric_name, series in selected_items:
            points = metric_points_from_series(series, inject_epoch)
            if points:
                service = metric_name.split("_")[0] if "_" in metric_name else default_service
                converted.append(
                    {
                        "metric_name": metric_name,
                        "service": service,
                        "unit": None,
                        "points": points,
                        "labels": {"source_dataset": "RCAEval", "fault": fault},
                    }
                )
    elif isinstance(metrics, list):
        grouped: dict[str, list[dict[str, float | str]]] = {}
        service_by_metric: dict[str, str] = {}
        for index, row in enumerate(metrics[:200]):
            if not isinstance(row, dict):
                continue
            metric_name = str(row.get("metric_name") or row.get("name") or row.get("metric") or "metric")
            value = row.get("value")
            if not isinstance(value, int | float):
                continue
            ts = str(row.get("time") or row.get("timestamp") or index)
            grouped.setdefault(metric_name, []).append({"ts": ts, "value": float(value)})
            service_by_metric[metric_name] = str(row.get("service") or default_service)
        for metric_name, points in list(grouped.items())[:8]:
            converted.append(
                {
                    "metric_name": metric_name,
                    "service": service_by_metric.get(metric_name, default_service),
                    "unit": None,
                    "points": points[:20],
                "labels": {"source_dataset": "RCAEval", "fault": fault},
                }
            )
    return converted


def metric_priority(metric_name: str, default_service: str, fault: str) -> tuple[int, str]:
    name = metric_name.lower()
    score = 0
    if name.startswith(default_service.lower()):
        score -= 30
    if fault.lower() in name:
        score -= 20
    if any(token in name for token in ["latency", "duration", "error", "request", "throughput", "cpu", "mem", "disk"]):
        score -= 10
    return score, metric_name


def parse_epoch(value: str) -> int | None:
    if value.isdigit():
        return int(value)
    try:
        normalized = value.replace("Z", "+00:00")
        return int(datetime.fromisoformat(normalized).timestamp())
    except ValueError:
        return None


def convert_logs(case_dir: Path, default_service: str) -> list[dict[str, Any]]:
    logs_file = case_dir / "logs.csv"
    if not logs_file.exists():
        return []
    logs: list[dict[str, Any]] = []
    with logs_file.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for index, row in enumerate(reader):
            if index >= 20:
                break
            message = row.get("message") or row.get("log") or row.get("content") or str(row)
            logs.append(
                {
                    "service": row.get("service") or default_service,
                    "ts": row.get("time") or row.get("timestamp") or str(index),
                    "level": row.get("level") or "info",
                    "message": message,
                    "trace_id": row.get("trace_id") or row.get("traceId"),
                    "labels": {"source_dataset": "RCAEval"},
                }
            )
    return logs


def parse_duration_ms(row: dict[str, str]) -> float | None:
    for key in ("duration_ms", "durationMs", "duration", "latency_ms", "elapsed_ms"):
        value = row.get(key)
        if value is None or value == "":
            continue
        try:
            number = float(value)
        except ValueError:
            continue
        if key == "duration" and number > 1_000_000:
            return number / 1_000_000
        return number
    return None


def convert_traces(case_dir: Path, default_service: str) -> list[dict[str, Any]]:
    traces_file = case_dir / "traces.csv"
    if not traces_file.exists():
        return []
    traces: list[dict[str, Any]] = []
    seen: set[str] = set()
    with traces_file.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for index, row in enumerate(reader):
            if len(traces) >= 20:
                break
            trace_id = row.get("trace_id") or row.get("traceId") or row.get("traceID") or f"{case_dir.name}-trace-{index}"
            span_id = row.get("span_id") or row.get("spanId") or row.get("spanID") or str(index)
            unique_key = f"{trace_id}:{span_id}"
            if unique_key in seen:
                continue
            seen.add(unique_key)
            service = row.get("service") or row.get("serviceName") or row.get("process.serviceName") or default_service
            status = row.get("status") or row.get("status_code") or row.get("statusCode") or row.get("error") or "unknown"
            traces.append(
                {
                    "trace_id": trace_id,
                    "span_id": span_id,
                    "parent_span_id": row.get("parent_span_id") or row.get("parentSpanId"),
                    "service": service,
                    "operation": row.get("operation") or row.get("operationName") or row.get("name") or row.get("span_name"),
                    "ts": row.get("time") or row.get("timestamp") or row.get("startTime") or str(index),
                    "duration_ms": parse_duration_ms(row),
                    "status": str(status),
                    "labels": {"source_dataset": "RCAEval"},
                }
            )
    return traces


def scenario_alert_fields(scenario: str | None, service: str, fault: str) -> dict[str, str]:
    if scenario == "latency-degradation":
        return {
            "severity": "high",
            "title": f"High p95 latency on {service} from RCAEval {fault} fault",
            "description": "RCAEval delay-like fault adapted into the TF1 latency degradation scenario.",
        }
    if scenario == "critical-service-down":
        return {
            "severity": "critical",
            "title": f"{service} is down from RCAEval {fault} fault",
            "description": "RCAEval loss-like fault adapted into the TF1 critical service down scenario.",
        }
    if scenario == "noisy-false-alert":
        return {
            "severity": "low",
            "title": f"Noisy flapping alert on {service} from RCAEval {fault} case",
            "description": "RCAEval resource/code fault used as ambiguous external telemetry for the TF1 noisy alert scenario.",
        }
    return {
        "severity": "high",
        "title": f"RCAEval {fault} fault on {service}",
        "description": "External RCAEval case adapted into TF1 triage contract.",
    }


def build_triage_request(case_dir: Path, scenario: str | None = None) -> dict[str, Any]:
    service, fault = infer_service_fault(case_dir)
    inject_time = read_inject_time(case_dir)
    inject_epoch = parse_epoch(inject_time)
    metrics_path = case_dir / "metrics.json"
    metrics = convert_metrics(load_json(metrics_path), service, fault, inject_epoch) if metrics_path.exists() else []
    logs = convert_logs(case_dir, service)
    traces = convert_traces(case_dir, service)
    alert_fields = scenario_alert_fields(scenario, service, fault)

    return {
        "correlation_id": f"rcaeval-{case_dir.name}",
        "tenant_id": "tenant-a",
        "incident_id": f"rcaeval-{case_dir.name}",
        "environment": "sandbox",
        "received_at": inject_time,
        "alert": {
            "alert_id": f"rcaeval-{case_dir.name}",
            "source": "RCAEval",
            "service": service,
            "severity": alert_fields["severity"],
            "title": alert_fields["title"],
            "description": alert_fields["description"],
            "started_at": inject_time,
            "labels": {"dataset": "RCAEval", "fault": fault, "case": case_dir.name, "mapped_scenario": scenario},
        },
        "metrics": metrics,
        "logs": logs,
        "traces": traces,
        "recent_deploys": [],
        "ownership": {
            "service": service,
            "owner_team": "dataset-owner",
            "slack_channel": "#aiops-demo",
            "jira_project": "AIOPS",
            "runbooks": [
                {
                    "title": "RCAEval case investigation",
                    "url": "runbook://rcaeval-case",
                    "excerpt": "Review root-cause service, root-cause indicator, metrics, logs, and traces from the RCAEval case.",
                }
            ],
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Adapt one RCAEval case into TF1 /v1/triage request JSON.")
    parser.add_argument("--case-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--scenario", choices=["latency-degradation", "critical-service-down", "noisy-false-alert"])
    args = parser.parse_args()

    request = build_triage_request(args.case_dir, args.scenario)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(request, indent=2), encoding="utf-8")
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
