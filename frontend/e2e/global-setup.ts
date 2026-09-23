/** Spins up a real API (isolated SQLite DB, migrated, one test admin user) for the e2e suite to
 * drive through the actual browser — no mocked network. Playwright's own `webServer` config
 * starts the Next.js dev server; this covers the backend half of the stack. */
import { spawn, spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

export const ROOT = path.resolve(__dirname, "..", "..");
export const API_PORT = 8123;
export const BASE_URL = `http://127.0.0.1:${API_PORT}`;
export const TEST_EMAIL = "e2e@orbitalsentinel.dev";
export const TEST_PASSWORD = "e2e-password-do-not-reuse";

const META_FILE = path.join(__dirname, ".backend-meta.json");
const DB_PATH = path.join(os.tmpdir(), `orbital-sentinel-e2e-${Date.now()}.sqlite`);

const env = {
  ...process.env,
  DATABASE_URL: `sqlite+aiosqlite:///${DB_PATH.replace(/\\/g, "/")}`,
  JWT_SECRET: "e2e-test-secret-at-least-32-bytes-long-0000",
  PUBLIC_DEMO_MODE: "true",
  CORS_ALLOW_ORIGINS: "http://127.0.0.1:3100,http://localhost:3100",
  ANTHROPIC_API_KEY: "",
  RATE_LIMIT_PER_MINUTE: "0", // the suite hammers /health while polling readiness; don't self-block
  DATA_ROOT: "nonexistent", // no real SMAP/MSL data needed for this scenario-only suite
  L2_MODELS_DIR: "nonexistent",
};

async function waitForHealth(url: string, timeoutMs = 60_000): Promise<void> {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    try {
      const res = await fetch(url);
      if (res.ok) return;
    } catch {
      // backend not accepting connections yet
    }
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error(`backend did not become healthy at ${url} within ${timeoutMs}ms`);
}

export default async function globalSetup(): Promise<void> {
  const migrate = spawnSync("uv", ["run", "alembic", "upgrade", "head"], {
    cwd: ROOT,
    env,
    stdio: "inherit",
  });
  if (migrate.status !== 0) throw new Error("alembic migration failed");

  const createUser = spawnSync(
    "uv",
    [
      "run",
      "python",
      "-m",
      "sentinel_api.db.create_user",
      "--email",
      TEST_EMAIL,
      "--role",
      "admin",
      "--password",
      TEST_PASSWORD,
    ],
    { cwd: ROOT, env, stdio: "inherit" }
  );
  if (createUser.status !== 0) throw new Error("test user creation failed");

  const backend = spawn(
    "uv",
    ["run", "uvicorn", "sentinel_api.main:app", "--host", "127.0.0.1", "--port", String(API_PORT)],
    { cwd: ROOT, env, stdio: "inherit", detached: process.platform !== "win32" }
  );
  fs.writeFileSync(META_FILE, JSON.stringify({ pid: backend.pid, dbPath: DB_PATH }));

  await waitForHealth(`${BASE_URL}/api/v1/health`);
}
