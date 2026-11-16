# Insulin-loop example: design note

Synthetic, illustrative. Not medical software, not a validated patient model or dosing algorithm,
not for clinical use. This note maps the Generic Glycemic Loop (GGL, `insulin-loop`) onto the
existing classes and records every place where the brief and the code disagreed.

## Mapping onto the existing classes

| Brief | Code |
| --- | --- |
| Controller Host (Baseline Island) | `BaselinePlatform`. Its published messages come from `sources`, and here the sources are the controller's `command()` and `status()` (`clinical/controller.py`). The controller reads the platform's `accepted` list, so the host stays an unmodified `BaselinePlatform`. |
| Host interface v1, v2 | `world.interface(version)`. v2: one rename (`ControllerStatus.iob`), one unit change (`DoseCommand.basal` U/h to mU/min), one added element (`trend`), one removed (`loop_mode`), as GIHP does. |
| SensorA, SensorB, PumpA, PumpB v2, CompanionDisplay (Unique Island) | `UniqueSubsystem` subclasses in `clinical/devices.py`. The sensors have outputs only, so they reach the host through mapping uplinks. The pumps take DoseCommand fields as needs and confirm through an uplink. The display takes ControllerStatus fields and acknowledges alarms. |
| Bridge | The existing `Bridge` (OIU, TM, UID, health, cyber), plus a `ContractMonitor` partition (`bridge/contract.py`) that acts only through three callbacks the Bridge owns (G6). |
| Environment | `clinical/physiology.py`, outside both islands. The sensors sample it and the pumps drive it. |
| direct | `p2p.direct.DirectAdapter` per device (a small subclass, `PortAdapter`, lets a new device be plugged into an old port). The adapters use the same generated mapping as the Bridge, so they have the same range checks. |

## Reused as is
Codegen and G5, transforms, the units table, `Scheduler`, the three bus models, `BaselinePlatform`,
`UniqueSubsystem`, `DirectAdapter`, the `check_rules` G1 to G6 checks, the change-impact counting
model, and the CSV and report conventions.

## Extended, with the reason
* **Bus frame rate** (`BaselinePlatform.minor_hz`, `UniqueSubsystem.bus_minor_hz`) and **health scan
  period** (`Bridge(health_period_s=...)`). The defaults stay at 100 Hz and 0.1 s. At those defaults a
  simulated day is more than 8 million bus frames, and the loop runs in minutes, so GGL uses 30-second
  frames.
* **`Bridge(contract=...)`**: an optional partition. `Bridge.PARTITIONS` is unchanged, and a Bridge
  built with a contract lists it in `bridge.partitions`.
* **Units**: mg/dL and mmol/L (glucose, factor 18.0182), U, U/h and mU/min. **Transforms**: `quantize`
  (U to integer pump pulses) and an optional `to_encoding` on `scale_offset` (pulses back to float U).
  Both are permitted transforms, so G5 holds.
* **Codegen**: a spec may name `devices:` (several device configurations of one world).
* **Rules**: `RULE_TIME_SCALE` (the checks run 600 times longer for a world that moves in minutes),
  G2 reads the run's own sources (the same values for the existing examples; needed for a stateful
  host), and G6 includes the contract partition when there is one. `EXTRA_RULES` adds G7 and G8 for
  this world only.
* **examples.py**: `insulin-loop` is in `ALL_EXAMPLES` and `CLINICAL`, but not in `EXAMPLES`. The
  ai-mapping experiment and the classic seconds-long app form iterate `EXAMPLES`, and neither fits a
  24-hour closed loop.

## Where the brief and the code disagreed (the code's design was followed)
1. **Plasma insulin alone cannot match a meal.** With `dI/dt = -n I + u/VI`, a bolus acts within
   minutes and a meal appears over hours, so no meal bolus fits. Two subcutaneous depot compartments
   (time constant `tau_i`) were added in front of plasma insulin. The Bergman structure is otherwise
   as stated.
2. **The factor 18.0182** is used as specified. The molar mass of glucose gives 18.016, a 0.015 %
   difference, far below sensor resolution.
3. **Scenario runs are limited to 1 hour** in `scenario.py`. Insulin-loop scenarios have their own
   format and parser (`clinical/scenarios.py`, up to 48 h), and `bridge-island run` sends them there.
4. **Command re-sends.** Re-sending a command to a pump that delivered but did not confirm would repeat
   the bolus. A re-send repeats the basal (idempotent) with the same id and never the bolus. The
   property "a command id is never executed twice" is checked as "no command id delivers its bolus
   twice", which is the hazard.
5. **`pump_silent`** uses PumpA (silence is a behaviour of any pump here). PumpB v2 is used for the
   firmware scenario.
6. **The host accepts `AlarmAck`**, so that acknowledgments appear in the critical event record (CFR
   (b)(4)). The brief's host accepted only glucose and confirmations.
7. **The replay** is injected 7 minutes after the lunch bolus command, after the next command. With a
   4-minute delay, bridge-basic's source-time rate cap drops the duplicate by accident. That is
   recorded as a finding, not used as protection.

## What direct has, and why
Each direct adapter is generated from the same mapping as the Bridge, so it has the same range checks
(glucose 40 to 400 mg/dL, alarm id range) and the same type checks. It has no message-age check, no
command-id check and no confirmation tracking: those are contract features. The host's own coarse
watchdogs (no valid glucose, or no confirmation, for 30 minutes) are on the Baseline Island and apply
to every architecture alike.
