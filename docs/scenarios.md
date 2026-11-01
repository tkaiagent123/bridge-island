# Scenarios

A scenario is one YAML file that describes one reproducible run: which example, which baseline
version, how long, what the Bridge's settings are, and what happens during the run. You can explore
the behaviour of the Bridge without writing Python. This is also the recommended way to share a
run: the file is the whole experiment.

```powershell
uv run bridge-island run examples/scenarios/stall_and_resume.yaml
```

## Format

```yaml
name: stall_and_resume
description: The Bridge stalls for 1.5 s. The health monitor annunciates it.
example: aviation            # required: aviation | medical
duration_s: 8                # required: simulated seconds
version: 1                   # baseline interface version (default: the first)
seed: 1                      # default 1
bridge:                      # optional Bridge settings
  time_tag: source           # source (G3) | delivery (the naive error)
  rate_policy: cap           # cap | extrapolate
  processing_delay_s: 0.001
  max_extrapolation_s: 0.2   # default: 1.5 source periods
events:                      # optional, each with a time `at` and exactly one action
  - {at: 2.2, stall: 1.5}                       # stall the Bridge for 1.5 s
  - at: 1.0                                     # a subsystem sends a message toward the baseline
    send: {from: PlanningDisplay, message: show_page, values: {page: MAP, priority: 1}}
```

Unknown keys, a time after the end of the run, a missing field, a sender that is not a subsystem
and a baseline version the example does not have are all rejected with a message that says what to
fix. A `send` may name any message, valid or not. That is how you inject a fault.

## Shipped scenarios

| File | What it shows |
| --- | --- |
| `stall_and_resume.yaml` | The Bridge stalls for 1.5 s. The health monitor reports `bridge_stall`, and the 1 Hz message held through the stall arrives visibly old (flagged *late*). |
| `naive_time_stamping.yaml` | The same stall with delivery-time stamping, the error G3 forbids: nothing is flagged late, because the old data looks new. Compare the `late` column with the file above. |
| `baseline_v2_removal.yaml` | Baseline v2 removes an element. The subsystem that used it is told the input is unavailable. |
| `extrapolate_rate_policy.yaml` | A 50 Hz subsystem fed by a 20 Hz source, with estimates filling the gaps. |
| `fault_injection.yaml` | A faulty add-on module in the medical example sends one valid request and three bad ones (out of range, an unauthorized message type, a baseline-format message). The Bridge blocks the three, and the baseline sees none. |

## Outputs

```powershell
uv run bridge-island run SCENARIO.yaml --json          # the summary and the manifest on stdout
uv run bridge-island run SCENARIO.yaml --trace t.csv   # every delivered datum
uv run bridge-island run SCENARIO.yaml --out results/  # summary.json, manifest.json, trace.csv
```

The **trace** has one row per datum a subsystem received: `subsystem`, `input`, `source_time`,
`basis_time` (set for estimates), `delivered_at`, `age_s` and `estimated`. Its first line is
`# synthetic, illustrative`.

The **manifest** records what is needed to reproduce the run: the package and Python versions, the
resolved scenario (seed, baseline version, Bridge settings, events) and the SHA-256 of the mapping
spec in use. It contains no wall-clock time, so running the same scenario twice gives the same
manifest, summary and trace, byte for byte.
