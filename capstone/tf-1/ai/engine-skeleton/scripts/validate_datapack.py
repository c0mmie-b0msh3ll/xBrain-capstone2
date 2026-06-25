from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
DATAPACK = ROOT / "datapack" / "scenarios"

sys.path.insert(0, str(ROOT))

from app.main import app  # noqa: E402

RAW_REQUIRED = {
    "tenant_id",
    "service",
    "environment",
    "timestamp",
    "source",
}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def validate_raw_records(scenario: Path, filename: str, extra_required: set[str]) -> None:
    records = load_json(scenario / filename)
    require(isinstance(records, list), f"{scenario.name}/{filename} must be a list")
    for index, record in enumerate(records):
        required = RAW_REQUIRED | extra_required
        missing = sorted(required - set(record))
        require(not missing, f"{scenario.name}/{filename}[{index}] missing {missing}")


def validate_optional_deploys(scenario: Path) -> None:
    records = load_json(scenario / "deploy-events.json")
    require(isinstance(records, list), f"{scenario.name}/deploy-events.json must be a list")
    for index, record in enumerate(records):
        missing = sorted((RAW_REQUIRED | {"version"}) - set(record))
        require(not missing, f"{scenario.name}/deploy-events.json[{index}] missing {missing}")


def validate_triage(scenario: Path, client: TestClient) -> None:
    request = load_json(scenario / "triage-request.json")
    expected = load_json(scenario / "expected-triage-summary.json")

    response = client.post(
        "/v1/triage",
        json=request,
        headers={
            "X-Tenant-Id": request["tenant_id"],
            "X-Correlation-Id": request["correlation_id"],
        },
    )
    require(response.status_code == 200, f"{scenario.name} triage failed: {response.text}")
    body = response.json()

    require(body["status"] == expected["expected_status"], f"{scenario.name} status mismatch")
    require(
        body["classification"] == expected["expected_classification"],
        f"{scenario.name} classification mismatch",
    )
    require(
        expected["confidence_min"] <= body["confidence"] <= expected["confidence_max"],
        f"{scenario.name} confidence out of range: {body['confidence']}",
    )
    for field in expected["must_include_fields"]:
        if field == "slack_payload":
            require(field not in body, f"{scenario.name} must not return deprecated response field {field}")
            continue
        require(field in body, f"{scenario.name} missing response field {field}")
    require("ticket_payload" in body, f"{scenario.name} missing response field ticket_payload")
    require("suggestion_reason" in body, f"{scenario.name} missing response field suggestion_reason")
    require("slack_payload" not in body, f"{scenario.name} must not return deprecated response field slack_payload")


def main() -> None:
    client = TestClient(app)
    scenarios = sorted(path for path in DATAPACK.iterdir() if path.is_dir())
    require(len(scenarios) >= 3, "expected at least 3 datapack scenarios")

    for scenario in scenarios:
        validate_raw_records(scenario, "raw-metrics.json", {"metric_name", "value"})
        validate_raw_records(scenario, "raw-logs.json", {"level", "message"})
        validate_optional_deploys(scenario)
        validate_triage(scenario, client)
        print(f"ok {scenario.name}")

    print("datapack validation passed")


if __name__ == "__main__":
    main()
