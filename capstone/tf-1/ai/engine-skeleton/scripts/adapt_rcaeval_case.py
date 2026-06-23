from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def read_inject_time(case_dir: Path) -> str:
    inject_file = case_dir / "inject_time.txt"
    if not inject_file.exists():
        return "unknown"
    return inject_file.read_text(encoding="utf-8").strip()


def infer_service_fault(case_dir: Path) -> tuple[str, str]:
    parts = case_dir.name.split("_")
    if len(parts) >= 3:
        return parts[1], parts[2]
    return case_dir.name, "unknown"


def metric_points_from_series(series: Any) -> list[dict[str, float | str]]:
    points: list[dict[str, float | str]] = []
    if isinstance(series, list):
        for index, item in enumerate(series[:20]):
            if isinstance(item, dict):
                ts = item.get("time") or item.get("timestamp") or str(index)
                value = item.get("value")
                if isinstance(value, int | float):
                    points.append({"ts": str(ts), "value": float(value)})
            elif isinstance(item, int | float):
                points.append({"ts": str(index), "value": float(item)})
    return points


def convert_metrics(metrics: Any, default_service: str) -> list[dict[str, Any]]:
    converted: list[dict[str, Any]] = []
    if isinstance(metrics, dict):
        for metric_name, series in list(metrics.items())[:8]:
            points = metric_points_from_series(series)
            if points:
                service = metric_name.split("_")[0] if "_" in metric_name else default_service
                converted.append(
                    {
                        "metric_name": metric_name,
                        "service": service,
                        "unit": None,
                        "points": points,
                        "labels": {"source_dataset": "RCAEval"},
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
                    "labels": {"source_dataset": "RCAEval"},
                }
            )
    return converted


def convert_logs(case_dir: Path, default_service: str) -> list[dict[str, Any]]:
    logs_file = case_dir / "logs.csv"
    if not logs_file.exists():
        return []
    logs: list[dict[str, Any]] = []
    with logs_file.open("r", encoding="utf-8", newline="") as handle:
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


def build_triage_request(case_dir: Path) -> dict[str, Any]:
    service, fault = infer_service_fault(case_dir)
    inject_time = read_inject_time(case_dir)
    metrics_path = case_dir / "metrics.json"
    metrics = convert_metrics(load_json(metrics_path), service) if metrics_path.exists() else []
    logs = convert_logs(case_dir, service)

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
            "severity": "high",
            "title": f"RCAEval {fault} fault on {service}",
            "description": "External RCAEval case adapted into TF1 triage contract.",
            "started_at": inject_time,
            "labels": {"dataset": "RCAEval", "fault": fault, "case": case_dir.name},
        },
        "metrics": metrics,
        "logs": logs,
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
    args = parser.parse_args()

    request = build_triage_request(args.case_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(request, indent=2), encoding="utf-8")
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
