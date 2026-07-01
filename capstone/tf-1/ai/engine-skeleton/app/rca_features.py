from __future__ import annotations

from collections import Counter, defaultdict
from statistics import mean, pstdev
from typing import Any


CLASS_LABELS = ("critical_service_down", "latency_degradation", "noisy_or_ambiguous_alert")


def extract_rca_features(request: Any, rca: dict[str, Any], include_metadata: bool = False) -> dict[str, float]:
    features: dict[str, float] = {}
    features["metric_count"] = float(len(request.metrics))
    features["log_count"] = float(len(request.logs))
    features["trace_count"] = float(len(getattr(request, "traces", []) or []))
    features["deploy_count"] = float(len(request.recent_deploys))
    features["runbook_count"] = float(len(request.ownership.runbooks) if request.ownership and request.ownership.runbooks else 0)
    features.update(primary_service_series_features(request))
    features.update(global_series_features(request))
    features.update(anomaly_evidence_features(rca))
    features.update(rca_candidate_features(request, rca))
    features.update(log_signal_features(request))
    if include_metadata:
        features.update(alert_metadata_features(request))
    return features


def primary_service_series_features(request: Any) -> dict[str, float]:
    return series_features_for_service(request.metrics, request.alert.service, prefix="primary")


def global_series_features(request: Any) -> dict[str, float]:
    features: dict[str, float] = {}
    by_service: dict[str, list[Any]] = defaultdict(list)
    for metric in request.metrics:
        by_service[metric.service].append(metric)
    service_scores: list[float] = []
    for service, metrics in by_service.items():
        service_feature = series_features_for_service(metrics, service, prefix="service")
        score = (
            service_feature.get("service_latency_ratio_max", 0.0)
            + service_feature.get("service_workload_drop_max", 0.0)
            + service_feature.get("service_resource_ratio_max", 0.0)
            + service_feature.get("service_error_ratio_max", 0.0)
        )
        service_scores.append(score)
        for key, value in service_feature.items():
            if key.endswith("_max"):
                aggregate_key = key.replace("service_", "global_")
                features[aggregate_key] = max(features.get(aggregate_key, 0.0), value)
    features["service_signal_count"] = float(len(service_scores))
    features["service_signal_spread"] = max(service_scores) - mean(service_scores) if service_scores else 0.0
    return features


def series_features_for_service(metrics: list[Any], service: str, prefix: str) -> dict[str, float]:
    features = {
        f"{prefix}_latency_ratio_max": 0.0,
        f"{prefix}_latency_abs_max": 0.0,
        f"{prefix}_latency_window_ratio_max": 0.0,
        f"{prefix}_workload_drop_max": 0.0,
        f"{prefix}_workload_baseline_max": 0.0,
        f"{prefix}_resource_ratio_max": 0.0,
        f"{prefix}_cpu_ratio_max": 0.0,
        f"{prefix}_memory_ratio_max": 0.0,
        f"{prefix}_disk_ratio_max": 0.0,
        f"{prefix}_error_abs_max": 0.0,
        f"{prefix}_error_ratio_max": 0.0,
        f"{prefix}_series_count": 0.0,
    }
    for series in metrics:
        if series.service != service:
            continue
        values = [float(point.value) for point in series.points]
        if len(values) < 2:
            continue
        features[f"{prefix}_series_count"] += 1.0
        baseline = values[:-1]
        current = values[-1]
        baseline_mean = mean(baseline)
        ratio = current / max(abs(baseline_mean), 1e-9)
        window_ratio = incident_window_ratio(values)
        name = series.metric_name.lower()
        if is_latency_metric(name):
            features[f"{prefix}_latency_ratio_max"] = max(features[f"{prefix}_latency_ratio_max"], ratio)
            features[f"{prefix}_latency_abs_max"] = max(features[f"{prefix}_latency_abs_max"], current)
            features[f"{prefix}_latency_window_ratio_max"] = max(features[f"{prefix}_latency_window_ratio_max"], window_ratio)
        elif is_workload_metric(name):
            if baseline_mean > 0:
                drop = (baseline_mean - current) / baseline_mean
                features[f"{prefix}_workload_drop_max"] = max(features[f"{prefix}_workload_drop_max"], drop)
                features[f"{prefix}_workload_baseline_max"] = max(features[f"{prefix}_workload_baseline_max"], baseline_mean)
        elif is_resource_metric(name):
            features[f"{prefix}_resource_ratio_max"] = max(features[f"{prefix}_resource_ratio_max"], ratio)
            if "cpu" in name:
                features[f"{prefix}_cpu_ratio_max"] = max(features[f"{prefix}_cpu_ratio_max"], ratio)
            elif "disk" in name or "diskio" in name:
                features[f"{prefix}_disk_ratio_max"] = max(features[f"{prefix}_disk_ratio_max"], ratio)
            elif "mem" in name or "memory" in name:
                features[f"{prefix}_memory_ratio_max"] = max(features[f"{prefix}_memory_ratio_max"], ratio)
        elif is_error_metric(name):
            features[f"{prefix}_error_abs_max"] = max(features[f"{prefix}_error_abs_max"], current)
            features[f"{prefix}_error_ratio_max"] = max(features[f"{prefix}_error_ratio_max"], ratio)
    return features


def incident_window_ratio(values: list[float]) -> float:
    if len(values) < 8:
        return 0.0
    tail_count = max(3, min(8, len(values) // 4))
    baseline = values[:-tail_count]
    incident = values[-tail_count:]
    if not baseline:
        return 0.0
    return mean(incident) / max(abs(mean(baseline)), 1e-9)


def anomaly_evidence_features(rca: dict[str, Any]) -> dict[str, float]:
    features: dict[str, float] = {
        "anomaly_count": 0.0,
        "anomaly_score_sum": 0.0,
        "anomaly_score_max": 0.0,
    }
    detector_counts: Counter[str] = Counter()
    family_scores: dict[str, float] = defaultdict(float)
    for item in rca.get("anomaly_evidence", []):
        if not isinstance(item, dict):
            continue
        features["anomaly_count"] += 1.0
        detector = str(item.get("detector", "unknown")).lower()
        detector_counts[detector] += 1
        try:
            score = float(item.get("score", 0.0))
        except (TypeError, ValueError):
            score = 0.0
        features["anomaly_score_sum"] += score
        features["anomaly_score_max"] = max(features["anomaly_score_max"], score)
        text = " ".join(str(item.get(key, "")) for key in ("metric_name", "reason", "detector", "family")).lower()
        for family in metric_families_from_text(text):
            family_scores[family] += score
    for detector, count in detector_counts.items():
        features[f"detector_count_{safe_key(detector)}"] = float(count)
    for family, score in family_scores.items():
        features[f"family_score_{family}"] = score
    return features


def rca_candidate_features(request: Any, rca: dict[str, Any]) -> dict[str, float]:
    candidates = rca.get("rca_candidates", [])
    top = candidates[0] if candidates and isinstance(candidates[0], dict) else {}
    return {
        "top_candidate_is_alert_service": 1.0 if top.get("service") == request.alert.service else 0.0,
        "top_candidate_score": float(top.get("score", 0.0) or 0.0),
        "top_candidate_confidence": float(top.get("confidence", 0.0) or 0.0),
        "candidate_count": float(len(candidates)),
    }


def log_signal_features(request: Any) -> dict[str, float]:
    text = " ".join(f"{log.level} {log.message}" for log in request.logs).lower()
    return {
        "log_error_token": 1.0 if any(token in text for token in ("error", "failed", "exception", "5xx")) else 0.0,
        "log_timeout_token": 1.0 if any(token in text for token in ("timeout", "deadline", "slow")) else 0.0,
        "log_down_token": 1.0 if any(token in text for token in ("down", "unavailable", "refused")) else 0.0,
    }


def alert_metadata_features(request: Any) -> dict[str, float]:
    text = f"{request.alert.title} {request.alert.description or ''}".lower()
    severity = str(request.alert.severity).lower()
    return {
        "severity_critical": 1.0 if severity == "critical" else 0.0,
        "severity_high": 1.0 if severity == "high" else 0.0,
        "severity_low_or_unknown": 1.0 if severity in {"low", "unknown"} else 0.0,
        "alert_latency_token": 1.0 if any(token in text for token in ("latency", "p95", "p90", "duration", "timeout", "delay", "slow")) else 0.0,
        "alert_down_token": 1.0 if any(token in text for token in ("down", "unavailable", "loss", "critical")) else 0.0,
        "alert_noisy_token": 1.0 if any(token in text for token in ("noisy", "flapping", "false", "ambiguous")) else 0.0,
    }


def metric_families_from_text(text: str) -> list[str]:
    families: list[str] = []
    if any(token in text for token in ("latency", "p95", "p90", "p50", "duration", "timeout", "delay", "slow")):
        families.append("latency")
    if any(token in text for token in ("traffic loss", "traffic_loss", "workload", "throughput", "request", "qps", "rps")):
        families.append("traffic_loss")
    if any(token in text for token in ("error", "5xx", "availability", "down", "unavailable", "refused")):
        families.append("error_availability")
    if "cpu" in text:
        families.append("resource_cpu")
    if any(token in text for token in ("mem", "memory")):
        families.append("resource_memory")
    if any(token in text for token in ("disk", "diskio")):
        families.append("resource_disk")
    return families


def is_latency_metric(name: str) -> bool:
    return any(token in name for token in ("latency", "duration", "timeout", "delay"))


def is_workload_metric(name: str) -> bool:
    return any(token in name for token in ("request", "requests", "traffic", "throughput", "qps", "rps", "success", "count", "rate", "workload")) and not is_error_metric(name)


def is_resource_metric(name: str) -> bool:
    return any(token in name for token in ("cpu", "mem", "memory", "disk", "diskio"))


def is_error_metric(name: str) -> bool:
    return any(token in name for token in ("error", "5xx", "availability"))


def safe_key(value: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in value.lower()).strip("_")
