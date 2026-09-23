"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth-store";
import type { IncidentOut, Severity, Verdict } from "@/lib/types";
import { SeverityBadge, SyntheticBadge, VerdictBadge } from "@/components/badges";

const VERDICTS: Verdict[] = [
  "cyberattack",
  "mechanical_failure",
  "environmental",
  "sensor_malfunction",
  "nominal",
  "needs_human",
];
const SEVERITIES: Severity[] = ["critical", "high", "medium", "low", "info"];

export default function IncidentsPage() {
  const token = useAuth((s) => s.token);
  const [verdict, setVerdict] = useState("");
  const [severity, setSeverity] = useState("");
  const [items, setItems] = useState<IncidentOut[] | null>(null);
  const [cursor, setCursor] = useState<string | undefined>(undefined);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .incidents(token, {
        verdict: verdict || undefined,
        severity: severity || undefined,
        cursor,
        limit: 20,
      })
      .then((p) => {
        if (cancelled) return;
        setItems(p.items);
        setNextCursor(p.next_cursor);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : "failed to load incidents");
      });
    return () => {
      cancelled = true;
    };
  }, [token, verdict, severity, cursor]);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-lg font-semibold">Incidents</h1>
        <select
          value={verdict}
          onChange={(e) => {
            setCursor(undefined);
            setVerdict(e.target.value);
          }}
          className="mono ml-auto rounded border border-border bg-surface-raised px-2 py-1 text-xs"
        >
          <option value="">all verdicts</option>
          {VERDICTS.map((v) => (
            <option key={v} value={v}>
              {v}
            </option>
          ))}
        </select>
        <select
          value={severity}
          onChange={(e) => {
            setCursor(undefined);
            setSeverity(e.target.value);
          }}
          className="mono rounded border border-border bg-surface-raised px-2 py-1 text-xs"
        >
          <option value="">all severities</option>
          {SEVERITIES.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
      </div>

      {error && <p className="text-sm text-danger">{error}</p>}
      <div className="hud-panel divide-y divide-border rounded-lg border">
        {items === null && <p className="p-4 text-sm text-muted">Loading…</p>}
        {items?.length === 0 && <p className="p-4 text-sm text-muted">No incidents match.</p>}
        {items?.map((inc) => (
          <Link
            key={inc.id}
            href={`/incidents/${inc.id}`}
            className="flex flex-wrap items-center gap-3 p-3 text-sm transition-colors hover:bg-surface-raised"
          >
            <span className="mono w-40 shrink-0 truncate text-muted">{inc.id}</span>
            <VerdictBadge verdict={inc.verdict} />
            <SeverityBadge severity={inc.severity} />
            <SyntheticBadge synthetic={inc.synthetic} />
            <span className="text-xs text-muted">{inc.affected_channels.join(", ")}</span>
            <span className="mono ml-auto text-xs text-muted">
              {new Date(inc.opened_at).toLocaleString()}
            </span>
          </Link>
        ))}
      </div>
      <div className="flex justify-end gap-2">
        {nextCursor && (
          <button
            onClick={() => setCursor(nextCursor)}
            className="rounded border border-border px-3 py-1 text-xs text-muted hover:text-foreground"
          >
            next page →
          </button>
        )}
      </div>
    </div>
  );
}
