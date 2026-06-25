from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.action_catalog import select_actions
from app.aiops_worker import build_report, build_triage_request, detect_incident, offline_raw_observability, process_sqs_message
from app.context_tools import ToolRegistry, ToolScope, ToolScopeError
from app.incident_seed import IncidentSeed, build_triage_request_from_seed
from app.llm import agentcore_session_id, investigate_with_tools, parse_tool_calls, read_agentcore_response, reword_catalog_actions
from app.main import MetricPoint, MetricSeries, TriageRequest, app
from app.rca import detect_metric_anomalies, infer_causal_hints
from app.report_store import write_report


def test_offline_scenario_detects_and_triages_latency_degradation() -> None:
    args = argparse.Namespace(
        datapack_root="datapack/scenarios",
        scenario="latency-degradation",
        tenant_id="tenant-a",
        service="payment-api",
        environment="sandbox",
    )
    metrics, logs, deploys = offline_raw_observability(args)
    incident = detect_incident(metrics, logs)

    assert incident is not None
    body = build_triage_request(args, incident, metrics, logs, deploys)
    response = TestClient(app).post(
        "/v1/triage",
        json=body,
        headers={"X-Tenant-Id": body["tenant_id"], "X-Correlation-Id": body["correlation_id"]},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "DIAGNOSED"
    assert payload["classification"] == "latency_degradation"
    assert "slack_payload" not in payload
    assert payload["suggestion_reason"]
    assert payload["anomaly_evidence"]
    assert payload["rca_candidates"]


def test_baseline_like_metrics_do_not_create_incident() -> None:
    metrics = [
        {
            "tenant_id": "tenant-a",
            "service": "payment-api",
            "environment": "sandbox",
            "timestamp": "2026-06-22T09:00:00Z",
            "metric_name": "http_latency_p95_ms",
            "value": 220,
            "unit": "ms",
        }
    ]

    assert detect_incident(metrics, logs=[]) is None


def test_statistical_detectors_emit_expected_evidence() -> None:
    series = MetricSeries(
        metric_name="http_latency_p95_ms",
        service="payment-api",
        unit="ms",
        points=[MetricPoint(ts=f"2026-06-22T09:{minute:02d}:00Z", value=value) for minute, value in enumerate([200, 205, 198, 207, 203, 201, 206, 2100])],
    )

    detectors = {item["detector"] for item in detect_metric_anomalies([series])}

    assert "threshold" in detectors
    assert "rolling_zscore_3sigma" in detectors
    assert "ewma_drift" in detectors
    assert "isolation_forest" in detectors


def test_causal_hints_degrade_when_series_is_too_short() -> None:
    series = MetricSeries(
        metric_name="http_error_rate_percent",
        service="payment-api",
        unit="percent",
        points=[MetricPoint(ts="2026-06-22T09:00:00Z", value=1), MetricPoint(ts="2026-06-22T09:01:00Z", value=9)],
    )

    hints = infer_causal_hints([series])

    assert hints[0]["type"] == "insufficient_points"


def test_sample_contract_responses_still_match() -> None:
    client = TestClient(app)
    for request_path in sorted(Path("samples").glob("*.request.json")):
        body = json.loads(request_path.read_text(encoding="utf-8"))
        response = client.post(
            "/v1/triage",
            json=body,
            headers={"X-Tenant-Id": body["tenant_id"], "X-Correlation-Id": body["correlation_id"]},
        )
        assert response.status_code == 200


def test_latency_database_timeout_selects_dependency_action() -> None:
    payload = post_sample("latency-degradation")
    actions = payload["recommended_actions"]

    assert actions[0]["id"] == "dependency_timeout_triage"
    assert actions[0]["risk"] == "low"
    assert actions[0]["evidence_refs"]


def test_recent_deploy_latency_selects_rollback_consider_with_approval() -> None:
    payload = post_sample("latency-degradation")
    rollback_actions = [action for action in payload["recommended_actions"] if action["id"] == "consider_recent_deploy_rollback"]

    assert rollback_actions
    assert rollback_actions[0]["requires_human_approval"] is True
    assert rollback_actions[0]["approval_reason"]


def test_noisy_alert_only_selects_observe_and_human_review_actions() -> None:
    payload = post_sample("noisy-alert")
    action_ids = {action["id"] for action in payload["recommended_actions"]}

    assert action_ids == {"observe_user_impact", "human_review_noisy_alert"}
    assert {action["risk"] for action in payload["recommended_actions"]} == {"low"}


def test_insufficient_context_selects_context_gathering_action_only() -> None:
    payload = post_sample("insufficient-context")

    assert [action["id"] for action in payload["recommended_actions"]] == ["attach_telemetry_context"]


def test_low_confidence_blocks_medium_risk_actions() -> None:
    body = json.loads(Path("samples/latency-degradation.request.json").read_text(encoding="utf-8"))
    request = TriageRequest.model_validate(body)
    decision = {
        "classification": "latency_degradation",
        "confidence": 0.45,
        "evidence": ["Latency evidence exists."],
        "summary": "Low-confidence latency signal.",
    }

    actions = select_actions(request, decision, {"anomaly_evidence": []}, "runbook://db-timeout")

    assert "consider_recent_deploy_rollback" not in {action["id"] for action in actions}
    assert {action["risk"] for action in actions} == {"low"}


def test_llm_action_wording_falls_back_when_bedrock_disabled(monkeypatch) -> None:
    monkeypatch.delenv("ENABLE_BEDROCK_LLM", raising=False)
    monkeypatch.delenv("ENABLE_AGENTCORE_LLM", raising=False)
    monkeypatch.delenv("BEDROCK_MODEL_ID", raising=False)
    monkeypatch.delenv("BEDROCK_MODEL_IDS", raising=False)
    body = json.loads(Path("samples/latency-degradation.request.json").read_text(encoding="utf-8"))
    request = TriageRequest.model_validate(body)
    actions = [
        {
            "id": "dependency_timeout_triage",
            "type": "RUNBOOK_CHECK",
            "priority": 1,
            "summary": "Check dependency timeout signals.",
            "runbook_ref": "runbook://db-timeout",
            "risk": "low",
            "why": "Timeout evidence exists.",
            "evidence_refs": ["logs[0]"],
            "requires_human_approval": False,
            "approval_reason": None,
        }
    ]

    result = reword_catalog_actions(request, {"classification": "latency_degradation", "status": "DIAGNOSED", "confidence": 0.82, "summary": "", "evidence": []}, {}, actions)

    assert result["actions"] == actions
    assert result["metadata"]["provider"] == "deterministic"


def test_report_json_is_written_and_report_apis_return_data(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("REPORTS_DIR", str(tmp_path))
    client = TestClient(app)
    body = json.loads(Path("samples/latency-degradation.request.json").read_text(encoding="utf-8"))
    response = client.post(
        "/v1/triage",
        json=body,
        headers={"X-Tenant-Id": body["tenant_id"], "X-Correlation-Id": body["correlation_id"]},
    )
    payload = response.json()
    report = build_report(body, payload, {"evidence": ["synthetic detector evidence"]}, "http://localhost:5173/reports/inc")

    path = write_report(report, tmp_path)
    list_response = client.get("/v1/reports")
    detail_response = client.get(f"/v1/reports/{body['incident_id']}")

    assert path.exists()
    assert list_response.status_code == 200
    assert list_response.json()["reports"][0]["incident_id"] == body["incident_id"]
    assert detail_response.status_code == 200
    assert detail_response.json()["triage_response"]["audit_id"] == payload["audit_id"]
    assert detail_response.json()["triage_response"]["recommended_actions"][0]["id"]


def test_incident_seed_builds_bounded_triage_request_from_registry() -> None:
    seed = IncidentSeed.model_validate(
        {
            "schema_version": "tf1.incident_seed.v1",
            "tenant_id": "tenant-a",
            "correlation_id": "corr-001",
            "incident_id": "inc-001",
            "environment": "prod",
            "service": "checkout-api",
            "severity": "high",
            "title": "High p95 latency on checkout-api",
            "description": "p95 latency above threshold",
            "started_at": "2026-06-24T08:45:00Z",
            "received_at": "2026-06-24T09:05:00Z",
            "labels": {"alert_id": "alert-001", "source": "cdo-detector"},
        }
    )
    registry = ToolRegistry(FakeContextClient())

    body = build_triage_request_from_seed(seed, registry)
    request = TriageRequest.model_validate(body)

    assert request.tenant_id == "tenant-a"
    assert request.alert.service == "checkout-api"
    assert request.metrics[0].metric_name == "http_latency_p95_ms"
    assert request.logs[0].message == "database timeout after 3000ms"


def test_tool_registry_rejects_unknown_and_out_of_scope_calls() -> None:
    scope = ToolScope(
        tenant_id="tenant-a",
        environment="prod",
        service="checkout-api",
        started_at="2026-06-24T08:45:00Z",
        received_at="2026-06-24T09:05:00Z",
    )
    registry = ToolRegistry(FakeContextClient())

    with pytest.raises(ToolScopeError):
        registry.execute("run_shell", {}, scope)
    with pytest.raises(ToolScopeError):
        registry.execute("get_logs", {"tenant_id": "tenant-b"}, scope)
    with pytest.raises(ToolScopeError):
        registry.execute("get_logs", {"window_start": "2026-06-24T07:00:00Z"}, scope)


def test_llm_tool_call_parser_accepts_only_registered_tools() -> None:
    calls = parse_tool_calls('{"tool_calls":[{"name":"get_logs","args":{"limit":5}}]}', {"get_logs"}, 3)

    assert calls == [{"name": "get_logs", "args": {"limit": 5}}]
    with pytest.raises(ToolScopeError):
        parse_tool_calls('{"tool_calls":[{"name":"delete_service","args":{}}]}', {"get_logs"}, 3)


def test_llm_tool_loop_merges_evidence_and_reruns_rca(monkeypatch) -> None:
    monkeypatch.setenv("ENABLE_AGENTCORE_LLM_TOOLS", "true")
    monkeypatch.setenv("AGENTCORE_RUNTIME_ARN", "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/tf1")
    monkeypatch.setattr(
        "app.llm.request_tool_calls_from_agentcore",
        lambda request, decision, rca, allowed_tools, max_calls: [{"name": "get_logs", "args": {"limit": 1}}],
    )
    request = TriageRequest.model_validate(json.loads(Path("samples/insufficient-context.request.json").read_text(encoding="utf-8")))
    rca = {"anomaly_evidence": [], "service_topology": None, "causal_hints": [], "rca_candidates": []}
    decision = {"status": "INSUFFICIENT_CONTEXT", "classification": "insufficient_context", "confidence": 0.25, "summary": "", "evidence": []}

    enriched, rerun_rca, _, metadata = investigate_with_tools(request, decision, rca, ToolRegistry(FakeContextClient()))

    assert enriched.logs
    assert rerun_rca["anomaly_evidence"]
    assert metadata["tool_calls"][0]["name"] == "get_logs"
    assert metadata["fallback"] is False
    assert metadata["provider"] == "agentcore"


def test_llm_tool_loop_falls_back_on_bedrock_failure(monkeypatch) -> None:
    monkeypatch.setenv("ENABLE_AGENTCORE_LLM_TOOLS", "true")
    monkeypatch.setenv("AGENTCORE_RUNTIME_ARN", "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/tf1")

    def fail(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        raise RuntimeError("bedrock unavailable")

    monkeypatch.setattr("app.llm.request_tool_calls_from_agentcore", fail)
    request = TriageRequest.model_validate(json.loads(Path("samples/insufficient-context.request.json").read_text(encoding="utf-8")))
    decision = {"status": "INSUFFICIENT_CONTEXT", "classification": "insufficient_context", "confidence": 0.25, "summary": "", "evidence": []}

    enriched, _, _, metadata = investigate_with_tools(request, decision, {}, ToolRegistry(FakeContextClient()))

    assert enriched == request
    assert metadata["fallback"] is True
    assert "bedrock unavailable" in metadata["error"]


def test_agentcore_response_reader_and_session_id_are_stable() -> None:
    request = TriageRequest.model_validate(json.loads(Path("samples/insufficient-context.request.json").read_text(encoding="utf-8")))
    response = {"contentType": "application/json", "response": [b'{"tool_calls":[{"name":"get_logs","args":{}}]}']}

    raw = read_agentcore_response(response)

    assert json.loads(raw)["tool_calls"][0]["name"] == "get_logs"
    assert agentcore_session_id(request) == agentcore_session_id(request)
    assert len(agentcore_session_id(request)) == 36


def test_sqs_seed_success_deletes_message_after_report_write(tmp_path, monkeypatch) -> None:
    args = argparse.Namespace(
        sqs_queue_url="https://sqs.example/queue",
        report_dir=str(tmp_path),
        report_base_url="http://localhost:5173/#/reports",
        dry_run_slack=True,
    )
    seed = {
        "schema_version": "tf1.incident_seed.v1",
        "tenant_id": "tenant-a",
        "correlation_id": "corr-001",
        "incident_id": "inc-001",
        "environment": "prod",
        "service": "checkout-api",
        "severity": "high",
        "title": "High p95 latency on checkout-api",
        "description": "p95 latency above threshold",
        "started_at": "2026-06-24T08:45:00Z",
        "received_at": "2026-06-24T09:05:00Z",
        "labels": {},
    }
    sqs = FakeSQS()

    monkeypatch.setattr(
        "app.aiops_worker.call_triage",
        lambda args, body: {
            "audit_id": "audit-001",
            "severity": "high",
            "classification": "latency_degradation",
            "status": "DIAGNOSED",
            "confidence": 0.82,
            "anomaly_evidence": [],
            "recommended_actions": [{"summary": "Review dependency timeout signals."}],
            "ticket_payload": {},
            "suggested_assignee_account_id": None,
            "suggestion_reason": "No Jira accountId history mapping is configured yet.",
        },
    )

    processed = process_sqs_message(args, sqs, {"Body": json.dumps(seed), "ReceiptHandle": "rh-001"}, ToolRegistry(FakeContextClient()))

    assert processed is True
    assert sqs.deleted == [("https://sqs.example/queue", "rh-001")]
    assert (tmp_path / "inc-001.json").exists()


def test_sqs_invalid_seed_is_not_deleted(tmp_path) -> None:
    args = argparse.Namespace(sqs_queue_url="https://sqs.example/queue", report_dir=str(tmp_path), dry_run_slack=True)
    sqs = FakeSQS()

    processed = process_sqs_message(args, sqs, {"Body": "{}", "ReceiptHandle": "rh-001"}, ToolRegistry(FakeContextClient()))

    assert processed is False
    assert sqs.deleted == []


def post_sample(name: str) -> dict[str, Any]:
    body = json.loads(Path(f"samples/{name}.request.json").read_text(encoding="utf-8"))
    response = TestClient(app).post(
        "/v1/triage",
        json=body,
        headers={"X-Tenant-Id": body["tenant_id"], "X-Correlation-Id": body["correlation_id"]},
    )
    assert response.status_code == 200
    return response.json()


class FakeContextClient:
    def get_metrics(self, service: str, environment: str, tenant_id: str, window: tuple[str, str]) -> list[dict[str, Any]]:
        return [
            {
                "metric_name": "http_latency_p95_ms",
                "service": service,
                "unit": "ms",
                "points": [{"ts": window[1], "value": 1300}],
                "labels": {"source": "fake"},
            }
        ]

    def get_logs(self, service: str, environment: str, tenant_id: str, window: tuple[str, str], limit: int) -> list[dict[str, Any]]:
        return [
            {
                "service": service,
                "ts": window[1],
                "level": "error",
                "message": "database timeout after 3000ms",
                "trace_id": "trace-001",
                "labels": {"dependency": "postgres"},
            }
        ][:limit]

    def get_recent_deploys(self, service: str, environment: str, window: tuple[str, str]) -> list[dict[str, Any]]:
        return [
            {
                "service": service,
                "version": "sha-001",
                "deployed_at": window[0],
                "deployed_by": "ci",
                "change_summary": "changed query path",
                "rollback_ref": "sha-prev",
            }
        ]

    def get_ownership(self, service: str) -> dict[str, Any]:
        return {
            "service": service,
            "owner_team": "payments-platform",
            "slack_channel": "#oncall-payments",
            "jira_project": "PAY",
            "runbooks": [{"title": "DB timeout", "url": "runbook://db-timeout"}],
        }


class FakeSQS:
    def __init__(self) -> None:
        self.deleted: list[tuple[str, str]] = []

    def delete_message(self, QueueUrl: str, ReceiptHandle: str) -> None:
        self.deleted.append((QueueUrl, ReceiptHandle))
