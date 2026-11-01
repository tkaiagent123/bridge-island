# Command-line reference

Every command works from a checkout of the repository (the examples live in `examples/`). All
content is invented and all numbers are synthetic, illustrative. Run `bridge-island --help` or
`bridge-island <command> --help` for the options.

| Command | What it does |
| --- | --- |
| `list` | Lists the examples, their baseline versions and subsystems, and the shipped scenarios. |
| `demo EXAMPLE` | Runs an example on both baseline versions, then compares what the change costs with point to point. |
| `describe EXAMPLE` | Shows the baseline interface, the unique systems and every mapped path, as text, Markdown, Mermaid or JSON. |
| `diff EXAMPLE` | Shows what a baseline version change touches, measured on the real mapping files. |
| `run SCENARIO.yaml` | Runs one reproducible scenario described in YAML. See [Scenarios](scenarios.md). |
| `report EXAMPLE` | Writes one self-contained HTML report. |
| `check-rules` | Runs the checks for guidelines G1 to G6 and prints pass or fail. |
| `experiment NAME` | Runs one of the four [experiments](experiments.md), writing CSV and plots. |
| `codegen --spec FILE` | Compiles a YAML mapping spec to a Python module (G5). |

## A short tour

```powershell
uv run bridge-island list
uv run bridge-island demo aviation
uv run bridge-island describe aviation --version 2
uv run bridge-island diff aviation
uv run bridge-island run examples/scenarios/stall_and_resume.yaml
uv run bridge-island report aviation --out report.html --stall 2.2,1.5
```

## `describe`

```text
$ bridge-island describe aviation --version 2
aviation: baseline interface v2 (all content invented; numbers are synthetic, illustrative)
13 published elements, 4 unique subsystems, 18 paths: 10 identity, 7 converted, 1 removed
...
  NavigationState.altitude         ->  PlanningDisplay.nav_alt_m         identity
  NavigationState.altitude         ->  StatusLogger.altitude_ft          unit m->ft
  AirData.baro_altitude_ft         ->  CueingProcessor.baro_alt_m        REMOVED: element removed ...
```

`--format mermaid` prints a flowchart you can paste into any Mermaid renderer, including the
diagram support in this documentation. `--format json` gives the same data for scripts.

## `diff`

`bridge-island diff aviation` lists the interface changes between two baseline versions and then
shows the change on the Bridge as it actually is: the number of substantive lines that change in the
mapping spec (comments and blank lines are not counted), and the unified diff. For the aviation
example the measured change is +11 / -8 lines in one file, and the only unique system affected is
the one that used the removed element. The modelled P2P-versus-Bridge counts follow, with the
caveats in [Experiments](experiments.md#2-change-impact). `--from` and `--to` choose the versions;
`--full` prints the whole diff.

## `report`

Writes one HTML file that you can open offline, e-mail or print. It contains the architecture, the
G1 to G6 results for every baseline version, a table of what each unique system received, a chart
per system of the age of its data, the full mapping with identity and converted paths marked, the
Bridge's event log, and what the version change touches. Options: `--seed`, `--duration`,
`--rate-policy cap|extrapolate`, and `--stall AT,DURATION` to stall the Bridge during the run.

The file has no scripts and loads nothing from the network. Charts are inline SVG with a title
and a text description, and they use marker shapes as well as colour (hollow markers are
estimates). The page follows the reader's light or dark setting, and the report is deterministic: the same options, with the same package and Python versions, give the same file.

## Machine-readable output

`list`, `demo`, `describe`, `diff`, `run` and `check-rules` accept `--json` (or `--format json`).
JSON goes to **stdout** and nothing else does: messages such as `wrote summary.json` go to
**stderr**, so the output can be piped straight into other tools.

```powershell
uv run bridge-island run examples/scenarios/fault_injection.yaml --json | python -m json.tool
```

## Colour

`PASS` and `FAIL` are coloured only when the output is a terminal. Colour is switched off by
`--no-color`, by the `NO_COLOR` environment variable, by `TERM=dumb`, and when output is piped or
redirected. `FORCE_COLOR=1` switches it on.

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Success. |
| 1 | A check failed: a guideline rule did not pass, or the code generator rejected a spec. |
| 2 | Invalid input or usage: a bad scenario file, an unknown version, a bad option. The message says what to fix. |

## Rate policy

`demo`, `report` and scenario files accept a rate policy for subsystems that can use a higher rate
than the baseline sends: `cap` (the default) or `extrapolate`. Neither policy gives the subsystem
what it asked for; see [Concepts](concepts.md#data-age-rates-and-what-the-bridge-does-not-do).
