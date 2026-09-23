import { defineConfig, devices } from "@playwright/test";
import { API_PORT } from "./e2e/global-setup";

const WEB_PORT = 3100;

export default defineConfig({
  testDir: "./e2e",
  testMatch: "**/*.spec.ts",
  timeout: 90_000,
  expect: { timeout: 15_000 },
  fullyParallel: false, // a single shared backend + DB; scenarios would race each other
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? "github" : "list",
  globalSetup: "./e2e/global-setup.ts",
  globalTeardown: "./e2e/global-teardown.ts",
  use: {
    baseURL: `http://127.0.0.1:${WEB_PORT}`,
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: `npm run dev -- --port ${WEB_PORT}`,
    url: `http://127.0.0.1:${WEB_PORT}`,
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
    env: { NEXT_PUBLIC_API_BASE: `http://127.0.0.1:${API_PORT}` },
  },
});
