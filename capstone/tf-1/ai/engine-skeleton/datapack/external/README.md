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

For case-by-case incident meaning, timestamps, expected AI output, and CDO hosting guidance, see `../../../docs/10_datapack_insights_for_cdo.md`.

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

These bundles are the primary scenario datapacks for CDO handoff. They use selected RCAEval case metrics as the primary scenario evidence. The checked-in adapted requests also include bounded log/trace snippets from exact selected RCAEval case files where those files exist. Each bundle has a `data_lineage` section that separates selected-case RCAEval telemetry from TF1 supplemental operational records such as deploy events, ownership, and runbooks.

To regenerate them from selected case files:

```powershell
$subsetRoot = "datapack\external\rcaeval-subsets"
$adaptRoot = "datapack\external\adapted"
foreach ($scenarioDir in Get-ChildItem -LiteralPath $subsetRoot -Directory) {
  foreach ($caseDir in Get-ChildItem -LiteralPath $scenarioDir.FullName -Directory) {
    $outDir = Join-Path $adaptRoot $scenarioDir.Name
    New-Item -ItemType Directory -Force $outDir | Out-Null
    python scripts\adapt_rcaeval_case.py `
      --case-dir $caseDir.FullName `
      --scenario $scenarioDir.Name `
      --output (Join-Path $outDir ($caseDir.Name + ".request.json"))
  }
}
python scripts/build_rcaeval_evidence_bundles.py
```

Raw `logs.csv` and `traces.csv` files are ignored under `rcaeval-subsets/` because some upstream selected files exceed normal Git file size limits. Keep full raw telemetry in `.cache/`, S3, MinIO, or the CDO evidence store, then regenerate bounded adapted requests/bundles from that local or hosted source.

Current selected-case evidence availability:

```text
re2ss_orders_loss_1: metrics + logs
re2ss_catalogue_delay_2: metrics + logs
re2tt_ts-auth-service_loss_1: metrics + logs + traces
RE1 selected cases: metrics only
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

After this selected-case utility download succeeds, rerun the adapter for each selected case and rebuild the bundles. The adapter includes logs/traces only when the selected case has its own `logs.csv` or `traces.csv`; it does not synthesize fallback telemetry.

To adapt one selected case:

```powershell
python scripts/adapt_rcaeval_case.py `
  --case-dir datapack\external\rcaeval-subsets\latency-degradation\re1ss_carts_delay_1 `
  --scenario latency-degradation `
  --output datapack\external\adapted\latency-degradation\re1ss_carts_delay_1.request.json
```

Current subset validation result: 9 adapted requests returned HTTP 200 from `/v1/triage`.

## Why Synthetic Fixtures Still Exist

The synthetic datapack under `datapack/scenarios/` is now treated as demo fixture data only. It is useful for stable API smoke tests, Jira ticket fields, and Slack-renderable raw response examples. RCAEval is the preferred evidence direction for RCA quality.
