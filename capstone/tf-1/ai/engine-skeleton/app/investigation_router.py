from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Literal


InvestigationMode = Literal["deterministic_only", "agent_assisted", "agent_platform"]
ModeSource = Literal["auto", "env"]

VALID_ENV_MODES = {"auto", "deterministic_only", "agent_assisted", "agent_platform"}


@dataclass(frozen=True)
class ModeSelection:
    selected_mode: InvestigationMode
    planned_mode: InvestigationMode
    source: ModeSource
    complexity_score: int
    reasons: list[str]
    agentcore_enabled: bool

    def metadata(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "complexity_score": self.complexity_score,
            "reasons": self.reasons,
            "planned_mode": self.planned_mode,
            "selected_mode": self.selected_mode,
            "agentcore_enabled": self.agentcore_enabled,
        }


def select_investigation_mode(request: Any, decision: dict[str, Any], rca: dict[str, Any], agentcore_enabled: bool) -> ModeSelection:
    score, reasons = complexity_score(request, decision, rca)
    planned_mode = mode_for_score(score)
    configured = os.getenv("AIOPS_INVESTIGATION_MODE", "auto").strip().lower()
    if configured not in VALID_ENV_MODES:
        configured = "auto"

    if configured != "auto":
        selected_mode = configured  # type: ignore[assignment]
        source: ModeSource = "env"
    elif not agentcore_enabled and planned_mode in {"agent_assisted", "agent_platform"}:
        selected_mode = "deterministic_only"
        source = "auto"
    else:
        selected_mode = planned_mode
        source = "auto"

    return ModeSelection(
        selected_mode=selected_mode,
        planned_mode=planned_mode,
        source=source,
        complexity_score=score,
        reasons=reasons,
        agentcore_enabled=agentcore_enabled,
    )


def complexity_score(request: Any, decision: dict[str, Any], rca: dict[str, Any]) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []

    score = add_if(score, reasons, not request.metrics, 2, "missing_metrics")
    score = add_if(score, reasons, not request.logs, 2, "missing_logs")
    score = add_if(score, reasons, not request.traces, 1, "missing_traces")
    score = add_if(score, reasons, not request.recent_deploys, 1, "missing_recent_deploys")
    score = add_if(score, reasons, not has_ownership_context(request), 1, "missing_ownership")

    confidence = float(decision.get("confidence", 0.0) or 0.0)
    score = add_if(score, reasons, confidence < 0.7, 2, "low_confidence")
    score = add_if(score, reasons, confidence < 0.5, 1, "very_low_confidence")

    status = decision.get("status")
    score = add_if(score, reasons, status == "INSUFFICIENT_CONTEXT", 3, "insufficient_context_status")
    score = add_if(score, reasons, status == "INVESTIGATE", 2, "investigate_status")

    candidates = rca.get("rca_candidates") if isinstance(rca, dict) else None
    if isinstance(candidates, list) and len(candidates) >= 2:
        top = float(candidates[0].get("confidence", 0.0) or 0.0)
        second = float(candidates[1].get("confidence", 0.0) or 0.0)
        score = add_if(score, reasons, abs(top - second) <= 0.15, 2, "ambiguous_rca_candidates")

    has_edges = bool((rca.get("service_topology") or {}).get("edges")) if isinstance(rca, dict) else False
    has_causal_hints = bool(rca.get("causal_hints")) if isinstance(rca, dict) else False
    score = add_if(
        score,
        reasons,
        (has_edges or has_causal_hints) and confidence < 0.7,
        1,
        "dependency_or_causal_hints_low_confidence",
    )

    missing_context = not (request.metrics and request.logs and request.traces and request.recent_deploys and has_ownership_context(request))
    score = add_if(score, reasons, request.alert.severity in {"critical", "high"} and missing_context, 1, "high_severity_missing_context")
    score = add_if(
        score,
        reasons,
        request.alert.labels.get("evidence_uri_status") == "missing_or_out_of_scope",
        1,
        "evidence_uri_missing_or_out_of_scope",
    )

    return score, reasons


def add_if(score: int, reasons: list[str], condition: bool, points: int, reason: str) -> int:
    if condition:
        reasons.append(reason)
        return score + points
    return score


def mode_for_score(score: int) -> InvestigationMode:
    platform_threshold = int(os.getenv("AIOPS_AGENT_COMPLEXITY_THRESHOLD", "6"))
    assisted_threshold = int(os.getenv("AIOPS_ASSISTED_COMPLEXITY_THRESHOLD", "3"))
    if score >= platform_threshold:
        return "agent_platform"
    if score >= assisted_threshold:
        return "agent_assisted"
    return "deterministic_only"


def has_ownership_context(request: Any) -> bool:
    ownership = request.ownership
    return bool(ownership and (ownership.owner_team or ownership.slack_channel or ownership.jira_project or ownership.runbooks))
