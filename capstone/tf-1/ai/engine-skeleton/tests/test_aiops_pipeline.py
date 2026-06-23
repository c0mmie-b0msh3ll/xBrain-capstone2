from __future__ import annotations

import argparse
import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.aiops_worker import build_report, build_triage_request, detect_incident, offline_raw_observability
from app.main import MetricPoint, MetricSeries, app
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
    assert payload["slack_payload"]["text"]
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
