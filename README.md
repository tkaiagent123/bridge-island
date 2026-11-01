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

## Statement of need

Systems that must work with a host platform they cannot change, because another authority
certifies it, face a recurring problem: every independently owned system carries its own
knowledge of the platform's interface, so each platform change reaches many teams. The
bridge/island architecture puts that knowledge in one mediation layer.

bridge-island is a runnable, openly licensed implementation of that pattern, with a
point-to-point comparison, reproducible experiments and tests for six stated design guidelines.
It is for systems engineers who want to see the pattern work and try changing it, instructors
and students who need a small example that runs on a laptop, and researchers who study interface
architecture and want something to run, extend and cite. It is a teaching and research aid, not
a tool for building certified systems.

## Installation

You need Python 3.10 or later and [uv](https://docs.astral.sh/uv/). The examples live in the
repository, so work from a clone:

```powershell
git clone <repository-url>
cd bridge-island
uv sync --all-groups
uv run bridge-island --version
```

Without uv: `python -m venv .venv`, activate it, then `pip install -e ".[plots]"` (the `plots`
extra adds matplotlib for the experiment plots). Run the tests with `uv run pytest`.

## Example usage

```powershell
uv run bridge-island demo aviation                   # run it, compare with point to point
uv run bridge-island describe aviation --version 2   # every path from baseline to subsystem
uv run bridge-island diff aviation                   # what the baseline v1 -> v2 change touches
uv run bridge-island run examples/scenarios/stall_and_resume.yaml
uv run bridge-island report aviation --out report.html   # one offline HTML file
uv run bridge-island check-rules                     # the guideline checks, pass or fail
uv run bridge-island experiment change-impact --seed 1
```

The demo runs an invented aviation example on two versions of the baseline interface. When the
baseline changes, the change lands in one mapping file. When it removes an element, the one
subsystem that used it is told, instead of failing silently. The same change on point-to-point
wiring breaks silently. Every command accepts `--help`; `list`, `demo`, `describe`, `diff`,
`run` and `check-rules` also accept `--json`. The [command-line
reference](docs/cli.md) has the details, and the 15-minute tutorial (`docs/tutorial.md`) adds a
new subsystem to the example.

Documentation: `uv run mkdocs serve` (concepts, tutorial, examples, command-line reference,
scenarios, experiments, limitations, related work, API).

## Status

Alpha. Two invented examples (aviation, medical), each with a baseline interface v1 and v2;
four reproducible experiments; scenario files; tests for the six design guidelines G1-G6. See the
[CHANGELOG](CHANGELOG.md). Not flight or medical software; not certified.

## Community

Contributions, bug reports and questions are welcome: see [CONTRIBUTING](CONTRIBUTING.md) (it
explains the rule that all content must be invented), the issue templates in `.github/`,
[CODE_OF_CONDUCT](CODE_OF_CONDUCT.md) and [SECURITY](SECURITY.md). The use of AI assistance in
this project is disclosed in [docs/AI_USAGE.md](docs/AI_USAGE.md).

## License and citation

Apache-2.0. See [LICENSE](LICENSE), [NOTICE](NOTICE) and [CITATION.cff](CITATION.cff).
