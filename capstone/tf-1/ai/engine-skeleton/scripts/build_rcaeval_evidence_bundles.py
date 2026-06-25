from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


SCENARIO_SUPPLEMENTS = {
    "latency-degradation": {
        "log_messages": [
            "dependency latency increased during RCAEval delay fault window",
            "request path exceeded latency SLO during RCAEval delay fault window",
        ],
        "trace_status": "error",
        "trace_reason": "delay fault produced slow downstream span in the mapped RCAEval service graph",
        "deploy_change": "TF1 supplemental deploy metadata placeholder for CDO routing demo; RCAEval does not provide deploy events",
        "runbook": {
            "title": "Latency degradation triage",
            "url": "runbook://latency-degradation",
            "excerpt": "Check latency percentiles, dependency timings, error rate, recent changes, and saturation before rollback.",
        },
        "slack_channel": "#oncall-payments",
        "jira_project": "PAY",
        "triage_queue": "payments-triage",
    },
    "critical-service-down": {
        "log_messages": [
            "availability dropped during RCAEval loss fault window",
            "dependency request failed during RCAEval loss fault window",
        ],
        "trace_status": "error",
        "trace_reason": "loss fault produced failing or missing downstream spans in the mapped RCAEval service graph",
        "deploy_change": "TF1 supplemental deploy metadata placeholder for CDO routing demo; RCAEval does not provide deploy events",
        "runbook": {
            "title": "Service down triage",
            "url": "runbook://service-down",
            "excerpt": "Check service health, dependency availability, error budget burn, and recent changes before rollback.",
        },
        "slack_channel": "#oncall-payments",
        "jira_project": "PAY",
        "triage_queue": "payments-triage",
    },
    "noisy-false-alert": {
        "log_messages": [
            "probe anomaly recovered on retry during RCAEval resource fault window",
        ],
        "trace_status": "ok",
        "trace_reason": "sample trace indicates no user-impacting request failure for the noisy alert path",
        "deploy_change": None,
        "runbook": {
            "title": "Noisy alert triage",
            "url": "runbook://noisy-alert",
            "excerpt": "Check repetition, user impact, and cross-signal agreement before escalating.",
        },
        "slack_channel": "#oncall-search",
        "jira_project": "SRCH",
        "triage_queue": "search-triage",
    },
}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def parse_time(value: str) -> datetime | None:
    try:
        if value.isdigit():
            return datetime.fromtimestamp(int(value), tz=timezone.utc)
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except (ValueError, OSError):
        return None


def format_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def metric_times(metrics: list[dict[str, Any]]) -> list[datetime]:
    times: list[datetime] = []
    for metric in metrics:
        for point in metric.get("points", []):
            parsed = parse_time(str(point.get("ts", "")))
            if parsed:
                times.append(parsed)
    return sorted(times)


def time_window(request: dict[str, Any]) -> dict[str, str]:
    received = parse_time(request["received_at"]) or datetime.now(timezone.utc)
    times = metric_times(request.get("metrics", []))
    baseline_start = times[0] if times else received - timedelta(minutes=20)
    incident_start = parse_time(request["alert"]["started_at"]) or received
    incident_end = received
    post_end = received + timedelta(minutes=15)
    return {
        "baseline_start": format_time(baseline_start),
        "incident_start": format_time(incident_start),
        "incident_end": format_time(incident_end),
        "post_end": format_time(post_end),
    }


def metric_names(metrics: list[dict[str, Any]]) -> list[str]:
    return sorted({str(metric.get("metric_name")) for metric in metrics if metric.get("metric_name")})


def services_from_metrics(metrics: list[dict[str, Any]], primary_service: str) -> list[str]:
    services = {primary_service}
    for metric in metrics:
        service = metric.get("service")
        if service:
            services.add(str(service))
    return sorted(services)


def supplemental_logs(request: dict[str, Any], scenario: str) -> list[dict[str, Any]]:
    supplement = SCENARIO_SUPPLEMENTS[scenario]
    service = request["alert"]["service"]
    started_at = request["alert"]["started_at"]
    logs = []
    for index, message in enumerate(supplement["log_messages"], start=1):
        logs.append(
            {
                "service": service,
                "ts": started_at,
                "level": "warning" if scenario == "noisy-false-alert" else "error",
                "message": message,
                "trace_id": f"rcaeval-{request['alert']['labels']['case']}-trace-{index}",
                "labels": {
                    "source": "tf1-supplemental-operational-context",
                    "dataset": "RCAEval",
                    "fault": request["alert"]["labels"].get("fault"),
                },
            }
        )
    return logs


def supplemental_traces(request: dict[str, Any], scenario: str) -> list[dict[str, Any]]:
    supplement = SCENARIO_SUPPLEMENTS[scenario]
    service = request["alert"]["service"]
    services = services_from_metrics(request.get("metrics", []), service)
    dependency = next((item for item in services if item != service), None)
    return [
        {
            "trace_id": f"rcaeval-{request['alert']['labels']['case']}-trace-1",
            "service": service,
            "root_span": f"RCAEval {request['alert']['labels'].get('fault')} fault request",
            "duration_ms": 3000 if scenario != "noisy-false-alert" else 180,
            "status": supplement["trace_status"],
            "bottleneck_service": dependency,
            "bottleneck_reason": supplement["trace_reason"],
            "labels": {
                "source": "tf1-supplemental-operational-context",
                "dataset": "RCAEval",
                "mapped_scenario": scenario,
            },
        }
    ]


def bundle_logs(request: dict[str, Any], scenario: str) -> tuple[list[dict[str, Any]], str]:
    logs = request.get("logs") or []
    if logs:
        lineage = request.get("metadata", {}).get("extra_evidence_lineage", {}).get("logs")
        if lineage:
            return logs, lineage
        return logs, "RCAEval logs.csv adapted into TF1 log evidence"
    return [], "Not available in this selected RCAEval case; no logs.csv is present in the extracted benchmark case"


def bundle_traces(request: dict[str, Any], scenario: str) -> tuple[list[dict[str, Any]], str]:
    traces = request.get("traces") or []
    if traces:
        lineage = request.get("metadata", {}).get("extra_evidence_lineage", {}).get("traces")
        if lineage:
            return traces, lineage
        return traces, "RCAEval traces.csv adapted into TF1 trace evidence"
    return [], "Not available in this selected RCAEval case; no traces.csv is present in the extracted benchmark case"


def supplemental_deploys(request: dict[str, Any], scenario: str) -> list[dict[str, Any]]:
    change = SCENARIO_SUPPLEMENTS[scenario]["deploy_change"]
    if not change:
        return []
    started = parse_time(request["alert"]["started_at"]) or datetime.now(timezone.utc)
    deployed_at = started - timedelta(minutes=7)
    return [
        {
            "service": request["alert"]["service"],
            "version": f"sample-{request['alert']['labels']['case']}",
            "deployed_at": format_time(deployed_at),
            "deployed_by": "ci",
            "change_summary": change,
            "rollback_ref": "previous-sample-version",
            "labels": {
                "source": "tf1-supplemental-operational-context",
                "dataset": "RCAEval",
            },
        }
    ]


def ownership(request: dict[str, Any], scenario: str) -> dict[str, Any]:
    supplement = SCENARIO_SUPPLEMENTS[scenario]
    service = request["alert"]["service"]
    return {
        "service": service,
        "owner_team": f"{service}-owner",
        "slack_channel": supplement["slack_channel"],
        "jira_project": supplement["jira_project"],
        "triage_queue": supplement["triage_queue"],
        "runbooks": [supplement["runbook"]],
        "labels": {
            "source": "tf1-supplemental-operational-context",
            "dataset": "RCAEval",
        },
    }


def build_bundle(request_path: Path, adapted_root: Path) -> tuple[Path, dict[str, Any]]:
    request = load_json(request_path)
    scenario = request_path.parent.name
    case = request_path.stem.replace(".request", "")
    metrics = request.get("metrics", [])
    logs, logs_lineage = bundle_logs(request, scenario)
    traces, traces_lineage = bundle_traces(request, scenario)
    tw = time_window(request)
    bundle = {
        "schema_version": "tf1.evidence_bundle.v1",
        "tenant_id": request["tenant_id"],
        "incident_id": request["incident_id"],
        "correlation_id": request["correlation_id"],
        "scenario": scenario,
        "source_case": request["alert"]["labels"].get("case", case),
        "service": request["alert"]["service"],
        "environment": request["environment"],
        "region": request["alert"].get("labels", {}).get("region", "unknown"),
        "time_window": tw,
        "query_hints": {
            "metrics": metric_names(metrics),
            "log_filters": [str(log.get("message")) for log in logs[:5] if log.get("message")],
            "trace_ids": [str(trace.get("trace_id")) for trace in traces[:5] if trace.get("trace_id")],
            "dependencies": [item for item in services_from_metrics(metrics, request["alert"]["service"]) if item != request["alert"]["service"]][:5],
        },
        "metrics": metrics,
        "logs": logs,
        "traces": traces,
        "deploy_events": supplemental_deploys(request, scenario),
        "ownership": ownership(request, scenario),
        "runbooks": [SCENARIO_SUPPLEMENTS[scenario]["runbook"]],
        "data_lineage": {
            "primary_dataset": "RCAEval",
            "primary_request": str(request_path.relative_to(adapted_root.parent)).replace("\\", "/"),
            "metrics": "RCAEval adapted request",
            "logs": logs_lineage,
            "traces": traces_lineage,
            "deploy_events": "TF1 supplemental deploy metadata because RCAEval does not provide deploy events",
            "ownership_runbooks": "TF1 supplemental routing/runbook config for CDO handoff",
        },
    }
    output = adapted_root.parent / "evidence-bundles" / scenario / f"{case}.evidence-bundle.json"
    return output, bundle


def main() -> None:
    parser = argparse.ArgumentParser(description="Build CDO-hostable evidence bundles from adapted RCAEval requests.")
    parser.add_argument("--adapted-root", type=Path, default=Path("datapack/external/adapted"))
    args = parser.parse_args()

    generated = []
    for request_path in sorted(args.adapted_root.glob("*/*.request.json")):
        output, bundle = build_bundle(request_path, args.adapted_root)
        write_json(output, bundle)
        generated.append(str(output).replace("\\", "/"))
    print(json.dumps({"generated": generated}, indent=2))


if __name__ == "__main__":
    main()
