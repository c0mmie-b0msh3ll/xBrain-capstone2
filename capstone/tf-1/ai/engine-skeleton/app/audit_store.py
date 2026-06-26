from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_AUDIT_LOG_PATH = "audit/audit-log.jsonl"
DEFAULT_RETENTION_DAYS = 90


def audit_log_path() -> Path:
    return Path(os.getenv("AIOPS_AUDIT_LOG_PATH", DEFAULT_AUDIT_LOG_PATH))


def retention_days() -> int:
    try:
        return max(DEFAULT_RETENTION_DAYS, int(os.getenv("AIOPS_AUDIT_RETENTION_DAYS", str(DEFAULT_RETENTION_DAYS))))
    except ValueError:
        return DEFAULT_RETENTION_DAYS


def append_audit_record(record: dict[str, Any], path: Path | None = None) -> None:
    target = path or audit_log_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
        handle.write("\n")


def latest_audit_record(audit_id: str, path: Path | None = None) -> dict[str, Any] | None:
    target = path or audit_log_path()
    if not target.exists():
        return None

    latest: dict[str, Any] | None = None
    match_count = 0
    with target.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("audit_id") == audit_id:
                latest = record
                match_count += 1
    if latest is not None:
        latest = dict(latest)
        latest["matching_records"] = match_count
    return latest


def build_success_audit_record(request: Any, response: Any, duration_ms: float) -> dict[str, Any]:
    request_body = _model_dump(request)
    response_body = _model_dump(response)
    llm_metadata = response_body.get("llm_metadata", {}) if isinstance(response_body, dict) else {}
    ticket_payload = response_body.get("ticket_payload", {}) if isinstance(response_body, dict) else {}

    return {
        "schema_version": "tf1.audit.v1",
        "record_type": "triage_decision",
        "audit_id": response_body.get("audit_id"),
        "recorded_at": _now_iso(),
        "retention_days": retention_days(),
        "tenant_id": request_body.get("tenant_id"),
        "incident_id": request_body.get("incident_id"),
        "correlation_id": request_body.get("correlation_id"),
        "service": request_body.get("alert", {}).get("service"),
        "environment": request_body.get("environment"),
        "severity": request_body.get("alert", {}).get("severity"),
        "duration_ms": duration_ms,
        "request_hash": _sha256_json(request_body),
        "evidence_hashes": _evidence_hashes(request_body),
        "decision": {
            "classification": response_body.get("classification"),
            "status": response_body.get("status"),
            "confidence": response_body.get("confidence"),
            "recommended_action_ids": [action.get("id") for action in response_body.get("recommended_actions", []) if action.get("id")],
            "evidence_count": len(response_body.get("suspected_root_cause", {}).get("evidence", [])),
            "summary_hash": _sha256_json(response_body.get("suspected_root_cause", {}).get("summary")),
            "root_cause_evidence_hash": _sha256_json(response_body.get("suspected_root_cause", {}).get("evidence", [])),
        },
        "mode_selection": llm_metadata.get("mode_selection"),
        "llm_metadata": _audit_llm_metadata(llm_metadata),
        "tool_lineage": _tool_lineage(llm_metadata),
        "ticket_lineage": {
            "project": ticket_payload.get("project"),
            "summary_hash": _sha256_json(ticket_payload.get("summary")),
            "labels": ticket_payload.get("labels", []),
            "fields": {
                "audit_id": ticket_payload.get("fields", {}).get("audit_id"),
                "correlation_id": ticket_payload.get("fields", {}).get("correlation_id"),
                "status": ticket_payload.get("fields", {}).get("status"),
                "owner_team": ticket_payload.get("fields", {}).get("owner_team"),
                "suggested_assignee_account_id": ticket_payload.get("fields", {}).get("suggested_assignee_account_id"),
            },
        },
        "guardrails": {
            "raw_customer_evidence_logged": False,
            "remediation_executed": False,
            "jira_mutation_executed": False,
            "slack_post_executed": False,
        },
    }


def build_failure_audit_record(request: Any, audit_id: str, error_class: str, duration_ms: float) -> dict[str, Any]:
    request_body = _model_dump(request)
    return {
        "schema_version": "tf1.audit.v1",
        "record_type": "triage_failure",
        "audit_id": audit_id,
        "recorded_at": _now_iso(),
        "retention_days": retention_days(),
        "tenant_id": request_body.get("tenant_id"),
        "incident_id": request_body.get("incident_id"),
        "correlation_id": request_body.get("correlation_id"),
        "service": request_body.get("alert", {}).get("service"),
        "environment": request_body.get("environment"),
        "severity": request_body.get("alert", {}).get("severity"),
        "duration_ms": duration_ms,
        "request_hash": _sha256_json(request_body),
        "evidence_hashes": _evidence_hashes(request_body),
        "error_class": error_class,
        "guardrails": {
            "raw_customer_evidence_logged": False,
            "remediation_executed": False,
            "jira_mutation_executed": False,
            "slack_post_executed": False,
        },
    }


def _model_dump(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return value
    return {}


def _audit_llm_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    agent = metadata.get("agent_platform") if isinstance(metadata.get("agent_platform"), dict) else {}
    tool_investigation = metadata.get("tool_investigation") if isinstance(metadata.get("tool_investigation"), dict) else {}
    return {
        "investigation_mode": metadata.get("investigation_mode"),
        "provider": metadata.get("provider"),
        "model": metadata.get("model"),
        "mode_selection": metadata.get("mode_selection"),
        "agent_platform": {
            "enabled": agent.get("enabled"),
            "iterations": agent.get("iterations"),
            "fallback": agent.get("fallback"),
            "fallback_reason": agent.get("fallback_reason"),
        },
        "tool_investigation": {
            "enabled": tool_investigation.get("enabled"),
            "skipped_reason": tool_investigation.get("skipped_reason"),
            "fallback_reason": tool_investigation.get("fallback_reason"),
        },
        "qa": metadata.get("qa"),
        "action_wording": metadata.get("action_wording"),
    }


def _tool_lineage(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    lineage: list[dict[str, Any]] = []
    for source in ("tool_investigation", "agent_platform"):
        section = metadata.get(source)
        if not isinstance(section, dict):
            continue
        for item in section.get("tool_calls", []):
            if not isinstance(item, dict):
                continue
            lineage.append(
                {
                    "source": source,
                    "name": item.get("name"),
                    "status": item.get("status"),
                    "blocked_reason": item.get("blocked_reason") or item.get("error"),
                    "result_count": item.get("result_count"),
                    "args_hash": _sha256_json(item.get("args", {})),
                }
            )
    return lineage


def _evidence_hashes(request_body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    sections = {
        "alert": request_body.get("alert"),
        "metrics": request_body.get("metrics", []),
        "logs": request_body.get("logs", []),
        "traces": request_body.get("traces", []),
        "recent_deploys": request_body.get("recent_deploys", []),
        "ownership": request_body.get("ownership"),
        "anomaly_evidence": request_body.get("anomaly_evidence", []),
        "service_topology": request_body.get("service_topology"),
        "rca_candidates": request_body.get("rca_candidates", []),
        "causal_hints": request_body.get("causal_hints", []),
    }
    return {
        name: {
            "present": value not in (None, [], {}),
            "count": len(value) if isinstance(value, list) else int(value not in (None, [], {})),
            "sha256": _sha256_json(value),
        }
        for name, value in sections.items()
    }


def _sha256_json(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
