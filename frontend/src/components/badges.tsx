import type { Severity, Verdict } from "@/lib/types";

const VERDICT_STYLE: Record<Verdict, string> = {
  nominal: "text-nominal border-nominal/40 bg-nominal/10",
  mechanical_failure: "text-warning border-warning/40 bg-warning/10",
  environmental: "text-info border-info/40 bg-info/10",
  sensor_malfunction: "text-warning border-warning/40 bg-warning/10",
  cyberattack: "text-critical border-critical/40 bg-critical/10",
  needs_human: "text-muted border-border bg-surface-raised",
};

const VERDICT_LABEL: Record<Verdict, string> = {
  nominal: "Nominal",
  mechanical_failure: "Mechanical failure",
  environmental: "Environmental",
  sensor_malfunction: "Sensor malfunction",
  cyberattack: "Cyberattack",
  needs_human: "Needs human",
};

export function VerdictBadge({ verdict }: { verdict: Verdict }) {
  return (
    <span
      className={`mono inline-flex items-center rounded border px-2 py-0.5 text-xs font-medium tracking-wide uppercase ${VERDICT_STYLE[verdict]}`}
    >
      {VERDICT_LABEL[verdict]}
    </span>
  );
}

const SEVERITY_STYLE: Record<Severity, string> = {
  info: "text-info border-info/40 bg-info/10",
  low: "text-nominal border-nominal/40 bg-nominal/10",
  medium: "text-warning border-warning/40 bg-warning/10",
  high: "text-danger border-danger/40 bg-danger/10",
  critical: "text-critical border-critical/40 bg-critical/10 animate-pulse",
};

export function SeverityBadge({ severity }: { severity: Severity }) {
  return (
    <span
      className={`mono inline-flex items-center rounded border px-2 py-0.5 text-xs font-medium tracking-wide uppercase ${SEVERITY_STYLE[severity]}`}
    >
      {severity}
    </span>
  );
}

export function SyntheticBadge({ synthetic }: { synthetic: boolean }) {
  return (
    <span
      className={`mono inline-flex items-center rounded border px-2 py-0.5 text-[10px] tracking-wide uppercase ${
        synthetic
          ? "text-muted border-border bg-surface-raised"
          : "text-accent border-accent/40 bg-accent/10"
      }`}
      title={synthetic ? "Simulated data" : "Real measured data"}
    >
      {synthetic ? "synthetic" : "real data"}
    </span>
  );
}
