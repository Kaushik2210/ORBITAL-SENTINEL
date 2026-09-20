# 2. Python package layout: one project, four import packages

Date: 2026-09-20 · Status: accepted

## Context
The brief lays out `backend/`, `simulator/` and `ml/` as top-level folders. The detection engine is needed by
the live API, the simulator's evaluation harness, and ML evaluation, so a shared domain package is required.
Separate installable projects per folder would add cross-package import and versioning friction.

## Decision
One `pyproject.toml` at the repo root and four import packages, each living under its top-level folder:
`sentinel_core` (backend/), `sentinel_api` (backend/), `sentinel_sim` (simulator/), `sentinel_ml` (ml/).
`sentinel_core` imports none of the others; `sentinel_sim` and `sentinel_api` import core; `sentinel_ml`
imports core and sim.

## Consequences
- One venv, one lockfile, one CI job; simple editable installs with `uv`.
- Heavy optional dependencies (PyTorch, ONNX Runtime) are added as extras in the phase that needs them, so
  early phases stay fast to install.
- The folder structure matches the brief while imports stay flat and explicit.
