# External Dataset Integration

Primary dataset: **RCAEval**.

RCAEval is not vendored in this repo because the full datasets are large. Download it from the official sources:

- GitHub: https://github.com/phamquiluan/RCAEval
- Zenodo: https://zenodo.org/records/14590730
- Figshare: https://figshare.com/articles/dataset/RCAEval_A_Benchmark_for_Root_Cause_Analysis_of_Microservice_Systems/31048672

## Expected RCAEval Case Shape

RCAEval documents each case directory with:

```text
{benchmark}_{service}_{fault}_{instance}/
  metrics.json
  inject_time.txt
  logs.csv      # RE2/RE3 when available
  traces.csv    # RE2/RE3 when available
```

## Local Usage

After downloading/extracting RCAEval data outside the repo, run:

```powershell
python scripts/adapt_rcaeval_case.py `
  --case-dir C:\path\to\RCAEval\data\RE2\some_case `
  --output datapack\external\sample-rcaeval-triage-request.json
```

The adapter emits a best-effort `/v1/triage` request. It does not replace the full RCAEval benchmark; it creates a bridge from public RCAEval cases into our contract shape.

## TF1 Subset Validation

For the capstone demo, we do not use the full RCAEval dataset. We use three RCAEval cases per TF1 scenario:

| TF1 scenario | RCAEval cases |
|---|---|
| latency-degradation | `re1ss_carts_delay_1`, `re2ss_catalogue_delay_2`, `re1ob_cartservice_delay_3` |
| critical-service-down | `re2tt_ts-auth-service_loss_1`, `re1ss_user_loss_2`, `re2ss_orders_loss_1` |
| noisy-false-alert | `re1ss_user_cpu_3`, `re1ob_cartservice_mem_1`, `re1tt_ts-route-service_disk_3` |

The subset is stored in:

```text
datapack/external/rcaeval-subsets/
```

Adapted `/v1/triage` requests and validation output are stored in:

```text
datapack/external/adapted/
datapack/external/adapted/rcaeval-subset-triage-results.json
```

CDO-hostable evidence bundles generated from the adapted RCAEval requests are stored in:

```text
datapack/external/evidence-bundles/
```

These bundles are the primary scenario datapacks for CDO handoff. They use RCAEval metrics as the primary evidence. Because the local subset extractor currently keeps only `metrics.json` and `inject_time.txt`, each bundle has a `data_lineage` section that marks logs, traces, deploy events, ownership, and runbooks as TF1 supplemental sample-derived records.

To regenerate them:

```powershell
python scripts/build_rcaeval_evidence_bundles.py
```

To reproduce the subset extraction from the Figshare RCAEval-v2 stream without storing the full archive:

```powershell
python scripts/extract_rcaeval_subsets.py `
  --output-dir datapack\external\rcaeval-subsets
```

Note: the Figshare archive is gzip/tar stream ordered by case. The extractor persists only selected `metrics.json` and `inject_time.txt` files, but may need to read through a large portion of the remote stream before all selected cases appear. To make logs/traces primary RCAEval data too, update `scripts/extract_rcaeval_subsets.py` to include `logs.csv` and `traces.csv` for RE2/RE3 cases, then rerun the adapter and evidence bundle generator.

To adapt one selected case:

```powershell
python scripts/adapt_rcaeval_case.py `
  --case-dir datapack\external\rcaeval-subsets\latency-degradation\re1ss_carts_delay_1 `
  --scenario latency-degradation `
  --output datapack\external\adapted\latency-degradation\re1ss_carts_delay_1.request.json
```

Current subset validation result: 9 adapted requests returned HTTP 200 from `/v1/triage`.

## Why Synthetic Fixtures Still Exist

The synthetic datapack under `datapack/scenarios/` is now treated as demo fixture data only. It is useful for stable API smoke tests and Jira/Slack payload examples. RCAEval is the preferred evidence direction for RCA quality.
