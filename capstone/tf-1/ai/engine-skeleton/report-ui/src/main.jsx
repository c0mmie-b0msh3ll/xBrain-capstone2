import React, { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import { ArrowLeft, Download, ExternalLink, RefreshCw } from "lucide-react";
import "./styles.css";

const API_BASES = [
  import.meta.env.VITE_API_BASE_URL,
  "http://127.0.0.1:8081",
  "http://localhost:8081",
  "http://127.0.0.1:8080",
  "http://localhost:8080",
].filter(Boolean);

async function apiFetch(path) {
  let lastError;
  for (const base of API_BASES) {
    try {
      const response = await fetch(`${base}${path}`);
      if (response.ok) return response;
      lastError = new Error(`API returned ${response.status} from ${base}`);
    } catch (error) {
      lastError = error;
    }
  }
  throw lastError || new Error("No API endpoint configured");
}

function App() {
  const [route, setRoute] = useState(parseRoute());
  useEffect(() => {
    const onHashChange = () => setRoute(parseRoute());
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  return route.incidentId ? (
    <ReportDetail incidentId={route.incidentId} />
  ) : (
    <ReportList />
  );
}

function parseRoute() {
  const match = window.location.hash.match(/^#\/reports\/(.+)$/);
  return { incidentId: match ? decodeURIComponent(match[1]) : null };
}

function ReportList() {
  const [state, setState] = useState({ loading: true, error: "", reports: [] });
  const load = () => {
    setState((current) => ({ ...current, loading: true, error: "" }));
    apiFetch("/v1/reports")
      .then((response) => {
        if (!response.ok) throw new Error(`API returned ${response.status}`);
        return response.json();
      })
      .then((payload) => setState({ loading: false, error: "", reports: payload.reports || [] }))
      .catch((error) => setState({ loading: false, error: error.message, reports: [] }));
  };

  useEffect(load, []);

  return (
    <main className="shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">TF1 AIOps</p>
          <h1>Triage Reports</h1>
        </div>
        <button className="iconButton" onClick={load} aria-label="Refresh reports" title="Refresh reports">
          <RefreshCw size={18} />
        </button>
      </header>

      {state.loading && <StatusLine text="Loading reports..." />}
      {state.error && <StatusLine text={`Unable to load reports: ${state.error}`} />}
      {!state.loading && !state.error && state.reports.length === 0 && (
        <StatusLine text="No triage reports have been written yet." />
      )}

      <section className="reportList" aria-label="Incident reports">
        {state.reports.map((report) => (
          <a className="reportRow" href={`#/reports/${encodeURIComponent(report.incident_id)}`} key={report.incident_id}>
            <div className="rowMain">
              <span className={`severity severity-${report.severity || "unknown"}`}>{report.severity || "unknown"}</span>
              <div className="rowText">
                <h2>{report.title || report.incident_id}</h2>
                <p>{report.service || "unknown service"} · {report.classification || "unclassified"}</p>
              </div>
            </div>
            <div className="rowMeta">
              <strong>{formatConfidence(report.confidence)}</strong>
              <span>{report.status || "unknown"}</span>
              <span>{formatDate(report.created_at)}</span>
            </div>
          </a>
        ))}
      </section>
    </main>
  );
}

function ReportDetail({ incidentId }) {
  const [state, setState] = useState({ loading: true, error: "", report: null });
  useEffect(() => {
    apiFetch(`/v1/reports/${encodeURIComponent(incidentId)}`)
      .then((response) => {
        if (!response.ok) throw new Error(`API returned ${response.status}`);
        return response.json();
      })
      .then((report) => setState({ loading: false, error: "", report }))
      .catch((error) => setState({ loading: false, error: error.message, report: null }));
  }, [incidentId]);

  const report = state.report;
  const triage = report?.triage_response || {};
  const request = report?.request_context || {};
  const alert = request.alert || {};
  const rootCause = triage.suspected_root_cause || {};
  const topology = report?.service_topology || triage.service_topology || {};
  const candidates = report?.rca_candidates || triage.rca_candidates || [];
  const evidence = report?.anomaly_evidence || triage.anomaly_evidence || [];
  const causalHints = report?.causal_hints || triage.causal_hints || [];
  const actions = triage.recommended_actions || [];

  const rawUrl = useMemo(() => `${API_BASES[0]}/v1/reports/${encodeURIComponent(incidentId)}/raw`, [incidentId]);

  if (state.loading) return <main className="shell"><StatusLine text="Loading report..." /></main>;
  if (state.error) return <main className="shell"><StatusLine text={`Unable to load report: ${state.error}`} /></main>;

  return (
    <main className="shell detailShell">
      <header className="topbar">
        <div>
          <a className="backLink" href="#"><ArrowLeft size={16} /> Reports</a>
          <p className="eyebrow">{alert.service || "unknown service"}</p>
          <h1>{alert.title || incidentId}</h1>
        </div>
        <a className="iconButton" href={rawUrl} aria-label="Download raw JSON" title="Download raw JSON">
          <Download size={18} />
        </a>
      </header>

      <section className="summaryGrid" aria-label="Incident summary">
        <Metric label="Status" value={triage.status || "unknown"} />
        <Metric label="Severity" value={triage.severity || alert.severity || "unknown"} tone={triage.severity || alert.severity} />
        <Metric label="Confidence" value={formatConfidence(triage.confidence)} />
        <Metric label="Classification" value={triage.classification || "unclassified"} />
      </section>

      <ReportSection title="Suspected Root Cause">
        <p className="lead">{rootCause.summary || "No suspected root cause available."}</p>
        <EvidenceList items={(rootCause.evidence || []).map((reason) => ({ reason }))} />
      </ReportSection>

      <ReportSection title="Recommended Actions">
        <RecommendedActions actions={actions} />
      </ReportSection>

      <ReportSection title="Ranked RCA Candidates">
        {candidates.length ? candidates.map((candidate) => (
          <div className="candidateRow" key={`${candidate.rank}-${candidate.service}`}>
            <strong>#{candidate.rank} {candidate.service}</strong>
            <span>{formatConfidence(candidate.confidence)} · score {candidate.score}</span>
            <EvidenceList items={(candidate.reasons || []).map((reason) => ({ reason }))} />
          </div>
        )) : <Empty text="No RCA candidates available." />}
      </ReportSection>

      <ReportSection title="Metric, Log, And Deploy Evidence">
        <EvidenceList items={evidence} />
        {request.recent_deploys?.length ? request.recent_deploys.map((deploy) => (
          <pre className="payload" key={`${deploy.service}-${deploy.version}`}>{JSON.stringify(deploy, null, 2)}</pre>
        )) : null}
      </ReportSection>

      <ReportSection title="Service Topology">
        {topology.nodes?.length ? (
          <>
            <div className="nodeLine">{topology.nodes.map((node) => <span key={node}>{node}</span>)}</div>
            <EvidenceList items={(topology.edges || []).map((edge) => ({ reason: `${edge.source} -> ${edge.target} (${edge.evidence})` }))} />
          </>
        ) : <Empty text="Topology is not available for this report." />}
      </ReportSection>

      <ReportSection title="Causal Hints">
        <EvidenceList items={causalHints} />
      </ReportSection>

      <ReportSection title="Investigator Summary">
        <p className="lead">{report.investigation_summary || triage.investigation_summary || "No investigator summary available."}</p>
      </ReportSection>

      <ReportSection title="Slack Context And Jira Payload">
        <div className="payloadGrid">
          <Payload title="Slack Context" value={report.slack_context || slackContextFromTriage(triage)} />
          <Payload title="Jira" value={report.jira_payload || triage.ticket_payload} />
        </div>
      </ReportSection>

      <ReportSection title="Audit Metadata">
        <dl className="auditGrid">
          <div><dt>Incident</dt><dd>{incidentId}</dd></div>
          <div><dt>Audit</dt><dd>{report.audit_id || triage.audit_id}</dd></div>
          <div><dt>Correlation</dt><dd>{request.correlation_id || "unknown"}</dd></div>
          <div><dt>Created</dt><dd>{formatDate(report.created_at)}</dd></div>
          <div><dt>LLM</dt><dd>{llmLabel(report.llm_metadata || triage.llm_metadata)}</dd></div>
        </dl>
        {report.report_url && <a className="inlineLink" href={report.report_url}><ExternalLink size={14} /> Report URL</a>}
      </ReportSection>
    </main>
  );
}

function ReportSection({ title, children }) {
  return <section className="reportSection"><h2>{title}</h2>{children}</section>;
}

function Metric({ label, value, tone }) {
  return <div className={`metric metric-${tone || "default"}`}><span>{label}</span><strong>{value}</strong></div>;
}

function EvidenceList({ items }) {
  if (!items?.length) return <Empty text="Not available." />;
  return <ul className="evidenceList">{items.map((item, index) => <li key={index}>{item.reason || JSON.stringify(item)}</li>)}</ul>;
}

function RecommendedActions({ actions }) {
  if (!actions?.length) return <Empty text="No recommended actions available." />;
  return (
    <div className="actionList">
      {actions.map((action) => (
        <article className="actionRow" key={action.id || `${action.type}-${action.priority}`}>
          <div className="actionHeader">
            <strong>#{action.priority} {action.summary}</strong>
            <div className="actionTags">
              <span className={`risk risk-${action.risk || "unknown"}`}>{action.risk || "unknown"} risk</span>
              {action.requires_human_approval && <span className="approvalTag">approval required</span>}
            </div>
          </div>
          {action.why && <p>{action.why}</p>}
          {action.approval_reason && <p className="muted">{action.approval_reason}</p>}
          <div className="actionMeta">
            <span>{action.type}</span>
            {action.id && <span>{action.id}</span>}
            {action.evidence_refs?.length ? <span>Evidence: {action.evidence_refs.join(", ")}</span> : null}
            {action.runbook_ref && <a className="inlineLink compactLink" href={action.runbook_ref}><ExternalLink size={14} /> Runbook</a>}
          </div>
        </article>
      ))}
    </div>
  );
}

function Payload({ title, value }) {
  return <div><h3>{title}</h3><pre className="payload">{JSON.stringify(value || {}, null, 2)}</pre></div>;
}

function slackContextFromTriage(triage) {
  return {
    incident_id: triage.incident_id,
    classification: triage.classification,
    severity: triage.severity,
    confidence: triage.confidence,
    status: triage.status,
    suggested_assignee_account_id: triage.suggested_assignee_account_id,
    suggestion_reason: triage.suggestion_reason,
  };
}

function Empty({ text }) {
  return <p className="empty">{text}</p>;
}

function StatusLine({ text }) {
  return <div className="statusLine">{text}</div>;
}

function formatConfidence(value) {
  return typeof value === "number" ? `${Math.round(value * 100)}%` : "n/a";
}

function formatDate(value) {
  if (!value) return "unknown time";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function llmLabel(metadata) {
  if (!metadata?.enabled) return "deterministic";
  if (metadata.error) return `bedrock error: ${metadata.error}`;
  return `${metadata.provider || "bedrock"} ${metadata.model_id || ""}`.trim();
}

createRoot(document.getElementById("root")).render(<App />);
