from __future__ import annotations

from copy import deepcopy
from typing import Any


RISK_ORDER = {"low": 1, "medium": 2, "high": 3}


ACTION_CATALOG: dict[str, dict[str, Any]] = {
    "attach_telemetry_context": {
        "id": "attach_telemetry_context",
        "type": "ESCALATE_OWNER",
        "risk": "low",
        "summary": "Ask the AIOps context layer to attach metrics, logs, recent deploys, ownership, and runbook context before diagnosis.",
        "why": "The alert does not include enough bounded telemetry to support a safe root-cause recommendation.",
        "requires_human_approval": False,
        "approval_reason": None,
        "scenario_tags": ["insufficient_context"],
    },
    "observe_user_impact": {
        "id": "observe_user_impact",
        "type": "OBSERVE",
        "risk": "low",
        "summary": "Watch for alert repetition and compare it with user-impacting metrics before opening remediation work.",
        "why": "The alert signal is weak or ambiguous, so observation is safer than remediation.",
        "requires_human_approval": False,
        "approval_reason": None,
        "scenario_tags": ["noisy_or_ambiguous_alert"],
    },
    "human_review_noisy_alert": {
        "id": "human_review_noisy_alert",
        "type": "HUMAN_REVIEW",
        "risk": "low",
        "summary": "Have the service owner confirm whether this alert is actionable before escalation.",
        "why": "Human confirmation is required when deterministic evidence does not establish a clear impact.",
        "requires_human_approval": False,
        "approval_reason": None,
        "scenario_tags": ["noisy_or_ambiguous_alert", "general_investigation"],
    },
    "service_down_runbook": {
        "id": "service_down_runbook",
        "type": "RUNBOOK_CHECK",
        "risk": "low",
        "summary": "Follow the service-down runbook and verify health checks, dependency availability, and recent deploy status.",
        "why": "Critical availability alerts need bounded investigation before any remediation is attempted.",
        "requires_human_approval": False,
        "approval_reason": None,
        "scenario_tags": ["critical_service_down"],
    },
    "page_service_owner": {
        "id": "page_service_owner",
        "type": "ESCALATE_OWNER",
        "risk": "low",
        "summary": "Page or notify the owning team for immediate human review.",
        "why": "Critical impact requires the accountable owner to validate and coordinate response.",
        "requires_human_approval": False,
        "approval_reason": None,
        "scenario_tags": ["critical_service_down"],
    },
    "dependency_timeout_triage": {
        "id": "dependency_timeout_triage",
        "type": "RUNBOOK_CHECK",
        "risk": "low",
        "summary": "Check dependency timeout signals, connection pool saturation, and slow query or cache latency logs.",
        "why": "Timeout and dependency evidence can explain latency without requiring immediate rollback or restart.",
        "requires_human_approval": False,
        "approval_reason": None,
        "scenario_tags": ["latency_degradation", "redis_database_timeout"],
    },
    "latency_saturation_review": {
        "id": "latency_saturation_review",
        "type": "HUMAN_REVIEW",
        "risk": "low",
        "summary": "Review saturation metrics, dependency latency, and trace samples before assigning remediation.",
        "why": "Latency degradation has supporting evidence, but the exact bottleneck still needs operator validation.",
        "requires_human_approval": False,
        "approval_reason": None,
        "scenario_tags": ["latency_degradation"],
    },
    "consider_recent_deploy_rollback": {
        "id": "consider_recent_deploy_rollback",
        "type": "ROLLBACK_CONSIDER",
        "risk": "medium",
        "summary": "If recent deploy correlation is confirmed, consider rollback through the approved runbook.",
        "why": "A recent deploy overlaps with the incident window and may be related to the observed degradation.",
        "requires_human_approval": True,
        "approval_reason": "Rollback changes production state and must be approved by the owning team.",
        "scenario_tags": ["recent_deploy_correlation", "latency_degradation", "critical_service_down"],
    },
    "resource_saturation_triage": {
        "id": "resource_saturation_triage",
        "type": "RUNBOOK_CHECK",
        "risk": "low",
        "summary": "Check CPU, memory, thread, and connection-pool saturation before changing service capacity.",
        "why": "Saturation evidence can explain latency, errors, or availability loss and should be verified before remediation.",
        "requires_human_approval": False,
        "approval_reason": None,
        "scenario_tags": ["cpu_saturation", "memory_pressure", "connection_pool_exhaustion"],
    },
    "disk_pressure_triage": {
        "id": "disk_pressure_triage",
        "type": "RUNBOOK_CHECK",
        "risk": "low",
        "summary": "Check disk usage, inode pressure, write errors, and log volume growth on the affected service.",
        "why": "Disk pressure can cause write failures, pod restarts, and degraded availability without requiring immediate code rollback.",
        "requires_human_approval": False,
        "approval_reason": None,
        "scenario_tags": ["disk_pressure"],
    },
    "queue_backlog_triage": {
        "id": "queue_backlog_triage",
        "type": "RUNBOOK_CHECK",
        "risk": "low",
        "summary": "Inspect queue lag, consumer health, retry rate, and downstream dependency latency.",
        "why": "Queue backlog evidence usually needs consumer/dependency validation before scaling or replay decisions.",
        "requires_human_approval": False,
        "approval_reason": None,
        "scenario_tags": ["queue_backlog", "kafka_lag"],
    },
    "auth_failure_triage": {
        "id": "auth_failure_triage",
        "type": "RUNBOOK_CHECK",
        "risk": "low",
        "summary": "Check authentication provider health, token validation errors, credential rotation, and recent auth config changes.",
        "why": "Authentication failures need identity-provider and config validation before user-impacting changes are made.",
        "requires_human_approval": False,
        "approval_reason": None,
        "scenario_tags": ["auth_failure"],
    },
    "network_dns_triage": {
        "id": "network_dns_triage",
        "type": "RUNBOOK_CHECK",
        "risk": "low",
        "summary": "Check DNS resolution, TLS/certificate validity, network policy, and upstream connectivity for the affected path.",
        "why": "Network, DNS, or TLS symptoms often look like dependency failures and should be isolated before remediation.",
        "requires_human_approval": False,
        "approval_reason": None,
        "scenario_tags": ["network_dns", "tls_certificate"],
    },
    "kubernetes_crashloop_triage": {
        "id": "kubernetes_crashloop_triage",
        "type": "RUNBOOK_CHECK",
        "risk": "low",
        "summary": "Check pod restart count, crash-loop events, readiness failures, and recent container image or config changes.",
        "why": "Crash-loop or readiness evidence should be confirmed from Kubernetes events before rollout or config actions.",
        "requires_human_approval": False,
        "approval_reason": None,
        "scenario_tags": ["k8s_crashloop", "readiness_failure"],
    },
    "rate_limit_throttling_triage": {
        "id": "rate_limit_throttling_triage",
        "type": "RUNBOOK_CHECK",
        "risk": "low",
        "summary": "Check throttling, 429 responses, quota limits, retry storms, and caller traffic changes.",
        "why": "Rate-limit symptoms need quota and traffic validation before changing limits or clients.",
        "requires_human_approval": False,
        "approval_reason": None,
        "scenario_tags": ["rate_limit", "throttling"],
    },
    "consult_internal_runbooks": {
        "id": "consult_internal_runbooks",
        "type": "RUNBOOK_CHECK",
        "risk": "low",
        "summary": "Review matching internal runbooks, known-error notes, or recent postmortems before choosing a remediation path.",
        "why": "Internal operational knowledge is safer and more specific than generic public guidance.",
        "requires_human_approval": False,
        "approval_reason": None,
        "scenario_tags": ["internal_docs", "known_error"],
    },
}


def select_actions(request: Any, decision: dict[str, Any], rca: dict[str, Any], runbook_ref: str | None) -> list[dict[str, Any]]:
    classification = decision["classification"]
    evidence_refs = build_evidence_refs(request, decision, rca)
    selected_ids = candidate_ids_for(request, decision, rca)
    max_risk = allowed_risk(classification, float(decision["confidence"]))

    actions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for action_id in selected_ids:
        if action_id in seen:
            continue
        catalog_action = ACTION_CATALOG[action_id]
        if RISK_ORDER[catalog_action["risk"]] > RISK_ORDER[max_risk]:
            continue
        action = deepcopy(catalog_action)
        action["priority"] = len(actions) + 1
        action["runbook_ref"] = runbook_ref
        action["evidence_refs"] = evidence_refs_for_action(action_id, evidence_refs)
        action.pop("scenario_tags", None)
        actions.append(action)
        seen.add(action_id)

    if actions:
        return actions

    fallback_id = "attach_telemetry_context" if classification == "insufficient_context" else "human_review_noisy_alert"
    fallback = deepcopy(ACTION_CATALOG[fallback_id])
    fallback["priority"] = 1
    fallback["runbook_ref"] = runbook_ref
    fallback["evidence_refs"] = evidence_refs_for_action(fallback_id, evidence_refs)
    fallback.pop("scenario_tags", None)
    return [fallback]


def candidate_ids_for(request: Any, decision: dict[str, Any], rca: dict[str, Any]) -> list[str]:
    classification = decision["classification"]
    if classification == "insufficient_context":
        return ["attach_telemetry_context"]
    if classification == "noisy_or_ambiguous_alert":
        return ["observe_user_impact", "human_review_noisy_alert"]
    if classification == "critical_service_down":
        ids = ["service_down_runbook", "page_service_owner"]
        ids.extend(signal_action_ids(request, rca))
        if request.recent_deploys:
            ids.append("consider_recent_deploy_rollback")
        return ids
    if classification == "latency_degradation":
        ids = []
        if has_timeout_or_dependency_signal(request, rca):
            ids.append("dependency_timeout_triage")
        ids.extend(signal_action_ids(request, rca))
        ids.append("latency_saturation_review")
        if request.recent_deploys:
            ids.append("consider_recent_deploy_rollback")
        return ids
    return [*signal_action_ids(request, rca), "human_review_noisy_alert"]


def allowed_risk(classification: str, confidence: float) -> str:
    if classification in {"insufficient_context", "noisy_or_ambiguous_alert"}:
        return "low"
    if confidence < 0.6:
        return "low"
    return "medium"


def has_timeout_or_dependency_signal(request: Any, rca: dict[str, Any]) -> bool:
    text_parts = [
        request.alert.title,
        request.alert.description or "",
        " ".join(metric.metric_name for metric in request.metrics),
        " ".join(log.message for log in request.logs),
        " ".join(item.get("reason", "") for item in rca.get("anomaly_evidence", [])),
    ]
    text = " ".join(text_parts).lower()
    return any(token in text for token in ["timeout", "redis", "database", "postgres", "mysql", "connection pool", "slow query"])


def signal_action_ids(request: Any, rca: dict[str, Any]) -> list[str]:
    text = signal_text(request, rca)
    ids: list[str] = []
    if any(token in text for token in ["cpu", "memory", "oom", "out of memory", "heap", "saturation", "connection pool", "connections exhausted"]):
        ids.append("resource_saturation_triage")
    if any(token in text for token in ["disk", "inode", "no space", "filesystem", "volume full"]):
        ids.append("disk_pressure_triage")
    if any(token in text for token in ["queue", "backlog", "lag", "consumer", "kafka", "sqs", "rabbitmq"]):
        ids.append("queue_backlog_triage")
    if any(token in text for token in ["auth", "oauth", "jwt", "token", "unauthorized", "forbidden", "401", "403", "credential"]):
        ids.append("auth_failure_triage")
    if any(token in text for token in ["dns", "tls", "certificate", "cert", "network", "connection refused", "connection reset", "egress"]):
        ids.append("network_dns_triage")
    if any(token in text for token in ["crashloop", "crash loop", "pod restart", "readiness", "liveness", "oomkilled", "imagepull"]):
        ids.append("kubernetes_crashloop_triage")
    if any(token in text for token in ["429", "rate limit", "ratelimit", "throttle", "throttling", "quota"]):
        ids.append("rate_limit_throttling_triage")
    if has_internal_docs(request):
        ids.append("consult_internal_runbooks")
    return dedupe(ids)


def signal_text(request: Any, rca: dict[str, Any]) -> str:
    parts = [
        request.alert.title,
        request.alert.description or "",
        " ".join(metric.metric_name for metric in request.metrics),
        " ".join(str(metric.labels or {}) for metric in request.metrics),
        " ".join(log.message for log in request.logs),
        " ".join(str(log.labels or {}) for log in request.logs),
        " ".join(item.get("reason", "") for item in rca.get("anomaly_evidence", [])),
        " ".join(reason for candidate in rca.get("rca_candidates", []) for reason in candidate.get("reasons", [])),
    ]
    return " ".join(parts).lower()


def has_internal_docs(request: Any) -> bool:
    return bool(request.ownership and request.ownership.runbooks)


def dedupe(ids: list[str]) -> list[str]:
    result: list[str] = []
    for action_id in ids:
        if action_id not in result:
            result.append(action_id)
    return result


def build_evidence_refs(request: Any, decision: dict[str, Any], rca: dict[str, Any]) -> list[str]:
    refs: list[str] = []
    for index, _ in enumerate(decision.get("evidence", [])[:2]):
        refs.append(f"suspected_root_cause.evidence[{index}]")
    for index, _ in enumerate(rca.get("anomaly_evidence", [])[:2]):
        refs.append(f"anomaly_evidence[{index}]")
    if request.logs:
        refs.append("logs[0]")
    if request.recent_deploys:
        refs.append("recent_deploys[0]")
    if request.ownership and request.ownership.runbooks:
        refs.append("ownership.runbooks[0]")
    return refs or ["alert"]


def evidence_refs_for_action(action_id: str, evidence_refs: list[str]) -> list[str]:
    preferred: list[str] = []
    if action_id == "consider_recent_deploy_rollback":
        preferred = ["recent_deploys[0]", "ownership.runbooks[0]"]
    elif action_id == "dependency_timeout_triage":
        preferred = ["logs[0]", "anomaly_evidence[0]", "ownership.runbooks[0]"]
    elif action_id == "attach_telemetry_context":
        preferred = ["alert"]
    elif action_id in {
        "resource_saturation_triage",
        "disk_pressure_triage",
        "queue_backlog_triage",
        "auth_failure_triage",
        "network_dns_triage",
        "kubernetes_crashloop_triage",
        "rate_limit_throttling_triage",
    }:
        preferred = ["anomaly_evidence[0]", "logs[0]", "suspected_root_cause.evidence[0]", "ownership.runbooks[0]"]
    elif action_id == "consult_internal_runbooks":
        preferred = ["ownership.runbooks[0]", "suspected_root_cause.evidence[0]"]

    ordered = [ref for ref in preferred if ref in evidence_refs]
    ordered.extend(ref for ref in evidence_refs if ref not in ordered)
    return ordered[:4] or ["alert"]
