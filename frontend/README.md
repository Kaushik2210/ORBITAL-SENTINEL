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

## Testing

```bash
npm run test              # vitest: lib/ and components/ units
npm run test:coverage     # same, with a v8 coverage summary
npm run e2e                # playwright: a real backend + a real browser, no mocks
```

`e2e/global-setup.ts` runs its own isolated backend for the suite — a temp SQLite DB, migrated
fresh, with one seeded admin user — on port 8123, and Playwright's own `webServer` config starts
the Next.js dev server on port 3100 pointed at it. Nothing is mocked: the spec signs in for real,
launches the `auth_bruteforce` scenario, waits for it to actually finish, opens the incident it
actually raised, and runs the actual (offline-fallback) investigation agent against it. Both
processes are torn down after the run (`e2e/global-teardown.ts`).

## What isn't here

- No 3D globe, no `react-three-fiber`, no Framer Motion — the original brief's more decorative
  pieces were cut to ship something that actually works end-to-end within scope. What's here is
  functional: live streaming, real auth, real agent calls, no mocked data.
- No "Attack or Accident?" scored challenge mode; `/sessions/[id]`'s ground-truth reveal button is
  the honest, unscored version of that idea (hidden until asked, so a live client can't peek — the
  API enforces the actual withholding, this is just the UI for it).
- No offline/error boundary polish beyond basic try/catch-to-message.
- No dark/light toggle — Mission Control is an always-on operator surface, not a document, so it
  doesn't follow the OS theme preference.
- The e2e suite covers one path end-to-end, not every page or role combination; the unit tests
  cover `lib/` and `components/` logic, not every page component's rendering.

## Type safety

`lib/types.ts` mirrors `backend/sentinel_api/schemas.py` by hand (no OpenAPI codegen) — the API
surface is small enough that this is easier to keep honestly in sync than to add a generator step.
If you add a field to a Pydantic schema, add it here too.
