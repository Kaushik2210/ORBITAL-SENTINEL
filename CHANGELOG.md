# Changelog

All notable changes are documented here. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning: [SemVer](https://semver.org/).

## [Unreleased]

### Added
- Project bootstrap: monorepo layout, tooling (ruff, mypy strict, pytest, pre-commit), CI, contributor docs.
- Dataset acquisition (`make data`): checksummed manifest, source fallback chain, `lite`/`full` profiles.
- `docs/DATASETS.md` with measured shapes, counts, licensing and surprises for SMAP/MSL, PCoE battery/bearings and DONKI.
- Incident taxonomy (`sentinel_core.taxonomy`): five classes plus an explicit `needs_human` verdict.
