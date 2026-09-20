# Datasets

Everything in this file was **measured** by the scripts named next to each section, run on 2026-09-20.
The raw outputs are committed in [`docs/data/`](data/) so the numbers are auditable. Where the original
project brief assumed something that turned out to be false, that is called out under **Surprises**.

Real data is never redistributed by this repository: `make data` downloads it into the gitignored
`data/raw/` and records a SHA-256 for every file in `data/raw/MANIFEST.json`.

| Dataset | Used for | Size fetched | Real or synthetic |
|---|---|---|---|
| SMAP + MSL telemetry anomalies | Operational-anomaly ground truth, nominal telemetry | 173 MB | **real** (NASA/JPL) |
| PCoE Li-ion battery aging | Battery-degradation scenario | 210 MB | **real** (NASA Ames) |
| PCoE / IMS bearing run-to-failure | Bearing-wear scenario | 1.08 GB (`full` profile only) | **real** (Univ. of Cincinnati IMS via NASA) |
| DONKI space weather | Environmental context, SEU scenario alignment | API, cached | **real** (NASA CCMC) |
| Commands, auth events, link metrics, packet headers, all attacks | Security layers and scenarios | generated | **synthetic** (always labeled) |

## 1. SMAP and MSL telemetry anomalies

**Source.** Hundman et al., *Detecting Spacecraft Anomalies Using LSTMs and Nonparametric Dynamic
Thresholding*, KDD 2018 (repo `khundman/telemanom`). Inspected with `scripts/inspect_smap_msl.py`.

**Where the bytes come from** (fallback chain implemented in `sentinel_sim/datasets/sources.py`, ADR 0003):

1. Original archive `s3-us-west-2.amazonaws.com/telemanom/data.zip` → **HTTP 403** (not public anymore).
2. Labels: `raw.githubusercontent.com/khundman/telemanom/master/labeled_anomalies.csv` (upstream).
3. Arrays: Hugging Face mirror `appleparan/telemanom`, **pinned to commit**
   `2d22e1061be83a88b7b9e48df35163d5147adc9d`. This is a third-party copy; provenance is TOFU
   (first-fetch SHA-256 recorded in the manifest), cross-checked against the label file's row counts
   (`num_values` equals the test-array length for every label row).

**Shapes and counts**

| | SMAP | MSL |
|---|---|---|
| labeled channels | 54 | 27 |
| features per array | 25 (1 telemetry + 24 command) | 55 (1 telemetry + 54 command) |
| train points | 138,004 | 58,317 |
| test points | 435,826 | 73,729 |
| anomaly sequences | 69 | 36 |

- Total anomaly sequences: **105** (62 `point`, 43 `contextual`). Length: min 11, median 121, max 4,218 points.
- 63,738 of 509,555 labeled test points are anomalous (**12.5 %**). Per channel this ranges up to 50.1 %
  (median 6.0 %), so "anomalies are rare" is not true of the per-channel test sets.
- All arrays are `float64`, no NaN/inf. Train length 312–4,308 (median 2,690), test length 670–8,640 (median 7,884).
- Anomaly intervals are inclusive `[start, end]` indices into the **test** array; all are within bounds.

**Feature layout (verified).** Column 0 is the telemetry value. Columns 1.. are binary command indicators.

**Surprises (differences from the brief or from the common description of this dataset)**

1. **Command columns are binary but *not* one-hot.** On average 84.3 % of test rows have *no* command
   active, 10.0 % have exactly one, and 5.7 % have more than one. 52 of 82 channels contain rows with
   more than one command active. Any code that assumes one-hot (e.g. `argmax`) is wrong.
2. **82 arrays but 81 labeled channels.** `labeled_anomalies.csv` has 82 rows but only 81 unique
   `chan_id`s: **`P-2` appears twice** (different intervals, same `num_values` = 8,209), and the mirror's
   array for **`T-10` has no label row at all**. `T-10` also has 55 features (the MSL layout) despite the
   SMAP-style `T-` name, and is short (train 425, test 670). We cannot tell which is a labeling error.
   **Policy:** `T-10` is fetched but treated as *unlabeled* and excluded from event-level scoring.
   `P-2` is scored with the union of both listed intervals and reported both with and without it.
3. **16 channels have a constant training signal** (`A-1, B-1, C-2, D-12, D-13, D-14, D-2, D-7, D-8, D-9,
   G-2, M-6, P-4, R-1, S-2, T-5`); 27 have at most 5 distinct training values. A forecaster or z-score fit
   on the training split alone has zero variance on these channels. Detectors must handle this explicitly.
4. **Not uniformly scaled.** Training telemetry spans −1.48…4.16, but test telemetry spans −1.42…**258.1**
   (channel `M-6`). In 43 of 82 channels the test values leave the training range, so range checks learned
   from train alone will fire on nominal test data.
5. **Channel IDs are anonymized.** Nothing here reveals real subsystem semantics. Any UI grouping of
   channels into subsystems is a *synthetic grouping* and is badged as such.

**License.** Copyright 2018 California Institute of Technology, "U.S. Government sponsorship acknowledged";
BSD-style redistribution terms (the GitHub API reports `NOASSERTION`; the Hugging Face mirror tags
`bsd-3-clause`). We do not redistribute the data; users fetch it themselves.

## 2. NASA PCoE Li-ion battery aging (power-subsystem degradation)

**Source.** B. Saha and K. Goebel (2007), "Battery Data Set", NASA Prognostics Data Repository, NASA Ames.
`phm-datasets.s3.amazonaws.com/NASA/5.+Battery+Data+Set.zip` (209.7 MB, HTTP 200). Inspected with
`scripts/inspect_pcoe_battery.py`.

- The archive is **6 nested zips** containing **34 distinct `.mat` cells** (`B0005`…`B0056`).
  `B0025`–`B0028` appear in two inner archives with byte-identical content.
- Each `.mat` holds a `cycle` array of `charge`, `discharge` and `impedance` cycles with voltage, current,
  temperature and time; discharge cycles carry `Capacity` (Ah). Example: `B0005` has 616 cycles
  (170 charge / 168 discharge / 278 impedance) and discharge capacity falls **1.856 → 1.325 Ah (−28.6 %)**.

**Surprises**

1. **Most cells are *not* clean run-to-failure aging curves.** Capacity fade (first → last discharge
   cycle, positive = capacity lost) ranges across the 34 cells from **−1,822 %** (capacity *grew*) to
   **100 %** (fell to ~zero), with a median of **5.3 %**. `B0034` *gains* capacity (0.746 → 1.280 Ah,
   fade −71.6 %). Cells were run at mixed ambient temperatures (4, 22, 24, 43, 44 °C) and load profiles.
2. **Canonical aging cells:** `B0005`, `B0006`, `B0007`, `B0018` (24 °C, 132–168 discharge cycles,
   24–42 % fade). These are the cells used for the battery-degradation scenario.
3. Some discharge cycles have a non-scalar `Capacity` (`B0050`: 4 cycles, `B0052`: 21). Loaders must skip
   those explicitly.

**License.** No formal license is stated. The repository asks that publications acknowledge the repository
and the data donors. We cite it as above, do not redistribute the raw data, and do not commit derived files.

## 3. IMS bearing run-to-failure (mechanism-wear analog)

**Source.** J. Lee, H. Qiu, G. Yu, J. Lin, and Rexnord Technical Services (2007), IMS, University of
Cincinnati, "Bearing Data Set", NASA Prognostics Data Repository. `.../NASA/4.+Bearings.zip`
(**1,075.6 MB**). Inspected with `scripts/inspect_ims.py` and the archive's own readme PDF.

**Nesting (surprise):** the zip contains one **solid 7z** (`IMS.7z`), which contains **three RAR archives**
(`1st_test.rar` 367 MB, `2nd_test.rar` 86 MB, `3rd_test.rar` 609 MB) plus a readme PDF. Extracting needs both
`py7zr` and a RAR-capable tool; on Windows `tar.exe` (bsdtar/libarchive) reads RAR, elsewhere
`bsdtar`/`unrar`/`7z` are needed. The loader tries several extractors and falls back to a labeled-synthetic
bearing model if none exist.

**Set 2 (the one we use, per the readme):** 984 snapshot files, one per 10 minutes, 2004-02-12 10:32:39 →
2004-02-19 06:22:39; each file is 1 second at **20 kHz = 20,480 rows × 4 channels** (one accelerometer per
bearing), ASCII, 519 MB extracted. Rig: 2,000 RPM, 6,000 lb radial load. **Outer-race failure in bearing 1.**

**Measured degradation.** RMS of the failing bearing (channel 1) rises **2.80×** between the first and
last 10 % of snapshots (0.078 → 0.218), and its kurtosis rises 3.46 → 4.67. The healthy bearings also drift
(RMS ×1.29–1.57), so RMS ratio alone is *not* a clean separator; this is why the wear scenario uses several
features and a per-bearing baseline.

**License.** As for §2 (acknowledgement requested; no redistribution).

## 4. NASA DONKI space-weather API

**Source.** `https://api.nasa.gov/DONKI/{FLR,CME,GST,SEP,IPS}` with `startDate`/`endDate`/`api_key`.
Inspected with `scripts/inspect_donki.py`, window 2024-05-01…2024-05-31 (the May 2024 solar storm period).

| Endpoint | Events in window | Time field | Notes |
|---|---|---|---|
| FLR | 181 | `peakTime` | classes C4.8 … **X8.7** |
| CME | 144 | `startTime` | |
| GST | 5 | `startTime` | max Kp = 9.0 |
| SEP | 14 | `eventTime` | |
| IPS | 14 | `eventTime` | |

- Times are UTC strings like `2024-05-01T06:57Z`. Each event carries `linkedEvents` (e.g. a CME linked to a flare).
- **Rate limit (measured): `DEMO_KEY` returned `X-RateLimit-Limit: 10`.** Five requests brought
  `X-RateLimit-Remaining` from 8 to 4. This is much tighter than the "30/hour" often quoted, so the client
  (Phase 3) caches every response on disk permanently, uses wide date windows, and supports a user key via
  `NASA_API_KEY`.
- The API is a live service: results for a past window can be revised. Cached responses are stamped with
  the fetch time.

**License.** NASA-produced data; NASA API terms apply. Cite CCMC/DONKI.

## 5. Optional / not yet verified

- **DSN Now** `eyes.nasa.gov/dsn/data/dsn.xml` responds (HTTP 200, 7,197 bytes); content not yet inspected.
- **JPL Horizons** rejects `HEAD` (405); a `GET` has not been tested yet. Both are stretch goals and the
  platform must run without them.

## 6. Synthetic data

Commands, authentication events, link metrics (SNR, BER, latency, packet loss), packet headers and sequence
counters, and every attack are **synthetic**, generated from documented, seeded statistical models.
They carry `synthetic = true` in the database, API and UI. They must never be described as measurements of
a real spacecraft. If a real-data download fails, a fully synthetic telemetry generator is used instead and
everything derived from it is labeled `synthetic` (implemented in Phase 3).

## Reproducing this document

```bash
python scripts/tasks.py data                      # lite: SMAP/MSL + battery
DATA_PROFILE=full python scripts/tasks.py data    # adds the 1 GB bearing archive
uv run python scripts/inspect_smap_msl.py
uv run python scripts/inspect_pcoe_battery.py
uv run python scripts/inspect_ims.py --dir <extracted 2nd_test dir> --failing-channel 0
uv run python scripts/inspect_donki.py            # uses ~5 of the DEMO_KEY's rate-limit budget
```
