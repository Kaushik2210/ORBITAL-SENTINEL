# 3. Dataset source chain, trust model and labeled fallbacks

Date: 2026-09-20 · Status: accepted

## Context
Phase 1 probing showed the brief's primary SMAP/MSL source (the Telemanom S3 archive) now returns HTTP 403.
The PCoE data has no formal license, the bearing archive is a 1 GB solid 7z of RARs, and DONKI's `DEMO_KEY`
allows only a handful of requests. `make data` must still work, and results must never present synthetic
data as real.

## Decision
1. **Ordered source chain per file** (`sentinel_sim.datasets`): upstream → pinned mirror → (Phase 3) a
   deterministic synthetic generator. The manifest records which source served each file.
2. **Trust-on-first-use checksums** in `data/raw/MANIFEST.json`, re-verified on every run; mirror
   downloads are pinned to a commit SHA so "first use" is reproducible.
3. **Profiles:** `lite` (SMAP/MSL + battery, ~380 MB) and `full` (+ 1 GB bearings).
4. **Never redistribute** real datasets or PCoE-derived files (no formal license). Only aggregate inspection
   statistics are committed (`docs/data/`).
5. **Unlabeled/ambiguous data is excluded from scoring, not guessed at:** `T-10` (no label row) is
   unlabeled; `P-2` (two rows) is scored with the union of intervals and also reported without it.
6. **Fallbacks are labeled.** Anything produced by the synthetic generator carries `synthetic = true` and
   is never reported as SMAP/MSL/PCoE performance.

## Consequences
- Depends on a third-party mirror for SMAP/MSL arrays; mitigated by pinning, checksums, and the fallback.
- The primary-archive extraction path is implemented but could not be exercised against the real archive
  (it is 403); it is covered by a test using a synthetic zip only.
- Bearing extraction is tool-dependent (RAR); handled by a multi-extractor loader plus a labeled fallback.
