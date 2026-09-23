import type { Verdict } from "@/lib/types";

const COLOR: Partial<Record<Verdict, string>> = {
  nominal: "bg-nominal",
  mechanical_failure: "bg-warning",
  environmental: "bg-info",
  sensor_malfunction: "bg-warning",
  cyberattack: "bg-critical",
  needs_human: "bg-muted",
};

export function PosteriorBars({ posterior }: { posterior: Record<string, number> }) {
  const rows = Object.entries(posterior).sort((a, b) => b[1] - a[1]);
  return (
    <div className="space-y-2">
      {rows.map(([klass, p]) => (
        <div key={klass} className="flex items-center gap-3">
          <span className="mono w-40 shrink-0 truncate text-xs text-muted">{klass}</span>
          <div className="h-2 flex-1 overflow-hidden rounded-full bg-surface-raised">
            <div
              className={`h-full rounded-full ${COLOR[klass as Verdict] ?? "bg-accent"}`}
              style={{ width: `${Math.max(2, p * 100)}%` }}
            />
          </div>
          <span className="mono w-12 shrink-0 text-right text-xs">{(p * 100).toFixed(1)}%</span>
        </div>
      ))}
    </div>
  );
}
