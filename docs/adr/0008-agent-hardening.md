# 8. Investigation agent: read-only tools and prompt-injection hardening

Date: 2026-09-20 · Status: accepted

## Context
Command names, hostnames and log free-text come from telemetry and are attacker-controlled. An LLM agent that
reads them can be steered by instructions embedded in them.

## Decision
1. Tools are strictly read-only; there is no tool that writes, sends commands or changes state.
2. Telemetry-derived strings are passed only inside a JSON data block with explicit delimiters and a system
   instruction that block contents are untrusted data; they are never interpolated into instructions.
3. The final report is validated against a Pydantic schema; on failure the loop retries once, then falls back
   to the deterministic report.
4. A test feeds an auth-log entry whose text tries to instruct the agent and asserts the report and tool calls
   are unaffected.
5. With no API key, a deterministic template report is generated from the same evidence.

## Consequences
- Injection is mitigated, not eliminated: hardening reduces risk but the read-only tool surface is the real
  safety boundary.
- The live-LLM path is unit-tested against a stub client and is reported as not end-to-end verified unless a key
  is supplied.
