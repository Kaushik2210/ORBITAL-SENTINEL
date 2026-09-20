# Security Policy

## Scope: this is a defensive simulation

Orbital Sentinel simulates attacks (telemetry manipulation, command injection, replay, spoofing, DoS, …)
**only against local synthetic or replayed data**. It does not connect to, probe, or attack any real
spacecraft, ground station, or third-party system, and contributions that do so will be rejected.

## Supported versions

Pre-1.0: only the latest commit on `main` is supported.

## Reporting a vulnerability in the platform itself

Vulnerabilities in the platform (auth, API, frontend, agent prompt-injection handling, dependency issues) are in scope.

Please report privately via GitHub's
[private vulnerability reporting](https://github.com/Kaushik2210/ORBITAL-SENTINEL/security/advisories/new)
rather than a public issue. Include reproduction steps, affected component, and impact.

You can expect an acknowledgement within 7 days. This is a volunteer project, so fix timelines are best-effort.

## Out of scope

- The simulated frames, keys and credentials in this repository are synthetic and intentionally weak
  (for example the demo HMAC key in `.env.example`). They protect nothing real.
- Findings that require attacker control of the local machine running the demo.
