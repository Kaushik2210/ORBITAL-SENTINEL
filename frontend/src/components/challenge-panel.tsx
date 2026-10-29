"use client";

import { useState } from "react";
import {
  accuracy,
  GUESS_OPTIONS,
  loadTally,
  record,
  saveTally,
  type Tally,
} from "@/lib/challenge";

function pct(n: number | null): string {
  return n === null ? "—" : `${Math.round(n * 100)}%`;
}

/** "Attack or Accident?": commit to a guess from the raw telemetry before the answer is shown. */
export function ChallengePanel({
  sessionId,
  truth,
  platformVerdict,
}: {
  sessionId: string;
  truth: string;
  platformVerdict: string | null;
}) {
  const [choice, setChoice] = useState<string>("");
  const [locked, setLocked] = useState<string | null>(null);
  // Lazy init, not an effect: this panel only mounts after client-side data has loaded, so there is
  // no server render to mismatch, and it avoids a synchronous setState-in-effect.
  const [tally, setTally] = useState<Tally>(() =>
    loadTally(typeof window === "undefined" ? undefined : window.localStorage)
  );

  function lockIn() {
    if (!choice) return;
    setLocked(choice);
    const next = record(tally, sessionId, choice, truth, platformVerdict);
    setTally(next);
    saveTally(window.localStorage, next);
  }

  const correct = locked !== null && locked === truth;
  const label = (v: string) => GUESS_OPTIONS.find((o) => o.value === v)?.label ?? v.replace(/_/g, " ");

  return (
    <section className="hud-panel rounded-lg border p-4" aria-labelledby="challenge-title">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="challenge-title" className="text-sm font-semibold tracking-wide text-muted uppercase">
          Attack or accident?
        </h2>
        <p className="mono text-xs text-muted" aria-live="polite">
          you {pct(accuracy(tally.correct, tally.played))} · platform{" "}
          {pct(accuracy(tally.platformCorrect, tally.played))} · {tally.played} played
        </p>
      </div>

      {locked === null ? (
        <fieldset className="mt-3 space-y-2">
          <legend className="text-sm text-muted">
            From the telemetry above, what was going on? Commit to a guess to see the answer.
          </legend>
          <div className="flex flex-wrap gap-2">
            {GUESS_OPTIONS.map((o) => (
              <label
                key={o.value}
                className={`challenge-option flex min-h-11 cursor-pointer items-center rounded border px-3 text-sm ${
                  choice === o.value ? "border-accent bg-accent/10 text-accent" : "border-border"
                }`}
              >
                <input
                  type="radio"
                  name="guess"
                  value={o.value}
                  checked={choice === o.value}
                  onChange={() => setChoice(o.value)}
                  className="sr-only"
                />
                {o.label}
              </label>
            ))}
          </div>
          <button
            onClick={lockIn}
            disabled={!choice}
            className="min-h-11 rounded bg-accent px-4 text-sm font-medium text-background transition-opacity hover:opacity-90 disabled:opacity-40"
          >
            Lock in guess
          </button>
        </fieldset>
      ) : (
        <div className="mt-3 space-y-1 text-sm" role="status">
          <p className={correct ? "text-nominal" : "text-warning"}>
            {correct ? "Correct." : "Not this time."} It was{" "}
            <strong className="mono">{label(truth)}</strong>; you said {label(locked)}.
          </p>
          <p className="text-muted">
            The platform said{" "}
            <strong className="mono">{platformVerdict ? label(platformVerdict) : "needs human"}</strong>
            {platformVerdict === truth ? " — also right." : platformVerdict ? " — a miss." : "."}
          </p>
        </div>
      )}
    </section>
  );
}
