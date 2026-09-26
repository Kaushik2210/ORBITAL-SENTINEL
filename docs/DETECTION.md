# Detection layers, as built

A per-detector reference. For the design rationale see [ARCHITECTURE.md §6](ARCHITECTURE.md); for
measured numbers see [EVALUATION.md](EVALUATION.md) — this page names the actual classes and
thresholds in `backend/sentinel_core/detection/` and says what each one catches, deliberately
without re-deriving any number that page already reports.

Every detector implements the same `Detector` protocol (`detection/base.py`): streaming, stateful
per channel, deterministic given a seed and input order, and blind to ground truth. `learn()`/
`freeze()` calibrate on nominal training data (ADR 0010: alarm thresholds are **empirical** —
measured on nominal data, not assumed Gaussian); `update()` scores live events and returns
`DetectorOutput`s with a `[0, 1]` score, a `fired` flag, and `Evidence` the attribution model and
the AI agent both read later.

## L1 — statistical (`detection/statistical.py`, `StatisticalDetector`, `l1.statistical`)

Per-channel range, EWMA z-score, detrended CUSUM, rate-of-change, flatline and variance checks.
Two things this layer had to be built around, both found inspecting the real SMAP/MSL data
(`docs/DATASETS.md`): test values leave the calibrated training range on 43 of 82 channels (so
limits are widened by a margin, not just taken from training min/max), and 16 channels have a
constant training signal (so they get a variance floor plus a flatline check instead of a
z-score). CUSUM detrends against a slow adaptive reference (`REF_ALPHA`) and winsorizes inputs at
`Z_CLIP=8` so a single bit-flip can't saturate the statistic. All thresholds are `MARGIN`/
`CUSUM_MARGIN` multiples of the largest value seen during calibration.

**Measured (real SMAP/MSL, event level):** F1 0.64 overall, **0.55 on the 65 channels with a
varying training signal** — the honest number, since the 16 constant-training channels are nearly
trivial for a range check. See `EVALUATION.md` → *Real data: L1 on SMAP/MSL anomalies*.

## L2 — ML forecaster (`detection/l2.py`, `ForecasterDetector`, `l2.forecaster`)

A per-channel Telemanom-style LSTM (trained in `ml/sentinel_ml/forecaster.py`, served here via
ONNX Runtime) predicts the next value from the trailing `WINDOW=48` steps plus the commands that
arrive with it; the error is EWMA-smoothed and compared against a threshold re-derived
periodically with the nonparametric dynamic-thresholding-and-pruning rule
(`detection/dynamic_threshold.py`, after Hundman et al.). Channels without an exported model are
silently skipped — this is how the API degrades without ONNX models mounted (see
`backend/Dockerfile`).

**Measured:** on the varying-signal channels, the forecaster alone scores **F1 0.40**, below L1's
0.55 and above an Isolation Forest baseline (0.12); the L1+L2 union is 0.57, within noise of L1
alone. Trained for a 15-epoch cap versus the paper's 35 — a fully trained forecaster is untested.
Not a clear win yet; see `EVALUATION.md` → *Real data: L2 forecaster and baselines*.

## L3 — cross-channel physics (`detection/physics.py`, synthetic bus only)

Two invariants the synthetic bus obeys that no single-channel detector can see:

- **`RedundantSensors` (`l3.redundant_sensors`)** — `batt_v_a`/`batt_v_b` and `rw1_vib_a`/
  `rw1_vib_b` should track each other. Emits *shape* evidence (a jump vs. a ramp vs. a change in
  noise character, and which side moved) because the interesting question for the spoofing-vs-drift
  pair is not "do they disagree" but "how."
- **`PowerBalance` (`l3.power_balance`)** — `batt_i - (solar_i - load_i)` should be ~0; a nonzero
  residual is a lying current sensor or a hidden load.

Real-only in the sense that this layer needs the synthetic EPS/ADCS bus (ADR 0004); it does not
run on real SMAP/MSL replay, which has no redundant channel pairs.

## L4 — protocol / security (`detection/protocol.py`)

Never looks at a telemetry *value* — independent of channel semantics, so it runs unchanged on
every family. Attacker-controlled strings (opcode names, sources, log text) flow only through
`Evidence` fields, never into the templated `explanation` sentence, on the same principle the AI
agent's tools use (ADR 0008): free text is data, never something downstream code interpolates into
a decision.

| Detector | Name | Catches |
|---|---|---|
| `SequenceIntegrity` | `l4.sequence_integrity` | Gaps, duplicates and regressions per APID, handling the 14-bit sequence-count wrap |
| `TimestampFreshness` | `l4.timestamp_freshness` | Stale (replay/delay) or future-dated frames |
| `AuthTagIntegrity` | `l4.auth_tag_integrity` | Failed HMAC tags and structurally malformed frames |
| `CommandPolicy` | `l4.command_policy` | Whitelist, authentication, contact-window, source and rate checks on telecommands |
| `AuthAnomaly` | `l4.auth_anomaly` | Failed-authentication bursts, unknown sources, off-window logins |
| `LinkShift` | `l4.link_shift` | SNR drop, BER rise, latency/loss shift versus the calibrated nominal link |
| `RateAnomaly` | `l4.rate_anomaly` | Frame-rate flood (DoS) or drop (dropout), plus queue depth |

This is the layer that separates **replay** (sequence regression + stale timestamp, but a *valid*
tag — it's a real old frame) from a **stuck sensor** (normal sequence, fresh timestamp, flat
value): the two look identical to L1 alone.

## L5 — environmental (`detection/environment.py`, second-stage over other detectors' outputs)

- **`SpaceWeatherOverlap` (`l5.weather_overlap`)** — notes when a fired L1/L3 anomaly overlaps a
  cached real NASA DONKI event (flare, SEP, geomagnetic storm), with a lead/lag window (`LEAD_S`/
  `LAG_S`) for timing uncertainty. Says nothing when there's no event — and whether that absence is
  trustworthy is a separate fact (`weather_context`), because the DONKI cache may simply not cover
  the session rather than the window genuinely being quiet.
- **`SimultaneousUpset` (`l5.simultaneous_upset`)** — the single-event-upset signature: several
  unrelated channels, in more than one subsystem, glitching at the same instant.

## What the ablation says about which layers actually matter

`EVALUATION.md` reports a real ablation (zero one layer's features, retrain, re-score the same test
split) rather than asserting layer importance:

| Configuration | Forced-choice accuracy |
|---|---|
| All layers | 86.4% |
| Without L3 physics/redundancy | 82.9% (largest drop) |
| Without L1 statistical | 83.4% |
| Without L5 environment | 84.4% |
| Without L4 protocol/security | 84.9% (smallest drop) |

L3 physics/redundancy matters most to the *attribution* decision in this evaluation — not
surprising, since it's the layer built specifically to separate the hardest confusable pair
(spoofing vs. drift). L4 matters least to attribution specifically because its detections are
already close to unambiguous on their own (a failed auth tag is strong evidence by itself); it
still matters for coverage on attacks L1/L3 can't see at all (command/link/auth attacks). All
differences here are within a few points of each other and of the differences reported as noise
elsewhere in `EVALUATION.md` (test split: 199 windows) — read this as "no single layer is
redundant," not as a precise ranking.

## The confusable pairs (the actual point of the layering)

| Pair | Looks alike because | What separates them |
|---|---|---|
| Sensor spoofing ↔ malfunction/drift | one redundant sensor diverges, values plausible | L3 disagreement *shape* (attacker-synthesized values are too clean/step-like) — **still the unsolved hard case**; see below |
| Replay ↔ stuck sensor | values repeat/stay flat | L4 sequence regression + stale timestamp (replay) vs. fresh timestamp + normal sequence (stuck) |
| SEU ↔ injected manipulation | abrupt value jump | L5 DONKI-window overlap + multi-channel simultaneity (SEU) vs. off-window + possible L4 tag failure (manipulation) |
| Battery degradation ↔ sensor drift | slow drift | L3: real degradation shows in *both* redundant sensors and satisfies current-integration; sensor drift is one-sided |
| DoS ↔ jamming ↔ dropout | data stops arriving | L4 rate↑/queue↑ (flood) vs. SNR↓/BER↑ (jamming) vs. one channel silent, link healthy (dropout) |

**Not solved:** slow, in-limits, single-sensor drift (spoofing, sensor drift, a low-and-slow
manipulation, a slow ramp) still looks the same to the current features — the platform correctly
abstains (`needs_human`) instead of guessing, but abstaining is not separating them. This is stated
in `docs/LIMITATIONS.md` and is the honest headline limitation of the whole project, not a detail.

## Incident building and attribution

Fired outputs are grouped into an incident by `IncidentBuilder`: opens on the first fire, extends
under hysteresis (closes after a quiet run with nothing firing), re-attributed as evidence arrives
and once more on close. `sentinel_core/attribution/` turns the incident's evidence into a posterior
over five classes plus `needs_human` — see [ADR 0006](adr/0006-attribution-model.md) for the model
and `EVALUATION.md` for what it actually achieves (86.4% forced-choice, 95.1% on the 61.8% of
windows it chooses to answer).
