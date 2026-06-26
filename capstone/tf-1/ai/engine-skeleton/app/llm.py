from __future__ import annotations

import json
import os
import uuid
from typing import Any

from app.context_tools import ToolRegistry, ToolScopeError, merge_tool_result_into_request, scope_from_request
from app.observability import (
    BUDGET_EXCEEDED_TOTAL,
    DEGRADED_MODE_TOTAL,
    LLM_CALLS_TOTAL,
    LLM_ESTIMATED_COST_USD_TOTAL,
    LLM_TOKENS_TOTAL,
    estimate_tokens,
    span,
)
from app.rca import analyze_request


DEFAULT_MODEL_IDS = [
    "us.anthropic.claude-opus-4-8",
    "us.anthropic.claude-opus-4-6-v1",
    "us.amazon.nova-2-lite-v1:0",
]


def synthesize_investigation_summary(request: Any, decision: dict[str, Any], rca: dict[str, Any]) -> dict[str, Any]:
    enabled = agentcore_llm_enabled()
    if not enabled:
        return {"enabled": False, "provider": "deterministic"}

    region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1"
    model = active_model_id()
    try:
        payload = {
                "task": "investigation_summary",
                "system_instructions": (
                    "You are an AIOps incident investigator. Use only the provided bounded evidence. "
                    "Do not invent services, metrics, logs, timestamps, owners, or remediation. "
                    "Write a concise operational summary with root-cause hypothesis, evidence, confidence caveat, and next action."
                ),
                "input": build_prompt_payload(request, decision, rca),
            }
        raw_text = tracked_llm_call(request, "summary", payload, model)
        return {
            "enabled": True,
            "provider": "agentcore",
            "runtime_arn_configured": True,
            "region": region,
            "summary": extract_agentcore_text(raw_text),
        }
    except Exception as exc:  # pragma: no cover - exercised only with live AWS credentials
        LLM_CALLS_TOTAL.labels(stage="summary", model=model, status="error").inc()
        DEGRADED_MODE_TOTAL.labels(reason="llm_summary_failure").inc()
        return {
            "enabled": True,
            "provider": "agentcore",
            "runtime_arn_configured": bool(os.getenv("AGENTCORE_RUNTIME_ARN")),
            "region": region,
            "error": f"{type(exc).__name__}: {exc}",
        }


def reword_catalog_actions(
    request: Any,
    decision: dict[str, Any],
    rca: dict[str, Any],
    actions: list[dict[str, Any]],
) -> dict[str, Any]:
    enabled = agentcore_llm_enabled()
    if not enabled:
        return {"actions": actions, "metadata": {"enabled": False, "provider": "deterministic"}}

    region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1"
    model = active_model_id()
    try:
        payload = {
                "task": "action_wording",
                "system_instructions": (
                    "You are an AIOps recommendation editor. Choose and reword only provided action IDs. "
                    "Do not invent action IDs, tools, commands, remediation steps, services, evidence, or approvals. "
                    "Return strict JSON with an actions array. Each item must include id, summary, and why only."
                ),
                "input": build_action_prompt_payload(request, decision, rca, actions),
            }
        raw_text = tracked_llm_call(request, "actions", payload, model)
        reworded = apply_action_rewording(actions, raw_text)
        return {
            "actions": reworded,
            "metadata": {
                "enabled": True,
                "provider": "agentcore",
                "runtime_arn_configured": True,
                "region": region,
                "fallback": False,
            },
        }
    except Exception as exc:  # pragma: no cover - exercised only with live AWS credentials
        LLM_CALLS_TOTAL.labels(stage="actions", model=model, status="error").inc()
        DEGRADED_MODE_TOTAL.labels(reason="llm_action_failure").inc()
        return {
            "actions": actions,
            "metadata": {
                "enabled": True,
                "provider": "agentcore",
                "runtime_arn_configured": bool(os.getenv("AGENTCORE_RUNTIME_ARN")),
                "region": region,
                "fallback": True,
                "error": f"{type(exc).__name__}: {exc}",
            },
        }


def investigate_with_tools(
    request: Any,
    decision: dict[str, Any],
    rca: dict[str, Any],
    registry: ToolRegistry | None = None,
) -> tuple[Any, dict[str, Any], dict[str, Any], dict[str, Any]]:
    metadata: dict[str, Any] = {
        "enabled": llm_tools_enabled(),
        "provider": "agentcore",
        "runtime_arn_configured": bool(os.getenv("AGENTCORE_RUNTIME_ARN")),
        "tool_calls": [],
        "fallback": False,
    }
    if not metadata["enabled"]:
        return request, rca, decision, metadata
    if not should_investigate_with_tools(request, decision):
        metadata["skipped_reason"] = "deterministic_confidence_sufficient"
        return request, rca, decision, metadata

    registry = registry or ToolRegistry()
    max_calls = int(os.getenv("LLM_TOOL_MAX_CALLS", "3"))
    scope = scope_from_request(request)
    try:
        requested_calls = request_tool_calls_from_agentcore(request, decision, rca, registry.names, max_calls)
        enriched_request = request
        for proposed in requested_calls[:max_calls]:
            name = proposed.get("name")
            args = proposed.get("args") if isinstance(proposed.get("args"), dict) else {}
            call_record: dict[str, Any] = {"name": name, "status": "blocked"}
            try:
                result = registry.execute(str(name), args, scope, enriched_request)
                metadata["tool_calls"].append({key: value for key, value in result.items() if key != "result"})
                enriched_request = merge_tool_result_into_request(enriched_request, result)
            except Exception as exc:
                call_record["error"] = f"{type(exc).__name__}: {exc}"
                metadata["tool_calls"].append(call_record)
        rerun_rca = analyze_request(enriched_request)
        rerun_decision = decision.copy()
        if decision["status"] in {"INSUFFICIENT_CONTEXT", "INVESTIGATE"}:
            # The API owns classification rules; callers reclassify after this function.
            rerun_decision["rca"] = rerun_rca
        return enriched_request, rerun_rca, rerun_decision, metadata
    except Exception as exc:
        metadata["fallback"] = True
        metadata["error"] = f"{type(exc).__name__}: {exc}"
        DEGRADED_MODE_TOTAL.labels(reason="llm_tool_failure").inc()
        return request, rca, decision, metadata


def configured_model_ids() -> list[str]:
    csv_value = os.getenv("BEDROCK_MODEL_IDS")
    single_value = os.getenv("BEDROCK_MODEL_ID")
    if csv_value:
        return [item.strip() for item in csv_value.split(",") if item.strip()]
    if single_value:
        return [single_value.strip(), *[model_id for model_id in DEFAULT_MODEL_IDS if model_id != single_value.strip()]]
    return DEFAULT_MODEL_IDS


def llm_tools_enabled() -> bool:
    return env_enabled("ENABLE_AGENTCORE_LLM_TOOLS") or env_enabled("ENABLE_BEDROCK_LLM_TOOLS")


def agentcore_llm_enabled() -> bool:
    return (
        env_enabled("ENABLE_AGENTCORE_LLM")
        or env_enabled("ENABLE_BEDROCK_LLM")
        or bool(os.getenv("BEDROCK_MODEL_ID") or os.getenv("BEDROCK_MODEL_IDS"))
    )


def env_enabled(name: str) -> bool:
    return os.getenv(name, "").lower() in {"1", "true", "yes"}


def should_investigate_with_tools(request: Any, decision: dict[str, Any]) -> bool:
    if os.getenv("LLM_TOOL_FORCE", "").lower() in {"1", "true", "yes"}:
        return True
    if float(decision.get("confidence", 0.0)) < 0.7:
        return True
    if decision.get("status") in {"INSUFFICIENT_CONTEXT", "INVESTIGATE"}:
        return True
    if not request.metrics or not request.logs or not request.recent_deploys:
        return True
    text = f"{request.alert.title} {request.alert.description or ''}".lower()
    return any(token in text for token in ["ambiguous", "noisy", "dependency", "missing context"])


def request_tool_calls_from_agentcore(
    request: Any,
    decision: dict[str, Any],
    rca: dict[str, Any],
    allowed_tools: set[str],
    max_calls: int,
) -> list[dict[str, Any]]:
    runtime_arn = os.getenv("AGENTCORE_RUNTIME_ARN")
    if not runtime_arn:
        raise RuntimeError("AGENTCORE_RUNTIME_ARN is required when AgentCore LLM tools are enabled")
    session_id = agentcore_session_id(request)
    prompt_payload = build_tool_prompt_payload(request, decision, rca, sorted(allowed_tools), max_calls)
    try:
        raw_text = tracked_llm_call(request, "tools", prompt_payload, active_model_id(), session_id=session_id)
    except Exception as exc:
        raise RuntimeError(f"AgentCore invocation failed: {exc}") from exc
    return parse_tool_calls(raw_text, allowed_tools, max_calls)


def tracked_llm_call(request: Any, stage: str, payload: dict[str, Any], model: str, session_id: str | None = None) -> str:
    prompt_tokens = estimate_tokens(payload)
    token_budget = int(os.getenv("AIOPS_LLM_MAX_TOKENS_PER_INCIDENT", "0") or 0)
    if token_budget and prompt_tokens > token_budget:
        BUDGET_EXCEEDED_TOTAL.labels(budget_type="llm_tokens").inc()
        raise RuntimeError("LLM token budget exceeded before provider call")
    with span("llm_call", stage=stage, model=model, service=request.alert.service, environment=request.environment):
        raw_text = invoke_agentcore_payload(request, stage, payload, session_id=session_id)
    completion_tokens = estimate_tokens(raw_text)
    LLM_CALLS_TOTAL.labels(stage=stage, model=model, status="ok").inc()
    LLM_TOKENS_TOTAL.labels(stage=stage, model=model, type="prompt").inc(prompt_tokens)
    LLM_TOKENS_TOTAL.labels(stage=stage, model=model, type="completion").inc(completion_tokens)
    LLM_ESTIMATED_COST_USD_TOTAL.labels(stage=stage, model=model).inc(estimate_llm_cost_usd(prompt_tokens, completion_tokens))
    return raw_text


def active_model_id() -> str:
    return configured_model_ids()[0] if configured_model_ids() else "agentcore-default"


def estimate_llm_cost_usd(prompt_tokens: int, completion_tokens: int) -> float:
    input_rate = float(os.getenv("AIOPS_LLM_INPUT_COST_PER_1K", "0") or 0)
    output_rate = float(os.getenv("AIOPS_LLM_OUTPUT_COST_PER_1K", "0") or 0)
    return (prompt_tokens / 1000.0 * input_rate) + (completion_tokens / 1000.0 * output_rate)


def invoke_agentcore_payload(request: Any, purpose: str, payload: dict[str, Any], session_id: str | None = None) -> str:
    runtime_arn = os.getenv("AGENTCORE_RUNTIME_ARN")
    if not runtime_arn:
        raise RuntimeError("AGENTCORE_RUNTIME_ARN is required for AgentCore LLM calls")
    region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1"
    import boto3

    client = boto3.client("bedrock-agentcore", region_name=region)
    response = client.invoke_agent_runtime(
        agentRuntimeArn=runtime_arn,
        runtimeSessionId=session_id or agentcore_session_id(request, purpose),
        payload=json.dumps(payload, ensure_ascii=True).encode("utf-8"),
        contentType="application/json",
        accept="application/json",
    )
    return read_agentcore_response(response)


def agentcore_session_id(request: Any, purpose: str = "investigator") -> str:
    seed = f"{request.tenant_id}:{request.correlation_id}:{request.incident_id}:{purpose}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, seed))


def read_agentcore_response(response: dict[str, Any]) -> str:
    content_type = response.get("contentType", "")
    stream = response.get("response")
    if stream is None:
        raise ValueError("AgentCore response did not include a response stream.")

    if "text/event-stream" in content_type and hasattr(stream, "iter_lines"):
        chunks: list[str] = []
        for line in stream.iter_lines(chunk_size=10):
            if not line:
                continue
            text = line.decode("utf-8") if isinstance(line, bytes) else str(line)
            if text.startswith("data: "):
                chunks.append(text[6:])
        return "\n".join(chunks).strip()

    chunks = []
    for chunk in stream:
        if isinstance(chunk, bytes):
            chunks.append(chunk.decode("utf-8"))
        elif isinstance(chunk, dict) and isinstance(chunk.get("chunk"), dict):
            bytes_value = chunk["chunk"].get("bytes", b"")
            chunks.append(bytes_value.decode("utf-8") if isinstance(bytes_value, bytes) else str(bytes_value))
        else:
            chunks.append(str(chunk))
    return "".join(chunks).strip()


def extract_agentcore_text(raw_text: str) -> str:
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError:
        return raw_text.strip()
    if isinstance(payload, dict):
        for key in ("summary", "text", "response", "output"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return raw_text.strip()


def parse_tool_calls(raw_text: str, allowed_tools: set[str], max_calls: int) -> list[dict[str, Any]]:
    payload = json.loads(raw_text)
    calls = payload.get("tool_calls") if isinstance(payload, dict) else None
    if not isinstance(calls, list):
        raise ValueError("Tool response must contain a tool_calls array.")
    parsed: list[dict[str, Any]] = []
    for call in calls[:max_calls]:
        if not isinstance(call, dict):
            continue
        name = call.get("name")
        if name not in allowed_tools:
            raise ToolScopeError(f"Unknown or disallowed tool: {name}")
        args = call.get("args") if isinstance(call.get("args"), dict) else {}
        parsed.append({"name": name, "args": args})
    return parsed


def build_tool_prompt_payload(
    request: Any,
    decision: dict[str, Any],
    rca: dict[str, Any],
    allowed_tools: list[str],
    max_calls: int,
) -> dict[str, Any]:
    return {
        "incident_scope": {
            "tenant_id": request.tenant_id,
            "environment": request.environment,
            "service": request.alert.service,
            "started_at": request.alert.started_at,
            "received_at": request.received_at,
            "max_window_minutes": int(os.getenv("LLM_TOOL_MAX_WINDOW_MINUTES", "60")),
        },
        "system_instructions": (
            "Return strict JSON only. You may request additional incident evidence only through allowed read-only tools. "
            "Do not include PromQL, LogQL, shell commands, remediation, rollback, restart, scale, or database actions."
        ),
        "current_state": build_prompt_payload(request, decision, rca),
        "allowed_tools": allowed_tools,
        "max_tool_calls": max_calls,
        "output_schema": {"tool_calls": [{"name": "string", "args": {}}]},
    }


def apply_action_rewording(actions: list[dict[str, Any]], raw_text: str) -> list[dict[str, Any]]:
    payload = json.loads(raw_text)
    proposed_actions = payload.get("actions") if isinstance(payload, dict) else None
    if not isinstance(proposed_actions, list):
        raise ValueError("Action wording response must contain an actions array.")

    by_id = {action["id"]: action.copy() for action in actions}
    ordered: list[dict[str, Any]] = []
    for proposed in proposed_actions:
        if not isinstance(proposed, dict):
            continue
        action_id = proposed.get("id")
        if action_id not in by_id:
            raise ValueError(f"Unknown action id from LLM: {action_id}")
        action = by_id.pop(action_id)
        for field in ("summary", "why"):
            value = proposed.get(field)
            if isinstance(value, str) and value.strip():
                action[field] = value.strip()
        ordered.append(action)

    ordered.extend(by_id.values())
    for index, action in enumerate(ordered):
        action["priority"] = index + 1
    return ordered


def build_prompt_payload(request: Any, decision: dict[str, Any], rca: dict[str, Any]) -> dict[str, Any]:
    return {
        "incident": {
            "incident_id": request.incident_id,
            "tenant_id": request.tenant_id,
            "environment": request.environment,
            "service": request.alert.service,
            "severity": request.alert.severity,
            "title": request.alert.title,
            "description": request.alert.description,
            "started_at": request.alert.started_at,
        },
        "classification": {
            "status": decision["status"],
            "classification": decision["classification"],
            "confidence": decision["confidence"],
            "deterministic_summary": decision["summary"],
            "deterministic_evidence": decision["evidence"][:6],
        },
        "anomaly_evidence": rca.get("anomaly_evidence", [])[:8],
        "rca_candidates": rca.get("rca_candidates", [])[:5],
        "service_topology": rca.get("service_topology"),
        "causal_hints": rca.get("causal_hints", [])[:5],
        "recent_deploys": [
            {
                "service": deploy.service,
                "version": deploy.version,
                "deployed_at": deploy.deployed_at,
                "change_summary": deploy.change_summary,
                "rollback_ref": deploy.rollback_ref,
            }
            for deploy in request.recent_deploys[:3]
        ],
        "logs": [
            {
                "service": log.service,
                "level": log.level,
                "message": log.message,
                "trace_id": log.trace_id,
            }
            for log in request.logs[:5]
        ],
        "runbooks": [
            {"title": runbook.title, "url": runbook.url, "excerpt": runbook.excerpt}
            for runbook in ((request.ownership.runbooks if request.ownership else [])[:3])
        ],
    }


def build_action_prompt_payload(
    request: Any,
    decision: dict[str, Any],
    rca: dict[str, Any],
    actions: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "instruction": "Choose/reword only these action IDs. Do not invent actions. Preserve approval requirements, risk, runbook, and evidence references.",
        "incident": {
            "incident_id": request.incident_id,
            "service": request.alert.service,
            "severity": request.alert.severity,
            "title": request.alert.title,
            "description": request.alert.description,
        },
        "classification": {
            "status": decision["status"],
            "classification": decision["classification"],
            "confidence": decision["confidence"],
            "deterministic_summary": decision["summary"],
            "deterministic_evidence": decision["evidence"][:4],
        },
        "selected_catalog_actions": [
            {
                "id": action["id"],
                "type": action["type"],
                "risk": action["risk"],
                "summary": action["summary"],
                "why": action["why"],
                "evidence_refs": action["evidence_refs"],
                "requires_human_approval": action["requires_human_approval"],
                "approval_reason": action["approval_reason"],
                "runbook_ref": action["runbook_ref"],
            }
            for action in actions
        ],
        "bounded_evidence": {
            "anomaly_evidence": rca.get("anomaly_evidence", [])[:5],
            "rca_candidates": rca.get("rca_candidates", [])[:3],
            "recent_deploys": [
                {
                    "service": deploy.service,
                    "version": deploy.version,
                    "deployed_at": deploy.deployed_at,
                    "change_summary": deploy.change_summary,
                    "rollback_ref": deploy.rollback_ref,
                }
                for deploy in request.recent_deploys[:2]
            ],
            "logs": [
                {
                    "service": log.service,
                    "level": log.level,
                    "message": log.message,
                }
                for log in request.logs[:3]
            ],
        },
        "output_schema": {"actions": [{"id": "string", "summary": "string", "why": "string"}]},
    }
