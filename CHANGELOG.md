# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.2] - 2026-11-09

### Added
- An in-browser version of the app (Python runs in your browser with Pyodide; nothing is uploaded) at <https://tkaiagent123.github.io/bridge-island/online/index.html>, built by `scripts/build_web.py` in the docs workflow.

## [0.1.1] - 2026-11-09

### Added
- The documentation site is published at <https://tkaiagent123.github.io/bridge-island/> and rebuilt on every push to `main`.
- The Zenodo concept DOI (`10.5281/zenodo.24069113`, all versions), the documentation URL and the release date are in `CITATION.cff` and the README.

### Changed
- The app has a new look: a colour system with light and dark themes, an app bar and tabs, a home page in three numbered steps (choose a platform, decide what happens, run it), and restyled result dashboards.

### Fixed
- `--no-color` is accepted after the command as well as before it (`bridge-island demo aviation --no-color`).

## [0.1.0] - 2026-11-08

### Added
- The app result page is now a dashboard: headline tiles, a status board with average and peak age bars on one scale (status is an icon plus a word), the age charts, and a count of what the Bridge noticed.
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
- Scenario files and `bridge-island run`: one YAML file per reproducible run, with stall and fault
  events, JSON output, a trace CSV and a manifest with no wall-clock times.
- `bridge-island list`, `describe` (text, Markdown, Mermaid, JSON), `diff` (the real mapping change,
  measured) and `report` (one self-contained, offline, accessible HTML file).
- `--json` on the main commands (JSON on stdout, messages on stderr), `--no-color` and `NO_COLOR`,
  documented exit codes, errors rewritten for humans, help that leads with examples.
- README: statement of need, installation, example usage, community.
- A point-and-click app that opens in the browser (`bridge-island ui`, or double-click `Start-bridge-island.bat`): forms for runs, faults and stalls, scenario files, reports, guideline checks and experiments, with plain-English results and downloads. Local only, standard library only.
- `python -m bridge_island`.

- Age-of-information measures: average age over time, peak age, and time spent with data older than
  the subsystem tolerates, alongside the age at arrival; shown in the summary, report and app.
- Strict YAML loading for specs and scenarios: duplicate keys, anchors and aliases are rejected.
- `scripts/mutation_check.py`: mutation testing without extra dependencies.
- Adversarial tests: fuzzing of every parser, hostile YAML, malformed HTTP, and system-level
  invariants over random scenarios.
- Related work: anti-corruption layer, age of information, temporal-database vocabulary, design
  structure matrices, medical-device and open-avionics integration (verified citations).

### Fixed
- The local app allowed inline scripts; each page now carries a one-time nonce, and pages without a script allow none.
- The `mmHg` constant used the torr value; it now uses conventional mercury (regenerated mappings).
- Health annunciations now say which message they are about.
- A subsystem could go stale after a Bridge stall because the rate cap was measured in arrival time
  instead of source time.
- `extrapolate` produced no estimates for sources slower than about 5 Hz; the default limit is now
  1.5 source periods.
- Two copies of the app could bind the same port on Windows; the port is now exclusive and the next free one is used.
- `duration_s: .inf` (or NaN, or a huge value) hung or exhausted the simulator; scenarios are now
  validated as finite and at most one hour of simulated time.
- Non-text values in scenario fields (for example a list as the example name) crashed with a raw
  `TypeError`; they now produce a message that says what to fix.
- Unknown or malformed transforms in a spec (a bad unit, a one-element unit pair, a string where a pair
  was expected, a reversed range) crashed or were silently misread; they are now `CodegenError`s.
- A mapping that referred to a subsystem that was never attached crashed on the first datum; the
  Bridge now refuses to start and names it.
- Linear extrapolation across an angle wrapping round (359 to 1 degrees) is no longer attempted.
- The app: unexpected errors return a page and are logged instead of dropping the request; an invalid
  or oversized `Content-Length` is refused at once; connections time out; pasted YAML is parsed strictly.
- The mapping generator wrote its output before building it completely; a failed build no longer
  leaves a partial module.
