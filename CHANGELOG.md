# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - TBD (set on release)

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
- Point-to-point integration module and analysis of interface counts and change impact.
- Invented medical example (baseline v1 and v2); aviation example baseline v2.
- `bridge-island` commands: `demo`, `codegen`, `experiment`, `check-rules`.
- Four reproducible experiments: topology sweep, change impact (with Monte Carlo),
  timing and continuity, fault containment.
- Documentation site: concepts, tutorial (executed by the test suite), examples,
  experiments, design rationale and limitations, related work, API reference.
- Rate policy for subsystems that can use a higher rate than the baseline sends: `cap`
  (default) or `extrapolate` (marked estimates, bounded in time). `demo --rate-policy`.
