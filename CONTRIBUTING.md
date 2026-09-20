# Contributing

Thanks for your interest in Orbital Sentinel! Contributions of all sizes are welcome: bug reports,
new detectors, new scenarios, docs, and UI polish.

## Ground rules

1. **No fabricated numbers.** Every metric, dataset statistic or benchmark in code, docs or UI must come from a
   run you can reproduce. Say which seed, dataset and profile produced it.
2. **Label synthetic data as synthetic**, everywhere: database, API, UI and docs.
3. **Defensive simulation only.** Attack code must operate solely on local synthetic or replayed data. Do not
   add code that targets real spacecraft, ground stations, or third-party services.
4. **Attacker-controlled strings are data, never instructions.** Anything derived from telemetry (command names,
   hostnames, log text) must be treated as untrusted, especially near the AI agent.

## Getting set up

```bash
python scripts/tasks.py setup    # uv sync (Python 3.12)
python scripts/tasks.py check    # ruff + mypy --strict + pytest
```

Install the git hooks with `uv run pre-commit install`.

## Workflow

- Branch from `main`; keep pull requests focused.
- Write tests with the change. Detectors are tested against labeled scenarios with fixed seeds.
- Use [Conventional Commits](https://www.conventionalcommits.org/): `feat(detect): …`, `fix(api): …`,
  `docs: …`, `test: …`, `chore: …`.
- Significant design choices get a short ADR in [`docs/adr/`](docs/adr/).
- CI must be green: ruff, mypy (strict), pytest, and the security scanners.

## Code style

Small, typed, well-named modules. Explain non-obvious algorithm choices in docstrings (the *why*, not the
*what*). Prefer editing an existing pattern over inventing a new one.

## Reporting security issues

See [SECURITY.md](SECURITY.md). Please do not open public issues for vulnerabilities in the platform itself.
