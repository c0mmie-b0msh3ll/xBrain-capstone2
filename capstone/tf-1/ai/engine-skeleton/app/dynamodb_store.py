from __future__ import annotations

import os
import time
from decimal import Decimal
from typing import Any

import boto3
from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError


DEFAULT_IDEMPOTENCY_RETENTION_DAYS = 7


class IdempotencyInProgressError(RuntimeError):
    pass


class IdempotencyCompletedError(RuntimeError):
    def __init__(self, record: dict[str, Any]) -> None:
        super().__init__("Idempotency record was completed before start")
        self.record = record


def persistence_backend() -> str:
    return os.getenv("AIOPS_PERSISTENCE_BACKEND", "file").strip().lower() or "file"


def use_dynamodb_backend() -> bool:
    return persistence_backend() == "dynamodb"


def dynamodb_table_name() -> str:
    table_name = os.getenv("AIOPS_DYNAMODB_TABLE")
    if not table_name:
        raise RuntimeError("AIOPS_DYNAMODB_TABLE is required when AIOPS_PERSISTENCE_BACKEND=dynamodb")
    return table_name


def dynamodb_table() -> Any:
    region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION")
    kwargs = {"region_name": region} if region else {}
    return boto3.resource("dynamodb", **kwargs).Table(dynamodb_table_name())


def audit_pk(audit_id: str) -> str:
    return f"AUDIT#{audit_id}"


def audit_sk(record: dict[str, Any]) -> str:
    recorded_at = str(record.get("recorded_at") or "")
    record_type = str(record.get("record_type") or "unknown")
    return f"RECORDED#{recorded_at}#{record_type}"


def idempotency_pk(audit_id: str) -> str:
    return f"IDEMPOTENCY#{audit_id}"


def jira_history_pk(tenant_id: str, environment: str, service: str) -> str:
    return f"JIRA_HISTORY#{tenant_id}#{environment}#{service}"


def append_audit_record(record: dict[str, Any], retention_days: int) -> None:
    table = dynamodb_table()
    audit_id = str(record.get("audit_id") or "")
    if not audit_id:
        raise ValueError("audit record requires audit_id")
    table.put_item(
        Item=_to_dynamodb_value(
            {
                "PK": audit_pk(audit_id),
                "SK": audit_sk(record),
                "record": record,
                "expires_at": _expires_at(retention_days),
            }
        )
    )


def latest_audit_record(audit_id: str) -> dict[str, Any] | None:
    table = dynamodb_table()
    items: list[dict[str, Any]] = []
    kwargs: dict[str, Any] = {
        "KeyConditionExpression": Key("PK").eq(audit_pk(audit_id)),
        "ScanIndexForward": False,
    }
    while True:
        response = table.query(**kwargs)
        items.extend(response.get("Items", []))
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break
        kwargs["ExclusiveStartKey"] = last_key

    if not items:
        return None
    latest = _from_dynamodb_value(items[0]).get("record")
    if not isinstance(latest, dict):
        return None
    latest = dict(latest)
    latest["matching_records"] = len(items)
    return latest


def read_idempotency_record(audit_id: str) -> dict[str, Any] | None:
    response = dynamodb_table().get_item(Key={"PK": idempotency_pk(audit_id), "SK": "STATE"})
    record = _from_dynamodb_value(response.get("Item", {})).get("record")
    return record if isinstance(record, dict) else None


def write_idempotency_record(audit_id: str, record: dict[str, Any], retention_days: int = DEFAULT_IDEMPOTENCY_RETENTION_DAYS) -> None:
    dynamodb_table().put_item(
        Item=_to_dynamodb_value(
            {
                "PK": idempotency_pk(audit_id),
                "SK": "STATE",
                "record": record,
                "expires_at": _expires_at(retention_days),
            }
        )
    )


def read_jira_history_record(service: str, environment: str, tenant_id: str) -> dict[str, Any] | None:
    response = dynamodb_table().get_item(Key={"PK": jira_history_pk(tenant_id, environment, service), "SK": "SUGGESTION"})
    record = _from_dynamodb_value(response.get("Item", {})).get("record")
    return record if isinstance(record, dict) else None


def write_jira_history_record(
    service: str,
    environment: str,
    tenant_id: str,
    record: dict[str, Any],
    retention_days: int = DEFAULT_IDEMPOTENCY_RETENTION_DAYS,
) -> None:
    dynamodb_table().put_item(
        Item=_to_dynamodb_value(
            {
                "PK": jira_history_pk(tenant_id, environment, service),
                "SK": "SUGGESTION",
                "record": record,
                "expires_at": _expires_at(retention_days),
            }
        )
    )


def start_idempotency_record(audit_id: str, record: dict[str, Any], stale_before: str) -> None:
    table = dynamodb_table()
    request_hash = str(record.get("request_hash") or "")
    condition = (
        Attr("PK").not_exists()
        | Attr("record.status").eq("failed_retryable")
        | (Attr("record.status").eq("completed") & Attr("record.request_hash").ne(request_hash))
        | (Attr("record.status").eq("in_progress") & (Attr("record.updated_at").lt(stale_before) | Attr("record.updated_at").not_exists()))
    )
    try:
        table.put_item(
            Item=_to_dynamodb_value(
                {
                    "PK": idempotency_pk(audit_id),
                    "SK": "STATE",
                    "record": record,
                    "expires_at": _expires_at(DEFAULT_IDEMPOTENCY_RETENTION_DAYS),
                }
            ),
            ConditionExpression=condition,
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
            raise
        current = read_idempotency_record(audit_id) or {}
        if current.get("status") == "completed" and current.get("request_hash") == request_hash:
            raise IdempotencyCompletedError(current) from exc
        raise IdempotencyInProgressError("Triage is already in progress for this audit_id") from exc


def _expires_at(retention_days: int) -> int:
    return int(time.time()) + max(1, retention_days) * 86400


def _to_dynamodb_value(value: Any) -> Any:
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {key: _to_dynamodb_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_to_dynamodb_value(item) for item in value]
    return value


def _from_dynamodb_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        if value % 1 == 0:
            return int(value)
        return float(value)
    if isinstance(value, dict):
        return {key: _from_dynamodb_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_from_dynamodb_value(item) for item in value]
    return value
