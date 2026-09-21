# 🛰️ ORBITAL SENTINEL

**Was that spacecraft anomaly a broken sensor, a solar storm, a failing bearing — or someone messing with it?**

[![CI](https://github.com/Kaushik2210/ORBITAL-SENTINEL/actions/workflows/ci.yml/badge.svg)](https://github.com/Kaushik2210/ORBITAL-SENTINEL/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Python 3.12](https://img.shields.io/badge/python-3.12-blue)
![Status: early development](https://img.shields.io/badge/status-early%20development-orange)

Orbital Sentinel treats a spacecraft as a distributed system. It ingests a telemetry stream, runs a layered
detection engine, and decides which of five things is going on: a **mechanical failure**, an **environmental
upset**, a **sensor malfunction**, a **cyberattack**, or **nothing at all**. When the evidence genuinely doesn't
separate the hypotheses, it says so and hands the case to a human instead of guessing.

The interesting part is the hard part: telling *confusable* cases apart.

| Looks like… | …but is actually | What separates them |
|---|---|---|
| Stale data from a stuck sensor | **Replay** attack | Sequence counters and timestamps, not the values |
| Radiation bit-flips | Injected **glitches** | Correlation with real NASA DONKI space-weather events |
| Traffic disappears | **Jamming** | Link SNR/BER versus frame rate and sequence gaps |
| One sensor slowly diverging | Sensor **spoofing** | Redundant-sensor shape... and this one is *not* solved (see below) |

> **Defensive simulation only.** No real spacecraft, ground system or third-party infrastructure is ever touched.
> All attack code operates on local synthetic or replayed data. See [`SECURITY.md`](SECURITY.md).

## Where the project stands

This is an honest snapshot. **The detection and attribution engine is built and evaluated; the API, web UI,
AI agent and Docker packaging are not built yet.** Details and next steps: [`docs/PROGRESS.md`](docs/PROGRESS.md).

| Area | State |
|---|---|
| Real NASA data acquisition, inspection and provenance ([DATASETS](docs/DATASETS.md)) | ✅ done |
| CCSDS-like packet layer with HMAC tag, async ingestion, mission replay engine | ✅ done |
| Synthetic physics bus driven by real PCoE battery / IMS bearing trajectories | ✅ done |
| Database schema, Alembic migrations, TimescaleDB hypertables (verified in CI) | ✅ done |
| Detectors: L1 statistical, L3 physics/redundancy, L4 protocol/security, L5 environment | ✅ done |
| 33-scenario library (7 attack families, faults, degradation, SEU) + explainable attribution | ✅ done |
| L2 machine-learning detectors (LSTM forecaster → ONNX, Isolation Forest) | ⏳ not built |
| REST / WebSocket / SSE API | ⏳ not built |
| Mission Control web UI, AI investigation agent, platform security, Docker | ⏳ not built |

## Results so far

All from [`docs/EVALUATION.md`](docs/EVALUATION.md), which is generated from saved run files (nothing typed by hand).
**These are simulator results on a held-out seed split, not real-spacecraft accuracy** — read
[`docs/LIMITATIONS.md`](docs/LIMITATIONS.md) alongside them.

- **Attacks are detected fast and attributed well.** Command, replay, authentication, jamming, denial-of-service and
  glitch-injection scenarios are caught within minutes (median 0-2 simulated minutes) and, with a few
  exceptions handed to a human, classified correctly; replay vs a stuck sensor, jamming vs dropout and SEU vs
  injected glitches are separated.
- **Attribution:** 86.4% accuracy when forced to choose; **95.1% on the windows it chooses to answer, at 61.8%
  coverage**. A transparent hand-written rule set scores 51.8%.
- **The hard case is not solved.** Slow, in-limits single-sensor drift (spoofing vs drift vs low-and-slow
  manipulation) cannot be separated by the current evidence. The platform abstains (`needs_human`) on
  essentially all of it rather than guessing.
- **Real data:** on the 81 labeled SMAP/MSL channels, the L1 statistical layer alone reaches event-level F1 0.64
  overall, but **0.55 on the 65 channels with a varying training signal** (the 16 constant-training channels are
  trivially perfect). There is no forecaster yet.
- **It does not generalize to failure modes it has never seen** (leave-one-scenario-out).

## Data

Real data: the NASA/JPL SMAP & MSL telemetry-anomaly set (Hundman et al., KDD 2018), NASA PCoE battery and IMS
bearing run-to-failure sets, and the NASA DONKI space-weather API. Everything else — commands, authentication
events, link metrics and **every attack** — is **synthetic and always labeled as such**. Real-data findings and
surprises (the original archive is gone, multi-hot command columns, unlabeled and duplicated channels, …) are in
[`docs/DATASETS.md`](docs/DATASETS.md). Real data is never redistributed: `make data` downloads it locally.

## Pipeline

```mermaid
flowchart LR
  A[Telemetry source<br/>SMAP/MSL replay + synthetic bus + side channels] --> B[CCSDS-like packets<br/>APID · seq · time · HMAC]
  B --> C[Async ingestion]
  C --> D[(TimescaleDB / SQLite)]
  C --> E[Detection engine L1 · L3 · L4 · L5]
  E --> F[Attribution<br/>posterior over 5 classes + needs-human]
  F --> G[Incident store]
  G -.->|not built yet| H[AI investigation agent → Mission Control UI]
```

Design: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) and the decision records in [`docs/adr/`](docs/adr/).

## Try it

Requirements: Python 3.12, [`uv`](https://docs.astral.sh/uv/). (`make` is optional; `scripts/tasks.py` is the real runner.)

```bash
python scripts/tasks.py setup                 # install dependencies
python scripts/tasks.py data                  # real datasets into data/raw (checksummed)
python scripts/tasks.py check                 # lint + strict typing + tests
```

Reproduce the evaluation (about ten minutes):

```bash
uv sync --extra ml
uv run python -m sentinel_ml.make_dataset     # 462 scenario runs through the full engine
uv run python -m sentinel_ml.evaluate --out ml/runs/attribution-v1
uv run python -m sentinel_ml.smap_eval        # real SMAP/MSL, event level
uv run python -m sentinel_ml.render_eval --metrics ml/runs/attribution-v1/metrics.json
```

Seed a local database with a replayed mission: `python scripts/tasks.py seed`.

## Contributing

Contributions of all sizes are welcome; please read [`CONTRIBUTING.md`](CONTRIBUTING.md). The one rule that matters
most: **no fabricated numbers** — every metric in code, docs or UI must come from a run you can reproduce.

## AI agent disclosure

The planned investigation agent will use the Anthropic API when `ANTHROPIC_API_KEY` is set, and a deterministic
template report otherwise. It is not built yet.

## License

[MIT](LICENSE) for this project's code. Third-party datasets keep their own licenses; see
[`docs/DATASETS.md`](docs/DATASETS.md).
