# 9. Live transport: WebSocket for telemetry, SSE for incidents and investigation

Date: 2026-09-20 · Status: accepted

## Context
The UI needs high-rate telemetry, an incident feed, and a streamed investigation trace.

## Decision
- **WebSocket** `/ws/sessions/{id}` for telemetry and detections (server to client only; batches up to about
  20 Hz, decimated). Session control (`play/pause/seek/speed`) is REST so every control action is authenticated
  and audit-logged.
- **SSE** for incidents and the investigation trace: one-directional, auto-reconnecting, proxy-friendly,
  and trivially replayable from `investigation_events`.

## Consequences
- The frontend keeps live samples in typed ring buffers outside React state and draws with uPlot.
- Auth for WS uses a short-lived ticket obtained via REST (browsers cannot set headers on WS).
