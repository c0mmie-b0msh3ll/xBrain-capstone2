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

## Why Synthetic Fixtures Still Exist

The synthetic datapack under `datapack/scenarios/` is now treated as demo fixture data only. It is useful for stable API smoke tests and Jira/Slack payload examples. RCAEval is the preferred evidence direction for RCA quality.
