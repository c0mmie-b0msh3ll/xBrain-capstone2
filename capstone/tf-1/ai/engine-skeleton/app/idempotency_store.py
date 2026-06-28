from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.audit_store import audit_log_path


DEFAULT_STALE_SECONDS = 120


def request_hash(request: Any) -> str:
    body = request.model_dump(mode="json") if hasattr(request, "model_dump") else request
    settings = {
        "AIOPS_LLM_MAX_TOKENS_PER_INCIDENT": os.getenv("AIOPS_LLM_MAX_TOKENS_PER_INCIDENT"),
        "AIOPS_INVESTIGATION_MODE": os.getenv("AIOPS_INVESTIGATION_MODE"),
        "ENABLE_AGENTCORE_LLM": os.getenv("ENABLE_AGENTCORE_LLM"),
        "ENABLE_AGENTCORE_LLM_TOOLS": os.getenv("ENABLE_AGENTCORE_LLM_TOOLS"),
        "AGENTCORE_RUNTIME_ARN": bool(os.getenv("AGENTCORE_RUNTIME_ARN")),
        "JIRA_HISTORY_PATH": os.getenv("JIRA_HISTORY_PATH"),
        "AIOPS_QA_MAX_ITERATIONS": os.getenv("AIOPS_QA_MAX_ITERATIONS"),
        "AIOPS_QA_REPAIR_MAX_ITERATIONS": os.getenv("AIOPS_QA_REPAIR_MAX_ITERATIONS"),
    }
    payload = json.dumps({"request": body, "settings": settings}, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def idempotency_dir() -> Path:
    configured = os.getenv("AIOPS_IDEMPOTENCY_DIR")
    if configured:
        return Path(configured)
    return audit_log_path().parent / "idempotency"


def idempotency_path(audit_id: str) -> Path:
    safe = "".join(char for char in audit_id if char.isalnum() or char in {"-", "_"})
    return idempotency_dir() / f"{safe}.json"


def read_record(audit_id: str) -> dict[str, Any] | None:
    path = idempotency_path(audit_id)
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as handle:
            record = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    return record if isinstance(record, dict) else None


def start_record(audit_id: str, request_hash_value: str) -> dict[str, Any]:
    existing = read_record(audit_id) or {}
    attempt = int(existing.get("attempt") or 0) + 1
    record = {
        "schema_version": "tf1.idempotency.v1",
        "audit_id": audit_id,
        "status": "in_progress",
        "started_at": now_iso(),
        "updated_at": now_iso(),
        "attempt": attempt,
        "owner_process_id": os.getpid(),
        "request_hash": request_hash_value,
    }
    write_record(audit_id, record)
    return record


def complete_record(audit_id: str, request_hash_value: str, response: Any) -> None:
    response_body = response.model_dump(mode="json") if hasattr(response, "model_dump") else response
    existing = read_record(audit_id) or {}
    record = {
        **existing,
        "schema_version": "tf1.idempotency.v1",
        "audit_id": audit_id,
        "status": "completed",
        "updated_at": now_iso(),
        "completed_at": now_iso(),
        "request_hash": request_hash_value,
        "response_hash": sha256_json(response_body),
        "response": response_body,
    }
    write_record(audit_id, record)


def fail_record(audit_id: str, request_hash_value: str, error_class: str) -> None:
    existing = read_record(audit_id) or {}
    record = {
        **existing,
        "schema_version": "tf1.idempotency.v1",
        "audit_id": audit_id,
        "status": "failed_retryable",
        "updated_at": now_iso(),
        "failed_at": now_iso(),
        "request_hash": request_hash_value,
        "error_class": error_class,
    }
    write_record(audit_id, record)


def write_record(audit_id: str, record: dict[str, Any]) -> None:
    path = idempotency_path(audit_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(record, handle, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    temp.replace(path)


def is_stale(record: dict[str, Any]) -> bool:
    stale_seconds = stale_threshold_seconds()
    updated = parse_iso(str(record.get("updated_at") or record.get("started_at") or ""))
    if updated is None:
        return True
    return (datetime.now(timezone.utc) - updated).total_seconds() > stale_seconds


def stale_threshold_seconds() -> int:
    try:
        return max(1, int(os.getenv("AIOPS_IDEMPOTENCY_STALE_SECONDS", str(DEFAULT_STALE_SECONDS))))
    except ValueError:
        return DEFAULT_STALE_SECONDS


def sha256_json(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
