# ML RCA Classifier Summary - 2026-07-01

Scope: lightweight supervised classifier experiment for hidden-metadata RCAEval classification.

## Implementation

- Feature extractor: `app/rca_features.py`
- Runtime feature flag: `app/ml_classifier.py`
- Training script: `scripts/train_rca_classifier.py`
- Model artifact: `models/rca_classifier_hidden_raw_v1.joblib`
- Training approach: `ExtraTreesClassifier(class_weight=balanced)` through a `DictVectorizer` pipeline.
- Runtime dependencies: existing `scikit-learn` + `joblib`; no new package is required.
- Runtime behavior: disabled by default; if enabled, model errors or low confidence fall back to deterministic classification.

## Strict Evaluation

Dataset: full extracted RCAEval-v2, 735 cases.

Split: stratified 70/30, random seed 42.

Mode: strict hidden metadata, raw telemetry features only. The model does not read RCAEval case names, fault labels, alert labels, or mapped scenario strings.

| Metric | Deterministic same split | ML classifier same split |
| --- | ---: | ---: |
| Accuracy | 66.06% | 76.92% |
| Macro F1 | 54.00% | 59.37% |
| Critical recall | 33.33% | 41.67% |
| Latency recall | 47.22% | 38.89% |
| Noisy recall | 78.52% | 94.63% |

## Interpretation

- ML improves hidden-metadata overall accuracy and macro F1 without adding external runtime calls.
- It helps critical-service-down recall and noisy/resource classification, but latency hard cases remain difficult.
- This is not a replacement for richer evidence or AgentCore. It is a cheap accuracy layer that can be enabled behind a confidence threshold.
- The mapped-metadata ML run reaches 100% on the split, but that result should not be used as a headline because the generated scenario metadata may encode the target class too directly.

## Runtime Config

```text
AIOPS_ML_CLASSIFIER_ENABLED=true
AIOPS_ML_CLASSIFIER_PATH=models/rca_classifier_hidden_raw_v1.joblib
AIOPS_ML_MIN_CONFIDENCE=0.70
```

## Deploy Impact

- `/v1/triage` contract is unchanged.
- CDO only needs to rebuild/redeploy the AI engine image if this feature is included.
- The model file is baked into the image; no S3/model registry/IAM/network access is required.
- Rollback can be done by setting `AIOPS_ML_CLASSIFIER_ENABLED=false` or deploying the previous tag.

## Evidence

- `reports-e2e/ml-rca-classifier-hidden-raw-v1.json`
- `reports-e2e/ml-hidden-raw-model-grid.json`
- `reports-e2e/rcaeval-hidden-metadata-ml-runtime-limit100.json` is a runtime smoke check only, not a strict benchmark headline.
