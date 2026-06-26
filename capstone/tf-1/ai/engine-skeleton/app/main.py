from __future__ import annotations

import hashlib
import os
import time
from typing import Any, Literal

from fastapi import FastAPI, Header, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.action_catalog import select_actions
from app.agent_runtime import agent_platform_enabled, run_agent_platform
from app.context_enrichment import enrich_triage_context
from app.context_tools import ToolRegistry, ToolScopeError, scope_from_request
from app.investigation_router import select_investigation_mode
from app.llm import investigate_with_tools, reword_catalog_actions, synthesize_investigation_summary
from app.observability import (
    BUDGET_EXCEEDED_TOTAL,
    DEGRADED_MODE_TOTAL,
    INVESTIGATION_MODE_SELECTED_TOTAL,
    QA_ITERATIONS_TOTAL,
    TRIAGE_INFLIGHT_REQUESTS,
    TRIAGE_REQUEST_DURATION_SECONDS,
    TRIAGE_REQUESTS_TOTAL,
    configure_logging,
    configure_tracing,
    log_event,
    metrics_response,
    span,
)
from app.rca import analyze_request
from app.report_store import list_reports, read_report


configure_logging()
configure_tracing()

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


class TraceSummary(BaseModel):
    trace_id: str = Field(min_length=1)
    span_id: str | None = None
    parent_span_id: str | None = None
    service: str = Field(min_length=1)
    operation: str | None = None
    ts: str | None = None
    duration_ms: float | None = None
    status: str | None = None
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
    traces: list[TraceSummary] = Field(default_factory=list)
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


class TriageResponse(BaseModel):
    incident_id: str
    classification: str
    severity: Severity
    confidence: float
    status: Status
    suspected_root_cause: RootCause
    recommended_actions: list[RecommendedAction]
    ticket_payload: TicketPayload
    suggested_assignee_account_id: str | None = None
    suggestion_reason: str | None = None
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


@app.get("/readyz")
def readyz() -> dict[str, Any]:
    return {
        "status": "ready",
        "service": "tf1-ai-triage-engine",
        "observability": os.getenv("AIOPS_OBSERVABILITY_ENABLED", "true"),
        "qa_max_iterations": int(os.getenv("AIOPS_QA_MAX_ITERATIONS", "1")),
    }


@app.get("/metrics")
def metrics() -> Response:
    payload, content_type = metrics_response()
    return Response(content=payload, media_type=content_type)


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
    with span("request_validation", tenant_id=request.tenant_id, service=request.alert.service, environment=request.environment):
        validate_headers(request, x_tenant_id, x_correlation_id, authorization)
        audit_id = build_audit_id(request)
    return triage_request(request, audit_id)


def triage_request(request: TriageRequest, audit_id: str | None = None) -> TriageResponse:
    audit_id = audit_id or build_audit_id(request)
    started = time.perf_counter()
    TRIAGE_INFLIGHT_REQUESTS.inc()
    classification = "unknown"
    status = "error"
    try:
        with span("triage_request", audit_id=audit_id, tenant_id=request.tenant_id, service=request.alert.service, environment=request.environment):
            log_triage_stage(request, audit_id, "started", "ok")
            with span("context_enrichment", audit_id=audit_id):
                enriched_body = enrich_triage_context(request.model_dump(mode="json"))
            request = TriageRequest.model_validate(enriched_body)

            with span("deterministic_rca", audit_id=audit_id):
                rca = analyze_request(request)
            decision = classify(request, rca)

            with span("mode_selection", audit_id=audit_id):
                mode_selection = select_investigation_mode(request, decision, rca, agent_platform_enabled())
                INVESTIGATION_MODE_SELECTED_TOTAL.labels(
                    mode=mode_selection.selected_mode,
                    source=mode_selection.source,
                ).inc()
            log_triage_stage(
                request,
                audit_id,
                "mode_selected",
                "ok",
                classification=decision["classification"],
                investigation_mode=mode_selection.selected_mode,
                complexity_score=mode_selection.complexity_score,
            )

            platform_action_ids: list[str] = []
            tool_metadata: dict[str, Any] = {"enabled": False, "skipped_reason": "mode_not_agent_assisted"}
            agent_metadata: dict[str, Any] | None = None
            if mode_selection.selected_mode == "agent_assisted":
                with span("llm_investigation", audit_id=audit_id):
                    request, rca, decision, tool_metadata = investigate_with_tools(request, decision, rca)
            elif mode_selection.selected_mode == "agent_platform":
                with span("agent_platform", audit_id=audit_id):
                    request, rca, decision, agent_metadata, platform_action_ids = run_agent_platform(request, decision, rca)

            with span("deterministic_rca_reclassify", audit_id=audit_id):
                rca = enrich_rca_with_jira_history(request, rca)
                if mode_selection.selected_mode != "agent_platform" or (agent_metadata and agent_metadata.get("fallback")):
                    decision = classify(request, rca)
                else:
                    decision = decision.copy()
                    decision["rca"] = rca

            with span("qa", audit_id=audit_id):
                qa_metadata = run_qa(request, decision, rca)
            if qa_metadata.get("confidence_delta"):
                decision = decision.copy()
                decision["confidence"] = max(0.0, round(decision["confidence"] + float(qa_metadata["confidence_delta"]), 2))

            with span("response_assembly", audit_id=audit_id):
                response = build_response(
                    request,
                    audit_id,
                    decision,
                    {
                        "investigation_mode": mode_selection.selected_mode,
                        "mode_selection": mode_selection.metadata(),
                        "tool_investigation": tool_metadata,
                        "agent_platform": agent_metadata,
                        "qa": qa_metadata,
                    },
                    platform_action_ids,
                )
            classification = response.classification
            status = response.status
            log_triage_stage(
                request,
                audit_id,
                "completed",
                status,
                classification=classification,
                started=started,
                investigation_mode=mode_selection.selected_mode,
                complexity_score=mode_selection.complexity_score,
                agent_iterations=(agent_metadata or {}).get("iterations"),
                fallback_reason=(agent_metadata or {}).get("fallback_reason"),
            )
            return response
    except Exception as exc:
        DEGRADED_MODE_TOTAL.labels(reason="triage_exception").inc()
        log_triage_stage(request, audit_id, "failed", "error", error_class=type(exc).__name__, started=started)
        raise
    finally:
        duration = time.perf_counter() - started
        deadline = float(os.getenv("AIOPS_TRIAGE_DEADLINE_SECONDS", "30"))
        if duration > deadline:
            BUDGET_EXCEEDED_TOTAL.labels(budget_type="triage_deadline").inc()
        TRIAGE_REQUEST_DURATION_SECONDS.observe(duration)
        TRIAGE_REQUESTS_TOTAL.labels(status=status, classification=classification).inc()
        TRIAGE_INFLIGHT_REQUESTS.dec()


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


def run_qa(request: TriageRequest, decision: dict[str, Any], rca: dict[str, Any]) -> dict[str, Any]:
    max_iterations = int(os.getenv("AIOPS_QA_MAX_ITERATIONS", "1"))
    repair_max_iterations = int(os.getenv("AIOPS_QA_REPAIR_MAX_ITERATIONS", "1"))
    token_budget = int(os.getenv("AIOPS_LLM_MAX_TOKENS_PER_INCIDENT", "0") or 0)
    metadata: dict[str, Any] = {
        "enabled": max_iterations > 0,
        "iterations": 0,
        "repair_iterations": 0,
        "result": "skipped" if max_iterations <= 0 else "passed",
    }
    if max_iterations <= 0:
        QA_ITERATIONS_TOTAL.labels(result="skipped").inc()
        return metadata

    metadata["iterations"] = 1
    issues = qa_findings(request, decision, rca)
    if token_budget and estimate_qa_tokens(request, decision, rca) > token_budget:
        metadata["result"] = "budget_exceeded"
        metadata["confidence_delta"] = -0.1
        BUDGET_EXCEEDED_TOTAL.labels(budget_type="qa_tokens").inc()
        DEGRADED_MODE_TOTAL.labels(reason="qa_budget_exceeded").inc()
    elif issues:
        metadata["result"] = "failed"
        metadata["issues"] = issues
        metadata["confidence_delta"] = -0.1
        if repair_max_iterations > 0:
            metadata["repair_iterations"] = 1
            metadata["repair_result"] = "not_attempted_deterministic_only"
        DEGRADED_MODE_TOTAL.labels(reason="qa_failed").inc()
    QA_ITERATIONS_TOTAL.labels(result=str(metadata["result"])).inc()
    return metadata


def qa_findings(request: TriageRequest, decision: dict[str, Any], rca: dict[str, Any]) -> list[str]:
    findings: list[str] = []
    if decision["status"] == "DIAGNOSED" and not decision.get("evidence"):
        findings.append("diagnosis_missing_evidence")
    if decision["status"] == "DIAGNOSED" and not (request.metrics or request.logs or request.recent_deploys or rca.get("anomaly_evidence")):
        findings.append("diagnosis_without_supporting_context")
    if decision["classification"] == "latency_degradation" and "latency" not in " ".join(decision.get("evidence", []) + [request.alert.title]).lower():
        findings.append("latency_classification_without_latency_evidence")
    return findings


def estimate_qa_tokens(request: TriageRequest, decision: dict[str, Any], rca: dict[str, Any]) -> int:
    evidence_items = len(request.metrics) + len(request.logs) + len(request.traces) + len(request.recent_deploys)
    return 64 + (evidence_items * 24) + (len(decision.get("evidence", [])) * 16) + (len(rca.get("anomaly_evidence", [])) * 24)


def log_triage_stage(
    request: TriageRequest,
    audit_id: str,
    stage: str,
    status: str,
    classification: str | None = None,
    error_class: str | None = None,
    started: float | None = None,
    investigation_mode: str | None = None,
    complexity_score: int | None = None,
    agent_iterations: int | None = None,
    fallback_reason: str | None = None,
) -> None:
    duration_ms = round((time.perf_counter() - started) * 1000, 2) if started else None
    log_event(
        "triage_stage",
        audit_id=audit_id,
        tenant_id=request.tenant_id,
        correlation_id=request.correlation_id,
        incident_id=request.incident_id,
        service=request.alert.service,
        environment=request.environment,
        stage=stage,
        status=status,
        classification=classification,
        duration_ms=duration_ms,
        metrics_count=len(request.metrics),
        logs_count=len(request.logs),
        traces_count=len(request.traces),
        deploys_count=len(request.recent_deploys),
        error_class=error_class,
        investigation_mode=investigation_mode,
        complexity_score=complexity_score,
        agent_iterations=agent_iterations,
        fallback_reason=fallback_reason,
    )


def enrich_rca_with_jira_history(request: TriageRequest, rca: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(rca)
    if isinstance(enriched.get("jira_history"), dict):
        return enriched
    try:
        result = ToolRegistry().execute("get_jira_history", {}, scope_from_request(request), request)
        jira_history = result.get("result")
    except (ToolScopeError, OSError, ValueError, RuntimeError):
        jira_history = {
            "suggested_assignee_account_id": None,
            "suggestion_reason": "Jira history lookup was unavailable; route to owner team for human confirmation.",
        }
    if isinstance(jira_history, dict):
        enriched["jira_history"] = jira_history
    return enriched


def build_response(
    request: TriageRequest,
    audit_id: str,
    decision: dict[str, Any],
    extra_llm_metadata: dict[str, Any] | None = None,
    advisory_action_ids: list[str] | None = None,
) -> TriageResponse:
    owner = request.ownership or Ownership(service=request.alert.service)
    project = owner.jira_project or "OPS"
    runbook_ref = owner.runbooks[0].url if owner.runbooks else None
    labels = ["ai-triage", request.tenant_id, request.alert.service, decision["classification"]]
    rca = decision.get("rca", {})
    anomaly_evidence = request.anomaly_evidence or rca.get("anomaly_evidence", [])
    service_topology = request.service_topology or rca.get("service_topology")
    rca_candidates = request.rca_candidates or rca.get("rca_candidates", [])
    causal_hints = request.causal_hints or rca.get("causal_hints", [])
    investigation_mode = (extra_llm_metadata or {}).get("investigation_mode")
    if investigation_mode in {"deterministic_only", "agent_platform"}:
        llm_result = {"enabled": False, "provider": "deterministic", "skipped_reason": f"{investigation_mode}_mode"}
    else:
        llm_result = synthesize_investigation_summary(request, decision, rca)
    investigation_summary = request.investigation_summary or llm_result.get("summary") or rca.get("investigation_summary")
    if investigation_mode == "agent_platform" and decision.get("agent_final"):
        investigation_summary = decision["summary"]
    llm_metadata = {key: value for key, value in llm_result.items() if key != "summary"}
    if extra_llm_metadata:
        llm_metadata.update({key: value for key, value in extra_llm_metadata.items() if value is not None})
    selected_actions = select_actions(request, decision, rca, runbook_ref)
    if investigation_mode == "agent_platform" and advisory_action_ids:
        advisory = [action for action in selected_actions if action.get("id") in set(advisory_action_ids)]
        if advisory:
            selected_actions = advisory
            for index, action in enumerate(selected_actions):
                action["priority"] = index + 1
    if investigation_mode in {"deterministic_only", "agent_platform"}:
        action_wording = {"actions": selected_actions, "metadata": {"enabled": False, "provider": "deterministic", "skipped_reason": f"{investigation_mode}_mode"}}
    else:
        action_wording = reword_catalog_actions(request, decision, rca, selected_actions)
    action_payloads = action_wording["actions"]
    llm_metadata["action_wording"] = action_wording["metadata"]
    actions = [RecommendedAction(**action) for action in action_payloads]
    suggested_assignee_account_id, suggestion_reason = suggest_assignee(request, owner, rca)

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
                "suggested_assignee_account_id": suggested_assignee_account_id,
                "suggestion_reason": suggestion_reason,
            },
        ),
        suggested_assignee_account_id=suggested_assignee_account_id,
        suggestion_reason=suggestion_reason,
        audit_id=audit_id,
        anomaly_evidence=anomaly_evidence,
        service_topology=service_topology,
        rca_candidates=rca_candidates,
        causal_hints=causal_hints,
        investigation_summary=investigation_summary,
        llm_metadata=llm_metadata,
    )


def suggest_assignee(
    request: TriageRequest,
    owner: Ownership,
    rca: dict[str, Any],
) -> tuple[str | None, str]:
    account_id = request.alert.labels.get("suggested_assignee_account_id")
    reason = request.alert.labels.get("suggestion_reason")
    if isinstance(account_id, str) and account_id:
        return account_id, str(reason or "Suggested by incident seed metadata from the integration layer.")

    jira_history = rca.get("jira_history") if isinstance(rca, dict) else None
    if isinstance(jira_history, dict):
        candidate = jira_history.get("suggested_assignee_account_id")
        if isinstance(candidate, str) and candidate:
            history_reason = jira_history.get("suggestion_reason")
            return candidate, str(history_reason or "Suggested from matching historical Jira incidents.")

    target = owner.owner_team or owner.service
    return None, f"No Jira accountId history mapping is configured yet; route to {target} for human confirmation."
