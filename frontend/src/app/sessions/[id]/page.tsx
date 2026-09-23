"use client";

import { use, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { api, ApiError } from "@/lib/api";
import { hasRole, useAuth } from "@/lib/auth-store";
import { useSessionStream } from "@/lib/use-session-stream";
import type { GroundTruthOut, SessionOut } from "@/lib/types";
import { SyntheticBadge } from "@/components/badges";
import { TelemetryChart, type ChartSeries } from "@/components/telemetry-chart";

const PALETTE = ["#22d3ee", "#fbbf24", "#34d399", "#f87171", "#60a5fa", "#c084fc"];
const MAX_CHARTED_CHANNELS = 4;

export default function SessionDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { token, role } = useAuth();
  const canControl = hasRole(role, "analyst");
  const [session, setSession] = useState<SessionOut | null>(null);
  const [truth, setTruth] = useState<GroundTruthOut | null>(null);
  const [revealed, setRevealed] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const stream = useSessionStream(id, token);

  useEffect(() => {
    const poll = () =>
      api
        .session(id, token)
        .then(setSession)
        .catch((e) => setError(e instanceof Error ? e.message : "failed to load the session"));
    poll();
    const t = setInterval(poll, 4000);
    return () => clearInterval(t);
  }, [id, token]);

  const finished = session?.status === "completed" || session?.status === "failed" || stream.done;

  useEffect(() => {
    if (!finished || session?.kind !== "scenario" || truth) return;
    api.groundTruth(id, token).then(setTruth).catch(() => undefined);
  }, [finished, id, session?.kind, token, truth]);

  const chartedChannels = useMemo(
    () => Object.keys(stream.values).slice(0, MAX_CHARTED_CHANNELS),
    [stream.values]
  );
  const series: ChartSeries[] = chartedChannels.map((ch, i) => ({
    label: ch,
    color: PALETTE[i % PALETTE.length],
    values: stream.values[ch] ?? [],
  }));

  async function control(action: "pause" | "play") {
    if (!token) return;
    try {
      setSession(await api.controlSession(id, action, token));
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "control failed");
    }
  }

  if (error && !session) return <p className="text-sm text-danger">{error}</p>;
  if (!session) return <p className="text-sm text-muted">Loading…</p>;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="mono text-lg font-semibold">{session.id}</h1>
        <span
          data-testid="session-status"
          className="mono rounded border border-border bg-surface-raised px-2 py-0.5 text-xs uppercase"
        >
          {stream.status ?? session.status}
        </span>
        <SyntheticBadge synthetic={session.synthetic} />
        <span
          className={`h-2 w-2 rounded-full ${stream.connected ? "bg-nominal" : "bg-border"}`}
          title={stream.connected ? "live" : "not connected"}
        />
        {canControl && !finished && (
          <div className="ml-auto flex gap-2">
            <button
              onClick={() => control("pause")}
              className="rounded border border-border px-2 py-1 text-xs hover:text-foreground"
            >
              pause
            </button>
            <button
              onClick={() => control("play")}
              className="rounded border border-border px-2 py-1 text-xs hover:text-foreground"
            >
              play
            </button>
          </div>
        )}
      </div>

      <div className="hud-panel rounded-lg border p-2">
        <div className="h-2 overflow-hidden rounded-full bg-surface-raised">
          <div
            className="h-full rounded-full bg-accent transition-all"
            style={{ width: `${Math.min(100, (session.position / Math.max(1, session.steps)) * 100)}%` }}
          />
        </div>
        <p className="mono mt-1 text-right text-xs text-muted">
          {session.position} / {session.steps} steps · {session.incidents} incident(s)
        </p>
      </div>

      <section className="hud-panel rounded-lg border p-4">
        <h2 className="mb-2 text-sm font-semibold tracking-wide text-muted uppercase">
          Live telemetry
        </h2>
        {series.length === 0 ? (
          <p className="text-sm text-muted">Waiting for samples…</p>
        ) : (
          <TelemetryChart x={stream.steps} series={series} />
        )}
      </section>

      <section className="hud-panel rounded-lg border p-4">
        <h2 className="mb-2 text-sm font-semibold tracking-wide text-muted uppercase">
          Incidents raised in this session ({stream.incidents.length})
        </h2>
        {stream.incidents.length === 0 ? (
          <p className="text-sm text-muted">None yet.</p>
        ) : (
          <ul className="space-y-1 text-sm">
            {stream.incidents.map((inc, i) => {
              const iid = String((inc as Record<string, unknown>).id ?? "");
              return (
                <li key={i}>
                  <Link
                    href={`/incidents/${iid}`}
                    data-testid="incident-link"
                    className="mono text-accent hover:underline"
                  >
                    {iid}
                  </Link>
                </li>
              );
            })}
          </ul>
        )}
      </section>

      {finished && session.kind === "scenario" && (
        <section className="hud-panel rounded-lg border p-4">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold tracking-wide text-muted uppercase">
              Ground truth
            </h2>
            {!revealed && (
              <button
                onClick={() => setRevealed(true)}
                className="rounded bg-accent px-3 py-1 text-xs font-medium text-background hover:opacity-90"
              >
                reveal
              </button>
            )}
          </div>
          {!revealed && (
            <p className="mt-1 text-sm text-muted">
              What actually happened is hidden until you ask — compare it against what the
              detectors and the incident above concluded.
            </p>
          )}
          {revealed && truth && (
            <dl className="mt-2 grid grid-cols-2 gap-2 text-sm">
              <dt className="text-muted">class</dt>
              <dd className="mono text-accent">{truth.class}</dd>
              <dt className="text-muted">subtype</dt>
              <dd className="mono">{truth.subtype}</dd>
              <dt className="text-muted">channels</dt>
              <dd className="mono">{truth.channels.join(", ") || "—"}</dd>
              {truth.attack.length > 0 && (
                <>
                  <dt className="text-muted">ATT&amp;CK</dt>
                  <dd className="mono">{truth.attack.join(", ")}</dd>
                </>
              )}
              <dt className="text-muted">space weather</dt>
              <dd className="mono">{truth.weather_context}</dd>
            </dl>
          )}
        </section>
      )}
    </div>
  );
}
