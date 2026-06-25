# Public Dataset Review - TF1 Triage Hub

Owner: AI team TF1  
Status: Selected primary external dataset  
Last updated: 2026-06-23

## Decision

Use **RCAEval** as the primary external dataset direction for RCA evaluation.

- GitHub: https://github.com/phamquiluan/RCAEval
- Zenodo: https://zenodo.org/records/14590730
- Figshare: https://figshare.com/articles/dataset/RCAEval_A_Benchmark_for_Root_Cause_Analysis_of_Microservice_Systems/31048672

Keep the current synthetic datapack only as controlled API/demo fixtures for contract shape, Jira ticket fields, Slack-renderable response fields, and deterministic smoke tests.

## Why RCAEval

RCAEval is closer to TF1 than generic log anomaly datasets because it is built for root cause analysis in microservice systems. Its README describes:

- nine datasets,
- 735 real failure cases,
- Online Boutique, Sock Shop, and Train Ticket systems,
- fault types such as CPU, memory, disk, delay, loss, and socket,
- root-cause service and root-cause indicator labels,
- metric-based, trace-based, and multi-source RCA baselines.

This maps better to TF1's triage goal than HDFS/BGL/OpenStack-style log anomaly datasets, which are useful for anomaly detection but weak for incident context, RCA, and Jira/Slack output.

## Dataset Comparison

| Dataset | Fit | Strength | Weakness | Decision |
|---|---|---|---|---|
| RCAEval | High | Microservice RCA with failure labels and telemetry. | Large download; needs adapter. | Primary external dataset. |
| LO2 | Medium | Microservice logs and metrics anomaly dataset. | More anomaly-focused than RCA workflow. | Backup/reference. |
| HDFS/BGL/OpenStack | Low-medium | Common log anomaly benchmarks. | Mostly logs-only; weak for RCA context package. | Not primary. |

## How We Use It

```text
RCAEval case directory
  -> metrics.json / logs.csv / traces.csv / inject_time.txt
  -> RCAEval adapter
  -> observability-data-contract records
  -> AIOps context package
  -> POST /v1/triage
```

Synthetic data remains useful for:

- repeatable demo scenarios,
- Jira ticket fields and Slack-renderable raw response shape,
- smoke testing `/v1/triage`,
- documenting expected happy-path and edge behavior.

Synthetic data must not be presented as the main evidence dataset for RCA quality.

## Acceptance Criteria

- At least one RCAEval case is mapped into our observability contract.
- The mapped case produces a valid triage request.
- Eval report separates:
  - synthetic fixture smoke tests,
  - RCAEval-based external validation.
- Any metric like precision/recall/F1 is reported only against external/public labeled data or clearly marked as synthetic.
