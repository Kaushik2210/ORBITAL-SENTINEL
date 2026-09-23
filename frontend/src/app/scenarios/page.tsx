"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api, ApiError } from "@/lib/api";
import { hasRole, useAuth } from "@/lib/auth-store";
import type { ScenarioOut } from "@/lib/types";

export default function ScenariosPage() {
  const router = useRouter();
  const { token, role } = useAuth();
  const [scenarios, setScenarios] = useState<ScenarioOut[] | null>(null);
  const [launching, setLaunching] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const canLaunch = hasRole(role, "analyst");

  useEffect(() => {
    // Reveal is honest here: it only shows the narrative/class for browsing, never the live
    // ground truth of an in-progress session (the API withholds that separately, see API.md).
    api
      .scenarios(token, true)
      .then((rows) => setScenarios(rows.sort((a, b) => (a.id < b.id ? -1 : 1))))
      .catch((e) => setError(e instanceof Error ? e.message : "failed to load scenarios"));
  }, [token]);

  async function launch(scenarioId: string) {
    if (!token) return;
    setLaunching(scenarioId);
    setError(null);
    try {
      const session = await api.createSession({ scenario_id: scenarioId, speed: 0 }, token);
      router.push(`/sessions/${session.id}`);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "could not start the session");
      setLaunching(null);
    }
  }

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-lg font-semibold">Scenario library</h1>
        <p className="text-sm text-muted">
          33 seeded scenarios: 7 attack families, sensor faults, and real battery/bearing
          degradation trajectories. Every attack is synthetic — see{" "}
          <a
            href="https://github.com/Kaushik2210/ORBITAL-SENTINEL/blob/main/SECURITY.md"
            className="text-accent hover:underline"
          >
            SECURITY.md
          </a>
          .
        </p>
      </div>
      {!canLaunch && (
        <p className="hud-panel rounded-lg border p-3 text-sm text-muted">
          {token
            ? "Your account is view-only — launching a session needs the analyst role."
            : "Sign in with an analyst or admin account to launch a scenario."}
        </p>
      )}
      {error && <p className="text-sm text-danger">{error}</p>}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {scenarios?.map((s) => (
          <div key={s.id} className="hud-panel flex flex-col gap-2 rounded-lg border p-4">
            <div className="flex items-center justify-between">
              <span className="mono text-xs text-accent">{s.class}</span>
              {s.hard && (
                <span className="rounded border border-warning/40 bg-warning/10 px-1.5 py-0.5 text-[10px] text-warning uppercase">
                  hard
                </span>
              )}
            </div>
            <h3 className="text-sm font-medium">{s.title ?? s.id}</h3>
            <p className="line-clamp-3 text-xs text-muted">{s.narrative}</p>
            <div className="mt-auto flex items-center justify-between pt-2">
              <span className="mono text-[11px] text-muted">{s.steps} steps</span>
              <button
                onClick={() => launch(s.id)}
                disabled={!canLaunch || launching !== null}
                data-testid={`launch-${s.id}`}
                className="rounded bg-accent px-3 py-1 text-xs font-medium text-background transition-opacity hover:opacity-90 disabled:opacity-40"
              >
                {launching === s.id ? "starting…" : "launch"}
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
