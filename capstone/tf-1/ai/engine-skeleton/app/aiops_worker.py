from __future__ import annotations

import argparse
import json
import os
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests
from pydantic import ValidationError

from app.context_tools import ToolRegistry
from app.incident_seed import IncidentSeed, build_triage_request_from_seed
from app.report_store import write_report


DEFAULT_PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://localhost:9090")
DEFAULT_LOKI_URL = os.getenv("LOKI_URL", "http://localhost:3100")
DEFAULT_JAEGER_URL = os.getenv("JAEGER_URL", "http://localhost:16686")
DEFAULT_TRIAGE_URL = os.getenv("TRIAGE_URL", "http://localhost:8080/v1/triage")
DEFAULT_REPORT_BASE_URL = os.getenv("REPORT_BASE_URL", "http://localhost:5173/#/reports")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Query observability backends, detect anomalies, and call /v1/triage.")
    parser.add_argument("--scenario", default=os.getenv("AIOPS_SCENARIO", "latency-degradation"))
    parser.add_argument("--datapack-root", default="datapack/scenarios")
    parser.add_argument("--tenant-id", default=os.getenv("TENANT_ID", "tenant-a"))
    parser.add_argument("--service", default=os.getenv("SERVICE_NAME"))
    parser.add_argument("--environment", default=os.getenv("ENVIRONMENT", "sandbox"))
    parser.add_argument("--prometheus-url", default=DEFAULT_PROMETHEUS_URL)
    parser.add_argument("--loki-url", default=DEFAULT_LOKI_URL)
    parser.add_argument("--jaeger-url", default=DEFAULT_JAEGER_URL)
    parser.add_argument("--triage-url", default=DEFAULT_TRIAGE_URL)
    parser.add_argument("--report-dir", default=os.getenv("REPORTS_DIR", "reports"))
    parser.add_argument("--report-base-url", default=DEFAULT_REPORT_BASE_URL)
    parser.add_argument("--window-minutes", type=int, default=20)
    parser.add_argument("--poll-seconds", type=int, default=0)
    parser.add_argument("--offline-scenario", action="store_true")
    parser.add_argument("--dry-run-slack", action="store_true", default=os.getenv("SLACK_WEBHOOK_URL") is None)
    parser.add_argument("--sqs-queue-url", default=os.getenv("SQS_QUEUE_URL"))
    parser.add_argument("--sqs-wait-seconds", type=int, default=int(os.getenv("SQS_WAIT_SECONDS", "5")))
    parser.add_argument("--sqs-max-messages", type=int, default=int(os.getenv("SQS_MAX_MESSAGES", "1")))
    parser.add_argument("--sqs-region", default=os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1")
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def scenario_root(datapack_root: str, scenario: str) -> Path:
    path = Path(datapack_root) / scenario
    if not path.exists():
        raise FileNotFoundError(f"Scenario not found: {path}")
    return path


def prom_query(base_url: str, query: str) -> list[dict[str, Any]]:
    response = requests.get(f"{base_url.rstrip('/')}/api/v1/query", params={"query": query}, timeout=5)
    response.raise_for_status()
    payload = response.json()
    if payload.get("status") != "success":
        raise RuntimeError(f"Prometheus query failed: {payload}")
    return payload.get("data", {}).get("result", [])


def loki_query(base_url: str, query: str, limit: int = 20) -> list[dict[str, Any]]:
    response = requests.get(
        f"{base_url.rstrip('/')}/loki/api/v1/query_range",
        params={"query": query, "limit": str(limit), "direction": "backward"},
        timeout=5,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("status") != "success":
        raise RuntimeError(f"Loki query failed: {payload}")
    return payload.get("data", {}).get("result", [])


def jaeger_query(base_url: str, service: str, lookback: str = "1h", limit: int = 20) -> list[dict[str, Any]]:
    response = requests.get(
        f"{base_url.rstrip('/')}/api/traces",
        params={"service": service, "lookback": lookback, "limit": str(limit)},
        timeout=5,
    )
    response.raise_for_status()
    return response.json().get("data", [])


def query_metric_points(args: argparse.Namespace) -> list[dict[str, Any]]:
    service_filter = f',service="{args.service}"' if args.service else ""
    query = (
        'aiops_scenario_metric_value{'
        f'tenant_id="{args.tenant_id}",environment="{args.environment}"{service_filter}'
        "}"
    )
    results = prom_query(args.prometheus_url, query)
    metrics: list[dict[str, Any]] = []
    for result in results:
        labels = result.get("metric", {})
        value = result.get("value", [None, 0])[1]
        metrics.append(
            {
                "tenant_id": labels.get("tenant_id", args.tenant_id),
                "service": labels.get("service", args.service or "unknown"),
                "environment": labels.get("environment", args.environment),
                "region": labels.get("region", "unknown"),
                "timestamp": utc_now(),
                "source": "prometheus",
                "metric_name": labels.get("metric_name", "unknown_metric"),
                "value": float(value),
                "unit": labels.get("unit"),
                "labels": {"scenario": labels.get("scenario", args.scenario)},
            }
        )
    return metrics


def query_logs(args: argparse.Namespace) -> list[dict[str, Any]]:
    service_filter = f',service="{args.service}"' if args.service else ""
    query = f'{{tenant_id="{args.tenant_id}",environment="{args.environment}"{service_filter}}} |~ "(?i)(error|timeout|failed|refused|exhausted|down)"'
    streams = loki_query(args.loki_url, query, limit=25)
    logs: list[dict[str, Any]] = []
    for stream in streams:
        labels = stream.get("stream", {})
        for _, line in stream.get("values", []):
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                parsed = {"message": line}
            logs.append(
                {
                    "tenant_id": labels.get("tenant_id", args.tenant_id),
                    "service": labels.get("service", args.service or parsed.get("service", "unknown")),
                    "environment": labels.get("environment", args.environment),
                    "timestamp": parsed.get("timestamp") or utc_now(),
                    "source": "loki",
                    "level": parsed.get("level") or labels.get("level", "error"),
                    "message": parsed.get("message", ""),
                    "trace_id": parsed.get("trace_id"),
                    "labels": parsed.get("labels", {}),
                }
            )
    return logs


def offline_raw_observability(args: argparse.Namespace) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    root = scenario_root(args.datapack_root, args.scenario)
    metrics = load_json(root / "raw-metrics.json")
    logs = load_json(root / "raw-logs.json")
    deploys = load_json(root / "deploy-events.json")
    return metrics, logs, deploys


def score_metric(metric: dict[str, Any]) -> tuple[int, str | None]:
    name = metric["metric_name"].lower()
    value = float(metric["value"])
    if "latency" in name and value >= 1000:
        return 3, f"{metric['metric_name']}={value:g}{metric.get('unit') or ''}"
    if ("error" in name or "5xx" in name) and value >= 5:
        return 3, f"{metric['metric_name']}={value:g}{metric.get('unit') or ''}"
    if "availability" in name and value < 95:
        return 4, f"{metric['metric_name']} dropped to {value:g}{metric.get('unit') or ''}"
    if "timeout" in name and value >= 10:
        return 3, f"{metric['metric_name']}={value:g}{metric.get('unit') or ''}"
    if ("cpu" in name or "memory" in name) and value >= 85:
        return 2, f"{metric['metric_name']}={value:g}{metric.get('unit') or ''}"
    return 0, None


def detect_incident(metrics: list[dict[str, Any]], logs: list[dict[str, Any]]) -> dict[str, Any] | None:
    scores: dict[str, int] = defaultdict(int)
    evidence: dict[str, list[str]] = defaultdict(list)
    for metric in metrics:
        score, reason = score_metric(metric)
        if score:
            service = metric["service"]
            scores[service] += score
            evidence[service].append(reason or metric["metric_name"])

    for log in logs:
        text = f"{log.get('level', '')} {log.get('message', '')}".lower()
        if any(token in text for token in ["error", "timeout", "failed", "refused", "exhausted", "down"]):
            service = log["service"]
            scores[service] += 1
            evidence[service].append(log.get("message", "error log"))

    for item in detect_raw_statistical_evidence(metrics):
        service = item["service"]
        scores[service] += item["weight"]
        evidence[service].append(item["reason"])

    if not scores:
        return None

    service = max(scores, key=scores.get)
    if scores[service] < 3:
        return None
    severity = "critical" if any("availability" in item or "down" in item.lower() for item in evidence[service]) else "high"
    return {"service": service, "severity": severity, "score": scores[service], "evidence": evidence[service][:5]}


def detect_raw_statistical_evidence(metrics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for metric in metrics:
        grouped[(metric["service"], metric["metric_name"])].append(metric)

    evidence: list[dict[str, Any]] = []
    for (service, metric_name), points in grouped.items():
        values = [float(point["value"]) for point in points]
        if len(values) < 2:
            continue
        baseline = values[:-1]
        current = values[-1]
        baseline_avg = sum(baseline) / len(baseline)
        spread = max(abs(baseline_avg) * 0.1, 1.0)
        if len(baseline) > 1:
            spread = max((sum((value - baseline_avg) ** 2 for value in baseline) / len(baseline)) ** 0.5, spread)
        z_score = (current - baseline_avg) / spread
        if abs(z_score) >= 3.0:
            evidence.append(
                {
                    "service": service,
                    "weight": 2,
                    "reason": f"{metric_name} current value {current:g} is {z_score:.1f} sigma from baseline",
                }
            )
    return evidence


def group_metrics(metrics: list[dict[str, Any]], service: str) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for metric in metrics:
        if metric["service"] != service:
            continue
        key = (metric["metric_name"], metric.get("unit") or "")
        entry = grouped.setdefault(
            key,
            {
                "metric_name": metric["metric_name"],
                "service": service,
                "unit": metric.get("unit"),
                "points": [],
                "labels": {"region": metric.get("region", "unknown")},
            },
        )
        entry["points"].append({"ts": metric.get("timestamp") or utc_now(), "value": float(metric["value"])})
    return list(grouped.values())


def normalize_logs(logs: list[dict[str, Any]], service: str) -> list[dict[str, Any]]:
    selected = [log for log in logs if log["service"] == service][:10]
    return [
        {
            "service": log["service"],
            "ts": log.get("timestamp") or log.get("ts") or utc_now(),
            "level": str(log.get("level", "error")).lower(),
            "message": log.get("message", ""),
            "trace_id": log.get("trace_id"),
            "labels": log.get("labels", {}),
        }
        for log in selected
    ]


def normalize_deploys(deploys: list[dict[str, Any]], service: str) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for deploy in deploys:
        if deploy.get("service") != service:
            continue
        results.append(
            {
                "service": service,
                "version": deploy.get("version", deploy.get("deploy_id", "unknown")),
                "deployed_at": deploy.get("deployed_at") or deploy.get("timestamp") or utc_now(),
                "deployed_by": deploy.get("deployed_by", "ci"),
                "change_summary": deploy.get("change_summary"),
                "rollback_ref": deploy.get("rollback_ref"),
            }
        )
    return results[:3]


def ownership_for(service: str) -> dict[str, Any]:
    return {
        "service": service,
        "owner_team": "payments-platform",
        "slack_channel": "#oncall-payments",
        "jira_project": "PAY",
        "runbooks": [
            {
                "title": "Synthetic scenario triage",
                "url": "runbook://synthetic-scenario-triage",
                "excerpt": "Check service health, saturation, dependency failures, trace latency, and recent deploys before rollback.",
            }
        ],
    }


def build_triage_request(
    args: argparse.Namespace,
    incident: dict[str, Any],
    metrics: list[dict[str, Any]],
    logs: list[dict[str, Any]],
    deploys: list[dict[str, Any]],
) -> dict[str, Any]:
    service = incident["service"]
    now = utc_now()
    started = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat().replace("+00:00", "Z")
    evidence_text = "; ".join(incident["evidence"][:3])
    title = f"{service} anomaly detected"
    if any("latency" in item.lower() or "timeout" in item.lower() for item in incident["evidence"]):
        title = f"High p95 latency and timeout signals on {service}"
    if incident["severity"] == "critical":
        title = f"{service} is down"

    return {
        "correlation_id": f"corr-{service}-{int(time.time())}",
        "tenant_id": args.tenant_id,
        "incident_id": f"inc-{service}-{int(time.time())}",
        "environment": args.environment,
        "received_at": now,
        "alert": {
            "alert_id": f"alert-{service}-{int(time.time())}",
            "source": "aiops-observability-worker",
            "service": service,
            "severity": incident["severity"],
            "title": title,
            "description": evidence_text,
            "started_at": started,
            "labels": {"detector": "threshold_baseline_log_trace_worker"},
        },
        "metrics": group_metrics(metrics, service),
        "logs": normalize_logs(logs, service),
        "recent_deploys": normalize_deploys(deploys, service),
        "ownership": ownership_for(service),
    }


def call_triage(args: argparse.Namespace, body: dict[str, Any]) -> dict[str, Any]:
    response = requests.post(
        args.triage_url,
        json=body,
        headers={"X-Tenant-Id": body["tenant_id"], "X-Correlation-Id": body["correlation_id"]},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


def build_report(
    body: dict[str, Any],
    response: dict[str, Any],
    incident: dict[str, Any],
    report_url: str,
) -> dict[str, Any]:
    return {
        "incident_id": body["incident_id"],
        "created_at": utc_now(),
        "audit_id": response["audit_id"],
        "report_url": report_url,
        "request_context": body,
        "triage_response": response,
        "detector_evidence": incident.get("evidence", []),
        "anomaly_evidence": response.get("anomaly_evidence", []),
        "service_topology": response.get("service_topology"),
        "rca_candidates": response.get("rca_candidates", []),
        "causal_hints": response.get("causal_hints", []),
        "investigation_summary": response.get("investigation_summary"),
        "llm_metadata": response.get("llm_metadata", {}),
        "slack_context": {
            "incident_id": response.get("incident_id"),
            "classification": response.get("classification"),
            "severity": response.get("severity"),
            "confidence": response.get("confidence"),
            "status": response.get("status"),
            "suggested_assignee_account_id": response.get("suggested_assignee_account_id"),
            "suggestion_reason": response.get("suggestion_reason"),
        },
        "jira_payload": response.get("ticket_payload", {}),
    }


def report_url_for(args: argparse.Namespace, incident_id: str) -> str:
    return f"{args.report_base_url.rstrip('/')}/{incident_id}"


def publish_slack(response: dict[str, Any], dry_run: bool, report_url: str) -> None:
    webhook = os.getenv("SLACK_WEBHOOK_URL")
    evidence = response.get("anomaly_evidence", [])
    top_evidence = evidence[0]["reason"] if evidence else response.get("suspected_root_cause", {}).get("evidence", [""])[0]
    actions = response.get("recommended_actions", [])
    top_action = actions[0].get("summary") if actions else "Review incident context."
    concise_payload = {
        "channel": "#oncall",
        "text": (
            f"{response.get('severity', 'unknown').upper()} {response.get('classification')} "
            f"for {response.get('incident_id')} ({response.get('status')}, confidence {response.get('confidence', 0):.2f}). "
            f"Top evidence: {top_evidence} Action: {top_action} Report: {report_url}"
        ),
    }
    if dry_run or not webhook:
        print(json.dumps({"slack_dry_run": concise_payload}, indent=2))
        return
    slack_response = requests.post(webhook, json={"text": concise_payload["text"]}, timeout=5)
    slack_response.raise_for_status()


def run_once(args: argparse.Namespace) -> bool:
    deploys: list[dict[str, Any]] = []
    if args.offline_scenario:
        metrics, logs, deploys = offline_raw_observability(args)
    else:
        metrics = query_metric_points(args)
        logs = query_logs(args)
        if args.service:
            try:
                traces = jaeger_query(args.jaeger_url, args.service)
                print(json.dumps({"jaeger_trace_count": len(traces)}))
            except requests.RequestException as exc:
                print(f"Jaeger query skipped: {exc}")

    incident = detect_incident(metrics, logs)
    if not incident:
        print(json.dumps({"detected": False, "message": "No incident candidate in bounded window."}))
        return False

    body = build_triage_request(args, incident, metrics, logs, deploys)
    print(json.dumps({"detected": True, "triage_request": body}, indent=2))
    response = call_triage(args, body)
    print(json.dumps({"triage_response": response}, indent=2))
    report_url = report_url_for(args, body["incident_id"])
    report = build_report(body, response, incident, report_url)
    report_path = write_report(report, Path(args.report_dir))
    print(json.dumps({"report_written": str(report_path), "report_url": report_url}, indent=2))
    publish_slack(response, args.dry_run_slack, report_url)
    return True


def process_sqs_message(
    args: argparse.Namespace,
    sqs_client: Any,
    message: dict[str, Any],
    registry: ToolRegistry | None = None,
) -> bool:
    body_text = message.get("Body", "")
    try:
        seed = IncidentSeed.model_validate_json(body_text)
    except ValidationError:
        print(json.dumps({"sqs_message": "invalid_incident_seed", "deleted": False}))
        return False

    body = build_triage_request_from_seed(seed, registry)
    response = call_triage(args, body)
    report_url = report_url_for(args, body["incident_id"])
    report = build_report(body, response, {"evidence": ["cdo incident seed"]}, report_url)
    report_path = write_report(report, Path(args.report_dir))
    sqs_client.delete_message(QueueUrl=args.sqs_queue_url, ReceiptHandle=message["ReceiptHandle"])
    print(
        json.dumps(
            {
                "sqs_message": "processed",
                "incident_id": body["incident_id"],
                "report_written": str(report_path),
                "deleted": True,
            },
            indent=2,
        )
    )
    publish_slack(response, args.dry_run_slack, report_url)
    return True


def run_sqs_once(args: argparse.Namespace, registry: ToolRegistry | None = None) -> int:
    if not args.sqs_queue_url:
        raise ValueError("--sqs-queue-url or SQS_QUEUE_URL is required for SQS worker mode")
    import boto3

    sqs_client = boto3.client("sqs", region_name=args.sqs_region)
    response = sqs_client.receive_message(
        QueueUrl=args.sqs_queue_url,
        MaxNumberOfMessages=max(1, min(args.sqs_max_messages, 10)),
        WaitTimeSeconds=max(0, min(args.sqs_wait_seconds, 20)),
    )
    messages = response.get("Messages", [])
    processed = 0
    for message in messages:
        if process_sqs_message(args, sqs_client, message, registry):
            processed += 1
    return processed


def main() -> None:
    args = parse_args()
    if args.sqs_queue_url:
        while True:
            run_sqs_once(args)
            if args.poll_seconds <= 0:
                break
            time.sleep(args.poll_seconds)
        return
    while True:
        run_once(args)
        if args.poll_seconds <= 0:
            break
        time.sleep(args.poll_seconds)


if __name__ == "__main__":
    main()
