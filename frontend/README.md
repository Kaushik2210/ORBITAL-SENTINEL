# Mission Control (frontend)

A Next.js (App Router, TypeScript strict, Tailwind v4) client for [the API](../docs/API.md). No
build step or server component talks to the database directly — everything goes through the
public REST/WebSocket API, the same one a `curl` script could use.

## Run it

```bash
npm install
cp .env.example .env.local   # NEXT_PUBLIC_API_BASE, default http://localhost:8000
npm run dev
```

The API must be running separately (`python scripts/tasks.py serve` from the repo root). With
`PUBLIC_DEMO_MODE=true` (the API's default) you can browse read-only immediately; sign in with an
account provisioned by `python scripts/tasks.py create-user` to launch scenarios or run the agent.

## Pages

| Route | Shows |
|---|---|
| `/` | Platform readiness, recent incidents |
| `/login` | Email/password → JWT, stored client-side (`lib/auth-store.ts`, a `zustand` store) |
| `/scenarios` | The 33-scenario library; launching one needs the `analyst` role |
| `/sessions/[id]` | Live telemetry (WebSocket, uPlot), incidents as they're raised, ground truth revealed on request once the session finishes |
| `/incidents` | Filterable incident list |
| `/incidents/[id]` | Posterior, signed evidence, detector outputs, and the AI investigation agent (`investigate` button, offline-vs-live badge, tool-use trace) |

## What isn't here

- No 3D globe, no `react-three-fiber`, no Framer Motion — the original brief's more decorative
  pieces were cut to ship something that actually works end-to-end within scope. What's here is
  functional: live streaming, real auth, real agent calls, no mocked data.
- No "Attack or Accident?" scored challenge mode; `/sessions/[id]`'s ground-truth reveal button is
  the honest, unscored version of that idea (hidden until asked, so a live client can't peek — the
  API enforces the actual withholding, this is just the UI for it).
- No offline/error boundary polish beyond basic try/catch-to-message; no Vitest/Playwright suite
  yet (tracked in `docs/PROGRESS.md`, Phase 10).
- No dark/light toggle — Mission Control is an always-on operator surface, not a document, so it
  doesn't follow the OS theme preference.

## Type safety

`lib/types.ts` mirrors `backend/sentinel_api/schemas.py` by hand (no OpenAPI codegen) — the API
surface is small enough that this is easier to keep honestly in sync than to add a generator step.
If you add a field to a Pydantic schema, add it here too.
