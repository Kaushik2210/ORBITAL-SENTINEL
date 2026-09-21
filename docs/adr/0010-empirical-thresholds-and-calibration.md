# 10. Detector thresholds are empirical, learned in a calibration pass

Date: 2026-09-21 · Status: accepted

## Context
Phase 1 showed the real data breaks Gaussian assumptions: 16 SMAP/MSL channels have a constant training
signal, 43 of 82 have test values outside the training range, and the synthetic bus has orbital cycles,
bimodal payload bursts and slow state-of-charge drift. A fixed "k sigma" rule either floods operators with
false alarms or is blind. Early probing confirmed it: a plain CUSUM on battery voltage fired 871 times
on a nominal 3,000-step run.

## Decision
1. **Calibration pass** (`DetectionEngine.calibrate`): detectors `learn` from a nominal stream, then `freeze`,
   like mission commissioning. Scenarios are calibrated on a *different seed* from the one they are scored on.
2. **Empirical alarm levels:** each statistic's alarm level is a margin times the largest value seen in
   calibration (1.25x for level statistics, 1.5x with a floor of h = 10 sigma for CUSUM).
3. **Seasonal baselines** (per-phase median over the 90-step orbit) for periodic channels.
4. **Detrended CUSUM and rate-of-change:** they run on the residual minus an outlier-gated EWMA reference.
   They catch abrupt persistent shifts and deliberately ignore slow drift, which is left to L3.
5. **Start-up inhibit:** alarms are suppressed for the first N seconds (`inhibit_s`), as real ground monitors
   do during acquisition; baselines keep updating.
6. **Redundant-sensor "which one is wrong"** uses each sensor's own jump history, and reports no preference
   for slow ramps instead of guessing.

## Consequences
- Thresholds depend on calibration length and content; tuning must use validation seeds that are not the
  evaluation seeds, and every reported metric states its profile and seeds.
- A very slow in-limits drift in a single channel can evade L1 by design; L3 catches it only where a redundant
  sensor or physical relation exists (synthetic bus). On SMAP/MSL such drift is a known blind spot.
- A tiny false-alarm rate remains (CUSUM excursions); it is measured and reported, not hidden.
