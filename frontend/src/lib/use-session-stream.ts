"use client";

import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import type { StreamMessage } from "./types";

const MAX_POINTS = 600; // rolling window; older samples are dropped client-side, not requested again

export interface SessionStreamState {
  connected: boolean;
  steps: number[];
  values: Record<string, (number | null)[]>;
  detections: Extract<StreamMessage, { type: "detection" }>[];
  incidents: Extract<StreamMessage, { type: "incident" }>[];
  status: string | null;
  done: boolean;
}

/** Subscribes to a session's WebSocket stream (docs/API.md). Reconnection is deliberately not
 * attempted — a finished/failed session sends `done` and closes on its own, and a dropped
 * connection to a still-running session is surfaced as `connected: false` rather than silently
 * masked, so the operator knows the view may be stale. */
export function useSessionStream(sessionId: string | null, token: string | null): SessionStreamState {
  const [state, setState] = useState<SessionStreamState>({
    connected: false,
    steps: [],
    values: {},
    detections: [],
    incidents: [],
    status: null,
    done: false,
  });
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    if (!sessionId) return;
    const ws = new WebSocket(api.wsUrl(sessionId, token));
    wsRef.current = ws;
    ws.onopen = () => setState((s) => ({ ...s, connected: true }));
    ws.onclose = () => setState((s) => ({ ...s, connected: false }));
    ws.onerror = () => setState((s) => ({ ...s, connected: false }));
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data) as StreamMessage;
      setState((s) => {
        if (msg.type === "samples") {
          const steps = [...s.steps, msg.k].slice(-MAX_POINTS);
          const values: Record<string, (number | null)[]> = {};
          const channels = new Set([...Object.keys(s.values), ...Object.keys(msg.values)]);
          for (const ch of channels) {
            const prev = s.values[ch] ?? [];
            const next = [...prev, msg.values[ch] ?? null].slice(-MAX_POINTS);
            values[ch] = next;
          }
          return { ...s, steps, values };
        }
        if (msg.type === "detection") return { ...s, detections: [...s.detections, msg].slice(-200) };
        if (msg.type === "incident") return { ...s, incidents: [...s.incidents, msg] };
        if (msg.type === "status")
          return { ...s, status: String((msg as { status?: string }).status ?? s.status) };
        if (msg.type === "done") return { ...s, done: true, status: "completed" };
        return s;
      });
    };
    return () => ws.close();
  }, [sessionId, token]);

  return state;
}
