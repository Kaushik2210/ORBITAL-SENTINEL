# syntax=docker/dockerfile:1.7
#
# Builds the API only (sentinel_core + sentinel_sim + sentinel_agent + sentinel_api). No ML extra
# (torch/onnxruntime) and no agent extra (anthropic) are installed here — see docs/PROGRESS.md and
# ADR 0002/0011. Without them the API still runs correctly: L2 detection is skipped when no ONNX
# models are mounted (state.py checks `l2_models_dir.is_dir()` first) and the investigation agent
# falls back to its deterministic offline report when no key/SDK is available (client.py). A
# deployment that wants live L2 or the live agent installs the relevant extra in its own build.
#
# Build context is the REPO ROOT (see docker-compose.yml), because the project is a uv workspace
# spanning backend/, simulator/ and ml/ — not just this directory.

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/app/.venv

COPY pyproject.toml uv.lock ./
COPY backend backend
COPY simulator simulator
COPY ml ml
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev

FROM python:3.12-slim-bookworm AS runtime
RUN groupadd --system sentinel && useradd --system --create-home --gid sentinel sentinel
WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY --chown=sentinel:sentinel backend backend
COPY --chown=sentinel:sentinel simulator simulator
COPY --chown=sentinel:sentinel ml ml
COPY --chown=sentinel:sentinel alembic.ini ./
COPY --chown=sentinel:sentinel data/scenarios data/scenarios
COPY --chown=sentinel:sentinel docs/data docs/data
COPY --chown=sentinel:sentinel models models

ENV PATH="/app/.venv/bin:$PATH" \
    SCENARIOS_DIR=/app/data/scenarios \
    DOCS_DATA_DIR=/app/docs/data \
    ATTRIBUTION_MODEL=/app/models/attribution-v1.json \
    DATA_ROOT=/data \
    DONKI_CACHE=/data/cache/donki \
    PUBLIC_DEMO_MODE=true

# Real SMAP/MSL data and the L2 ONNX models are never baked into the image (they're either
# gitignored, licensed third-party data, or reproducible artifacts) — mount them at /data and
# L2_MODELS_DIR if a deployment has them; the API degrades gracefully without either.
VOLUME ["/data"]

USER sentinel
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=5 \
    CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=2)"

CMD ["uvicorn", "sentinel_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
