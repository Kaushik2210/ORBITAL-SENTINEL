"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth-store";
import type { IncidentOut, Ready } from "@/lib/types";
import { VerdictBadge, SeverityBadge, SyntheticBadge } from "@/components/badges";

export default function DashboardPage() {
  const token = useAuth((s) => s.token);
  const [ready, setReady] = useState<Ready | null>(null);
  const [incidents, setIncidents] = useState<IncidentOut[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.ready().then(setReady).catch(() => setReady(null));
  }, []);

  useEffect(() => {
    api
      .incidents(token, { limit: 8 })
      .then((p) => setIncidents(p.items))
      .catch((e) => setError(e instanceof Error ? e.message : "failed to load incidents"));
  }, [token]);

  return (
    <div className="space-y-6">
      <section className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <StatTile label="Detection engine" value={ready?.detection_engine ? "online" : "…"} ok={ready?.detection_engine} />
        <StatTile
          label="Attribution model"
          value={ready?.attribution_model ?? "…"}
          ok={!!ready?.attribution_model}
        />
        <StatTile label="L2 forecasters" value={ready ? `${ready.l2_models} onnx` : "…"} ok={(ready?.l2_models ?? 0) > 0} />
        <StatTile label="Real SMAP/MSL data" value={ready?.real_data ? "available" : "not downloaded"} ok={ready?.real_data} />
      </section>

      <section className="hud-panel rounded-lg border p-4">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-semibold tracking-wide text-muted uppercase">Recent incidents</h2>
          <Link href="/incidents" className="text-xs text-accent hover:underline">
            view all →
          </Link>
        </div>
        {error && <p className="text-sm text-danger">{error}</p>}
        {incidents === null && !error && <p className="text-sm text-muted">Loading…</p>}
        {incidents?.length === 0 && <p className="text-sm text-muted">No incidents yet — launch a scenario.</p>}
        <div className="divide-y divide-border">
          {incidents?.map((inc) => (
            <Link
              key={inc.id}
              href={`/incidents/${inc.id}`}
              className="flex flex-wrap items-center gap-3 py-2 text-sm transition-colors hover:bg-surface-raised"
            >
              <span className="mono w-40 shrink-0 truncate text-muted">{inc.id}</span>
              <VerdictBadge verdict={inc.verdict} />
              <SeverityBadge severity={inc.severity} />
              <SyntheticBadge synthetic={inc.synthetic} />
              <span className="mono ml-auto text-xs text-muted">
                confidence {(inc.confidence * 100).toFixed(0)}%
              </span>
            </Link>
          ))}
        </div>
      </section>

      <section className="hud-panel rounded-lg border p-4">
        <h2 className="mb-2 text-sm font-semibold tracking-wide text-muted uppercase">Get started</h2>
        <p className="text-sm text-muted">
          Launch an attack or fault scenario from the{" "}
          <Link href="/scenarios" className="text-accent hover:underline">
            scenario library
          </Link>
          , watch telemetry and detections stream in live, then open the incident it raises to see
          the exact evidence and ask the AI investigation agent for a report.
        </p>
      </section>
    </div>
  );
}

function StatTile({ label, value, ok }: { label: string; value: string; ok?: boolean }) {
  return (
    <div className="hud-panel rounded-lg border p-3">
      <div className="text-[11px] tracking-wide text-muted uppercase">{label}</div>
      <div className={`mono mt-1 truncate text-sm ${ok ? "text-nominal" : "text-muted"}`}>{value}</div>
    </div>
  );
}
