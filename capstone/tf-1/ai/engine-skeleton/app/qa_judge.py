from __future__ import annotations

import json
import os
from typing import Any

from app.observability import BUDGET_EXCEEDED_TOTAL, DEGRADED_MODE_TOTAL, LLM_CALLS_TOTAL, QA_ITERATIONS_TOTAL, estimate_tokens


VALID_VERDICTS = {"pass", "fail", "uncertain"}
DEFAULT_QA_MODEL_ID = "us.amazon.nova-micro-v1:0"


def run_qa(request: Any, decision: dict[str, Any], rca: dict[str, Any]) -> dict[str, Any]:
    max_iterations = env_int("AIOPS_QA_MAX_ITERATIONS", 1)
    repair_max_iterations = env_int("AIOPS_QA_REPAIR_MAX_ITERATIONS", 1)
    token_budget = env_int("AIOPS_LLM_MAX_TOKENS_PER_INCIDENT", 0)
    metadata: dict[str, Any] = {
        "enabled": max_iterations > 0,
        "provider": "deterministic",
        "iterations": 0,
        "repair_iterations": 0,
        "result": "skipped" if max_iterations <= 0 else "passed",
        "deterministic_result": "skipped" if max_iterations <= 0 else "passed",
    }
    if max_iterations <= 0:
        QA_ITERATIONS_TOTAL.labels(result="skipped").inc()
        return metadata

    metadata["iterations"] = 1
    issues = qa_findings(request, decision, rca)
    if token_budget and estimate_qa_tokens(request, decision, rca) > token_budget:
        metadata["result"] = "budget_exceeded"
        metadata["deterministic_result"] = "budget_exceeded"
        metadata["confidence_delta"] = -0.1
        BUDGET_EXCEEDED_TOTAL.labels(budget_type="qa_tokens").inc()
        DEGRADED_MODE_TOTAL.labels(reason="qa_budget_exceeded").inc()
    elif issues:
        metadata["result"] = "failed"
        metadata["deterministic_result"] = "failed"
        metadata["issues"] = issues
        metadata["confidence_delta"] = -0.1
        if repair_max_iterations > 0:
            metadata["repair_iterations"] = 1
            metadata["repair_result"] = "not_attempted_deterministic_only"
        DEGRADED_MODE_TOTAL.labels(reason="qa_failed").inc()

    if qa_llm_enabled():
        metadata = apply_llm_qa(request, decision, rca, metadata)

    QA_ITERATIONS_TOTAL.labels(result=str(metadata["result"])).inc()
    return metadata


def apply_llm_qa(request: Any, decision: dict[str, Any], rca: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any]:
    model_id = os.getenv("AIOPS_QA_MODEL_ID", DEFAULT_QA_MODEL_ID)
    provider = os.getenv("AIOPS_QA_PROVIDER", "bedrock")
    llm_metadata = dict(metadata)
    llm_metadata.update(
        {
            "enabled": True,
            "provider": provider,
            "model": model_id,
            "llm_result": "skipped",
        }
    )
    if provider != "bedrock":
        llm_metadata["llm_result"] = "degraded"
        llm_metadata["error"] = f"Unsupported QA provider: {provider}"
        DEGRADED_MODE_TOTAL.labels(reason="qa_llm_unsupported_provider").inc()
        return llm_metadata

    payload = build_qa_payload(request, decision, rca)
    prompt_tokens = estimate_tokens(payload)
    token_budget = env_int("AIOPS_QA_MAX_TOKENS_PER_INCIDENT", 1200)
    if token_budget and prompt_tokens > token_budget:
        llm_metadata["llm_result"] = "budget_exceeded"
        llm_metadata["budget_exceeded"] = "qa_llm_tokens"
        llm_metadata["prompt_tokens"] = prompt_tokens
        BUDGET_EXCEEDED_TOTAL.labels(budget_type="qa_llm_tokens").inc()
        DEGRADED_MODE_TOTAL.labels(reason="qa_llm_budget_exceeded").inc()
        return llm_metadata

    try:
        raw_text = invoke_bedrock_qa(model_id, payload)
        completion_tokens = estimate_tokens(raw_text)
        verdict = parse_qa_judge_response(raw_text, configured_penalty())
    except Exception as exc:  # pragma: no cover - live AWS path
        LLM_CALLS_TOTAL.labels(stage="qa", model=model_id, status="error").inc()
        DEGRADED_MODE_TOTAL.labels(reason="qa_llm_failure").inc()
        llm_metadata.update(
            {
                "llm_result": "degraded",
                "error": f"{type(exc).__name__}: {exc}",
                "prompt_tokens": prompt_tokens,
            }
        )
        if env_enabled("AIOPS_QA_FAIL_CLOSED"):
            llm_metadata["required_human_review"] = True
            llm_metadata["confidence_delta"] = min(float(llm_metadata.get("confidence_delta", 0) or 0), configured_penalty())
        return llm_metadata

    LLM_CALLS_TOTAL.labels(stage="qa", model=model_id, status="ok").inc()
    estimated_cost = estimate_qa_cost_usd(prompt_tokens, completion_tokens)
    llm_metadata.update(
        {
            "llm_result": "passed" if verdict["verdict"] == "pass" else "failed",
            "verdict": verdict["verdict"],
            "issues": merge_issues(llm_metadata.get("issues", []), verdict["issues"]),
            "rationale": verdict["rationale"],
            "required_human_review": verdict["required_human_review"],
            "confidence_delta": min(float(llm_metadata.get("confidence_delta", 0) or 0), float(verdict["confidence_delta"])),
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "estimated_cost_usd": estimated_cost,
        }
    )
    if verdict["verdict"] in {"fail", "uncertain"}:
        llm_metadata["result"] = "failed"
        DEGRADED_MODE_TOTAL.labels(reason="qa_llm_failed").inc()
    return llm_metadata


def qa_findings(request: Any, decision: dict[str, Any], rca: dict[str, Any]) -> list[str]:
    findings: list[str] = []
    if decision["status"] == "DIAGNOSED" and not decision.get("evidence"):
        findings.append("diagnosis_missing_evidence")
    if decision["status"] == "DIAGNOSED" and not (request.metrics or request.logs or request.recent_deploys or rca.get("anomaly_evidence")):
        findings.append("diagnosis_without_supporting_context")
    if decision["classification"] == "latency_degradation" and "latency" not in " ".join(decision.get("evidence", []) + [request.alert.title]).lower():
        findings.append("latency_classification_without_latency_evidence")
    return findings


def estimate_qa_tokens(request: Any, decision: dict[str, Any], rca: dict[str, Any]) -> int:
    evidence_items = len(request.metrics) + len(request.logs) + len(request.traces) + len(request.recent_deploys)
    return 64 + (evidence_items * 24) + (len(decision.get("evidence", [])) * 16) + (len(rca.get("anomaly_evidence", [])) * 24)


def build_qa_payload(request: Any, decision: dict[str, Any], rca: dict[str, Any]) -> dict[str, Any]:
    return {
        "task": "qa_judge",
        "instruction": (
            "Judge whether the draft AIOps diagnosis is grounded in the supplied bounded evidence. "
            "Return strict JSON only. Do not call tools, suggest commands, create tickets, post messages, or invent evidence."
        ),
        "allowed_schema": {
            "verdict": "pass|fail|uncertain",
            "issues": [
                "missing_evidence",
                "unsupported_classification",
                "unsupported_root_cause",
                "unsafe_action",
                "invented_service_or_signal",
                "insufficient_context",
                "schema_mismatch",
            ],
            "confidence_delta": "number from -0.2 to 0",
            "rationale": "short evidence-grounded explanation",
            "required_human_review": True,
        },
        "incident": {
            "tenant_id": request.tenant_id,
            "incident_id": request.incident_id,
            "service": request.alert.service,
            "severity": request.alert.severity,
            "title": request.alert.title,
        },
        "draft_result": {
            "classification": decision.get("classification"),
            "status": decision.get("status"),
            "confidence": decision.get("confidence"),
            "summary": decision.get("summary"),
            "evidence": decision.get("evidence", [])[:6],
            "recommended_action_ids": [action[0] for action in decision.get("actions", []) if isinstance(action, (list, tuple)) and action],
        },
        "bounded_evidence": {
            "metrics": compact_metrics(request),
            "logs": compact_logs(request),
            "deploys": compact_deploys(request),
            "runbooks": compact_runbooks(request),
            "rca_candidates": rca.get("rca_candidates", [])[:5],
            "anomaly_evidence": rca.get("anomaly_evidence", [])[:8],
        },
    }


def invoke_bedrock_qa(model_id: str, payload: dict[str, Any]) -> str:
    import boto3

    client = boto3.client("bedrock-runtime", region_name=os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1")
    response = client.converse(
        modelId=model_id,
        messages=[{"role": "user", "content": [{"text": json.dumps(payload, ensure_ascii=True)}]}],
        inferenceConfig={"maxTokens": env_int("AIOPS_QA_MAX_OUTPUT_TOKENS", 350), "temperature": 0.0},
    )
    parts = response.get("output", {}).get("message", {}).get("content", [])
    return "\n".join(part.get("text", "") for part in parts if isinstance(part, dict)).strip()


def parse_qa_judge_response(raw_text: str, penalty_floor: float | None = None) -> dict[str, Any]:
    penalty_floor = configured_penalty() if penalty_floor is None else penalty_floor
    payload = json.loads(extract_json_object(raw_text))
    if not isinstance(payload, dict):
        raise ValueError("QA judge response must be a JSON object.")
    verdict = payload.get("verdict")
    if verdict not in VALID_VERDICTS:
        raise ValueError("QA judge verdict must be pass, fail, or uncertain.")
    issues = payload.get("issues")
    if not isinstance(issues, list):
        issues = []
    issues = [str(issue) for issue in issues if isinstance(issue, str) and issue.strip()][:8]
    try:
        confidence_delta = float(payload.get("confidence_delta", 0))
    except (TypeError, ValueError):
        confidence_delta = 0.0
    confidence_delta = max(penalty_floor, min(0.0, confidence_delta))
    rationale = payload.get("rationale")
    required_human_review = payload.get("required_human_review")
    if verdict == "pass":
        issues = []
        confidence_delta = 0.0
        required_human_review = False

    return {
        "verdict": verdict,
        "issues": issues,
        "confidence_delta": confidence_delta,
        "rationale": rationale.strip() if isinstance(rationale, str) and rationale.strip() else "",
        "required_human_review": bool(required_human_review) or verdict in {"fail", "uncertain"},
    }


def compact_metrics(request: Any) -> list[dict[str, Any]]:
    return [
        {
            "metric_name": metric.metric_name,
            "service": metric.service,
            "unit": metric.unit,
            "points": [{"ts": point.ts, "value": point.value} for point in metric.points[-3:]],
        }
        for metric in request.metrics[:5]
    ]


def compact_logs(request: Any) -> list[dict[str, Any]]:
    return [{"service": log.service, "level": log.level, "message": log.message[:240], "trace_id": log.trace_id} for log in request.logs[:5]]


def compact_deploys(request: Any) -> list[dict[str, Any]]:
    return [
        {
            "service": deploy.service,
            "version": deploy.version,
            "deployed_at": deploy.deployed_at,
            "change_summary": deploy.change_summary,
        }
        for deploy in request.recent_deploys[:3]
    ]


def compact_runbooks(request: Any) -> list[dict[str, Any]]:
    runbooks = request.ownership.runbooks if request.ownership else []
    return [{"title": runbook.title, "url": runbook.url, "excerpt": runbook.excerpt} for runbook in runbooks[:3]]


def extract_json_object(text: str) -> str:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return text
    return text[start : end + 1]


def estimate_qa_cost_usd(prompt_tokens: int, completion_tokens: int) -> float:
    input_rate = float(os.getenv("AIOPS_QA_INPUT_COST_PER_1K", os.getenv("AIOPS_LLM_INPUT_COST_PER_1K", "0")) or 0)
    output_rate = float(os.getenv("AIOPS_QA_OUTPUT_COST_PER_1K", os.getenv("AIOPS_LLM_OUTPUT_COST_PER_1K", "0")) or 0)
    return round((prompt_tokens / 1000.0 * input_rate) + (completion_tokens / 1000.0 * output_rate), 8)


def configured_penalty() -> float:
    try:
        value = float(os.getenv("AIOPS_QA_CONFIDENCE_PENALTY", "-0.1"))
    except ValueError:
        value = -0.1
    return max(-0.2, min(0.0, value))


def merge_issues(existing: Any, new: list[str]) -> list[str]:
    values = [str(issue) for issue in existing if isinstance(issue, str)] if isinstance(existing, list) else []
    values.extend(new)
    return list(dict.fromkeys(values))


def qa_llm_enabled() -> bool:
    return env_enabled("ENABLE_QA_LLM")


def env_enabled(name: str) -> bool:
    return os.getenv(name, "").lower() in {"1", "true", "yes"}


def env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default
