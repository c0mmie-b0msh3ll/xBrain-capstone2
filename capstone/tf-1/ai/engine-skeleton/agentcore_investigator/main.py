from __future__ import annotations

import json
import os
from typing import Any

import boto3
from bedrock_agentcore import BedrockAgentCoreApp


app = BedrockAgentCoreApp()

VALID_CLASSIFICATIONS = {
    "insufficient_context",
    "noisy_or_ambiguous_alert",
    "critical_service_down",
    "latency_degradation",
    "general_investigation",
}
VALID_STATUSES = {"DIAGNOSED", "INVESTIGATE", "INSUFFICIENT_CONTEXT"}
DEFAULT_MODEL_ID = "us.amazon.nova-micro-v1:0"


@app.entrypoint
def invoke(payload: dict[str, Any]) -> dict[str, Any]:
    task = str(payload.get("task", "agent_platform_investigation"))
    if task in {"tool_planning", "tools"}:
        return model_tool_calls(payload)
    if task == "investigation_summary":
        return model_summary(payload)
    if task == "action_wording":
        return model_action_wording(payload)
    return model_final_diagnosis(payload)


def model_tool_calls(payload: dict[str, Any]) -> dict[str, Any]:
    allowed_tools = payload.get("allowed_tools", [])
    if not isinstance(allowed_tools, list):
        allowed_tools = []
    allowed_tools = [str(item) for item in allowed_tools if isinstance(item, str)]
    max_tool_calls = safe_int(payload.get("max_tool_calls"), 3)
    model_id = os.getenv("BEDROCK_MODEL_ID", DEFAULT_MODEL_ID)
    prompt = {
        "instruction": (
            "Return one strict JSON object for TF1 AI Ops tool planning. "
            "Use only allowed read-only tools. Return an empty tool_calls array when supplied evidence is enough. "
            "Do not include PromQL, LogQL, shell commands, remediation, rollback, restart, scale, Jira, or Slack actions."
        ),
        "required_schema": {"tool_calls": [{"name": "one allowed tool name", "args": {}}]},
        "allowed_tools": allowed_tools,
        "max_tool_calls": max_tool_calls,
        "incident_state": payload.get("current_state", payload.get("input", {})),
    }
    body = invoke_bedrock(model_id, prompt)
    return normalize_tool_calls(body, allowed_tools, max_tool_calls)


def model_summary(payload: dict[str, Any]) -> dict[str, Any]:
    current_state = payload.get("input", {}).get("current_state", payload.get("input", {}))
    model_id = os.getenv("BEDROCK_MODEL_ID", DEFAULT_MODEL_ID)
    prompt = {
        "instruction": (
            "Return one strict JSON object with only a summary string. Use only supplied bounded incident evidence. "
            "Do not invent services, metrics, logs, owners, commands, or remediation."
        ),
        "required_schema": {"summary": "short operational diagnosis"},
        "incident_state": current_state,
    }
    body = invoke_bedrock(model_id, prompt)
    return normalize_summary(body, current_state)


def model_action_wording(payload: dict[str, Any]) -> dict[str, Any]:
    prompt_input = payload.get("input", {})
    selected_actions = prompt_input.get("selected_catalog_actions", []) if isinstance(prompt_input, dict) else []
    if not isinstance(selected_actions, list):
        selected_actions = []
    model_id = os.getenv("BEDROCK_MODEL_ID", DEFAULT_MODEL_ID)
    prompt = {
        "instruction": (
            "Return one strict JSON object with an actions array. Reword only supplied action IDs. "
            "Each item must include id, summary, and why. Do not invent action IDs, commands, remediations, services, evidence, or approval changes."
        ),
        "required_schema": {"actions": [{"id": "existing action id", "summary": "string", "why": "string"}]},
        "selected_actions": selected_actions,
        "incident_state": prompt_input,
    }
    body = invoke_bedrock(model_id, prompt)
    return normalize_actions(body, selected_actions)


def model_final_diagnosis(payload: dict[str, Any]) -> dict[str, Any]:
    current_state = payload.get("input", {}).get("current_state", payload.get("input", {}))
    model_id = os.getenv("BEDROCK_MODEL_ID", DEFAULT_MODEL_ID)
    prompt = {
        "instruction": (
            "Return one strict JSON object for TF1 AI Ops. Use only the supplied evidence. "
            "Allowed classifications: insufficient_context, noisy_or_ambiguous_alert, "
            "critical_service_down, latency_degradation, general_investigation. "
            "Allowed statuses: DIAGNOSED, INVESTIGATE, INSUFFICIENT_CONTEXT. "
            "Do not include shell, PromQL, LogQL, Jira/Slack mutation, rollback, restart, scale, or commands."
        ),
        "required_schema": {
            "type": "final_diagnosis",
            "classification": "one allowed classification",
            "status": "one allowed status",
            "confidence": "number from 0.0 to 1.0",
            "summary": "short operational diagnosis",
            "evidence": ["non-empty evidence strings when diagnosed"],
            "recommended_action_ids": ["known safe action ids if useful"],
            "qa": {"passed": True, "gaps": []},
        },
        "incident_state": current_state,
    }
    body = invoke_bedrock(model_id, prompt)
    return normalize_agent_json(body, current_state)


def invoke_bedrock(model_id: str, prompt: dict[str, Any]) -> str:
    client = boto3.client("bedrock-runtime", region_name=os.getenv("AWS_REGION", "us-east-1"))
    response = client.converse(
        modelId=model_id,
        messages=[
            {
                "role": "user",
                "content": [{"text": json.dumps(prompt, ensure_ascii=True)}],
            }
        ],
        inferenceConfig={"maxTokens": int(os.getenv("BEDROCK_MAX_TOKENS", "700")), "temperature": 0.0},
    )
    parts = response.get("output", {}).get("message", {}).get("content", [])
    return "\n".join(part.get("text", "") for part in parts if isinstance(part, dict)).strip()


def normalize_agent_json(raw_text: str, current_state: dict[str, Any]) -> dict[str, Any]:
    try:
        data = json.loads(extract_json_object(raw_text))
    except Exception:
        data = fallback_from_state(current_state, "Model returned malformed JSON.")
    if not isinstance(data, dict):
        data = fallback_from_state(current_state, "Model returned a non-object JSON value.")

    data["type"] = "final_diagnosis"
    if data.get("classification") not in VALID_CLASSIFICATIONS:
        data["classification"] = current_state.get("classification", {}).get("classification", "general_investigation")
    if data.get("status") not in VALID_STATUSES:
        data["status"] = current_state.get("classification", {}).get("status", "INVESTIGATE")
    try:
        data["confidence"] = max(0.0, min(1.0, float(data.get("confidence", 0.55))))
    except (TypeError, ValueError):
        data["confidence"] = 0.55
    if not isinstance(data.get("summary"), str) or not data["summary"].strip():
        data["summary"] = current_state.get("classification", {}).get("deterministic_summary", "Evidence requires human review.")
    evidence = data.get("evidence")
    if not isinstance(evidence, list) or not all(isinstance(item, str) and item.strip() for item in evidence):
        deterministic = current_state.get("classification", {}).get("deterministic_evidence", [])
        evidence = deterministic if deterministic else ["No additional model evidence beyond deterministic baseline."]
    data["evidence"] = [str(item).strip() for item in evidence if str(item).strip()][:6]
    if data["status"] == "DIAGNOSED" and not data["evidence"]:
        data["evidence"] = ["Diagnosis is based on supplied bounded incident evidence."]
    if not isinstance(data.get("recommended_action_ids"), list):
        data["recommended_action_ids"] = []
    data["recommended_action_ids"] = [item for item in data["recommended_action_ids"] if isinstance(item, str)]
    qa = data.get("qa")
    data["qa"] = qa if isinstance(qa, dict) else {"passed": True, "gaps": []}
    return data


def normalize_tool_calls(raw_text: str, allowed_tools: list[str], max_tool_calls: int) -> dict[str, Any]:
    try:
        data = json.loads(extract_json_object(raw_text))
    except Exception:
        data = {}
    calls = data.get("tool_calls") if isinstance(data, dict) else None
    if not isinstance(calls, list):
        calls = []
    allowed = set(allowed_tools)
    normalized: list[dict[str, Any]] = []
    for call in calls:
        if not isinstance(call, dict):
            continue
        name = call.get("name")
        if name not in allowed:
            continue
        args = call.get("args") if isinstance(call.get("args"), dict) else {}
        normalized.append({"name": name, "args": args})
        if len(normalized) >= max_tool_calls:
            break
    return {"tool_calls": normalized}


def normalize_summary(raw_text: str, current_state: dict[str, Any]) -> dict[str, Any]:
    fallback = current_state.get("classification", {}).get("deterministic_summary", "Evidence requires human review.")
    try:
        data = json.loads(extract_json_object(raw_text))
    except Exception:
        text = raw_text.strip()
        return {"summary": text or fallback}
    if isinstance(data, dict):
        for key in ("summary", "text", "response", "output"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return {"summary": value.strip()}
    return {"summary": fallback}


def normalize_actions(raw_text: str, selected_actions: list[dict[str, Any]]) -> dict[str, Any]:
    by_id = {action.get("id"): action for action in selected_actions if isinstance(action, dict) and isinstance(action.get("id"), str)}
    try:
        data = json.loads(extract_json_object(raw_text))
    except Exception:
        data = {}
    proposed_actions = data.get("actions") if isinstance(data, dict) else None
    if not isinstance(proposed_actions, list):
        proposed_actions = []

    normalized: list[dict[str, Any]] = []
    used: set[str] = set()
    for proposed in proposed_actions:
        if not isinstance(proposed, dict):
            continue
        action_id = proposed.get("id")
        if action_id not in by_id or action_id in used:
            continue
        source = by_id[action_id]
        summary = proposed.get("summary") if isinstance(proposed.get("summary"), str) else source.get("summary", "")
        why = proposed.get("why") if isinstance(proposed.get("why"), str) else source.get("why", "")
        normalized.append({"id": action_id, "summary": summary.strip(), "why": why.strip()})
        used.add(action_id)

    for action_id, source in by_id.items():
        if action_id in used:
            continue
        normalized.append(
            {
                "id": action_id,
                "summary": str(source.get("summary", "")).strip(),
                "why": str(source.get("why", "")).strip(),
            }
        )
    return {"actions": normalized}


def fallback_from_state(current_state: dict[str, Any], gap: str) -> dict[str, Any]:
    classification = current_state.get("classification", {})
    return {
        "type": "final_diagnosis",
        "classification": classification.get("classification", "general_investigation"),
        "status": classification.get("status", "INVESTIGATE"),
        "confidence": classification.get("confidence", 0.55),
        "summary": classification.get("deterministic_summary", "Evidence requires human review."),
        "evidence": classification.get("deterministic_evidence", ["Deterministic baseline evidence was used."]),
        "recommended_action_ids": [],
        "qa": {"passed": False, "gaps": [gap]},
    }


def extract_json_object(text: str) -> str:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return text
    return text[start : end + 1]


def safe_int(value: Any, default: int) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


if __name__ == "__main__":
    app.run()
