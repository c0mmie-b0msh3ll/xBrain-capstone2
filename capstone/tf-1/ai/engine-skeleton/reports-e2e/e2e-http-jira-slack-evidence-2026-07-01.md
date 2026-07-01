# E2E HTTP + Jira/Slack Evidence - 2026-07-01

Scope: one end-to-end demo flow run through the real HTTP API process, not FastAPI `TestClient`.

## Flow

```text
aiops_worker offline scenario
-> HTTP POST http://127.0.0.1:18080/v1/triage
-> tf1-ai-triage-engine
-> local audit/report JSON
-> Slack webhook publisher dry-run payload
-> Triage-Hub SQS dry-run payload
-> live Jira evidence ticket via Atlassian API
```

## Result

| Item | Value |
| --- | --- |
| Incident ID | `inc-payment-api-1782888580` |
| Audit ID | `audit-837ef7ca3ca2` |
| Classification | `latency_degradation` |
| Status | `DIAGNOSED` |
| Confidence | `0.82` |
| Service | `payment-api` |
| Owner team | `payments-platform` |
| Slack channel in payload | `#oncall-payments` |
| Jira evidence ticket | `A0X-36` |
| Jira URL | `https://aio1-xbrain.atlassian.net/browse/A0X-36` |

## Evidence Files

- Worker stdout/evidence: `reports-e2e/e2e-http-worker-output.jsonl`
- Generated report: `reports-e2e/e2e-http/inc-payment-api-1782888580.json`
- API logs: `reports-e2e/uvicorn-e2e.out.log`, `reports-e2e/uvicorn-e2e.err.log`

## Slack / Jira Boundary

- Jira mutation evidence is live: issue `A0X-36` was created through Atlassian API and assigned to the current user.
- Slack publisher emitted a valid dry-run payload because this local AI session has no `SLACK_WEBHOOK_URL`.
- Triage-Hub SQS publisher emitted a valid dry-run payload because this local AI session has no `TRIAGE_HUB_NOTIFY_SQS_URL`.
- This matches the agreed boundary: AI owns triage response and normalized handoff payload; CDO owns live Slack/Jira dispatcher mutation in the platform integration layer.

## Smoke Commands

```powershell
$env:SERVICE_AUTH_TOKEN='demo-e2e-token'
python -m uvicorn app.main:app --host 127.0.0.1 --port 18080

$env:TRIAGE_URL='http://127.0.0.1:18080/v1/triage'
python -m app.aiops_worker `
  --offline-scenario `
  --scenario latency-degradation `
  --triage-url http://127.0.0.1:18080/v1/triage `
  --report-dir reports-e2e/e2e-http `
  --report-base-url http://localhost:5173/#/reports `
  --dry-run-slack `
  --dry-run-triage-hub-sqs
```

## Demo Talking Point

Use the generated Slack/SQS payload as the AI-to-CDO handoff proof, and use Jira `A0X-36` as proof that the same incident output can be turned into a real ticket. If CDO provides live `SLACK_WEBHOOK_URL` or `TRIAGE_HUB_NOTIFY_SQS_URL`, rerun the same worker command without the relevant dry-run flag to publish live Slack/SQS.
