from __future__ import annotations

import json
import os
from typing import Any


DEFAULT_MODEL_IDS = [
    "us.anthropic.claude-opus-4-8",
    "us.anthropic.claude-opus-4-6-v1",
    "us.amazon.nova-2-lite-v1:0",
]


def synthesize_investigation_summary(request: Any, decision: dict[str, Any], rca: dict[str, Any]) -> dict[str, Any]:
    model_ids = configured_model_ids()
    enabled = os.getenv("ENABLE_BEDROCK_LLM", "").lower() in {"1", "true", "yes"} or bool(os.getenv("BEDROCK_MODEL_ID") or os.getenv("BEDROCK_MODEL_IDS"))
    if not enabled:
        return {"enabled": False, "provider": "deterministic"}

    region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1"
    try:
        import boto3

        client = boto3.client("bedrock-runtime", region_name=region)
    except Exception as exc:  # pragma: no cover - exercised only with live AWS credentials
        return {
            "enabled": True,
            "provider": "bedrock",
            "model_ids": model_ids,
            "region": region,
            "error": f"{type(exc).__name__}: {exc}",
        }

    errors: list[dict[str, str]] = []
    prompt_payload = build_prompt_payload(request, decision, rca)
    for model_id in model_ids:
        try:
            response = client.converse(
                modelId=model_id,
                system=[
                    {
                        "text": (
                            "You are an AIOps incident investigator. Use only the provided bounded evidence. "
                            "Do not invent services, metrics, logs, timestamps, owners, or remediation. "
                            "Write a concise operational summary with root-cause hypothesis, evidence, confidence caveat, and next action."
                        )
                    }
                ],
                messages=[
                    {
                        "role": "user",
                        "content": [{"text": json.dumps(prompt_payload, ensure_ascii=True)}],
                    }
                ],
                inferenceConfig={"maxTokens": 450, "temperature": 0.1},
            )
            summary = response["output"]["message"]["content"][0]["text"].strip()
            return {
                "enabled": True,
                "provider": "bedrock",
                "model_id": model_id,
                "model_ids": model_ids,
                "region": region,
                "fallback_errors": errors,
                "summary": summary,
            }
        except Exception as exc:  # pragma: no cover - exercised only with live AWS credentials
            errors.append({"model_id": model_id, "error": f"{type(exc).__name__}: {exc}"})

    return {
        "enabled": True,
        "provider": "bedrock",
        "model_ids": model_ids,
        "region": region,
        "fallback_errors": errors,
        "error": "All configured Bedrock models failed.",
    }


def configured_model_ids() -> list[str]:
    csv_value = os.getenv("BEDROCK_MODEL_IDS")
    single_value = os.getenv("BEDROCK_MODEL_ID")
    if csv_value:
        return [item.strip() for item in csv_value.split(",") if item.strip()]
    if single_value:
        return [single_value.strip(), *[model_id for model_id in DEFAULT_MODEL_IDS if model_id != single_value.strip()]]
    return DEFAULT_MODEL_IDS


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
