# RCAEval Expanded Sample Analysis - 2026-07-01

Purpose: validate whether larger AgentCore samples and focused weak-case sampling change the demo decision.

## Runs

| Run | Scope | Metadata | AgentCore | Accuracy | Macro F1 | Avg latency |
| --- | ---: | --- | --- | ---: | ---: | ---: |
| Stratified sample | 30 cases, 10/class | hidden | on | 80.00% | 79.80% | 2187.47 ms |
| Focused weak cases | 20 deterministic hidden-metadata failures, 10 critical + 10 latency | hidden | on | 0.00% | 0.00% | 2115.64 ms |
| Same focused weak cases | same 20 cases | mapped | off | 40.00% | 26.67% | 0.28 ms |

## Evidence Files

- `reports-e2e/rcaeval-hidden-metadata-agentcore-sample30.json`
- `reports-e2e/rcaeval-hidden-metadata-agentcore-focused-critical-latency-failures-sample20.json`
- `reports-e2e/rcaeval-mapped-metadata-deterministic-focused-critical-latency-failures-sample20.json`

## Findings

- The previous 15-case AgentCore sample was optimistic. Increasing to 30 cases gives a more conservative 80.0% accuracy / 79.8% macro F1.
- Current AgentCore prompt/runtime does not rescue the hardest hidden-metadata critical/latency failures. On the 20 focused deterministic failures, it mostly returns `noisy_or_ambiguous_alert`/`INVESTIGATE`.
- Production-like metadata helps critical-service-down on the focused weak sample, but latency remains weak on those hardest selected cases.
- Root service localization on the focused sample is still useful at 70%, suggesting the RCA ranker often identifies the right service even when final incident class is wrong.

## Decision

Do not tune AgentCore or deterministic rules further before demo. Present:

- hidden metadata full benchmark as lower-bound stress test;
- production-like metadata full benchmark as expected integration path;
- AgentCore as selective bounded-reasoning path, not guaranteed accuracy booster;
- ML classifier as post-demo path for improving critical/latency class separation.
