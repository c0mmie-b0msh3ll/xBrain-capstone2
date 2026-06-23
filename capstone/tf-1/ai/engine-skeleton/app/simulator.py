from __future__ import annotations

import argparse
import json
import os
import signal
import time
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import Gauge, start_http_server


METRIC_GAUGE = Gauge(
    "aiops_scenario_metric_value",
    "Sanitized replay metric value emitted by the TF1 telemetry simulator.",
    ["tenant_id", "service", "environment", "scenario", "metric_name", "unit", "region"],
)

REQUESTS_GAUGE = Gauge(
    "aiops_http_requests_total",
    "Synthetic request volume generated during scenario replay.",
    ["tenant_id", "service", "environment", "scenario", "phase"],
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay sanitized RCA scenarios as near-realtime telemetry.")
    parser.add_argument("--datapack-root", default="datapack/scenarios")
    parser.add_argument("--scenario", default=os.getenv("SIM_SCENARIO", "latency-degradation"))
    parser.add_argument("--tenant-id", default=os.getenv("SIM_TENANT_ID"))
    parser.add_argument("--service", default=os.getenv("SIM_SERVICE"))
    parser.add_argument("--environment", default=os.getenv("SIM_ENVIRONMENT"))
    parser.add_argument("--speed", type=float, default=float(os.getenv("SIM_SPEED", "1.0")))
    parser.add_argument("--loop", action="store_true", default=os.getenv("SIM_LOOP", "false").lower() == "true")
    parser.add_argument("--metrics-port", type=int, default=int(os.getenv("SIM_METRICS_PORT", "9108")))
    parser.add_argument("--loki-url", default=os.getenv("LOKI_URL", "http://localhost:3100"))
    parser.add_argument("--otlp-endpoint", default=os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"))
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def scenario_dir(datapack_root: str, scenario: str) -> Path:
    path = Path(datapack_root) / scenario
    if not path.exists():
        raise FileNotFoundError(f"Scenario directory not found: {path}")
    return path


def apply_overrides(items: Iterable[dict[str, Any]], args: argparse.Namespace) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for item in items:
        copied = dict(item)
        if args.tenant_id:
            copied["tenant_id"] = args.tenant_id
        if args.service:
            copied["service"] = args.service
        if args.environment:
            copied["environment"] = args.environment
        results.append(copied)
    return results


def configure_tracing(endpoint: str | None, scenario: str) -> trace.Tracer:
    resource = Resource.create({"service.name": "tf1-telemetry-simulator", "aiops.scenario": scenario})
    provider = TracerProvider(resource=resource)
    if endpoint:
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    trace.set_tracer_provider(provider)
    return trace.get_tracer("tf1.telemetry_simulator")


def phase_for_metric(metric: dict[str, Any]) -> str:
    labels = metric.get("labels") or {}
    return str(labels.get("window") or labels.get("phase") or "current")


def emit_metric(metric: dict[str, Any], scenario: str) -> None:
    METRIC_GAUGE.labels(
        tenant_id=metric["tenant_id"],
        service=metric["service"],
        environment=metric["environment"],
        scenario=scenario,
        metric_name=metric["metric_name"],
        unit=metric.get("unit") or "value",
        region=metric.get("region") or "unknown",
    ).set(float(metric["value"]))
    REQUESTS_GAUGE.labels(
        tenant_id=metric["tenant_id"],
        service=metric["service"],
        environment=metric["environment"],
        scenario=scenario,
        phase=phase_for_metric(metric),
    ).inc(25)


def loki_payload(log: dict[str, Any], scenario: str) -> dict[str, Any]:
    labels = {
        "job": "tf1-telemetry-simulator",
        "tenant_id": log["tenant_id"],
        "service": log["service"],
        "environment": log["environment"],
        "scenario": scenario,
        "level": str(log.get("level", "info")).lower(),
    }
    line = json.dumps(
        {
            "timestamp": log.get("timestamp") or utc_now(),
            "service": log["service"],
            "level": log.get("level", "info"),
            "message": log["message"],
            "trace_id": log.get("trace_id"),
            "labels": log.get("labels", {}),
        },
        separators=(",", ":"),
    )
    return {"streams": [{"stream": labels, "values": [[str(time.time_ns()), line]]}]}


def emit_log(log: dict[str, Any], scenario: str, loki_url: str, dry_run: bool) -> None:
    if dry_run:
        print(json.dumps({"type": "log", "payload": loki_payload(log, scenario)}, indent=2))
        return
    response = requests.post(f"{loki_url.rstrip('/')}/loki/api/v1/push", json=loki_payload(log, scenario), timeout=5)
    response.raise_for_status()


def emit_trace(log: dict[str, Any], scenario: str, tracer: trace.Tracer) -> None:
    attributes = {
        "tenant_id": log["tenant_id"],
        "service": log["service"],
        "environment": log["environment"],
        "scenario": scenario,
        "log.level": log.get("level", "info"),
        "log.message": log["message"],
    }
    if log.get("trace_id"):
        attributes["trace_id"] = log["trace_id"]
    with tracer.start_as_current_span(f"{log['service']} synthetic request", attributes=attributes):
        time.sleep(0.03)


def replay_once(args: argparse.Namespace, tracer: trace.Tracer) -> None:
    root = scenario_dir(args.datapack_root, args.scenario)
    metrics = apply_overrides(load_json(root / "raw-metrics.json"), args)
    logs = apply_overrides(load_json(root / "raw-logs.json"), args)

    for metric in metrics:
        emit_metric(metric, args.scenario)
        print(f"[{utc_now()}] metric {metric['service']} {metric['metric_name']}={metric['value']}")
        time.sleep(max(0.1, 1.0 / max(args.speed, 0.1)))

    for log in logs:
        emit_trace(log, args.scenario, tracer)
        emit_log(log, args.scenario, args.loki_url, args.dry_run)
        print(f"[{utc_now()}] log {log['service']} {log.get('level', 'info')} {log['message']}")
        time.sleep(max(0.1, 1.0 / max(args.speed, 0.1)))


def main() -> None:
    args = parse_args()
    tracer = configure_tracing(args.otlp_endpoint, args.scenario)
    start_http_server(args.metrics_port)
    print(f"Simulator metrics endpoint listening on :{args.metrics_port}/metrics")

    stop = False

    def handle_stop(_signum: int, _frame: Any) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, handle_stop)
    signal.signal(signal.SIGTERM, handle_stop)

    while not stop:
        replay_once(args, tracer)
        if not args.loop:
            break
        time.sleep(max(1.0, 5.0 / max(args.speed, 0.1)))


if __name__ == "__main__":
    main()
