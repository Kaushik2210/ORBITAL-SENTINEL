import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "node:path";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": path.resolve(import.meta.dirname, "src") },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./vitest.setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    exclude: ["e2e/**"],
    coverage: {
      provider: "v8",
      reporter: ["text", "text-summary"],
      include: ["src/lib/**", "src/components/**"],
      // Floor set a few points under the measured baseline (51 / 65 / 45 / 54). The uPlot chart and
      // the WebSocket hook are untested here (jsdom has neither canvas nor a socket server); the
      // Playwright suite exercises them. Raise these as tests are added, never lower them.
      thresholds: { statements: 48, branches: 60, functions: 40, lines: 50 },
    },
  },
});
