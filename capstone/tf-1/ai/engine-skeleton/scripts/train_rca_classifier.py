from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import joblib
import numpy as np
from sklearn.ensemble import ExtraTreesClassifier
from sklearn.feature_extraction import DictVectorizer
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix
from sklearn.model_selection import StratifiedShuffleSplit
from sklearn.pipeline import Pipeline

from app.main import TriageRequest, classify
from app.rca import analyze_request
from app.rca_features import CLASS_LABELS, extract_rca_features
from scripts.adapt_rcaeval_case import build_triage_request
from scripts.benchmark_rcaeval_telemetry_only import expected_classification, scenario_for_case


def strict_hide_alert_metadata(body: dict[str, Any]) -> dict[str, Any]:
    sanitized = json.loads(json.dumps(body))
    sanitized["alert"]["severity"] = "high"
    sanitized["alert"]["title"] = "Telemetry anomaly detected"
    sanitized["alert"]["description"] = "Alert metadata intentionally hidden for telemetry-only RCA evaluation."
    sanitized["alert"]["labels"] = {
        "dataset": "RCAEval",
        "metadata_hidden": True,
    }
    return sanitized


def build_case_request(case_dir: Path, metadata_mode: str) -> TriageRequest:
    scenario = scenario_for_case(case_dir) if metadata_mode == "mapped" else None
    body = build_triage_request(case_dir, scenario=scenario)
    if metadata_mode == "hidden":
        body = strict_hide_alert_metadata(body)
    return TriageRequest.model_validate(body)


def load_dataset(data_root: Path, metadata_mode: str, include_metadata_features: bool, fast_raw_only: bool) -> tuple[list[dict[str, float]], list[str], list[dict[str, Any]]]:
    features: list[dict[str, float]] = []
    labels: list[str] = []
    rows: list[dict[str, Any]] = []
    cases = sorted(path for path in data_root.iterdir() if path.is_dir() and (path / "metrics.json").exists())
    for index, case_dir in enumerate(cases, start=1):
        request = build_case_request(case_dir, metadata_mode)
        rca = {"anomaly_evidence": [], "rca_candidates": []} if fast_raw_only else analyze_request(request)
        deterministic = {"classification": "not_run", "confidence": 0.0} if fast_raw_only else classify(request, rca)
        label = expected_classification(case_dir)
        feature_row = extract_rca_features(request, rca, include_metadata=include_metadata_features)
        feature_row["deterministic_confidence"] = float(deterministic.get("confidence", 0.0) or 0.0)
        feature_row[f"deterministic_class_{deterministic.get('classification')}"] = 1.0
        features.append(feature_row)
        labels.append(label)
        rows.append(
            {
                "case": case_dir.name,
                "expected": label,
                "deterministic": deterministic.get("classification"),
                "service": request.alert.service,
            }
        )
        if index % 50 == 0:
            print(f"extracted {index} cases...")
    return features, labels, rows


def build_model(random_state: int) -> Pipeline:
    model = ExtraTreesClassifier(
        n_estimators=800,
        max_depth=None,
        min_samples_leaf=1,
        class_weight="balanced",
        random_state=random_state,
        n_jobs=-1,
    )
    return Pipeline([("vectorizer", DictVectorizer(sparse=False)), ("model", model)])


def evaluate_predictions(y_true: list[str], y_pred: list[str], probabilities: np.ndarray | None = None) -> dict[str, Any]:
    report = classification_report(y_true, y_pred, labels=list(CLASS_LABELS), output_dict=True, zero_division=0)
    payload: dict[str, Any] = {
        "accuracy": round(float(accuracy_score(y_true, y_pred)), 4),
        "macro_precision": round(float(report["macro avg"]["precision"]), 4),
        "macro_recall": round(float(report["macro avg"]["recall"]), 4),
        "macro_f1": round(float(report["macro avg"]["f1-score"]), 4),
        "per_class": {
            label: {
                "precision": round(float(report[label]["precision"]), 4),
                "recall": round(float(report[label]["recall"]), 4),
                "f1": round(float(report[label]["f1-score"]), 4),
                "support": int(report[label]["support"]),
            }
            for label in CLASS_LABELS
        },
        "confusion_matrix": {
            label: {
                predicted: int(value)
                for predicted, value in zip(CLASS_LABELS, row, strict=False)
            }
            for label, row in zip(CLASS_LABELS, confusion_matrix(y_true, y_pred, labels=list(CLASS_LABELS)), strict=False)
        },
    }
    if probabilities is not None and len(probabilities):
        payload["confidence"] = {
            "avg_max_probability": round(float(np.max(probabilities, axis=1).mean()), 4),
            "min_max_probability": round(float(np.max(probabilities, axis=1).min()), 4),
        }
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Train a lightweight supervised RCA classifier from RCAEval-derived features.")
    parser.add_argument("--data-root", type=Path, default=Path("datapack/RCAEval-v2/data"))
    parser.add_argument("--metadata-mode", choices=["hidden", "mapped"], default="hidden")
    parser.add_argument("--include-metadata-features", action="store_true")
    parser.add_argument("--test-size", type=float, default=0.3)
    parser.add_argument("--random-state", type=int, default=42)
    parser.add_argument("--fast-raw-only", action="store_true", help="Train on raw telemetry/metadata features without running RCA detectors.")
    parser.add_argument("--model-output", type=Path, default=Path("models/rca_classifier.joblib"))
    parser.add_argument("--report-output", type=Path, default=Path("reports-e2e/ml-rca-classifier-report.json"))
    args = parser.parse_args()

    features, labels, rows = load_dataset(args.data_root, args.metadata_mode, args.include_metadata_features, args.fast_raw_only)
    splitter = StratifiedShuffleSplit(n_splits=1, test_size=args.test_size, random_state=args.random_state)
    train_idx, test_idx = next(splitter.split(features, labels))
    x_train = [features[index] for index in train_idx]
    x_test = [features[index] for index in test_idx]
    y_train = [labels[index] for index in train_idx]
    y_test = [labels[index] for index in test_idx]

    pipeline = build_model(args.random_state)
    pipeline.fit(x_train, y_train)
    y_pred = list(pipeline.predict(x_test))
    probabilities = pipeline.predict_proba(x_test) if hasattr(pipeline, "predict_proba") else None
    evaluation = evaluate_predictions(y_test, y_pred, probabilities)
    test_rows = []
    for row_index, prediction in zip(test_idx, y_pred, strict=False):
        row = dict(rows[row_index])
        row["prediction"] = prediction
        row["correct"] = prediction == row["expected"]
        test_rows.append(row)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "metadata_mode": args.metadata_mode,
        "include_metadata_features": args.include_metadata_features,
        "fast_raw_only": args.fast_raw_only,
        "model": "ExtraTreesClassifier(class_weight=balanced)",
        "random_state": args.random_state,
        "test_size": args.test_size,
        "total_cases": len(labels),
        "train_cases": len(train_idx),
        "test_cases": len(test_idx),
        "label_counts": dict(Counter(labels)),
        "evaluation": evaluation,
        "test_failures": [row for row in test_rows if not row["correct"]][:100],
        "test_cases_detail": test_rows,
    }

    args.model_output.parent.mkdir(parents=True, exist_ok=True)
    args.report_output.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(
        {
            "pipeline": pipeline,
            "labels": CLASS_LABELS,
            "metadata_mode": args.metadata_mode,
            "include_metadata_features": args.include_metadata_features,
            "fast_raw_only": args.fast_raw_only,
            "random_state": args.random_state,
            "created_at": payload["generated_at"],
        },
        args.model_output,
    )
    args.report_output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps({key: payload[key] for key in ("metadata_mode", "include_metadata_features", "fast_raw_only", "total_cases", "train_cases", "test_cases", "evaluation")}, indent=2))
    print(f"wrote model {args.model_output}")
    print(f"wrote report {args.report_output}")


if __name__ == "__main__":
    main()
