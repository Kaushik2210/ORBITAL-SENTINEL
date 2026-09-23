"use client";

import { use, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { api, ApiError } from "@/lib/api";
import { hasRole, useAuth } from "@/lib/auth-store";
import type { EvidenceOut, InvestigateOut } from "@/lib/types";
import { SeverityBadge, SyntheticBadge, VerdictBadge } from "@/components/badges";
import { PosteriorBars } from "@/components/posterior-bars";

export default function IncidentDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { token, role } = useAuth();
  const [evidence, setEvidence] = useState<EvidenceOut | null>(null);
  const [report, setReport] = useState<InvestigateOut | null>(null);
  const [investigating, setInvestigating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const canInvestigate = hasRole(role, "analyst");

  const load = useCallback(() => {
    api
      .evidence(id, token)
      .then(setEvidence)
      .catch((e) => setError(e instanceof Error ? e.message : "failed to load evidence"));
    api
      .report(id, token)
      .then(setReport)
      .catch(() => setReport(null)); // 404 until someone has run the agent — not an error
  }, [id, token]);

  useEffect(() => load(), [load]);

  async function investigate() {
    if (!token) return;
    setInvestigating(true);
    setError(null);
    try {
      const r = await api.investigate(id, token);
      setReport(r);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "the agent run failed");
    } finally {
      setInvestigating(false);
    }
  }

  if (error && !evidence) return <p className="text-sm text-danger">{error}</p>;
  if (!evidence) return <p className="text-sm text-muted">Loading…</p>;

  const inc = evidence.incident;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="mono text-lg font-semibold">{inc.id}</h1>
        <VerdictBadge verdict={inc.verdict} />
        <SeverityBadge severity={inc.severity} />
        <SyntheticBadge synthetic={inc.synthetic} />
        <Link href={`/sessions/${inc.session_id}`} className="ml-auto text-xs text-accent hover:underline">
          view session →
        </Link>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <section className="hud-panel space-y-3 rounded-lg border p-4">
          <h2 className="text-sm font-semibold tracking-wide text-muted uppercase">Posterior</h2>
          <PosteriorBars posterior={inc.posterior} />
          <p className="mono text-xs text-muted">model {inc.model_version}</p>
        </section>

        <section className="hud-panel space-y-3 rounded-lg border p-4">
          <h2 className="text-sm font-semibold tracking-wide text-muted uppercase">Evidence</h2>
          {evidence.evidence_for.length === 0 && evidence.evidence_against.length === 0 ? (
            <p className="text-sm text-muted">No feature crossed the attribution threshold.</p>
          ) : (
            <div className="space-y-1 text-sm">
              {evidence.evidence_for.map((c) => (
                <div key={c.feature} className="flex justify-between">
                  <span className="mono text-nominal">+ {c.feature}</span>
                  <span className="mono text-muted">{c.contribution.toFixed(3)}</span>
                </div>
              ))}
              {evidence.evidence_against.map((c) => (
                <div key={c.feature} className="flex justify-between">
                  <span className="mono text-danger">− {c.feature}</span>
                  <span className="mono text-muted">{c.contribution.toFixed(3)}</span>
                </div>
              ))}
            </div>
          )}
          {evidence.runner_up && (
            <p className="text-xs text-muted">
              runner-up: <span className="text-accent">{evidence.runner_up}</span>
            </p>
          )}
        </section>
      </div>

      <section className="hud-panel space-y-2 rounded-lg border p-4">
        <h2 className="text-sm font-semibold tracking-wide text-muted uppercase">
          Detector outputs ({evidence.detector_outputs.length})
        </h2>
        <div className="max-h-64 space-y-1 overflow-y-auto text-xs">
          {evidence.detector_outputs.map((o, i) => (
            <div key={i} className="flex flex-wrap gap-2 border-b border-border py-1">
              <span className="mono w-28 shrink-0 text-accent">{o.detector}</span>
              <span className="mono w-8 shrink-0 text-muted">{o.layer}</span>
              <span className="mono w-16 shrink-0 text-muted">{o.score.toFixed(2)}</span>
              <span className="text-muted">{o.explanation}</span>
            </div>
          ))}
        </div>
      </section>

      <section className="hud-panel space-y-3 rounded-lg border p-4">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold tracking-wide text-muted uppercase">
            AI investigation
          </h2>
          <button
            onClick={investigate}
            disabled={!canInvestigate || investigating}
            data-testid="investigate-button"
            className="rounded bg-accent px-3 py-1 text-xs font-medium text-background transition-opacity hover:opacity-90 disabled:opacity-40"
          >
            {investigating ? "investigating…" : report ? "re-run" : "investigate"}
          </button>
        </div>
        {!canInvestigate && !report && (
          <p className="text-xs text-muted">Sign in with an analyst account to run the agent.</p>
        )}
        {error && <p className="text-sm text-danger">{error}</p>}
        {report && (
          <div className="space-y-3">
            <div className="flex items-center gap-2 text-xs text-muted">
              <span
                data-testid="report-mode"
                className={`mono rounded border px-1.5 py-0.5 uppercase ${
                  report.mode === "llm"
                    ? "border-accent/40 text-accent"
                    : "border-border text-muted"
                }`}
              >
                {report.mode === "llm" ? `live: ${report.model}` : "offline fallback"}
              </span>
              <span>{new Date(report.generated_at).toLocaleString()}</span>
            </div>
            <p className="text-sm">{report.report.summary}</p>
            <div>
              <h3 className="text-xs font-semibold text-muted uppercase">Key evidence</h3>
              <ul className="list-inside list-disc text-sm text-muted">
                {report.report.key_evidence.map((e, i) => (
                  <li key={i}>{e}</li>
                ))}
              </ul>
            </div>
            <div>
              <h3 className="text-xs font-semibold text-muted uppercase">Recommended action</h3>
              <p className="text-sm">{report.report.recommended_action}</p>
            </div>
            {report.report.caveats.length > 0 && (
              <div>
                <h3 className="text-xs font-semibold text-muted uppercase">Caveats</h3>
                <ul className="list-inside list-disc text-sm text-warning">
                  {report.report.caveats.map((c, i) => (
                    <li key={i}>{c}</li>
                  ))}
                </ul>
              </div>
            )}
            <details className="text-xs text-muted">
              <summary className="cursor-pointer select-none text-accent">
                tool-use trace ({report.trace.length} events)
              </summary>
              <pre className="mono mt-2 max-h-64 overflow-auto rounded bg-surface-raised p-2 whitespace-pre-wrap">
                {JSON.stringify(report.trace, null, 2)}
              </pre>
            </details>
          </div>
        )}
      </section>
    </div>
  );
}
