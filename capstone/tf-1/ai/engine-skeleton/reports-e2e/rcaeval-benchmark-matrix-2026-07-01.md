# RCAEval Benchmark Matrix - 2026-07-01

Scope: TF1 AIOps RCA benchmark on extracted `RCAEval-v2` cases.

## Modes

| Mode | Dataset Size | Metadata | AgentCore/LLM | Accuracy | Macro Precision | Macro Recall | Macro F1 | Avg Latency |
| --- | ---: | --- | --- | ---: | ---: | ---: | ---: | ---: |
| Conservative lower bound | 735 full | hidden alert title/description/severity | off | 62.45% | 48.08% | 48.17% | 48.11% | 0.47 ms |
| Production-like metadata | 735 full | mapped scenario metadata | off | 89.66% | 89.53% | 78.89% | 81.03% | 0.45 ms |
| AgentCore sample | 15, 5/class | hidden alert title/description/severity | on | 93.33% | 94.44% | 93.33% | 93.27% | 2138.69 ms |
| AgentCore + metadata sample | 15, 5/class | mapped scenario metadata | on | 93.33% | 94.44% | 93.33% | 93.27% | 2162.09 ms |

## Evidence Files

- `reports-e2e/rcaeval-hidden-metadata-deterministic-full.json`
- `reports-e2e/rcaeval-mapped-metadata-deterministic-full.json`
- `reports-e2e/rcaeval-hidden-metadata-agentcore-sample15.json`
- `reports-e2e/rcaeval-mapped-metadata-agentcore-sample15.json`

## Notes For Demo

- Hidden-metadata full run is the conservative lower bound: alert hints are intentionally removed, AgentCore/LLM is disabled, and the engine must infer from telemetry only.
- Production-like metadata run is the more realistic default path: service, severity, alert title, ownership, and scenario-level context are available.
- AgentCore runs were sampled because each case invokes the runtime and has non-zero cost/latency. The sample uses 5 cases per class, matching the mentor feedback minimum.
- AgentCore average latency was about 2.1 seconds in this run with `AIOPS_AGENT_MAX_ITERATIONS=1` and `AIOPS_AGENT_MAX_TOOL_CALLS=1`. The trade-off is higher latency for bounded reasoning and auditability.
- Deterministic path remains sub-millisecond and should be the default for high-volume triage; AgentCore should be routed to ambiguous, missing-context, or high-value incidents.
