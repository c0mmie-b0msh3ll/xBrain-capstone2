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
    if task != "agent_platform_investigation":
        return model_final_diagnosis(payload)
    return model_final_diagnosis(payload)


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


if __name__ == "__main__":
    app.run()
