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

(The `demo` and `experiment` commands arrive in later phases; see the [CHANGELOG](CHANGELOG.md).)

## Status

Alpha scaffold. The design is described in the project documentation (`docs/`).

## License and citation

Apache-2.0. See [LICENSE](LICENSE), [NOTICE](NOTICE) and [CITATION.cff](CITATION.cff).
