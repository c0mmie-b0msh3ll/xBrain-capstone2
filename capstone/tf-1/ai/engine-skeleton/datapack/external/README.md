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

These bundles are the primary scenario datapacks for CDO handoff. They use RCAEval telemetry as the primary evidence. RE2/RE3 `logs.csv` and `traces.csv` are adapted into the bundles when available from the official RCAEval utility download. Each bundle has a `data_lineage` section that marks operational records RCAEval does not provide, such as deploy events, ownership, and runbooks, as TF1 supplemental records.

To regenerate them:

```powershell
python scripts/build_rcaeval_evidence_bundles.py
```

To reproduce the subset extraction from the Figshare RCAEval-v2 stream without storing the full archive:

```powershell
python scripts/extract_rcaeval_subsets.py `
  --output-dir datapack\external\rcaeval-subsets
```

Note: the Figshare archive is gzip/tar stream ordered by case. Prefer the official RCAEval utility download path below when possible because it downloads dataset zips by suite/system instead of streaming the full Figshare archive.

To download with the official RCAEval utility, run it outside the checked-in datapack directory:

```powershell
git clone https://github.com/phamquiluan/RCAEval E:\xBrain-capstone2\.cache\rcaeval\RCAEval
$env:PYTHONPATH = "E:\xBrain-capstone2\.cache\rcaeval\RCAEval;$env:PYTHONPATH"

@'
from RCAEval.utility import (
    download_re1ob_dataset,
    download_re1ss_dataset,
    download_re1tt_dataset,
    download_re2ss_dataset,
    download_re2tt_dataset,
)

root = r"E:\xBrain-capstone2\.cache\rcaeval\data"
download_re1ob_dataset(local_path=fr"{root}\RE1")
download_re1ss_dataset(local_path=fr"{root}\RE1")
download_re1tt_dataset(local_path=fr"{root}\RE1")
download_re2ss_dataset(local_path=fr"{root}\RE2")
download_re2tt_dataset(local_path=fr"{root}\RE2")
'@ | python -
```

Then copy only the selected TF1 cases into the repo datapack:

```powershell
python scripts/extract_selected_rcaeval_cases.py `
  --data-root E:\xBrain-capstone2\.cache\rcaeval\data `
  --output-dir datapack\external\rcaeval-subsets
```

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
