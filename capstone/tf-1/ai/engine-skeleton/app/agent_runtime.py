from __future__ import annotations

import json
import os
from typing import Any

from app.action_catalog import ACTION_CATALOG
from app.context_tools import ToolRegistry, ToolScopeError, merge_tool_result_into_request, scope_from_request
from app.llm import active_model_id, agentcore_llm_enabled, agentcore_session_id, build_prompt_payload, tracked_llm_call
from app.observability import AGENT_FALLBACK_TOTAL, AGENT_ITERATIONS_TOTAL, AGENT_TOOL_REQUESTS_TOTAL, DEGRADED_MODE_TOTAL, span
from app.rca import analyze_request


VALID_STATUSES = {"DIAGNOSED", "INVESTIGATE", "INSUFFICIENT_CONTEXT", "UNSAFE_SUGGESTION_BLOCKED"}
VALID_CLASSIFICATIONS = {
    "insufficient_context",
    "noisy_or_ambiguous_alert",
    "critical_service_down",
    "latency_degradation",
    "general_investigation",
}
BLOCKED_TEXT_TOKENS = (
    "kubectl ",
    "curl ",
    "rm ",
    "delete ",
    "restart ",
    "rollback ",
    "scale ",
    "promql",
    "logql",
    "jira create",
    "slack post",
    "shell",
)


def agent_platform_enabled() -> bool:
    return bool(os.getenv("AGENTCORE_RUNTIME_ARN")) and agentcore_llm_enabled()


def run_agent_platform(
    request: Any,
    decision: dict[str, Any],
    rca: dict[str, Any],
    registry: ToolRegistry | None = None,
) -> tuple[Any, dict[str, Any], dict[str, Any], dict[str, Any], list[str]]:
    metadata: dict[str, Any] = {
        "enabled": True,
        "provider": "agentcore",
        "runtime_arn_configured": bool(os.getenv("AGENTCORE_RUNTIME_ARN")),
        "iterations": 0,
        "tool_calls": [],
        "fallback": False,
    }
    if not agent_platform_enabled():
        return fallback(request, rca, decision, metadata, "agentcore_disabled")

    registry = registry or ToolRegistry()
    scope = scope_from_request(request)
    max_iterations = int(os.getenv("AIOPS_AGENT_MAX_ITERATIONS", "2"))
    max_tool_calls = int(os.getenv("AIOPS_AGENT_MAX_TOOL_CALLS", "5"))
    total_tool_calls = 0
    observations: list[dict[str, Any]] = []
    current_request = request
    current_rca = rca

    try:
        for iteration in range(1, max_iterations + 1):
            metadata["iterations"] = iteration
            with span("agent_platform_iteration", iteration=iteration, service=request.alert.service, environment=request.environment):
                raw_payload = invoke_agentcore_investigator(
                    current_request,
                    decision,
                    current_rca,
                    sorted(registry.names),
                    observations,
                    max_tool_calls - total_tool_calls,
                )
                payload = parse_agent_payload(raw_payload)

            payload_type = payload.get("type")
            if payload_type == "final_diagnosis":
                try:
                    final_decision, advisory_action_ids = validate_final_diagnosis(payload, current_rca)
                except ValueError as exc:
                    metadata["error"] = f"{type(exc).__name__}: {exc}"
                    return fallback(current_request, current_rca, decision, metadata, "invalid_final_diagnosis")
                AGENT_ITERATIONS_TOTAL.labels(result="final").inc()
                metadata["final_validation"] = {"passed": True}
                metadata["qa"] = payload.get("qa") if isinstance(payload.get("qa"), dict) else None
                return current_request, current_rca, final_decision, metadata, advisory_action_ids

            if payload_type != "tool_requests":
                return fallback(current_request, current_rca, decision, metadata, "unknown_agent_response_type")

            tool_calls = payload.get("tool_calls")
            if not isinstance(tool_calls, list):
                return fallback(current_request, current_rca, decision, metadata, "malformed_tool_requests")

            for proposed in tool_calls:
                if total_tool_calls >= max_tool_calls:
                    metadata["budget_exceeded"] = "max_tool_calls"
                    break
                total_tool_calls += 1
                name = proposed.get("name") if isinstance(proposed, dict) else None
                args = proposed.get("args") if isinstance(proposed, dict) and isinstance(proposed.get("args"), dict) else {}
                call_record: dict[str, Any] = {"name": name, "status": "blocked"}
                with span("agent_platform_tool_gateway", tool=str(name), service=request.alert.service, environment=request.environment):
                    try:
                        if name not in registry.names:
                            raise ToolScopeError(f"Unknown or disallowed tool: {name}")
                        result = registry.execute(str(name), args, scope, current_request)
                        gateway_record = {key: value for key, value in result.items() if key != "result"}
                        metadata["tool_calls"].append(gateway_record)
                        observations.append(gateway_record)
                        current_request = merge_tool_result_into_request(current_request, result)
                        AGENT_TOOL_REQUESTS_TOTAL.labels(tool=str(name), status="ok").inc()
                    except Exception as exc:
                        call_record["error"] = f"{type(exc).__name__}: {exc}"
                        metadata["tool_calls"].append(call_record)
                        observations.append(call_record)
                        AGENT_TOOL_REQUESTS_TOTAL.labels(tool=str(name or "unknown"), status="blocked").inc()
            current_rca = analyze_request(current_request)

        AGENT_ITERATIONS_TOTAL.labels(result="budget_exceeded").inc()
        return fallback(current_request, current_rca, decision, metadata, "max_iterations")
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        metadata["error"] = f"{type(exc).__name__}: {exc}"
        return fallback(current_request, current_rca, decision, metadata, "malformed_agent_json")
    except Exception as exc:
        metadata["error"] = f"{type(exc).__name__}: {exc}"
        return fallback(current_request, current_rca, decision, metadata, "agent_runtime_error")


def fallback(
    request: Any,
    rca: dict[str, Any],
    decision: dict[str, Any],
    metadata: dict[str, Any],
    reason: str,
) -> tuple[Any, dict[str, Any], dict[str, Any], dict[str, Any], list[str]]:
    metadata["fallback"] = True
    metadata["fallback_reason"] = reason
    AGENT_FALLBACK_TOTAL.labels(reason=reason).inc()
    AGENT_ITERATIONS_TOTAL.labels(result="fallback").inc()
    DEGRADED_MODE_TOTAL.labels(reason=f"agent_platform_{reason}").inc()
    return request, rca, decision, metadata, []


def invoke_agentcore_investigator(
    request: Any,
    decision: dict[str, Any],
    rca: dict[str, Any],
    allowed_tools: list[str],
    observations: list[dict[str, Any]],
    remaining_tool_calls: int,
) -> str:
    payload = {
        "task": "agent_platform_investigation",
        "system_instructions": (
            "Return strict JSON only. You are the incident investigator, but TF1 owns policy and tool execution. "
            "Request evidence only through allowed read-only tools. Do not propose shell, PromQL, LogQL, Jira/Slack mutation, or remediation commands."
        ),
        "input": {
            "current_state": build_prompt_payload(request, decision, rca),
            "allowed_tools": allowed_tools,
            "remaining_tool_calls": max(0, remaining_tool_calls),
            "observations": observations,
        },
        "output_schemas": {
            "tool_requests": {"type": "tool_requests", "thought_summary": "string", "tool_calls": [{"name": "string", "args": {}}]},
            "final_diagnosis": {
                "type": "final_diagnosis",
                "classification": "string",
                "status": "string",
                "confidence": 0.0,
                "summary": "string",
                "evidence": ["string"],
                "recommended_action_ids": ["string"],
                "qa": {"passed": True, "gaps": []},
            },
        },
    }
    return tracked_llm_call(
        request,
        "agent_platform",
        payload,
        active_model_id(),
        session_id=agentcore_session_id(request, "agent-platform"),
    )


def parse_agent_payload(raw_text: str) -> dict[str, Any]:
    payload = json.loads(raw_text)
    if not isinstance(payload, dict):
        raise ValueError("Agent response must be a JSON object.")
    return payload


def validate_final_diagnosis(payload: dict[str, Any], rca: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    with span("agent_platform_final_validation"):
        classification = payload.get("classification")
        status = payload.get("status")
        confidence = payload.get("confidence")
        summary = payload.get("summary")
        evidence = payload.get("evidence")
        if classification not in VALID_CLASSIFICATIONS:
            raise ValueError(f"Invalid classification from agent: {classification}")
        if status not in VALID_STATUSES:
            raise ValueError(f"Invalid status from agent: {status}")
        if not isinstance(confidence, (int, float)) or not 0.0 <= float(confidence) <= 1.0:
            raise ValueError("Agent confidence must be between 0.0 and 1.0.")
        if not isinstance(summary, str) or not summary.strip():
            raise ValueError("Agent final diagnosis must include a summary.")
        if not isinstance(evidence, list) or not all(isinstance(item, str) and item.strip() for item in evidence):
            raise ValueError("Agent final diagnosis must include string evidence.")
        if status == "DIAGNOSED" and not evidence:
            raise ValueError("Agent DIAGNOSED response requires evidence.")
        if contains_blocked_text([summary, *evidence]):
            raise ValueError("Agent final diagnosis contained blocked operational text.")

        action_ids = payload.get("recommended_action_ids")
        advisory_action_ids = []
        if isinstance(action_ids, list):
            advisory_action_ids = [str(action_id) for action_id in action_ids if isinstance(action_id, str) and action_id in ACTION_CATALOG]
        return (
            {
                "status": status,
                "classification": classification,
                "confidence": round(float(confidence), 2),
                "summary": summary.strip(),
                "evidence": [item.strip() for item in evidence],
                "actions": [],
                "rca": rca,
                "agent_final": True,
            },
            advisory_action_ids,
        )


def contains_blocked_text(items: list[str]) -> bool:
    text = " ".join(items).lower()
    return any(token in text for token in BLOCKED_TEXT_TOKENS)
