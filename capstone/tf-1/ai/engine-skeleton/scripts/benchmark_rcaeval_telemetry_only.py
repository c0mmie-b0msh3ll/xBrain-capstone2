from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent_runtime import run_agent_platform
from app.context_tools import ToolRegistry
from app.main import TriageRequest, classify
from app.rca import analyze_request
from scripts.adapt_rcaeval_case import build_triage_request, infer_service_fault


EXPECTED_BY_FAULT = {
    "delay": "latency_degradation",
    "loss": "critical_service_down",
}
NOISY_FAULTS = {
    "cpu",
    "mem",
    "memory",
    "disk",
    "diskio",
    "socket",
    "f1",
    "f2",
    "f3",
    "f4",
}


def expected_classification(case_dir: Path) -> str:
    _, fault = infer_service_fault(case_dir)
    normalized = fault.lower()
    if normalized in EXPECTED_BY_FAULT:
        return EXPECTED_BY_FAULT[normalized]
    if normalized in NOISY_FAULTS:
        return "noisy_or_ambiguous_alert"
    return "noisy_or_ambiguous_alert"


def hide_alert_metadata(body: dict[str, Any]) -> dict[str, Any]:
    sanitized = json.loads(json.dumps(body))
    case = sanitized["alert"]["labels"].get("case", sanitized["incident_id"])
    sanitized["alert"]["severity"] = "high"
    sanitized["alert"]["title"] = f"Telemetry anomaly detected for case {case}"
    sanitized["alert"]["description"] = "Alert metadata intentionally hidden for telemetry-only RCA evaluation."
    sanitized["alert"]["labels"] = {
        "dataset": "RCAEval",
        "case": case,
        "metadata_hidden": True,
    }
    return sanitized


def scenario_for_case(case_dir: Path) -> str | None:
    expected = expected_classification(case_dir)
    if expected == "latency_degradation":
        return "latency-degradation"
    if expected == "critical_service_down":
        return "critical-service-down"
    if expected == "noisy_or_ambiguous_alert":
        return "noisy-false-alert"
    return None


def normalize_prediction(classification: str, map_general_to_noisy: bool) -> str:
    if map_general_to_noisy and classification == "general_investigation":
        return "noisy_or_ambiguous_alert"
    return classification


def safe_div(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


def classification_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    labels = sorted({row["expected"] for row in rows} | {row["predicted"] for row in rows})
    per_class: dict[str, Any] = {}
    confusion: dict[str, dict[str, int]] = {label: {} for label in labels}
    for row in rows:
        confusion.setdefault(row["expected"], {})
        confusion[row["expected"]][row["predicted"]] = confusion[row["expected"]].get(row["predicted"], 0) + 1

    for label in labels:
        tp = sum(1 for row in rows if row["expected"] == label and row["predicted"] == label)
        fp = sum(1 for row in rows if row["expected"] != label and row["predicted"] == label)
        fn = sum(1 for row in rows if row["expected"] == label and row["predicted"] != label)
        precision = safe_div(tp, tp + fp)
        recall = safe_div(tp, tp + fn)
        f1 = safe_div(2 * precision * recall, precision + recall)
        per_class[label] = {
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
        }

    accuracy = safe_div(sum(1 for row in rows if row["correct"]), len(rows))
    macro_precision = safe_div(sum(item["precision"] for item in per_class.values()), len(per_class))
    macro_recall = safe_div(sum(item["recall"] for item in per_class.values()), len(per_class))
    macro_f1 = safe_div(sum(item["f1"] for item in per_class.values()), len(per_class))
    return {
        "accuracy": round(accuracy, 4),
        "macro_precision": round(macro_precision, 4),
        "macro_recall": round(macro_recall, 4),
        "macro_f1": round(macro_f1, 4),
        "per_class": per_class,
        "confusion_matrix": confusion,
    }


def case_dirs(data_root: Path, limit: int | None, sample_per_class: int | None) -> list[Path]:
    cases = sorted(path for path in data_root.iterdir() if path.is_dir() and (path / "metrics.json").exists())
    if sample_per_class:
        selected: list[Path] = []
        counts: Counter[str] = Counter()
        for case in cases:
            label = expected_classification(case)
            if counts[label] >= sample_per_class:
                continue
            selected.append(case)
            counts[label] += 1
            if all(counts[label] >= sample_per_class for label in ("critical_service_down", "latency_degradation", "noisy_or_ambiguous_alert")):
                break
        cases = selected
    return cases[:limit] if limit else cases


def run_case(case_dir: Path, map_general_to_noisy: bool, metadata_mode: str, use_agentcore: bool) -> dict[str, Any]:
    scenario = scenario_for_case(case_dir) if metadata_mode == "mapped" else None
    body = build_triage_request(case_dir, scenario=scenario)
    if metadata_mode == "hidden":
        body = hide_alert_metadata(body)
    request = TriageRequest.model_validate(body)
    rca = analyze_request(request)
    started = time.perf_counter()
    decision = classify(request, rca)
    agent_metadata: dict[str, Any] | None = None
    if use_agentcore:
        request, rca, decision, agent_metadata, _ = run_agent_platform(request, decision, rca, ToolRegistry())
    duration_ms = round((time.perf_counter() - started) * 1000, 2)
    expected = expected_classification(case_dir)
    predicted = normalize_prediction(decision["classification"], map_general_to_noisy)
    top_candidate = rca.get("rca_candidates", [{}])[0].get("service") if rca.get("rca_candidates") else None
    service, fault = infer_service_fault(case_dir)
    return {
        "case": case_dir.name,
        "service": service,
        "fault": fault,
        "expected": expected,
        "raw_classification": decision["classification"],
        "predicted": predicted,
        "correct": predicted == expected,
        "status": decision["status"],
        "confidence": decision["confidence"],
        "duration_ms": duration_ms,
        "agent_metadata": agent_metadata,
        "anomaly_evidence_count": len(rca.get("anomaly_evidence", [])),
        "detectors": sorted(Counter(item.get("detector", "unknown") for item in rca.get("anomaly_evidence", [])).items()),
        "top_rca_candidate": top_candidate,
        "root_service_top1": top_candidate == service,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run RCAEval benchmark against TF1 AIOps classifier/AgentCore paths.")
    parser.add_argument("--data-root", type=Path, default=Path("datapack/RCAEval-v2/data"))
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--sample-per-class", type=int, default=None)
    parser.add_argument("--output", type=Path, default=Path("reports-e2e/rcaeval-telemetry-only-benchmark.json"))
    parser.add_argument("--metadata-mode", choices=["hidden", "mapped"], default="hidden")
    parser.add_argument("--agentcore", action="store_true")
    parser.add_argument("--no-map-general-to-noisy", action="store_true")
    args = parser.parse_args()

    map_general_to_noisy = not args.no_map_general_to_noisy
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for index, case_dir in enumerate(case_dirs(args.data_root, args.limit, args.sample_per_class), start=1):
        try:
            rows.append(run_case(case_dir, map_general_to_noisy, args.metadata_mode, args.agentcore))
        except Exception as exc:
            errors.append({"case": case_dir.name, "error": type(exc).__name__, "message": str(exc)})
        if index % 50 == 0 or args.agentcore:
            print(f"processed {index} cases...")

    metrics = classification_metrics(rows)
    root_top1_accuracy = safe_div(sum(1 for row in rows if row["root_service_top1"]), len(rows))
    durations = [row["duration_ms"] for row in rows]
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": "RCAEval benchmark for TF1 AIOps classifier/AgentCore paths.",
        "metadata_mode": args.metadata_mode,
        "agentcore_enabled": args.agentcore,
        "data_root": str(args.data_root).replace("\\", "/"),
        "total_cases": len(rows),
        "errors": errors,
        "expected_counts": dict(Counter(row["expected"] for row in rows)),
        "predicted_counts": dict(Counter(row["predicted"] for row in rows)),
        "classification": metrics,
        "root_service_top1_accuracy": round(root_top1_accuracy, 4),
        "latency_ms": {
            "avg": round(safe_div(sum(durations), len(durations)), 2),
            "max": round(max(durations), 2) if durations else 0,
            "min": round(min(durations), 2) if durations else 0,
        },
        "worst_failures": [row for row in rows if not row["correct"]][:50],
        "cases": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("total_cases", "metadata_mode", "agentcore_enabled", "expected_counts", "predicted_counts", "classification", "root_service_top1_accuracy", "latency_ms")}, indent=2))
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
