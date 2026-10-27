# bridge-island

[![CI](https://img.shields.io/badge/CI-placeholder-lightgrey)](#)
[![DOI](https://img.shields.io/badge/DOI-placeholder-lightgrey)](#)
[![PyPI](https://img.shields.io/badge/PyPI-placeholder-lightgrey)](#)

A small Python reference implementation of the **bridge/island integration architecture**.
A single mediation layer, the **Bridge**, sits between an immutable, externally certified
host platform (the **Baseline Island**) and independently owned, independently evolving
mission systems (the **Unique Island**). Every cross-domain data exchange goes through the
Bridge. The package simulates the pattern on invented platforms and compares it with
point-to-point integration.

> All platforms, messages, fields, rates and numbers are invented. Experiment outputs are
> synthetic and illustrative. This is not flight or medical software and is not certified.

## Quick start

```powershell
uv sync --all-groups
uv run bridge-island demo aviation
uv run bridge-island experiment change-impact --seed 1
```

The demo runs an invented aviation example on two baseline interface versions and compares\nwhat the change costs with point-to-point integration. Other commands:\n\n```powershell\nuv run bridge-island demo medical\nuv run bridge-island codegen --spec examples/aviation_invented/mapping_v2.yaml\nuv run bridge-island experiment topology|change-impact|timing|containment [--seed N] [--out DIR]\nuv run bridge-island check-rules      # runs the G1-G6 rule checks on an example\n```\n\nDocumentation: `uv run mkdocs serve` (concepts, a 15-minute tutorial, examples, experiments,\nlimitations, related work, API).

## Status

Alpha. Two invented examples (aviation, medical), each with a baseline interface v1 and v2;\nfour reproducible experiments; tests for the six design guidelines G1-G6. See the\n[CHANGELOG](CHANGELOG.md). Not flight or medical software; not certified.

## License and citation

Apache-2.0. See [LICENSE](LICENSE), [NOTICE](NOTICE) and [CITATION.cff](CITATION.cff).
