# 5. CCSDS-like packet layer with an HMAC authentication tag

Date: 2026-09-20 · Status: accepted

## Context
Protocol-level detection (sequence, timestamp, authentication) needs a realistic frame format. CCSDS Space
Packet Protocol defines the primary header; real missions layer security (SDLS) separately.

## Decision
Implement the CCSDS 133.0-B primary header (version, type, secondary-header flag, 11-bit APID, sequence flags,
14-bit sequence count, length), an 8-byte timestamp, a float32 payload, and a 16-byte tag = HMAC-SHA256
truncated, over header+timestamp+payload. Verification uses `hmac.compare_digest`. The key is synthetic.
Scenarios declare `attacker_has_key`.

## Consequences
- Replayed frames carry valid tags, so replay is caught by sequence/time, not by authentication.
- Manipulation by a keyless attacker breaks the tag; a keyed attacker is invisible to L4 and must be caught by
  L1-L3. This produces genuinely hard cases.
- The 14-bit counter wraps at 16,384; detectors must not read a wrap as a regression.
- Not SDLS, and not a security claim about real links; documented as a simulation.
