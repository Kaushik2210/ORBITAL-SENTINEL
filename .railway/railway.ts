import { defineRailway, project, service, postgres, github } from "railway/iac";

const REPO = "Kaushik2210/ORBITAL-SENTINEL";
const BRANCH = "main";

export default defineRailway((ctx) => {
  const db = postgres("db");

  const api = service("api", {
    source: github(REPO, { branch: BRANCH }),
    build: {
      builder: "DOCKERFILE",
      dockerfilePath: "backend/Dockerfile",
    },
    deploy: {
      healthcheckPath: "/api/v1/health",
      healthcheckTimeout: 30,
    },
    networking: {
      serviceDomains: { default: { port: 8000 } },
    },
    env: {
      // Pinned rather than left to Railway's own default (observed: 8080), so this number and
      // the domain's target port above can never drift apart. The Dockerfile's CMD reads $PORT.
      PORT: "8000",
      DATABASE_URL:
        "postgresql+asyncpg://${{db.PGUSER}}:${{db.PGPASSWORD}}@${{db.PGHOST}}:${{db.PGPORT}}/${{db.PGDATABASE}}",
      JWT_SECRET: ctx.randomString("jwt-secret", 48),
      CORS_ALLOW_ORIGINS: "https://${{web.RAILWAY_PUBLIC_DOMAIN}}",
      PUBLIC_DEMO_MODE: "true",
      RATE_LIMIT_PER_MINUTE: "120",
      ANTHROPIC_API_KEY: "",
      ANTHROPIC_MODEL: "claude-sonnet-5",
    },
  });

  const web = service("web", {
    source: github(REPO, { branch: BRANCH, rootDirectory: "frontend" }),
    build: {
      builder: "DOCKERFILE",
      dockerfilePath: "Dockerfile",
    },
    networking: {
      serviceDomains: { default: { port: 3000 } },
    },
    env: {
      // Pinned rather than left to Railway's own default (observed: 8080) — see the api service.
      PORT: "3000",
      NEXT_PUBLIC_API_BASE: "https://${{api.RAILWAY_PUBLIC_DOMAIN}}",
    },
  });

  return project("orbital-sentinel", {
    resources: [db, api, web],
  });
});
