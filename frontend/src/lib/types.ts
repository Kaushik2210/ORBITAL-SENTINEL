// Mirrors backend/sentinel_api/schemas.py. Kept hand-written and minimal rather than generated
// from the OpenAPI schema — the API surface is small enough that this is easier to keep honest.

export type Verdict =
  | "nominal"
  | "mechanical_failure"
  | "environmental"
  | "sensor_malfunction"
  | "cyberattack"
  | "needs_human";

export type Severity = "info" | "low" | "medium" | "high" | "critical";
export type Role = "viewer" | "analyst" | "admin";

export interface Page<T> {
  items: T[];
  next_cursor: string | null;
}

export interface Health {
  status: "ok";
  version: string;
}

export interface Ready {
  ready: boolean;
  detection_engine: boolean;
  attribution_model: string | null;
  l2_models: number;
  real_data: boolean;
}

export interface ChannelOut {
  id: string;
  family: string;
  group: string;
  unit: string | null;
  synthetic: boolean;
  group_is_synthetic_grouping: boolean;
}

export interface ScenarioOut {
  id: string;
  steps: number;
  title?: string;
  narrative?: string;
  class?: string;
  subtype?: string;
  hard?: boolean;
  confusable_with?: string[];
  sparta?: string[];
  attack?: string[];
}

export interface SessionCreate {
  kind?: "scenario" | "replay";
  scenario_id?: string;
  variant?: number;
  speed?: number;
  channels?: string[];
  offset?: number;
  steps?: number;
}

export interface SessionOut {
  id: string;
  kind: string;
  status: "created" | "running" | "paused" | "completed" | "failed";
  scenario_id: string | null;
  seed: number;
  speed: number;
  position: number;
  steps: number;
  synthetic: boolean;
  start: string;
  incidents: number;
  error: string | null;
  labels: Record<string, [number, number][]> | null;
}

export interface TelemetryPoint {
  ts: string;
  step: number;
  value: number | null;
}

export interface TelemetrySeries {
  channel: string;
  synthetic: boolean;
  points: TelemetryPoint[];
}

export interface IncidentOut {
  id: string;
  session_id: string;
  opened_at: string;
  closed_at: string | null;
  status: string;
  verdict: Verdict;
  confidence: number;
  severity: Severity;
  posterior: Record<string, number>;
  affected_channels: string[];
  model_version: string;
  synthetic: boolean;
}

export interface ContributionOut {
  feature: string;
  value: number;
  contribution: number;
}

export interface DetectorOutputOut {
  ts: string;
  detector: string;
  layer: string;
  channel: string | null;
  score: number;
  explanation: string;
  evidence: unknown[];
}

export interface EvidenceOut {
  incident: IncidentOut;
  features: Record<string, number>;
  evidence_for: ContributionOut[];
  evidence_against: ContributionOut[];
  runner_up: string | null;
  detector_outputs: DetectorOutputOut[];
}

export interface ReportOut {
  verdict: Verdict;
  confidence: number;
  summary: string;
  key_evidence: string[];
  reasoning: string[];
  recommended_action: string;
  caveats: string[];
}

export interface InvestigateOut {
  incident_id: string;
  mode: "llm" | "offline";
  model: string | null;
  report: ReportOut;
  markdown: string;
  trace: Record<string, unknown>[];
  generated_at: string;
}

export interface GroundTruthOut {
  scenario_id: string;
  class: string;
  subtype: string;
  start_step: number | null;
  end_step: number | null;
  channels: string[];
  sparta: string[];
  attack: string[];
  confusable_with: string[];
  weather_context: string;
  data_sources: Record<string, string>;
}

export interface TokenOut {
  access_token: string;
  token_type: "bearer";
  role: Role;
  expires_in_seconds: number;
}

export interface MeOut {
  email: string;
  role: Role;
}

export interface AuditLogOut {
  id: number;
  ts: string;
  actor: string;
  action: string;
  target: string;
  detail: Record<string, unknown>;
}

export interface AuditVerifyOut {
  ok: boolean;
  first_bad_row: number | null;
}

export type StreamMessage =
  | { type: "samples"; k: number; ts: string; values: Record<string, number> }
  | { type: "detection"; [key: string]: unknown }
  | { type: "incident"; [key: string]: unknown }
  | { type: "status"; status: string; speed: number }
  | { type: "done"; [key: string]: unknown };
