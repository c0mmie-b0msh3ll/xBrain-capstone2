from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import joblib

from app.rca_features import CLASS_LABELS, extract_rca_features


DEFAULT_MODEL_PATH = "models/rca_classifier_hidden_raw_v1.joblib"


def ml_classifier_enabled() -> bool:
    return os.getenv("AIOPS_ML_CLASSIFIER_ENABLED", "").lower() in {"1", "true", "yes"}


def predict_ml_classification(request: Any, rca: dict[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "enabled": ml_classifier_enabled(),
        "applied": False,
        "provider": "sklearn",
    }
    if not metadata["enabled"]:
        metadata["skipped_reason"] = "disabled"
        return metadata

    model_path = os.getenv("AIOPS_ML_CLASSIFIER_PATH", DEFAULT_MODEL_PATH)
    metadata["model_path"] = model_path
    try:
        artifact = load_model(model_path)
        include_metadata = bool(artifact.get("include_metadata_features", False))
        features = extract_rca_features(request, {"anomaly_evidence": [], "rca_candidates": []}, include_metadata=include_metadata)
        pipeline = artifact["pipeline"]
        prediction = str(pipeline.predict([features])[0])
        probabilities = pipeline.predict_proba([features])[0]
        classes = [str(label) for label in pipeline.classes_]
        probability_by_class = {label: round(float(probabilities[index]), 4) for index, label in enumerate(classes)}
        confidence = max(probability_by_class.values()) if probability_by_class else 0.0
    except Exception as exc:
        metadata["error"] = f"{type(exc).__name__}: {exc}"
        metadata["skipped_reason"] = "prediction_failed"
        return metadata

    metadata.update(
        {
            "prediction": prediction,
            "confidence": confidence,
            "probabilities": probability_by_class,
            "model_metadata_mode": artifact.get("metadata_mode"),
            "model_created_at": artifact.get("created_at"),
        }
    )
    if prediction not in CLASS_LABELS:
        metadata["skipped_reason"] = "invalid_prediction"
    return metadata


def apply_ml_decision(decision: dict[str, Any], ml_metadata: dict[str, Any]) -> dict[str, Any]:
    if not ml_metadata.get("enabled") or ml_metadata.get("prediction") not in CLASS_LABELS:
        return decision
    threshold = env_float("AIOPS_ML_MIN_CONFIDENCE", 0.7)
    confidence = float(ml_metadata.get("confidence", 0.0) or 0.0)
    if confidence < threshold:
        ml_metadata["skipped_reason"] = "below_confidence_threshold"
        return decision

    prediction = str(ml_metadata["prediction"])
    revised = decision.copy()
    revised["classification"] = prediction
    revised["status"] = "INVESTIGATE" if prediction == "noisy_or_ambiguous_alert" else "DIAGNOSED"
    revised["confidence"] = round(min(max(confidence, 0.0), 0.92), 2)
    revised["summary"] = f"ML classifier selected {prediction} from telemetry feature patterns."
    revised["agent_final"] = False
    ml_metadata["applied"] = True
    return revised


@lru_cache(maxsize=4)
def load_model(model_path: str) -> dict[str, Any]:
    resolved = Path(model_path)
    if not resolved.is_absolute():
        resolved = Path(__file__).resolve().parents[1] / resolved
    artifact = joblib.load(resolved)
    if not isinstance(artifact, dict) or "pipeline" not in artifact:
        raise ValueError("ML classifier artifact must contain a pipeline.")
    return artifact


def env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default
