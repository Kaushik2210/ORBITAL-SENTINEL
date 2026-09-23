import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // The e2e suite (playwright.config.ts) runs the dev server bound to 127.0.0.1 rather than
  // localhost, which Next.js's dev-mode cross-origin guard otherwise blocks HMR requests from.
  allowedDevOrigins: ["127.0.0.1", "localhost"],
  // A self-contained server bundle for the Docker image (Dockerfile) — copies only the files a
  // production `node server.js` needs, instead of the whole node_modules tree.
  output: "standalone",
};

export default nextConfig;
