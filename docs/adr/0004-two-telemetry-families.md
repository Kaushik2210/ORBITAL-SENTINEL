# 4. Two telemetry families: real SMAP/MSL replay and a synthetic physics bus

Date: 2026-09-20 · Status: accepted

## Context
Phase 1 confirmed SMAP/MSL channel IDs are anonymized. There are no units, no redundant sensors and no
physical relations between channels, yet the brief's L3 checks (redundant-sensor disagreement, power balance)
and its hardest confusable case (spoofing vs malfunction) need exactly those.

## Decision
Run two families side by side. **Real** SMAP/MSL replay carries L1/L2 evaluation on real operational anomalies.
A **synthetic physics bus** (EPS + reaction wheels) provides redundant sensors and physical relations for L3
and for every scenario class. The bus is *driven by real PCoE trajectories* (battery capacity/resistance from
B0005/6/7/18; bearing vibration from IMS test 2) so the degradation signals are real even though the telemetry
around them is synthetic. Every synthetic channel is flagged `synthetic`; SMAP/MSL "subsystem" grouping is a
labeled synthetic grouping.

## Consequences
- L3 and attribution are evaluated on synthetic scenarios only; docs and UI must say so.
- Real-data metrics (event-level SMAP/MSL) come from L1/L2 only.
- A parametric aging model stands in (still labeled) if PCoE/IMS data is unavailable.
