from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def reports_dir() -> Path:
    return Path(os.getenv("REPORTS_DIR", "reports"))


def report_path(incident_id: str) -> Path:
    return reports_dir() / f"{incident_id}.json"


def write_report(report: dict[str, Any], directory: Path | None = None) -> Path:
    target_dir = directory or reports_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{report['incident_id']}.json"
    path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return path


def list_reports() -> list[dict[str, Any]]:
    directory = reports_dir()
    if not directory.exists():
        return []
    summaries: list[dict[str, Any]] = []
    for path in directory.glob("*.json"):
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        response = report.get("triage_response", {})
        request = report.get("request_context", {})
        alert = request.get("alert", {})
        summaries.append(
            {
                "incident_id": report.get("incident_id") or path.stem,
                "created_at": report.get("created_at"),
                "service": alert.get("service"),
                "title": alert.get("title"),
                "severity": response.get("severity") or alert.get("severity"),
                "status": response.get("status"),
                "classification": response.get("classification"),
                "confidence": response.get("confidence"),
                "audit_id": report.get("audit_id") or response.get("audit_id"),
            }
        )
    return sorted(summaries, key=lambda item: item.get("created_at") or "", reverse=True)


def read_report(incident_id: str) -> dict[str, Any] | None:
    path = report_path(incident_id)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
