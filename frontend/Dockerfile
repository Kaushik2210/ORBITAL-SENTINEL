# syntax=docker/dockerfile:1.7
#
# NEXT_PUBLIC_* variables are inlined into the JS bundle at build time, so the API's URL has to be
# known at `docker build`, not just at `docker run` — pass it as a build arg (docker-compose.yml
# does). It must be a URL the *browser* can reach, not a Docker-internal service name: with the
# default compose setup that's http://localhost:8000, because the API's port is published to the
# host.

FROM node:22-bookworm-slim AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:22-bookworm-slim AS builder
WORKDIR /app
ARG NEXT_PUBLIC_API_BASE=http://localhost:8000
ENV NEXT_PUBLIC_API_BASE=${NEXT_PUBLIC_API_BASE} NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:22-bookworm-slim AS runtime
RUN groupadd --system sentinel && useradd --system --create-home --gid sentinel sentinel
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0

COPY --from=builder --chown=sentinel:sentinel /app/public ./public
COPY --from=builder --chown=sentinel:sentinel /app/.next/standalone ./
COPY --from=builder --chown=sentinel:sentinel /app/.next/static ./.next/static

USER sentinel
EXPOSE 3000
HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=5 \
    CMD node -e "fetch('http://127.0.0.1:3000/login').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"

CMD ["node", "server.js"]
