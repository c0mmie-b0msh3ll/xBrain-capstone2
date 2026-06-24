from __future__ import annotations

import hashlib
import os
from typing import Any, Literal

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.action_catalog import select_actions
from app.llm import investigate_with_tools, reword_catalog_actions, synthesize_investigation_summary
from app.rca import analyze_request
from app.report_store import list_reports, read_report


app = FastAPI(title="TF1 AI Triage Engine", version="v1")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


Severity = Literal["critical", "high", "medium", "low", "unknown"]
Environment = Literal["prod", "staging", "sandbox"]
Status = Literal["DIAGNOSED", "INVESTIGATE", "INSUFFICIENT_CONTEXT", "UNSAFE_SUGGESTION_BLOCKED"]
ActionType = Literal["HUMAN_REVIEW", "RUNBOOK_CHECK", "ROLLBACK_CONSIDER", "ESCALATE_OWNER", "OBSERVE"]


class Alert(BaseModel):
    alert_id: str = Field(min_length=1)
    source: str = Field(min_length=1)
    service: str = Field(min_length=1)
    severity: Severity
    title: str = Field(min_length=1)
    description: str | None = None
    started_at: str = Field(min_length=1)
    labels: dict[str, Any] = Field(default_factory=dict)


class MetricPoint(BaseModel):
    ts: str
    value: float


class MetricSeries(BaseModel):
    metric_name: str = Field(min_length=1)
    service: str = Field(min_length=1)
    unit: str | None = None
    points: list[MetricPoint] = Field(default_factory=list)
    labels: dict[str, Any] = Field(default_factory=dict)


class LogEntry(BaseModel):
    service: str = Field(min_length=1)
    ts: str = Field(min_length=1)
    level: str = Field(min_length=1)
    message: str = Field(min_length=1)
    trace_id: str | None = None
    labels: dict[str, Any] = Field(default_factory=dict)


class RecentDeploy(BaseModel):
    service: str = Field(min_length=1)
    version: str = Field(min_length=1)
    deployed_at: str = Field(min_length=1)
    deployed_by: str | None = None
    change_summary: str | None = None
    rollback_ref: str | None = None


class Runbook(BaseModel):
    title: str = Field(min_length=1)
    url: str = Field(min_length=1)
    excerpt: str | None = None


class Ownership(BaseModel):
    service: str = Field(min_length=1)
    owner_team: str | None = None
    slack_channel: str | None = None
    jira_project: str | None = None
    runbooks: list[Runbook] = Field(default_factory=list)


class TriageRequest(BaseModel):
    correlation_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    incident_id: str = Field(min_length=1)
    environment: Environment
    received_at: str = Field(min_length=1)
    alert: Alert
    metrics: list[MetricSeries] = Field(default_factory=list)
    logs: list[LogEntry] = Field(default_factory=list)
    recent_deploys: list[RecentDeploy] = Field(default_factory=list)
    ownership: Ownership | None = None
    anomaly_evidence: list[dict[str, Any]] = Field(default_factory=list)
    service_topology: dict[str, Any] | None = None
    rca_candidates: list[dict[str, Any]] = Field(default_factory=list)
    causal_hints: list[dict[str, Any]] = Field(default_factory=list)
    investigation_summary: str | None = None


class RootCause(BaseModel):
    summary: str
    evidence: list[str]


class RecommendedAction(BaseModel):
    id: str | None = None
    type: ActionType
    priority: int
    summary: str
    runbook_ref: str | None = None
    risk: Literal["low", "medium", "high"] | None = None
    why: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    requires_human_approval: bool | None = None
    approval_reason: str | None = None


class TicketPayload(BaseModel):
    project: str
    summary: str
    description: str
    labels: list[str]
    fields: dict[str, Any]


class SlackPayload(BaseModel):
    channel: str
    text: str


class TriageResponse(BaseModel):
    incident_id: str
    classification: str
    severity: Severity
    confidence: float
    status: Status
    suspected_root_cause: RootCause
    recommended_actions: list[RecommendedAction]
    ticket_payload: TicketPayload
    slack_payload: SlackPayload
    audit_id: str
    anomaly_evidence: list[dict[str, Any]] = Field(default_factory=list)
    service_topology: dict[str, Any] | None = None
    rca_candidates: list[dict[str, Any]] = Field(default_factory=list)
    causal_hints: list[dict[str, Any]] = Field(default_factory=list)
    investigation_summary: str | None = None
    llm_metadata: dict[str, Any] = Field(default_factory=dict)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok", "service": "tf1-ai-triage-engine", "version": "v1"}


@app.get("/v1/reports")
def get_reports() -> dict[str, Any]:
    return {"reports": list_reports()}


@app.get("/v1/reports/{incident_id}")
def get_report(incident_id: str) -> dict[str, Any]:
    report = read_report(incident_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")
    return report


@app.get("/v1/reports/{incident_id}/raw")
def get_raw_report(incident_id: str) -> JSONResponse:
    report = read_report(incident_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")
    return JSONResponse(
        content=report,
        headers={"Content-Disposition": f'attachment; filename="{incident_id}.json"'},
    )


@app.post("/v1/triage", response_model=TriageResponse)
def triage(
    request: TriageRequest,
    x_tenant_id: str = Header(..., alias="X-Tenant-Id"),
    x_correlation_id: str = Header(..., alias="X-Correlation-Id"),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> TriageResponse:
    validate_headers(request, x_tenant_id, x_correlation_id, authorization)
    audit_id = build_audit_id(request)
    return triage_request(request, audit_id)


def triage_request(request: TriageRequest, audit_id: str | None = None) -> TriageResponse:
    audit_id = audit_id or build_audit_id(request)
    rca = analyze_request(request)
    decision = classify(request, rca)
    request, rca, decision, tool_metadata = investigate_with_tools(request, decision, rca)
    decision = classify(request, rca)
    return build_response(request, audit_id, decision, {"tool_investigation": tool_metadata})


def validate_headers(
    request: TriageRequest,
    tenant_header: str,
    correlation_header: str,
    authorization: str | None,
) -> None:
    if tenant_header != request.tenant_id:
        raise HTTPException(status_code=400, detail="X-Tenant-Id must match body tenant_id")
    if correlation_header != request.correlation_id:
        raise HTTPException(status_code=400, detail="X-Correlation-Id must match body correlation_id")

    expected_token = os.getenv("SERVICE_AUTH_TOKEN")
    if expected_token and authorization != f"Bearer {expected_token}":
        raise HTTPException(status_code=401, detail="Invalid service token")


def build_audit_id(request: TriageRequest) -> str:
    seed = f"{request.tenant_id}:{request.correlation_id}:{request.incident_id}"
    return "audit-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]


def classify(request: TriageRequest, rca: dict[str, Any] | None = None) -> dict[str, Any]:
    rca = rca or analyze_request(request)
    text = " ".join(
        [
            request.alert.title,
            request.alert.description or "",
            " ".join(log.message for log in request.logs),
            " ".join(metric.metric_name for metric in request.metrics),
            " ".join((deploy.change_summary or "") for deploy in request.recent_deploys),
        ]
    ).lower()

    has_context = bool(request.metrics or request.logs or request.recent_deploys or has_ownership_context(request))
    if not has_context:
        return {
            "status": "INSUFFICIENT_CONTEXT",
            "classification": "insufficient_context",
            "confidence": 0.25,
            "summary": "Alert metadata was provided, but supporting metrics, logs, deploys, and ownership context are missing.",
            "evidence": ["No supporting telemetry context was included with the alert."],
            "actions": [
                ("ESCALATE_OWNER", "Ask the AIOps context layer to attach metrics, logs, recent deploys, and ownership context before diagnosis."),
            ],
            "rca": rca,
        }

    if any(token in text for token in ["noisy", "flapping", "false alarm", "ambiguous"]) or request.alert.severity in {
        "low",
        "unknown",
    }:
        return {
            "status": "INVESTIGATE",
            "classification": "noisy_or_ambiguous_alert",
            "confidence": 0.45,
            "summary": "Signals are weak or ambiguous; the alert should be investigated without assigning a firm root cause.",
            "evidence": collect_evidence(request, fallback="Alert text or severity indicates a noisy or ambiguous condition."),
            "actions": [
                ("OBSERVE", "Check whether the alert repeats and compare it against user-impacting metrics."),
                ("HUMAN_REVIEW", "Have the service owner confirm whether this is actionable before creating remediation work."),
            ],
            "rca": rca,
        }

    if request.alert.severity == "critical" or any(token in text for token in ["down", "unavailable", "connection refused"]):
        return {
            "status": "DIAGNOSED",
            "classification": "critical_service_down",
            "confidence": 0.86,
            "summary": f"{request.alert.service} appears unavailable or critically degraded based on the alert and supporting context.",
            "evidence": collect_evidence(request, fallback="Critical severity alert indicates service availability impact."),
            "actions": [
                ("RUNBOOK_CHECK", "Follow the service-down runbook and verify health checks, dependency availability, and recent deploy status."),
                ("ESCALATE_OWNER", "Page or notify the owning team for immediate human review."),
            ],
            "rca": rca,
        }

    anomaly_text = " ".join(item.get("reason", "") for item in rca.get("anomaly_evidence", []))
    if "latency" in text or "p95" in text or "timeout" in text or "latency" in anomaly_text.lower():
        return {
            "status": "DIAGNOSED",
            "classification": "latency_degradation",
            "confidence": 0.82,
            "summary": f"{request.alert.service} is showing latency degradation, likely related to timeout or recent change signals.",
            "evidence": collect_evidence(request, fallback="Latency-related alert title or metrics were included."),
            "actions": [
                ("HUMAN_REVIEW", "Check saturation metrics, dependency latency, and slow query or timeout logs."),
                ("ROLLBACK_CONSIDER", "If recent deploy correlation is confirmed, consider rollback through the approved runbook."),
            ],
            "rca": rca,
        }

    return {
        "status": "INVESTIGATE",
        "classification": "general_investigation",
        "confidence": 0.55,
        "summary": "The alert has context but does not match a high-confidence TF1 skeleton scenario.",
        "evidence": collect_evidence(request, fallback="Context was present but did not match a known deterministic rule."),
        "actions": [
            ("HUMAN_REVIEW", "Review supplied logs, metrics, and deploys before assigning a root cause."),
        ],
        "rca": rca,
    }


def has_ownership_context(request: TriageRequest) -> bool:
    ownership = request.ownership
    return bool(ownership and (ownership.owner_team or ownership.slack_channel or ownership.jira_project or ownership.runbooks))


def collect_evidence(request: TriageRequest, fallback: str) -> list[str]:
    evidence: list[str] = []
    if request.metrics:
        names = ", ".join(metric.metric_name for metric in request.metrics[:3])
        evidence.append(f"Metrics provided: {names}.")
    if request.logs:
        evidence.append(f"Representative log: {request.logs[0].message}")
    if request.recent_deploys:
        deploy = request.recent_deploys[0]
        evidence.append(f"Recent deploy {deploy.version} at {deploy.deployed_at}.")
    if request.ownership and request.ownership.runbooks:
        evidence.append(f"Runbook available: {request.ownership.runbooks[0].title}.")
    return evidence or [fallback]


def build_response(
    request: TriageRequest,
    audit_id: str,
    decision: dict[str, Any],
    extra_llm_metadata: dict[str, Any] | None = None,
) -> TriageResponse:
    owner = request.ownership or Ownership(service=request.alert.service)
    project = owner.jira_project or "OPS"
    channel = owner.slack_channel or "#oncall"
    runbook_ref = owner.runbooks[0].url if owner.runbooks else None
    labels = ["ai-triage", request.tenant_id, request.alert.service, decision["classification"]]
    rca = decision.get("rca", {})
    anomaly_evidence = request.anomaly_evidence or rca.get("anomaly_evidence", [])
    service_topology = request.service_topology or rca.get("service_topology")
    rca_candidates = request.rca_candidates or rca.get("rca_candidates", [])
    causal_hints = request.causal_hints or rca.get("causal_hints", [])
    llm_result = synthesize_investigation_summary(request, decision, rca)
    investigation_summary = request.investigation_summary or llm_result.get("summary") or rca.get("investigation_summary")
    llm_metadata = {key: value for key, value in llm_result.items() if key != "summary"}
    if extra_llm_metadata:
        llm_metadata.update(extra_llm_metadata)
    evidence_preview = "; ".join(item.get("reason", "") for item in anomaly_evidence[:2])

    selected_actions = select_actions(request, decision, rca, runbook_ref)
    action_wording = reword_catalog_actions(request, decision, rca, selected_actions)
    action_payloads = action_wording["actions"]
    llm_metadata["action_wording"] = action_wording["metadata"]
    actions = [RecommendedAction(**action) for action in action_payloads]
    top_action = actions[0] if actions else None

    return TriageResponse(
        incident_id=request.incident_id,
        classification=decision["classification"],
        severity=request.alert.severity,
        confidence=decision["confidence"],
        status=decision["status"],
        suspected_root_cause=RootCause(summary=decision["summary"], evidence=decision["evidence"]),
        recommended_actions=actions,
        ticket_payload=TicketPayload(
            project=project,
            summary=f"[{request.alert.severity}] {request.alert.service} {decision['classification']}",
            description=f"{decision['summary']} Evidence: {'; '.join(decision['evidence'])}",
            labels=labels,
            fields={
                "confidence": decision["confidence"],
                "owner_team": owner.owner_team,
                "correlation_id": request.correlation_id,
                "audit_id": audit_id,
                "status": decision["status"],
            },
        ),
        slack_payload=SlackPayload(
            channel=channel,
            text=(
                f"{request.alert.service}: {decision['classification']} "
                f"({decision['status']}, confidence {decision['confidence']:.2f}). "
                f"Evidence: {evidence_preview or decision['evidence'][0]}. "
                f"Action: {top_action.summary if top_action else 'Review incident context.'} "
                f"Audit: {audit_id}."
            ),
        ),
        audit_id=audit_id,
        anomaly_evidence=anomaly_evidence,
        service_topology=service_topology,
        rca_candidates=rca_candidates,
        causal_hints=causal_hints,
        investigation_summary=investigation_summary,
        llm_metadata=llm_metadata,
    )
