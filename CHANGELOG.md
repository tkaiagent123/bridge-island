# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- Project scaffold: packaging, tooling, CI definitions, documentation skeleton,
  release-check script.
- Core domain model: elements, units table, permitted transforms, deterministic
  clock and scheduler, event log, island/link registry.
- Simplified command/response, label-word and publish/subscribe bus models.
- Baseline platform, unique subsystem base, and the Bridge (OIU, Translator Module,
  UID, health monitor, cyber monitor) composed in one `Bridge` object.
- YAML mapping spec to generated Python module (`bridge_island.codegen`).
- Invented aviation example, baseline interface v1, with tests for guidelines G1 to G6.
