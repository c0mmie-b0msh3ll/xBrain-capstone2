from __future__ import annotations

import json
import os
from typing import Any

import requests


def build_slack_message(response: dict[str, Any], report_url: str) -> dict[str, Any]:
    evidence = response.get("anomaly_evidence", [])
    top_evidence = evidence[0]["reason"] if evidence else response.get("suspected_root_cause", {}).get("evidence", [""])[0]
    actions = response.get("recommended_actions", [])
    top_action = actions[0].get("summary") if actions else "Review incident context."
    return {
        "text": (
            f"{response.get('severity', 'unknown').upper()} {response.get('classification')} "
            f"for {response.get('incident_id')} ({response.get('status')}, confidence {response.get('confidence', 0):.2f}). "
            f"Top evidence: {top_evidence} Action: {top_action} Report: {report_url}"
        )
    }


def publish_slack_webhook(
    response: dict[str, Any],
    report_url: str,
    dry_run: bool = False,
    http_post: Any | None = None,
) -> dict[str, Any]:
    webhook = os.getenv("SLACK_WEBHOOK_URL")
    payload = build_slack_message(response, report_url)
    if dry_run or not webhook:
        result = {
            "provider": "slack",
            "status": "dry_run",
            "reason": "dry_run_enabled" if dry_run else "missing_SLACK_WEBHOOK_URL",
            "payload": payload,
        }
        print(json.dumps({"slack_dry_run": payload, "reason": result["reason"]}, indent=2))
        return result

    post = http_post or requests.post
    slack_response = post(webhook, json=payload, timeout=5)
    slack_response.raise_for_status()
    result = {"provider": "slack", "status": "published", "incident_id": response.get("incident_id")}
    print(json.dumps({"slack_published": True, "incident_id": response.get("incident_id")}, indent=2))
    return result
