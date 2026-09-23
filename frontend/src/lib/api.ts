// A thin typed fetch wrapper over the Orbital Sentinel API (docs/API.md). No code generation —
// the surface is small enough to keep this hand-written and honest about what it covers.
import type {
  AuditLogOut,
  AuditVerifyOut,
  ChannelOut,
  EvidenceOut,
  GroundTruthOut,
  IncidentOut,
  InvestigateOut,
  MeOut,
  Page,
  Ready,
  ScenarioOut,
  SessionCreate,
  SessionOut,
  TelemetrySeries,
  TokenOut,
} from "./types";

const BASE = (process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000").replace(/\/$/, "");
const API = `${BASE}/api/v1`;

export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: string
  ) {
    super(detail);
    this.name = "ApiError";
  }
}

async function request<T>(
  path: string,
  opts: { method?: string; body?: unknown; token?: string | null; params?: Record<string, string | number | boolean | undefined> } = {}
): Promise<T> {
  const url = new URL(`${API}${path}`);
  for (const [k, v] of Object.entries(opts.params ?? {})) {
    if (v !== undefined) url.searchParams.set(k, String(v));
  }
  const headers: Record<string, string> = {};
  if (opts.body !== undefined) headers["content-type"] = "application/json";
  if (opts.token) headers["authorization"] = `Bearer ${opts.token}`;
  const res = await fetch(url, {
    method: opts.method ?? "GET",
    headers,
    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
    cache: "no-store",
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body?.detail === "string" ? body.detail : JSON.stringify(body);
    } catch {
      // body wasn't JSON; keep statusText
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  base: BASE,

  health: () => request<{ status: string }>("/health"),
  ready: () => request<Ready>("/ready"),
  channels: (token?: string | null) => request<ChannelOut[]>("/channels", { token }),
  scenarios: (token?: string | null, reveal = false) =>
    request<ScenarioOut[]>("/scenarios", { token, params: { reveal } }),
  scenario: (id: string, token?: string | null, reveal = false) =>
    request<ScenarioOut>(`/scenarios/${id}`, { token, params: { reveal } }),

  login: (email: string, password: string) =>
    request<TokenOut>("/auth/login", { method: "POST", body: { email, password } }),
  me: (token: string) => request<MeOut>("/auth/me", { token }),

  createSession: (body: SessionCreate, token: string) =>
    request<SessionOut>("/sessions", { method: "POST", body, token }),
  sessions: (token?: string | null) => request<SessionOut[]>("/sessions", { token }),
  session: (id: string, token?: string | null) => request<SessionOut>(`/sessions/${id}`, { token }),
  controlSession: (id: string, action: "pause" | "play" | "speed", token: string, speed?: number) =>
    request<SessionOut>(`/sessions/${id}/control`, { method: "POST", body: { action, speed }, token }),
  telemetry: (id: string, channels: string[], token?: string | null, maxPoints = 1500) =>
    request<TelemetrySeries[]>(`/sessions/${id}/telemetry`, {
      token,
      params: { channels: channels.join(","), max_points: maxPoints },
    }),
  groundTruth: (id: string, token?: string | null) =>
    request<GroundTruthOut>(`/sessions/${id}/ground-truth`, { token }),

  incidents: (
    token?: string | null,
    params?: { session_id?: string; verdict?: string; severity?: string; cursor?: string; limit?: number }
  ) => request<Page<IncidentOut>>("/incidents", { token, params }),
  incident: (id: string, token?: string | null) => request<IncidentOut>(`/incidents/${id}`, { token }),
  evidence: (id: string, token?: string | null) => request<EvidenceOut>(`/incidents/${id}/evidence`, { token }),
  investigate: (id: string, token: string) =>
    request<InvestigateOut>(`/incidents/${id}/investigate`, { method: "POST", token }),
  report: (id: string, token?: string | null) => request<InvestigateOut>(`/incidents/${id}/report`, { token }),

  evaluation: (token?: string | null) => request<Record<string, unknown>>("/evaluation", { token }),

  auditLog: (token: string, cursor?: string) =>
    request<Page<AuditLogOut>>("/audit", { token, params: { cursor } }),
  auditVerify: (token: string) => request<AuditVerifyOut>("/audit/verify", { token }),

  wsUrl: (sessionId: string, token?: string | null) => {
    const wsBase = BASE.replace(/^http/, "ws");
    const q = token ? `?token=${encodeURIComponent(token)}` : "";
    return `${wsBase}/api/v1/ws/sessions/${sessionId}${q}`;
  },
};
