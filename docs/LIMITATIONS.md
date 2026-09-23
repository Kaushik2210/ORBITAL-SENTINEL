# Limitations

What this project does **not** do, what it cannot show, and where it is known to be weak. This page is
meant to be read next to [EVALUATION.md](EVALUATION.md); every claim here traces to a measurement or to a
component that is visibly not built.

## Not built yet

| Missing | Consequence |
|---|---|
| The Mission Control frontend, platform security (JWT, roles, rate limiting, audit log), Docker images and `docker compose` | The API exists but is **unauthenticated** and must only be run locally; there is no UI or one-command demo yet |
| Attribution on real channels | The attribution model was trained on the synthetic bus; the API records real-channel replay incidents as `needs_human` with a note rather than an unvalidated class |
| The investigation agent's live-LLM path | Unit-tested against a stub Anthropic client only (see `tests/agent/test_client.py`); it has not been run end-to-end against the real API, because doing so requires a paid key. The offline fallback is the only path exercised in CI, and it is also the default without `ANTHROPIC_API_KEY` set — see [API.md](API.md). |

## What the evaluation can and cannot claim

- **Synthetic scenarios, one simulator.** Every attack and fault is injected by the same simulator that produced
  the training data. The results measure separability *within that simulator*, not accuracy on a real
  spacecraft or against a real adversary. Real attackers are not constrained to the scenario families here.
- **Small effective sample.** 33 scenarios x 14 seeded variants; the test split has 4 seeds per scenario and 199
  incident windows. Windows from one scenario are correlated, so the reported 95% intervals are optimistic.
  Differences of a few points (including most of the layer ablation) should be read as noise.
- **Coverage/accuracy trade-off is a design choice.** The abstention thresholds were tuned on validation data to
  reach >= 90% answered accuracy; that gives 61.8% coverage on the test split. A different target gives a
  different point on the curve.
- **The learned model does not generalize to unseen failure modes.** Leave-one-scenario-out evaluation shows
  several scenario types are never classified correctly when they are absent from training. It recognizes
  families it has seen.
- **Real-data results are modest.** On the labeled SMAP/MSL anomalies, the statistical layer (L1) reaches event-level
  F1 0.55 on the 65 channels with a varying training signal; the LSTM forecaster (L2), trained for only 15 epochs
  (the paper uses 35), scores lower on its own (0.40), and the L1+L2 union is within noise of L1. The 16
  constant-training channels score 1.00 (L1) almost trivially and are reported separately. There are no
  cross-channel checks (the channels are anonymized) and no tuning on this data. A fully trained forecaster
  might do better; that has not been tested.

## Known technical weaknesses

- **Slow, in-limits, single-sensor drift is the hard case.** Spoofing, sensor drift, a low-and-slow manipulation
  and a slow ramp look the same to the current features. The platform correctly hands these to a human
  (`needs_human`) instead of guessing, but it does **not** solve the problem. L1's detrended CUSUM is blind to
  very slow trends by design; only a redundant sensor or a physical relation (synthetic bus only) catches them.
- **False alarms.** On nominal test runs the detectors opened incident windows at a measured rate reported in
  the evaluation; most spurious windows end as `needs_human` rather than `nominal`, so they would reach an
  operator. Thresholds depend on calibration length (ADR 0010).
- **Start-up transient is inhibited, not modeled.** Alarms are suppressed for the first 150 simulated minutes.
- **The redundancy check is two-sensor.** With only two sensors it cannot say *which* one is wrong unless one
  jumps out of character; for ramps it reports no preference.
- **SEU vs manipulation depends on real space-weather context.** The environmental class relies on cached DONKI
  events; a session outside the cached span has `weather_context = unavailable`, and the model then has no
  environmental evidence at all.

## Modeling shortcuts (all labeled in code and docs)

- **Time step.** One simulation step is 60 s. This is a convention; the SMAP/MSL documentation available to us does
  not state the true sample cadence.
- **The synthetic physics bus is simplified** (linear open-circuit voltage, a toy charge controller, vibration
  taken from one IMS bearing). Its *degradation trajectories* are real (PCoE cells B0005/6/7/18, IMS test 2);
  everything around them is synthetic and flagged `synthetic`.
- **Side channels are synthetic**: commands, authentication events, link metrics, packet headers and all attacks.
  Ground stations, operators and opcodes are invented names.
- **The packet layer is CCSDS-like, not CCSDS-conformant security.** The HMAC tag is a simulation, not SDLS, and the
  key is synthetic.
- **Technique tags are labeling aids.** SPARTA and ATT&CK ids were checked against the official sites on
  2026-09-21, but tagging a simulated scenario does not mean it reproduces a real campaign.

## Data caveats (details in [DATASETS.md](DATASETS.md))

- The original SMAP/MSL archive is no longer public; a pinned third-party Hugging Face mirror is used, verified by
  checksum on first fetch and by its label file.
- SMAP/MSL: `T-10` has no label row, `P-2` has two, 16 channels have a constant training signal, and test values
  leave the training range on 43 of 82 channels. Channel IDs are anonymized.
- PCoE has no formal license; nothing derived from it is committed. Most battery cells are not clean aging
  curves; only B0005/6/7/18 are used. The IMS bearing archive needs a RAR-capable tool.
- DONKI: the `DEMO_KEY` rate limit measured 10 requests; only May 2024 (FLR, SEP, GST) is cached, and that cache
  is not committed.

## Verification gaps

- Docker images and `docker compose` are not built or run on the development machine (Docker is not installed).
- TimescaleDB behavior (hypertables, compression, retention) is verified only in the GitHub Actions `postgres` job.
- The investigation agent's live LLM path is built but only tested against a stub client, not a real key (see
  the table above).
