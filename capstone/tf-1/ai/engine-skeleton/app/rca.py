from __future__ import annotations

from collections import defaultdict
from statistics import mean, pstdev
from typing import Any

import numpy as np
from sklearn.ensemble import IsolationForest


ERROR_TOKENS = ("error", "timeout", "failed", "refused", "exhausted", "down", "deadline")


def analyze_request(request: Any) -> dict[str, Any]:
    metric_evidence = detect_metric_anomalies(request.metrics)
    log_evidence = detect_log_anomalies(request.logs)
    evidence = metric_evidence + log_evidence
    topology = infer_topology(request)
    causal_hints = infer_causal_hints(request.metrics)
    candidates = rank_rca_candidates(request, evidence, topology, causal_hints)
    summary = build_investigation_summary(request, evidence, candidates, causal_hints)
    return {
        "anomaly_evidence": evidence,
        "service_topology": topology,
        "causal_hints": causal_hints,
        "rca_candidates": candidates,
        "investigation_summary": summary,
    }


def detect_metric_anomalies(metrics: list[Any]) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for series in metrics:
        values = [float(point.value) for point in series.points]
        if not values:
            continue
        metric_name = series.metric_name
        service = series.service
        unit = series.unit

        threshold_reason = threshold_anomaly(metric_name, values[-1], unit)
        if threshold_reason:
            evidence.append(
                {
                    "detector": "threshold",
                    "service": service,
                    "metric_name": metric_name,
                    "severity": "high",
                    "score": 1.0,
                    "reason": threshold_reason,
                }
            )

        if len(values) >= 2:
            baseline = values[:-1]
            current = values[-1]
            baseline_mean = mean(baseline)
            baseline_std = pstdev(baseline) if len(baseline) > 1 else max(abs(baseline_mean) * 0.1, 1.0)
            z_score = (current - baseline_mean) / baseline_std if baseline_std else 0.0
            if abs(z_score) >= 3.0:
                evidence.append(
                    {
                        "detector": "rolling_zscore_3sigma",
                        "service": service,
                        "metric_name": metric_name,
                        "severity": "high",
                        "score": round(min(abs(z_score) / 6.0, 1.0), 3),
                        "reason": f"{metric_name} current value {current:g}{unit or ''} is {z_score:.1f} sigma from baseline.",
                    }
                )

        ewma = ewma_anomaly(values)
        if ewma:
            evidence.append(
                {
                    "detector": "ewma_drift",
                    "service": service,
                    "metric_name": metric_name,
                    "severity": "medium",
                    "score": ewma["score"],
                    "reason": f"{metric_name} drifted from EWMA {ewma['expected']:.2f} to {values[-1]:g}{unit or ''}.",
                }
            )

        isolation = isolation_forest_anomaly(values)
        if isolation:
            evidence.append(
                {
                    "detector": "isolation_forest",
                    "service": service,
                    "metric_name": metric_name,
                    "severity": "medium",
                    "score": isolation["score"],
                    "reason": f"{metric_name} latest point was isolated against recent metric shape.",
                }
            )
    return evidence


def threshold_anomaly(metric_name: str, value: float, unit: str | None) -> str | None:
    name = metric_name.lower()
    suffix = unit or ""
    if "latency" in name and value >= 1000:
        return f"{metric_name} breached latency threshold at {value:g}{suffix}."
    if ("error" in name or "5xx" in name) and value >= 5:
        return f"{metric_name} breached error threshold at {value:g}{suffix}."
    if "availability" in name and value < 95:
        return f"{metric_name} fell below availability threshold at {value:g}{suffix}."
    if "timeout" in name and value >= 10:
        return f"{metric_name} breached timeout threshold at {value:g}{suffix}."
    if ("cpu" in name or "memory" in name) and value >= 85:
        return f"{metric_name} breached saturation threshold at {value:g}{suffix}."
    return None


def ewma_anomaly(values: list[float], alpha: float = 0.35) -> dict[str, float] | None:
    if len(values) < 4:
        return None
    expected = values[0]
    residuals: list[float] = []
    for value in values[1:-1]:
        residuals.append(abs(value - expected))
        expected = alpha * value + (1 - alpha) * expected
    spread = pstdev(residuals) if len(residuals) > 1 else max(mean(residuals), 1.0)
    drift = abs(values[-1] - expected)
    if drift >= max(3 * spread, abs(expected) * 0.25, 1.0):
        return {"expected": expected, "score": round(min(drift / max(abs(expected), 1.0), 1.0), 3)}
    return None


def isolation_forest_anomaly(values: list[float]) -> dict[str, float] | None:
    if len(values) < 8 or len(set(values)) < 3:
        return None
    data = np.array(values, dtype=float).reshape(-1, 1)
    model = IsolationForest(contamination=0.15, random_state=7)
    predictions = model.fit_predict(data)
    if predictions[-1] == -1:
        raw_score = -float(model.score_samples(data[-1:])[0])
        return {"score": round(min(max(raw_score, 0.1), 1.0), 3)}
    return None


def detect_log_anomalies(logs: list[Any]) -> list[dict[str, Any]]:
    counts: dict[str, int] = defaultdict(int)
    samples: dict[str, str] = {}
    for log in logs:
        text = f"{log.level} {log.message}".lower()
        if any(token in text for token in ERROR_TOKENS):
            counts[log.service] += 1
            samples.setdefault(log.service, log.message)
    return [
        {
            "detector": "log_keyword",
            "service": service,
            "metric_name": "logs",
            "severity": "medium",
            "score": round(min(count / 5.0, 1.0), 3),
            "reason": f"{count} error-like log entries; sample: {samples[service]}",
        }
        for service, count in counts.items()
    ]


def infer_topology(request: Any) -> dict[str, Any]:
    root_service = request.alert.service
    nodes = {root_service}
    edges: set[tuple[str, str, str]] = set()
    for metric in request.metrics:
        nodes.add(metric.service)
        dependency = metric.labels.get("dependency") if metric.labels else None
        if dependency:
            nodes.add(str(dependency))
            edges.add((metric.service, str(dependency), "metric_dependency_label"))
    for log in request.logs:
        nodes.add(log.service)
        labels = log.labels or {}
        dependency = labels.get("dependency")
        if not dependency:
            dependency = dependency_from_text(log.message)
        if dependency:
            nodes.add(str(dependency))
            edges.add((log.service, str(dependency), "log_dependency_hint"))
    for deploy in request.recent_deploys:
        nodes.add(deploy.service)
    return {
        "root_service": root_service,
        "nodes": sorted(nodes),
        "edges": [
            {"source": source, "target": target, "evidence": evidence}
            for source, target, evidence in sorted(edges)
        ],
    }


def dependency_from_text(text: str) -> str | None:
    lowered = text.lower()
    for token in ("redis", "postgres", "mysql", "kafka", "s3", "checkout", "payment", "inventory"):
        if token in lowered:
            return token
    return None


def infer_causal_hints(metrics: list[Any]) -> list[dict[str, Any]]:
    hints: list[dict[str, Any]] = []
    by_service: dict[str, list[float]] = {}
    for metric in metrics:
        values = [float(point.value) for point in metric.points]
        if len(values) >= 6:
            by_service.setdefault(metric.service, []).extend(values[-12:])
    services = sorted(by_service)
    for left_index, left in enumerate(services):
        for right in services[left_index + 1 :]:
            left_values = by_service[left]
            right_values = by_service[right]
            count = min(len(left_values), len(right_values))
            if count < 6:
                continue
            corr = lag_correlation(left_values[-count:], right_values[-count:])
            if abs(corr) >= 0.7:
                hints.append(
                    {
                        "type": "lag_correlation",
                        "source": left,
                        "target": right,
                        "score": round(abs(corr), 3),
                        "direction": "experimental",
                        "reason": f"{left} and {right} metric movement is strongly lag-correlated; this is supporting evidence, not proof.",
                    }
                )
    if not hints and metrics:
        return [
            {
                "type": "insufficient_points",
                "score": 0.0,
                "reason": "Causal hints require at least six aligned points per compared service.",
            }
        ]
    return hints


def lag_correlation(left: list[float], right: list[float]) -> float:
    if len(left) < 3 or len(right) < 3:
        return 0.0
    left_lagged = np.array(left[:-1], dtype=float)
    right_current = np.array(right[1:], dtype=float)
    if np.std(left_lagged) == 0 or np.std(right_current) == 0:
        return 0.0
    return float(np.corrcoef(left_lagged, right_current)[0, 1])


def rank_rca_candidates(
    request: Any,
    evidence: list[dict[str, Any]],
    topology: dict[str, Any],
    causal_hints: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    scores: dict[str, float] = defaultdict(float)
    reasons: dict[str, list[str]] = defaultdict(list)
    for item in evidence:
        service = item["service"]
        scores[service] += float(item.get("score", 0.5))
        reasons[service].append(item["reason"])
    for edge in topology.get("edges", []):
        if scores.get(edge["source"], 0) > 0:
            scores[edge["target"]] += 0.35
            reasons[edge["target"]].append(f"Dependency of impacted service {edge['source']} via {edge['evidence']}.")
    for deploy in request.recent_deploys:
        scores[deploy.service] += 0.45
        reasons[deploy.service].append(f"Recent deploy {deploy.version} at {deploy.deployed_at}.")
    for hint in causal_hints:
        source = hint.get("source")
        if source:
            scores[str(source)] += float(hint.get("score", 0)) * 0.3
            reasons[str(source)].append(hint["reason"])

    if not scores:
        scores[request.alert.service] = 0.25
        reasons[request.alert.service].append("Alerted service is the only available RCA anchor.")

    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    top_score = max(ranked[0][1], 1.0)
    return [
        {
            "rank": index + 1,
            "service": service,
            "score": round(score, 3),
            "confidence": round(min(score / top_score, 1.0), 3),
            "reasons": reasons[service][:4],
        }
        for index, (service, score) in enumerate(ranked[:5])
    ]


def build_investigation_summary(
    request: Any,
    evidence: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    causal_hints: list[dict[str, Any]],
) -> str:
    top = candidates[0]["service"] if candidates else request.alert.service
    evidence_count = len(evidence)
    causal_state = "causal hints available" if causal_hints and causal_hints[0].get("type") != "insufficient_points" else "causal hints unavailable"
    return (
        f"Deterministic investigator summary: {request.alert.service} produced {evidence_count} anomaly evidence item(s). "
        f"Top RCA candidate is {top}. Recent deploy, topology, metric, and log signals were scored without external LLM calls; {causal_state}."
    )
