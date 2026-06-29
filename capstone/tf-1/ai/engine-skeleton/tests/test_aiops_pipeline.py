from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient

from app.action_catalog import select_actions
from app.agent_runtime import run_agent_platform
from app.aiops_worker import (
    build_report,
    build_triage_hub_notify_payload,
    build_triage_request,
    detect_incident,
    offline_raw_observability,
    process_sqs_message,
    publish_slack,
    publish_to_triage_hub_sqs,
)
from app.context_tools import ContextClient, ToolRegistry, ToolScope, ToolScopeError
from app.evidence_budget import compact_request_evidence
from app.audit_store import append_audit_record, latest_audit_record
from app.idempotency_store import complete_record, fail_record, read_record, request_hash, start_record, write_record
from app.incident_seed import IncidentSeed, build_triage_request_from_seed
from app.investigation_router import select_investigation_mode
from app.llm import agentcore_session_id, build_prompt_payload, investigate_with_tools, parse_tool_calls, read_agentcore_response, reword_catalog_actions
from app.main import MetricPoint, MetricSeries, TriageRequest, _rate_limit_hits, app, build_audit_id, classify
from app.observability import sanitize_log_fields
from app.qa_judge import parse_qa_judge_response, run_qa
from app.rca import analyze_request, detect_metric_anomalies, infer_causal_hints
from app.report_store import write_report


class FakeDynamoTable:
    def __init__(self) -> None:
        self.items: dict[tuple[str, str], dict[str, Any]] = {}
        self.fail_conditional_put = False
        self.hidden_gets_remaining = 0

    def put_item(self, Item: dict[str, Any], ConditionExpression: Any | None = None) -> None:
        if ConditionExpression is not None and self.fail_conditional_put:
            raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "PutItem")
        self.items[(Item["PK"], Item["SK"])] = Item

    def get_item(self, Key: dict[str, str]) -> dict[str, Any]:
        if self.hidden_gets_remaining:
            self.hidden_gets_remaining -= 1
            return {}
        item = self.items.get((Key["PK"], Key["SK"]))
        return {"Item": item} if item else {}

    def query(self, **kwargs: Any) -> dict[str, Any]:
        audit_items = [item for (pk, _sk), item in self.items.items() if pk.startswith("AUDIT#")]
        return {"Items": sorted(audit_items, key=lambda item: item["SK"], reverse=not kwargs.get("ScanIndexForward", True))}


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
        payload = response.json()
        assert "slack_payload" not in payload
        assert "ticket_payload" in payload
        assert "suggested_assignee_account_id" in payload
        assert "suggestion_reason" in payload

    for response_path in sorted(Path("samples").glob("*.response.json")):
        payload = json.loads(response_path.read_text(encoding="utf-8"))
        assert "slack_payload" not in payload


def test_metrics_endpoint_exposes_triage_engine_metrics() -> None:
    payload = post_sample("latency-degradation")
    response = TestClient(app).get("/metrics")
    body = response.text

    assert payload["status"] == "DIAGNOSED"
    assert response.status_code == 200
    assert "aiops_triage_requests_total" in body
    assert 'classification="latency_degradation"' in body
    assert "aiops_context_enrichment_result_total" in body
    assert "aiops_investigation_mode_selected_total" in body


def test_router_chooses_deterministic_for_low_complexity(monkeypatch) -> None:
    monkeypatch.delenv("AIOPS_INVESTIGATION_MODE", raising=False)
    request = TriageRequest.model_validate(json.loads(Path("samples/latency-degradation.request.json").read_text(encoding="utf-8")))
    rca = analyze_request(request)
    decision = classify(request, rca)

    selection = select_investigation_mode(request, decision, rca, agentcore_enabled=True)

    assert selection.selected_mode == "deterministic_only"
    assert selection.metadata()["selected_mode"] == "deterministic_only"


def test_router_chooses_assisted_for_medium_complexity(monkeypatch) -> None:
    monkeypatch.delenv("AIOPS_INVESTIGATION_MODE", raising=False)
    request = TriageRequest.model_validate(json.loads(Path("samples/latency-degradation.request.json").read_text(encoding="utf-8")))
    body = request.model_dump(mode="json")
    body["traces"] = []
    body["recent_deploys"] = []
    body["ownership"] = None
    request = TriageRequest.model_validate(body)
    rca = analyze_request(request)
    decision = classify(request, rca)

    selection = select_investigation_mode(request, decision, rca, agentcore_enabled=True)

    assert selection.selected_mode == "agent_assisted"
    assert "missing_traces" in selection.reasons


def test_router_chooses_platform_for_high_complexity(monkeypatch) -> None:
    monkeypatch.delenv("AIOPS_INVESTIGATION_MODE", raising=False)
    request = TriageRequest.model_validate(json.loads(Path("samples/insufficient-context.request.json").read_text(encoding="utf-8")))
    rca = analyze_request(request)
    decision = classify(request, rca)

    selection = select_investigation_mode(request, decision, rca, agentcore_enabled=True)

    assert selection.selected_mode == "agent_platform"
    assert selection.complexity_score >= 6


def test_forced_investigation_mode_overrides_auto(monkeypatch) -> None:
    monkeypatch.setenv("AIOPS_INVESTIGATION_MODE", "agent_assisted")
    request = TriageRequest.model_validate(json.loads(Path("samples/insufficient-context.request.json").read_text(encoding="utf-8")))
    rca = analyze_request(request)
    decision = classify(request, rca)

    selection = select_investigation_mode(request, decision, rca, agentcore_enabled=False)

    assert selection.source == "env"
    assert selection.selected_mode == "agent_assisted"
    assert selection.planned_mode == "agent_platform"


def test_agentcore_disabled_selects_deterministic_with_planned_mode(monkeypatch) -> None:
    monkeypatch.delenv("AIOPS_INVESTIGATION_MODE", raising=False)
    request = TriageRequest.model_validate(json.loads(Path("samples/insufficient-context.request.json").read_text(encoding="utf-8")))
    rca = analyze_request(request)
    decision = classify(request, rca)

    selection = select_investigation_mode(request, decision, rca, agentcore_enabled=False)

    assert selection.selected_mode == "deterministic_only"
    assert selection.planned_mode == "agent_platform"
    assert selection.metadata()["agentcore_enabled"] is False


def test_agent_platform_tool_loop_executes_allowed_tool_and_finalizes(monkeypatch) -> None:
    monkeypatch.setenv("ENABLE_AGENTCORE_LLM", "true")
    monkeypatch.setenv("AGENTCORE_RUNTIME_ARN", "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/tf1")
    responses = iter(
        [
            json.dumps({"type": "tool_requests", "thought_summary": "Need logs.", "tool_calls": [{"name": "get_logs", "args": {"limit": 1}}]}),
            json.dumps(
                {
                    "type": "final_diagnosis",
                    "classification": "latency_degradation",
                    "status": "DIAGNOSED",
                    "confidence": 0.78,
                    "summary": "checkout-api latency is likely tied to dependency timeout signals.",
                    "evidence": ["Representative log: database timeout after 3000ms"],
                    "recommended_action_ids": ["dependency_timeout_triage"],
                    "qa": {"passed": True, "gaps": []},
                }
            ),
        ]
    )
    monkeypatch.setattr("app.agent_runtime.invoke_agentcore_investigator", lambda *args, **kwargs: next(responses))
    request = TriageRequest.model_validate(json.loads(Path("samples/insufficient-context.request.json").read_text(encoding="utf-8")))
    rca = analyze_request(request)
    decision = classify(request, rca)

    enriched, rerun_rca, final_decision, metadata, advisory_ids = run_agent_platform(request, decision, rca, ToolRegistry(FakeContextClient()))

    assert enriched.logs
    assert rerun_rca["anomaly_evidence"]
    assert final_decision["classification"] == "latency_degradation"
    assert metadata["tool_calls"][0]["status"] == "ok"
    assert advisory_ids == ["dependency_timeout_triage"]


def test_agent_platform_blocks_disallowed_tool_and_continues(monkeypatch) -> None:
    monkeypatch.setenv("ENABLE_AGENTCORE_LLM", "true")
    monkeypatch.setenv("AGENTCORE_RUNTIME_ARN", "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/tf1")
    responses = iter(
        [
            json.dumps({"type": "tool_requests", "tool_calls": [{"name": "delete_service", "args": {}}]}),
            json.dumps(
                {
                    "type": "final_diagnosis",
                    "classification": "general_investigation",
                    "status": "INVESTIGATE",
                    "confidence": 0.55,
                    "summary": "Evidence remains ambiguous after blocked unsafe tool request.",
                    "evidence": ["No allowed tool evidence changed the baseline."],
                    "recommended_action_ids": ["human_review_noisy_alert"],
                }
            ),
        ]
    )
    monkeypatch.setattr("app.agent_runtime.invoke_agentcore_investigator", lambda *args, **kwargs: next(responses))
    request = TriageRequest.model_validate(json.loads(Path("samples/insufficient-context.request.json").read_text(encoding="utf-8")))
    rca = analyze_request(request)
    decision = classify(request, rca)

    _, _, final_decision, metadata, advisory_ids = run_agent_platform(request, decision, rca, ToolRegistry(FakeContextClient()))

    assert final_decision["classification"] == "general_investigation"
    assert metadata["tool_calls"][0]["status"] == "blocked"
    assert advisory_ids == ["human_review_noisy_alert"]


def test_agent_platform_malformed_json_falls_back_deterministic(monkeypatch) -> None:
    monkeypatch.setenv("ENABLE_AGENTCORE_LLM", "true")
    monkeypatch.setenv("AGENTCORE_RUNTIME_ARN", "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/tf1")
    monkeypatch.setattr("app.agent_runtime.invoke_agentcore_investigator", lambda *args, **kwargs: "{not-json")
    request = TriageRequest.model_validate(json.loads(Path("samples/insufficient-context.request.json").read_text(encoding="utf-8")))
    rca = analyze_request(request)
    decision = classify(request, rca)

    _, _, final_decision, metadata, advisory_ids = run_agent_platform(request, decision, rca, ToolRegistry(FakeContextClient()))

    assert final_decision == decision
    assert metadata["fallback"] is True
    assert metadata["fallback_reason"] == "malformed_agent_json"
    assert advisory_ids == []


def test_agent_platform_invalid_final_diagnosis_falls_back(monkeypatch) -> None:
    monkeypatch.setenv("ENABLE_AGENTCORE_LLM", "true")
    monkeypatch.setenv("AGENTCORE_RUNTIME_ARN", "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/tf1")
    monkeypatch.setattr(
        "app.agent_runtime.invoke_agentcore_investigator",
        lambda *args, **kwargs: json.dumps(
            {
                "type": "final_diagnosis",
                "classification": "made_up",
                "status": "DIAGNOSED",
                "confidence": 2.0,
                "summary": "bad",
                "evidence": ["bad"],
            }
        ),
    )
    request = TriageRequest.model_validate(json.loads(Path("samples/insufficient-context.request.json").read_text(encoding="utf-8")))
    rca = analyze_request(request)
    decision = classify(request, rca)

    _, _, final_decision, metadata, _ = run_agent_platform(request, decision, rca, ToolRegistry(FakeContextClient()))

    assert final_decision == decision
    assert metadata["fallback"] is True
    assert metadata["fallback_reason"] == "invalid_final_diagnosis"


def test_triage_metadata_contains_mode_selection_for_simple_request() -> None:
    payload = post_sample("latency-degradation")

    assert payload["llm_metadata"]["investigation_mode"] == "deterministic_only"
    assert payload["llm_metadata"]["mode_selection"]["selected_mode"] == "deterministic_only"
    assert payload["llm_metadata"]["action_wording"]["skipped_reason"] == "deterministic_only_mode"
    assert payload["llm_metadata"]["cost_estimate"]["total_estimated_cost_usd"] == 0
    assert payload["llm_metadata"]["cost_estimate"]["total_tokens"] == 0


def test_triage_cost_estimate_records_llm_usage(monkeypatch) -> None:
    monkeypatch.setenv("ENABLE_AGENTCORE_LLM", "true")
    monkeypatch.setenv("AGENTCORE_RUNTIME_ARN", "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/tf1")
    monkeypatch.setenv("AIOPS_INVESTIGATION_MODE", "agent_assisted")
    monkeypatch.setenv("AIOPS_LLM_INPUT_COST_PER_1K", "0.002")
    monkeypatch.setenv("AIOPS_LLM_OUTPUT_COST_PER_1K", "0.006")
    monkeypatch.setattr("app.llm.invoke_agentcore_payload", lambda *args, **kwargs: json.dumps({"summary": "bounded summary"}))
    body = json.loads(Path("samples/insufficient-context.request.json").read_text(encoding="utf-8"))

    response = TestClient(app).post(
        "/v1/triage",
        json=body,
        headers={"X-Tenant-Id": body["tenant_id"], "X-Correlation-Id": body["correlation_id"]},
    )

    assert response.status_code == 200
    cost = response.json()["llm_metadata"]["cost_estimate"]
    assert cost["total_estimated_cost_usd"] > 0
    assert cost["total_prompt_tokens"] > 0
    assert cost["total_completion_tokens"] > 0
    assert {call["stage"] for call in cost["calls"]} >= {"summary", "actions"}


def test_missing_context_routes_to_agent_platform_when_agentcore_enabled(monkeypatch) -> None:
    monkeypatch.setattr("app.main.agent_platform_enabled", lambda: True)
    monkeypatch.setattr(
        "app.main.run_agent_platform",
        lambda request, decision, rca: (request, rca, decision, {"enabled": True, "fallback": True, "fallback_reason": "mocked"}, []),
    )
    body = json.loads(Path("samples/insufficient-context.request.json").read_text(encoding="utf-8"))
    response = TestClient(app).post(
        "/v1/triage",
        json=body,
        headers={"X-Tenant-Id": body["tenant_id"], "X-Correlation-Id": body["correlation_id"]},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["llm_metadata"]["investigation_mode"] == "agent_platform"
    assert payload["llm_metadata"]["mode_selection"]["planned_mode"] == "agent_platform"


def test_metadata_log_policy_omits_raw_evidence_fields() -> None:
    fields = sanitize_log_fields(
        {
            "audit_id": "audit-001",
            "tenant_id": "tenant-a",
            "stage": "completed",
            "status": "DIAGNOSED",
            "raw_evidence": "database password and customer payload",
            "message": "full customer log line",
        }
    )

    assert fields == {"audit_id": "audit-001", "tenant_id": "tenant-a", "stage": "completed", "status": "DIAGNOSED"}


def test_triage_rate_limit_returns_429(monkeypatch) -> None:
    monkeypatch.setenv("AIOPS_RATE_LIMIT_PER_MINUTE", "1")
    _rate_limit_hits.clear()
    body = metadata_only_triage_body()
    body["tenant_id"] = "tenant-rate"
    body["correlation_id"] = "corr-rate-001"
    body["incident_id"] = "inc-rate-001"
    client = TestClient(app)
    headers = {"X-Tenant-Id": body["tenant_id"], "X-Correlation-Id": body["correlation_id"]}

    first = client.post("/v1/triage", json=body, headers=headers)
    second = client.post("/v1/triage", json=body, headers=headers)

    assert first.status_code == 200
    assert second.status_code == 429


def test_triage_payload_limit_returns_413(monkeypatch) -> None:
    monkeypatch.setenv("AIOPS_MAX_REQUEST_BYTES", "512")
    body = metadata_only_triage_body()
    body["tenant_id"] = "tenant-payload"
    body["correlation_id"] = "corr-payload-001"
    body["incident_id"] = "inc-payload-001"
    body["alert"]["description"] = "x" * 2000

    response = TestClient(app).post(
        "/v1/triage",
        json=body,
        headers={"X-Tenant-Id": body["tenant_id"], "X-Correlation-Id": body["correlation_id"]},
    )

    assert response.status_code == 413


def test_qa_budget_degrades_confidence_and_records_metadata(monkeypatch) -> None:
    monkeypatch.setenv("AIOPS_LLM_MAX_TOKENS_PER_INCIDENT", "1")

    payload = post_sample("latency-degradation")

    assert payload["confidence"] < 0.82
    assert payload["llm_metadata"]["qa"]["result"] == "budget_exceeded"


def test_llm_qa_disabled_keeps_deterministic_behavior(monkeypatch) -> None:
    monkeypatch.delenv("ENABLE_QA_LLM", raising=False)
    request = TriageRequest.model_validate(json.loads(Path("samples/latency-degradation.request.json").read_text(encoding="utf-8")))
    rca = analyze_request(request)
    decision = classify(request, rca)

    metadata = run_qa(request, decision, rca)

    assert metadata["provider"] == "deterministic"
    assert metadata["result"] == "passed"
    assert "verdict" not in metadata


def test_llm_qa_pass_verdict_does_not_reduce_confidence(monkeypatch) -> None:
    monkeypatch.setenv("ENABLE_QA_LLM", "true")
    monkeypatch.setattr(
        "app.qa_judge.invoke_bedrock_qa",
        lambda model_id, payload: json.dumps(
            {
                "verdict": "pass",
                "issues": [],
                "confidence_delta": 0,
                "rationale": "Latency diagnosis is supported by metric and timeout log evidence.",
                "required_human_review": False,
            }
        ),
    )
    request = TriageRequest.model_validate(json.loads(Path("samples/latency-degradation.request.json").read_text(encoding="utf-8")))
    rca = analyze_request(request)
    decision = classify(request, rca)

    metadata = run_qa(request, decision, rca)

    assert metadata["provider"] == "bedrock"
    assert metadata["verdict"] == "pass"
    assert metadata["confidence_delta"] == 0
    assert metadata["required_human_review"] is False


def test_llm_qa_fail_verdict_reduces_confidence_and_records_issues(monkeypatch) -> None:
    monkeypatch.setenv("ENABLE_QA_LLM", "true")
    monkeypatch.setenv("AIOPS_QA_CONFIDENCE_PENALTY", "-0.2")
    monkeypatch.setattr(
        "app.qa_judge.invoke_bedrock_qa",
        lambda model_id, payload: json.dumps(
            {
                "verdict": "fail",
                "issues": ["unsupported_classification"],
                "confidence_delta": -0.2,
                "rationale": "The draft classification is not supported by the provided evidence.",
                "required_human_review": True,
            }
        ),
    )
    request = TriageRequest.model_validate(json.loads(Path("samples/latency-degradation.request.json").read_text(encoding="utf-8")))
    rca = analyze_request(request)
    decision = classify(request, rca)

    metadata = run_qa(request, decision, rca)

    assert metadata["result"] == "failed"
    assert metadata["llm_result"] == "failed"
    assert metadata["confidence_delta"] == -0.2
    assert metadata["issues"] == ["unsupported_classification"]
    assert metadata["required_human_review"] is True


def test_llm_qa_malformed_output_degrades_safely(monkeypatch) -> None:
    monkeypatch.setenv("ENABLE_QA_LLM", "true")
    monkeypatch.setattr("app.qa_judge.invoke_bedrock_qa", lambda model_id, payload: "{not-json")
    request = TriageRequest.model_validate(json.loads(Path("samples/latency-degradation.request.json").read_text(encoding="utf-8")))
    rca = analyze_request(request)
    decision = classify(request, rca)

    metadata = run_qa(request, decision, rca)

    assert metadata["provider"] == "bedrock"
    assert metadata["llm_result"] == "degraded"
    assert "error" in metadata
    assert metadata["result"] == "passed"


def test_llm_qa_budget_cap_skips_llm_and_keeps_deterministic(monkeypatch) -> None:
    monkeypatch.setenv("ENABLE_QA_LLM", "true")
    monkeypatch.setenv("AIOPS_QA_MAX_TOKENS_PER_INCIDENT", "1")
    request = TriageRequest.model_validate(json.loads(Path("samples/latency-degradation.request.json").read_text(encoding="utf-8")))
    rca = analyze_request(request)
    decision = classify(request, rca)

    metadata = run_qa(request, decision, rca)

    assert metadata["provider"] == "bedrock"
    assert metadata["llm_result"] == "budget_exceeded"
    assert metadata["result"] == "passed"


def test_qa_response_parser_clamps_schema() -> None:
    parsed = parse_qa_judge_response(
        '{"verdict":"uncertain","issues":["missing_evidence"],"confidence_delta":-5,"rationale":"Needs more evidence.","required_human_review":false}',
        penalty_floor=-0.1,
    )

    assert parsed["verdict"] == "uncertain"
    assert parsed["confidence_delta"] == -0.1
    assert parsed["required_human_review"] is True


def test_qa_response_parser_normalizes_pass_to_no_penalty() -> None:
    parsed = parse_qa_judge_response(
        '{"verdict":"pass","issues":["EVIDENCE_MATCH"],"confidence_delta":-0.05,"rationale":"Supported.","required_human_review":true}',
        penalty_floor=-0.1,
    )

    assert parsed["verdict"] == "pass"
    assert parsed["issues"] == []
    assert parsed["confidence_delta"] == 0
    assert parsed["required_human_review"] is False


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


@pytest.mark.parametrize(
    ("signal", "expected_action_id"),
    [
        ("cpu utilization breached 95 percent and memory pressure is high", "resource_saturation_triage"),
        ("disk filesystem no space left on device", "disk_pressure_triage"),
        ("kafka consumer lag and queue backlog increasing", "queue_backlog_triage"),
        ("oauth token validation returned 401 unauthorized", "auth_failure_triage"),
        ("dns lookup failed with tls certificate error", "network_dns_triage"),
        ("pod restart count increased and crashloopbackoff detected", "kubernetes_crashloop_triage"),
        ("429 rate limit throttling from upstream quota", "rate_limit_throttling_triage"),
    ],
)
def test_signal_specific_actions_are_selected(signal: str, expected_action_id: str) -> None:
    body = metadata_only_triage_body()
    body["metrics"] = [
        {
            "metric_name": signal.replace(" ", "_"),
            "service": "checkout-api",
            "unit": "count",
            "points": [{"ts": "2026-06-24T09:00:00Z", "value": 1}],
            "labels": {},
        }
    ]
    body["logs"] = [
        {
            "service": "checkout-api",
            "ts": "2026-06-24T09:00:00Z",
            "level": "error",
            "message": signal,
            "labels": {},
        }
    ]
    request = TriageRequest.model_validate(body)
    decision = {
        "classification": "general_investigation",
        "confidence": 0.65,
        "evidence": [signal],
        "summary": "General investigation with specific operational signal.",
    }
    rca = {"anomaly_evidence": [{"reason": signal}], "rca_candidates": []}

    actions = select_actions(request, decision, rca, None)

    assert expected_action_id in {action["id"] for action in actions}
    matched = [action for action in actions if action["id"] == expected_action_id][0]
    assert matched["risk"] == "low"
    assert matched["evidence_refs"]


def test_internal_runbook_action_is_selected_when_runbook_exists() -> None:
    body = metadata_only_triage_body()
    body["metrics"] = [
        {
            "metric_name": "http_5xx_count",
            "service": "checkout-api",
            "unit": "count",
            "points": [{"ts": "2026-06-24T09:00:00Z", "value": 12}],
            "labels": {},
        }
    ]
    body["ownership"] = {
        "service": "checkout-api",
        "owner_team": "payments-platform",
        "jira_project": "PAY",
        "runbooks": [{"title": "Checkout 5xx triage", "url": "runbook://checkout-5xx", "excerpt": "Review upstream and dependency errors."}],
    }
    request = TriageRequest.model_validate(body)
    decision = {
        "classification": "general_investigation",
        "confidence": 0.65,
        "evidence": ["5xx evidence exists."],
        "summary": "General investigation with runbook context.",
    }

    actions = select_actions(request, decision, {"anomaly_evidence": []}, "runbook://checkout-5xx")

    assert "consult_internal_runbooks" in {action["id"] for action in actions}


def test_internal_document_tools_are_scoped_and_searchable(tmp_path) -> None:
    ownership_path = tmp_path / "ownership.json"
    known_errors_path = tmp_path / "known-errors.json"
    ownership_path.write_text(
        json.dumps(
            {
                "service": "checkout-api",
                "owner_team": "payments-platform",
                "runbooks": [
                    {
                        "title": "Database timeout triage",
                        "url": "runbook://db-timeout",
                        "excerpt": "Check database connection pool and slow queries.",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    known_errors_path.write_text(
        json.dumps(
            [
                {
                    "tenant_id": "tenant-a",
                    "environment": "prod",
                    "service": "checkout-api",
                    "title": "Checkout database timeout known error",
                    "url": "known-error://checkout-db-timeout",
                    "description": "Recurring timeout when connection pool is exhausted.",
                },
                {
                    "tenant_id": "tenant-b",
                    "environment": "prod",
                    "service": "checkout-api",
                    "title": "Cross tenant item",
                    "description": "Must not be returned.",
                },
            ]
        ),
        encoding="utf-8",
    )
    registry = ToolRegistry(ContextClient(ownership_path=str(ownership_path), known_errors_path=str(known_errors_path)))
    scope = ToolScope(
        tenant_id="tenant-a",
        environment="prod",
        service="checkout-api",
        started_at="2026-06-24T08:45:00Z",
        received_at="2026-06-24T09:05:00Z",
    )

    runbooks = registry.execute("search_runbooks", {"query": "database"}, scope)["result"]
    known_errors = registry.execute("search_known_errors", {"query": "timeout"}, scope)["result"]

    assert runbooks[0]["url"] == "runbook://db-timeout"
    assert known_errors[0]["url"] == "known-error://checkout-db-timeout"
    assert len(known_errors) == 1
    with pytest.raises(ToolScopeError):
        registry.execute("search_known_errors", {"tenant_id": "tenant-b", "query": "timeout"}, scope)


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


def test_audit_record_is_persisted_and_queryable_without_raw_evidence(tmp_path, monkeypatch) -> None:
    audit_path = tmp_path / "audit-log.jsonl"
    monkeypatch.setenv("AIOPS_AUDIT_LOG_PATH", str(audit_path))
    client = TestClient(app)
    body = json.loads(Path("samples/latency-degradation.request.json").read_text(encoding="utf-8"))

    response = client.post(
        "/v1/triage",
        json=body,
        headers={"X-Tenant-Id": body["tenant_id"], "X-Correlation-Id": body["correlation_id"]},
    )
    payload = response.json()
    audit_response = client.get(f"/v1/audit/{payload['audit_id']}", headers={"X-Tenant-Id": body["tenant_id"]})
    cross_tenant_response = client.get(f"/v1/audit/{payload['audit_id']}", headers={"X-Tenant-Id": "tenant-b"})

    assert response.status_code == 200
    assert audit_response.status_code == 200
    record = audit_response.json()
    assert record["audit_id"] == payload["audit_id"]
    assert record["record_type"] == "triage_decision"
    assert record["retention_days"] >= 90
    assert record["decision"]["classification"] == payload["classification"]
    assert record["evidence_hashes"]["logs"]["count"] == len(body["logs"])
    assert record["tool_lineage"] == [] or all("args_hash" in item for item in record["tool_lineage"])
    assert body["logs"][0]["message"] not in json.dumps(record)
    assert cross_tenant_response.status_code == 404


def test_dynamodb_audit_record_is_persisted_with_expected_keys(monkeypatch) -> None:
    table = FakeDynamoTable()
    monkeypatch.setenv("AIOPS_PERSISTENCE_BACKEND", "dynamodb")
    monkeypatch.setenv("AIOPS_DYNAMODB_TABLE", "aiops-audit")
    monkeypatch.setattr("app.dynamodb_store.dynamodb_table", lambda: table)
    first = {
        "audit_id": "audit-001",
        "record_type": "triage_failure",
        "recorded_at": "2026-06-24T09:00:00Z",
        "tenant_id": "tenant-a",
        "duration_ms": 12.5,
    }
    second = {**first, "record_type": "triage_decision", "recorded_at": "2026-06-24T09:01:00Z", "duration_ms": 13.75}

    append_audit_record(first)
    append_audit_record(second)
    latest = latest_audit_record("audit-001")

    assert ("AUDIT#audit-001", "RECORDED#2026-06-24T09:00:00Z#triage_failure") in table.items
    item = table.items[("AUDIT#audit-001", "RECORDED#2026-06-24T09:01:00Z#triage_decision")]
    assert item["record"]["audit_id"] == "audit-001"
    assert item["expires_at"] > 0
    assert latest
    assert latest["record_type"] == "triage_decision"
    assert latest["duration_ms"] == 13.75
    assert latest["matching_records"] == 2


def test_dynamodb_idempotency_lifecycle_matches_file_shape(monkeypatch) -> None:
    table = FakeDynamoTable()
    monkeypatch.setenv("AIOPS_PERSISTENCE_BACKEND", "dynamodb")
    monkeypatch.setenv("AIOPS_DYNAMODB_TABLE", "aiops-audit")
    monkeypatch.setattr("app.dynamodb_store.dynamodb_table", lambda: table)

    started = start_record("audit-002", "hash-1")
    complete_record("audit-002", "hash-1", {"audit_id": "audit-002", "status": "DIAGNOSED"})
    completed = read_record("audit-002")
    fail_record("audit-003", "hash-2", "RuntimeError")
    failed = read_record("audit-003")

    assert started["status"] == "in_progress"
    assert completed
    assert completed["status"] == "completed"
    assert completed["response"] == {"audit_id": "audit-002", "status": "DIAGNOSED"}
    assert completed["response_hash"]
    assert failed
    assert failed["status"] == "failed_retryable"
    assert failed["error_class"] == "RuntimeError"
    assert ("IDEMPOTENCY#audit-002", "STATE") in table.items


def test_dynamodb_conditional_in_progress_conflict_returns_409(monkeypatch) -> None:
    table = FakeDynamoTable()
    monkeypatch.setenv("AIOPS_PERSISTENCE_BACKEND", "dynamodb")
    monkeypatch.setenv("AIOPS_DYNAMODB_TABLE", "aiops-audit")
    monkeypatch.setattr("app.dynamodb_store.dynamodb_table", lambda: table)
    body = metadata_only_triage_body()
    body["incident_id"] = "inc-dynamodb-race"
    body["correlation_id"] = "corr-dynamodb-race"
    request = TriageRequest.model_validate(body)
    audit_id = build_audit_id(request)
    write_record(
        audit_id,
        {
            "audit_id": audit_id,
            "status": "in_progress",
            "updated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "request_hash": "hash-in-flight",
        },
    )
    table.hidden_gets_remaining = 1
    table.fail_conditional_put = True

    response = TestClient(app).post("/v1/triage", json=body, headers={"X-Tenant-Id": body["tenant_id"], "X-Correlation-Id": body["correlation_id"]})

    assert response.status_code == 409


def test_large_inline_logs_are_truncated_and_audit_stays_metadata_only(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AIOPS_AUDIT_LOG_PATH", str(tmp_path / "audit-log.jsonl"))
    monkeypatch.setenv("AIOPS_MAX_LOG_RECORDS", "3")
    monkeypatch.setenv("AIOPS_MAX_LOG_MESSAGE_CHARS", "20")
    body = metadata_only_triage_body()
    body["incident_id"] = "inc-large-logs"
    body["correlation_id"] = "corr-large-logs"
    body["logs"] = [
        {
            "service": "checkout-api",
            "ts": f"2026-06-24T09:{minute:02d}:00Z",
            "level": "error",
            "message": f"very long timeout message {minute} " + ("x" * 200),
            "labels": {},
        }
        for minute in range(8)
    ]

    response = TestClient(app).post(
        "/v1/triage",
        json=body,
        headers={"X-Tenant-Id": body["tenant_id"], "X-Correlation-Id": body["correlation_id"]},
    )

    assert response.status_code == 200
    payload = response.json()
    budget = payload["llm_metadata"]["evidence_budget"]
    assert budget["truncated"] is True
    assert budget["counts_before"]["logs"] == 8
    assert budget["counts_after"]["logs"] == 3
    assert "max_log_message_chars" in budget["reasons"]
    record = read_record(payload["audit_id"])
    assert record and "x" * 80 not in json.dumps(record)


def test_large_metrics_are_capped_by_series_and_points(monkeypatch) -> None:
    monkeypatch.setenv("AIOPS_MAX_METRIC_SERIES", "2")
    monkeypatch.setenv("AIOPS_MAX_METRIC_POINTS_PER_SERIES", "3")
    body = metadata_only_triage_body()
    body["metrics"] = [
        {
            "metric_name": f"http_latency_p95_ms_{series}",
            "service": "checkout-api",
            "unit": "ms",
            "points": [{"ts": f"2026-06-24T09:{minute:02d}:00Z", "value": float(minute)} for minute in range(8)],
            "labels": {},
        }
        for series in range(5)
    ]
    request = TriageRequest.model_validate(body)

    compacted, metadata = compact_request_evidence(request)

    assert metadata["truncated"] is True
    assert len(compacted.metrics) == 2
    assert all(len(metric.points) == 3 for metric in compacted.metrics)


def test_tool_result_exceeding_budget_is_compacted(monkeypatch) -> None:
    monkeypatch.setenv("AIOPS_MAX_LOG_RECORDS", "2")
    monkeypatch.setenv("AIOPS_MAX_LOG_MESSAGE_CHARS", "12")
    registry = ToolRegistry(LargeLogContextClient())
    scope = ToolScope(
        tenant_id="tenant-a",
        environment="prod",
        service="checkout-api",
        started_at="2026-06-24T08:45:00Z",
        received_at="2026-06-24T09:05:00Z",
    )

    result = registry.execute("get_logs", {"limit": 50}, scope)

    assert result["truncated"] is True
    assert result["evidence_budget"]["counts_before"]["logs"] == 10
    assert len(result["result"]) == 2
    assert all(len(item["message"]) <= 12 for item in result["result"])


def test_agentcore_prompt_payload_receives_compacted_evidence(monkeypatch) -> None:
    monkeypatch.setenv("AIOPS_MAX_LOG_RECORDS", "2")
    body = metadata_only_triage_body()
    body["logs"] = [
        {
            "service": "checkout-api",
            "ts": f"2026-06-24T09:{minute:02d}:00Z",
            "level": "error",
            "message": f"timeout {minute}",
            "labels": {},
        }
        for minute in range(7)
    ]
    request, _ = compact_request_evidence(TriageRequest.model_validate(body))
    decision = classify(request)
    rca = analyze_request(request)

    payload = build_prompt_payload(request, decision, rca)

    assert len(payload["logs"]) == 2
    assert {item["message"] for item in payload["logs"]} == {"timeout 6", "timeout 5"}


def test_idempotency_replays_completed_response_for_same_hash(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AIOPS_AUDIT_LOG_PATH", str(tmp_path / "audit-log.jsonl"))
    body = metadata_only_triage_body()
    body["incident_id"] = "inc-idempotent"
    body["correlation_id"] = "corr-idempotent"
    client = TestClient(app)
    first = client.post("/v1/triage", json=body, headers={"X-Tenant-Id": body["tenant_id"], "X-Correlation-Id": body["correlation_id"]})
    assert first.status_code == 200

    def fail_if_called(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("triage_request should not run for completed replay")

    monkeypatch.setattr("app.main.triage_request", fail_if_called)
    second = client.post("/v1/triage", json=body, headers={"X-Tenant-Id": body["tenant_id"], "X-Correlation-Id": body["correlation_id"]})

    assert second.status_code == 200
    assert second.json() == first.json()


def test_idempotency_conflict_reprocesses_different_request_hash(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AIOPS_AUDIT_LOG_PATH", str(tmp_path / "audit-log.jsonl"))
    body = metadata_only_triage_body()
    body["incident_id"] = "inc-conflict"
    body["correlation_id"] = "corr-conflict"
    client = TestClient(app)
    first = client.post("/v1/triage", json=body, headers={"X-Tenant-Id": body["tenant_id"], "X-Correlation-Id": body["correlation_id"]})
    assert first.status_code == 200
    body["logs"] = [{"service": "checkout-api", "ts": "2026-06-24T09:00:00Z", "level": "error", "message": "new timeout evidence", "labels": {}}]

    second = client.post("/v1/triage", json=body, headers={"X-Tenant-Id": body["tenant_id"], "X-Correlation-Id": body["correlation_id"]})

    assert second.status_code == 200
    assert second.json()["llm_metadata"]["idempotency"]["conflict"] is True


def test_non_stale_in_progress_idempotency_record_rejects_duplicate(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AIOPS_AUDIT_LOG_PATH", str(tmp_path / "audit-log.jsonl"))
    body = metadata_only_triage_body()
    body["incident_id"] = "inc-in-progress"
    body["correlation_id"] = "corr-in-progress"
    request = TriageRequest.model_validate(body)
    write_record(
        build_audit_id(request),
        {
            "audit_id": build_audit_id(request),
            "status": "in_progress",
            "updated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "request_hash": request_hash({"request": body}),
        },
    )

    response = TestClient(app).post("/v1/triage", json=body, headers={"X-Tenant-Id": body["tenant_id"], "X-Correlation-Id": body["correlation_id"]})

    assert response.status_code == 409


def test_stale_in_progress_idempotency_record_allows_reprocessing(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AIOPS_AUDIT_LOG_PATH", str(tmp_path / "audit-log.jsonl"))
    body = metadata_only_triage_body()
    body["incident_id"] = "inc-stale"
    body["correlation_id"] = "corr-stale"
    request = TriageRequest.model_validate(body)
    stale_time = (datetime.now(timezone.utc) - timedelta(seconds=300)).isoformat().replace("+00:00", "Z")
    write_record(build_audit_id(request), {"audit_id": build_audit_id(request), "status": "in_progress", "updated_at": stale_time, "request_hash": "old"})

    response = TestClient(app).post("/v1/triage", json=body, headers={"X-Tenant-Id": body["tenant_id"], "X-Correlation-Id": body["correlation_id"]})

    assert response.status_code == 200
    assert response.json()["llm_metadata"]["idempotency"]["stale_reprocess"] is True


def test_exception_writes_failed_idempotency_record(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AIOPS_AUDIT_LOG_PATH", str(tmp_path / "audit-log.jsonl"))
    body = metadata_only_triage_body()
    body["incident_id"] = "inc-failed"
    body["correlation_id"] = "corr-failed"

    def fail_triage(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("simulated failure")

    monkeypatch.setattr("app.main.triage_request", fail_triage)
    response = TestClient(app, raise_server_exceptions=False).post(
        "/v1/triage",
        json=body,
        headers={"X-Tenant-Id": body["tenant_id"], "X-Correlation-Id": body["correlation_id"]},
    )

    assert response.status_code == 500
    record = read_record(build_audit_id(TriageRequest.model_validate(body)))
    assert record and record["status"] == "failed_retryable"
    assert record["error_class"] == "RuntimeError"


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


def test_incident_seed_ingests_local_evidence_bundle(tmp_path) -> None:
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(
        json.dumps(
            {
                "schema_version": "tf1.evidence_bundle.v1",
                "tenant_id": "tenant-a",
                "environment": "prod",
                "service": "checkout-api",
                "metrics": [
                    {
                        "metric_name": "http_latency_p95_ms",
                        "service": "checkout-api",
                        "unit": "ms",
                        "points": [{"ts": "2026-06-24T09:00:00Z", "value": 1500}],
                    }
                ],
                "logs": [
                    {
                        "service": "checkout-api",
                        "ts": "2026-06-24T09:00:30Z",
                        "level": "error",
                        "message": "redis timeout",
                    }
                ],
                "traces": [
                    {
                        "trace_id": "trace-001",
                        "service": "checkout-api",
                        "root_span": "POST /checkout",
                        "duration_ms": 3100,
                        "status": "error",
                        "bottleneck_service": "redis",
                    }
                ],
                "deploy_events": [
                    {
                        "service": "checkout-api",
                        "version": "sha-002",
                        "deployed_at": "2026-06-24T08:55:00Z",
                    }
                ],
                "ownership": {
                    "service": "checkout-api",
                    "owner_team": "payments-platform",
                    "jira_project": "PAY",
                },
                "runbooks": [{"title": "Redis timeout", "url": "runbook://redis-timeout"}],
                "data_lineage": {"primary_dataset": "synthetic"},
            }
        ),
        encoding="utf-8",
    )
    seed = base_seed(labels={"evidence_uri": "bundle.json"})
    registry = ToolRegistry(ContextClient(evidence_bundle_base_path=str(tmp_path)))

    body = build_triage_request_from_seed(seed, registry)
    request = TriageRequest.model_validate(body)

    assert request.metrics[0].metric_name == "http_latency_p95_ms"
    assert request.logs[0].message == "redis timeout"
    assert request.traces[0].operation == "POST /checkout"
    assert request.recent_deploys[0].version == "sha-002"
    assert request.ownership and request.ownership.runbooks[0].url == "runbook://redis-timeout"
    assert request.alert.labels["evidence_uri_status"] == "loaded"
    assert request.alert.labels["evidence_data_lineage"]["primary_dataset"] == "synthetic"


def test_triage_enriches_alert_metadata_first_request_from_registry(monkeypatch) -> None:
    monkeypatch.setattr("app.context_enrichment.ToolRegistry", lambda: ToolRegistry(FakeContextClient()))
    body = metadata_only_triage_body()

    response = TestClient(app).post(
        "/v1/triage",
        json=body,
        headers={"X-Tenant-Id": body["tenant_id"], "X-Correlation-Id": body["correlation_id"]},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "DIAGNOSED"
    assert payload["classification"] == "latency_degradation"
    assert payload["ticket_payload"]["project"] == "PAY"
    assert any(item["metric_name"] == "http_latency_p95_ms" for item in payload["anomaly_evidence"])


def test_triage_preserves_inline_evidence_when_evidence_uri_is_present(monkeypatch) -> None:
    monkeypatch.setattr("app.context_enrichment.ToolRegistry", lambda: ToolRegistry(BundleAndToolContextClient()))
    body = metadata_only_triage_body(labels={"evidence_uri": "bundle.json"})
    body["metrics"] = [
        {
            "metric_name": "http_latency_p95_ms",
            "service": "checkout-api",
            "unit": "ms",
            "points": [{"ts": "2026-06-24T09:00:00Z", "value": 1700}],
            "labels": {"source": "inline"},
        }
    ]

    response = TestClient(app).post(
        "/v1/triage",
        json=body,
        headers={"X-Tenant-Id": body["tenant_id"], "X-Correlation-Id": body["correlation_id"]},
    )

    assert response.status_code == 200
    payload = response.json()
    reasons = [item["reason"] for item in payload["anomaly_evidence"]]
    assert any("1700ms" in reason for reason in reasons)
    assert not any("2400ms" in reason for reason in reasons)
    assert payload["status"] == "DIAGNOSED"


def test_triage_missing_evidence_bundle_falls_back_to_scoped_tools(monkeypatch) -> None:
    monkeypatch.setattr("app.context_enrichment.ToolRegistry", lambda: ToolRegistry(FakeContextClient()))
    body = metadata_only_triage_body(labels={"evidence_uri": "missing.json"})

    response = TestClient(app).post(
        "/v1/triage",
        json=body,
        headers={"X-Tenant-Id": body["tenant_id"], "X-Correlation-Id": body["correlation_id"]},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "DIAGNOSED"
    assert payload["classification"] == "latency_degradation"


def test_out_of_scope_evidence_bundle_falls_back_to_scoped_tools(tmp_path, monkeypatch) -> None:
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(
        json.dumps(
            {
                "tenant_id": "tenant-b",
                "environment": "prod",
                "service": "checkout-api",
                "metrics": [
                    {
                        "metric_name": "http_latency_p95_ms",
                        "service": "checkout-api",
                        "unit": "ms",
                        "points": [{"ts": "2026-06-24T09:00:00Z", "value": 2400}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("app.context_enrichment.ToolRegistry", lambda: ToolRegistry(ScopedBundleContextClient(str(tmp_path))))
    body = metadata_only_triage_body(labels={"evidence_uri": "bundle.json"})

    response = TestClient(app).post(
        "/v1/triage",
        json=body,
        headers={"X-Tenant-Id": body["tenant_id"], "X-Correlation-Id": body["correlation_id"]},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "DIAGNOSED"
    assert any("1300ms" in item["reason"] for item in payload["anomaly_evidence"])
    assert not any("2400ms" in item["reason"] for item in payload["anomaly_evidence"])


def test_missing_evidence_bundle_keeps_context_sparse_for_insufficient_context() -> None:
    seed = base_seed(labels={"evidence_uri": "missing.json"})
    registry = ToolRegistry(ContextClient(evidence_bundle_base_path="datapack/scenarios"))

    body = build_triage_request_from_seed(seed, registry)
    response = TestClient(app).post(
        "/v1/triage",
        json=body,
        headers={"X-Tenant-Id": body["tenant_id"], "X-Correlation-Id": body["correlation_id"]},
    )

    assert body["metrics"] == []
    assert body["logs"] == []
    assert body["recent_deploys"] == []
    assert body["ownership"] is None
    assert response.status_code == 200
    assert response.json()["status"] == "INSUFFICIENT_CONTEXT"


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
    with pytest.raises(ToolScopeError):
        registry.execute("get_jira_history", {"service": "billing-api"}, scope)


def test_jira_history_suggests_configured_account_id(tmp_path, monkeypatch) -> None:
    history_path = tmp_path / "jira-history.json"
    history_path.write_text(
        json.dumps(
            [
                {
                    "tenant_id": "tenant-a",
                    "environment": "sandbox",
                    "service": "checkout-api",
                    "account_id": "acct-123",
                    "suggestion_reason": "Most recent checkout-api incidents were handled by the primary on-call.",
                }
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("JIRA_HISTORY_PATH", str(history_path))

    payload = post_sample("latency-degradation")

    assert payload["suggested_assignee_account_id"] == "acct-123"
    assert "primary on-call" in payload["suggestion_reason"]
    assert payload["ticket_payload"]["fields"]["suggested_assignee_account_id"] == "acct-123"


def test_jira_history_missing_mapping_routes_to_team(tmp_path, monkeypatch) -> None:
    history_path = tmp_path / "jira-history.json"
    history_path.write_text("[]", encoding="utf-8")
    monkeypatch.setenv("JIRA_HISTORY_PATH", str(history_path))

    payload = post_sample("latency-degradation")

    assert payload["suggested_assignee_account_id"] is None
    assert "route to payments-platform" in payload["suggestion_reason"]


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
    assert metadata["error"] == "RuntimeError"


def test_agentcore_response_reader_and_session_id_are_stable() -> None:
    request = TriageRequest.model_validate(json.loads(Path("samples/insufficient-context.request.json").read_text(encoding="utf-8")))
    response = {"contentType": "application/json", "response": [b'{"tool_calls":[{"name":"get_logs","args":{}}]}']}

    raw = read_agentcore_response(response)

    assert json.loads(raw)["tool_calls"][0]["name"] == "get_logs"
    assert agentcore_session_id(request) == agentcore_session_id(request)
    assert len(agentcore_session_id(request)) == 36


def test_triage_hub_notify_payload_maps_request_and_response() -> None:
    request_context = metadata_only_triage_body()
    request_context["ownership"] = {
        "service": "checkout-api",
        "owner_team": "payments-platform",
        "slack_channel": "#oncall-payments",
        "jira_project": "PAY",
    }
    response = {
        "incident_id": "inc-001",
        "classification": "latency_degradation",
        "confidence": 0.82,
        "status": "DIAGNOSED",
        "suspected_root_cause": {"summary": "Database timeout", "evidence": ["timeout log"]},
        "recommended_actions": [{"id": "dependency_timeout_triage", "summary": "Review dependency timeout signals."}],
        "suggested_assignee_account_id": "acct-123",
        "suggestion_reason": "Most recent checkout-api incidents were handled by this account.",
    }

    payload = build_triage_hub_notify_payload(response, request_context)

    assert payload == {
        "incident_id": "inc-001",
        "tenant_id": "tenant-a",
        "alert": request_context["alert"],
        "ownership": request_context["ownership"],
        "classification": "latency_degradation",
        "confidence": 0.82,
        "status": "DIAGNOSED",
        "suspected_root_cause": {"summary": "Database timeout", "evidence": ["timeout log"]},
        "recommended_actions": [{"id": "dependency_timeout_triage", "summary": "Review dependency timeout signals."}],
        "suggested_assignee_account_id": "acct-123",
        "suggestion_reason": "Most recent checkout-api incidents were handled by this account.",
    }


def test_triage_hub_sqs_dry_run_prints_payload_and_skips_aws(monkeypatch, capsys) -> None:
    monkeypatch.delenv("TRIAGE_HUB_NOTIFY_SQS_URL", raising=False)
    sqs = FakeSQS()
    request_context = metadata_only_triage_body()
    response = {"incident_id": "inc-001", "classification": "latency_degradation", "confidence": 0.82, "status": "DIAGNOSED"}

    publish_to_triage_hub_sqs(response, request_context, dry_run=True, sqs_client=sqs)

    output = json.loads(capsys.readouterr().out)
    assert output["triage_hub_sqs_dry_run"]["incident_id"] == "inc-001"
    assert output["triage_hub_sqs_dry_run"]["tenant_id"] == "tenant-a"
    assert output["reason"] == "dry_run_enabled"
    assert sqs.sent == []


def test_triage_hub_sqs_live_publish_sends_json_body(monkeypatch) -> None:
    monkeypatch.setenv("TRIAGE_HUB_NOTIFY_SQS_URL", "https://sqs.example/notify")
    sqs = FakeSQS()
    request_context = metadata_only_triage_body()
    response = {
        "incident_id": "inc-001",
        "classification": "latency_degradation",
        "confidence": 0.82,
        "status": "DIAGNOSED",
        "recommended_actions": [{"summary": "Review dependency timeout signals."}],
    }

    publish_to_triage_hub_sqs(response, request_context, sqs_client=sqs)

    assert len(sqs.sent) == 1
    queue_url, body = sqs.sent[0]
    assert queue_url == "https://sqs.example/notify"
    payload = json.loads(body)
    assert payload["incident_id"] == "inc-001"
    assert payload["tenant_id"] == "tenant-a"
    assert payload["alert"] == request_context["alert"]
    assert payload["recommended_actions"] == [{"summary": "Review dependency timeout signals."}]


def test_legacy_slack_webhook_publish_still_works(monkeypatch) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

    def fake_post(url: str, json: dict[str, Any], timeout: int) -> FakeResponse:
        calls.append((url, json))
        assert timeout == 5
        return FakeResponse()

    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.example/test")
    monkeypatch.setattr("app.aiops_worker.requests.post", fake_post)
    response = {
        "incident_id": "inc-001",
        "severity": "high",
        "classification": "latency_degradation",
        "status": "DIAGNOSED",
        "confidence": 0.82,
        "anomaly_evidence": [{"reason": "http_latency_p95_ms breached threshold"}],
        "recommended_actions": [{"summary": "Review dependency timeout signals."}],
    }

    publish_slack(response, dry_run=False, report_url="http://localhost:5173/#/reports/inc-001")

    assert calls
    assert calls[0][0] == "https://hooks.slack.example/test"
    assert "latency_degradation" in calls[0][1]["text"]
    assert "Report: http://localhost:5173/#/reports/inc-001" in calls[0][1]["text"]


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


def base_seed(labels: dict[str, Any] | None = None) -> IncidentSeed:
    return IncidentSeed.model_validate(
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
            "labels": labels or {},
        }
    )


def metadata_only_triage_body(labels: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "correlation_id": "corr-001",
        "tenant_id": "tenant-a",
        "incident_id": "inc-001",
        "environment": "prod",
        "received_at": "2026-06-24T09:05:00Z",
        "alert": {
            "alert_id": "alert-001",
            "source": "cdo-detector",
            "service": "checkout-api",
            "severity": "high",
            "title": "High p95 latency on checkout-api",
            "description": "p95 latency above threshold",
            "started_at": "2026-06-24T08:45:00Z",
            "labels": labels or {},
        },
        "metrics": [],
        "logs": [],
        "traces": [],
        "recent_deploys": [],
        "ownership": None,
    }


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

    def get_evidence_bundle(self, evidence_uri: str, scope: ToolScope) -> dict[str, Any] | None:
        return None

    def get_jira_history(self, service: str, environment: str, tenant_id: str) -> dict[str, Any]:
        return {
            "suggested_assignee_account_id": None,
            "suggestion_reason": "No fake Jira accountId history mapping is configured.",
        }


class LargeLogContextClient(FakeContextClient):
    def get_logs(self, service: str, environment: str, tenant_id: str, window: tuple[str, str], limit: int) -> list[dict[str, Any]]:
        return [
            {
                "service": service,
                "ts": f"2026-06-24T09:{minute:02d}:00Z",
                "level": "error",
                "message": f"timeout from overloaded dependency {minute}",
                "trace_id": f"trace-{minute}",
                "labels": {},
            }
            for minute in range(10)
        ]


class BundleAndToolContextClient(FakeContextClient):
    def get_evidence_bundle(self, evidence_uri: str, scope: ToolScope) -> dict[str, Any] | None:
        return {
            "tenant_id": scope.tenant_id,
            "environment": scope.environment,
            "service": scope.service,
            "metrics": [
                {
                    "metric_name": "http_latency_p95_ms",
                    "service": scope.service,
                    "unit": "ms",
                    "points": [{"ts": scope.received_at, "value": 2400}],
                    "labels": {"source": "bundle"},
                }
            ],
            "logs": [
                {
                    "service": scope.service,
                    "ts": scope.received_at,
                    "level": "error",
                    "message": "bundle redis timeout",
                }
            ],
            "deploy_events": [],
            "ownership": self.get_ownership(scope.service),
        }


class ScopedBundleContextClient(ContextClient):
    def __init__(self, evidence_bundle_base_path: str) -> None:
        ContextClient.__init__(self, evidence_bundle_base_path=evidence_bundle_base_path)

    @property
    def metrics_access_configured(self) -> bool:
        return True

    @property
    def logs_access_configured(self) -> bool:
        return True

    def get_metrics(self, service: str, environment: str, tenant_id: str, window: tuple[str, str]) -> list[dict[str, Any]]:
        return FakeContextClient().get_metrics(service, environment, tenant_id, window)

    def get_logs(self, service: str, environment: str, tenant_id: str, window: tuple[str, str], limit: int) -> list[dict[str, Any]]:
        return FakeContextClient().get_logs(service, environment, tenant_id, window, limit)


class FakeSQS:
    def __init__(self) -> None:
        self.deleted: list[tuple[str, str]] = []
        self.sent: list[tuple[str, str]] = []

    def delete_message(self, QueueUrl: str, ReceiptHandle: str) -> None:
        self.deleted.append((QueueUrl, ReceiptHandle))

    def send_message(self, QueueUrl: str, MessageBody: str) -> None:
        self.sent.append((QueueUrl, MessageBody))
