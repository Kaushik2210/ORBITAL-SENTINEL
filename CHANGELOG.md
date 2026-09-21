# Changelog

All notable changes are documented here. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning: [SemVer](https://semver.org/).

## [Unreleased]

### Added
- Project bootstrap: monorepo layout, tooling (ruff, mypy strict, pytest, pre-commit), CI, contributor docs.
- Dataset acquisition (`make data`): checksummed manifest, source fallback chain, `lite`/`full` profiles.
- `docs/DATASETS.md` with measured shapes, counts, licensing and surprises for SMAP/MSL, PCoE battery/bearings and DONKI.
- CCSDS-like packet codec with HMAC tag, typed events, async ingestion, mission replay engine with injection hooks.
- Synthetic physics bus (EPS + reaction wheels, redundant sensors) driven by real PCoE/IMS trajectories.
- DONKI client with permanent cache; loaders for SMAP/MSL and PCoE with labeled fallbacks.
- Incident taxonomy (`sentinel_core.taxonomy`): five classes plus an explicit `needs_human` verdict.
