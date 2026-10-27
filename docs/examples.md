# Examples

Both examples live in `examples/`. Each has a `world.py` (the platforms and subsystems),
`mapping_v1.yaml` and `mapping_v2.yaml` (the mapping specs), and `generated/` (the compiled
mappings). Every name, field, rate and number is invented.

```powershell
uv run bridge-island demo aviation
uv run bridge-island demo medical
uv run bridge-island check-rules --example medical
```

## Aviation: Generic Host Air Vehicle (GHAV)

The baseline (command/response bus) publishes:

| Message | Elements | Rate (v1) |
| --- | --- | --- |
| `NavigationState` | latitude, longitude, altitude, heading, groundspeed | 20 Hz |
| `AirData` | barometric altitude, airspeed | 10 Hz |
| `AttitudeState` | roll, pitch, yaw | 50 Hz |
| `PowerStatus` | bus voltage, power state | 1 Hz |
| `TimeReference` | UTC seconds | 1 Hz |

and accepts one input, `DisplayRequest`. The Unique Island has four subsystems with
deliberately different native interfaces:

| Subsystem | Native interface | What it shows |
| --- | --- | --- |
| `PlanningDisplay` | publish/subscribe, high latency tolerance | Metric units, 5 Hz. Sends `show_page` requests upward. |
| `CueingProcessor` | label words | Position and attitude in radians, tight latency budget. |
| `VoiceRelay` | label words | The tightest latency budget. |
| `StatusLogger` | command/response | Already speaks the baseline representation, so every path is an identity (G2). |

### Baseline v1 to v2

Baseline v2 applies one change of each kind:

| Kind | Change |
| --- | --- |
| rename | `NavigationState.groundspeed_kt` becomes `ground_speed_kt` |
| unit | `NavigationState.altitude` changes from feet to meters |
| encoding | `NavigationState.heading` changes from a 16-bit binary angle to float degrees |
| rate | `AttitudeState` goes from 50 Hz to 100 Hz |
| added | `NavigationState.vertical_speed_mps` (nothing uses it) |
| removed | `AirData.baro_altitude_ft` |

What happens, with the files you would edit:

* **Everything but the removal** is absorbed by `mapping_v2.yaml`. No unique system is
  edited. `PlanningDisplay`'s altitude and heading used to need conversions and now pass
  through unchanged; `StatusLogger` used to pass altitude through and now needs a
  meters-to-feet conversion. The faster attitude rate is absorbed by the UID, which keeps
  delivering at each subsystem's own cap.
* **The removal is visible, not silent.** `CueingProcessor` used the barometric altitude.
  The mapping marks that entry `status: removed`. The Bridge logs it at start-up, and the UID
  tells `CueingProcessor` that `baro_alt_m` is unavailable. `CueingProcessor.check()` then
  reports `baro_alt_m: unavailable (element removed from baseline interface v2)`. If the
  entry were left as it was, the code generator would fail the build instead.
* **A Bridge built for v1 refuses to run against v2.** It raises `InterfaceMismatch` and
  lists what no longer fits.
* **The same change on point-to-point** shows the failure the Bridge avoids: with each
  subsystem's adapter still written for v1, `PlanningDisplay` converts the altitude from
  feet to meters when it is already in meters, and no error is raised. The renamed field
  is a lookup failure inside each adapter. Nothing tells `CueingProcessor` that the
  barometric altitude is gone. It simply stops receiving it.
  (`tests/test_p2p.py::test_p2p_adapters_written_for_v1_break_silently_on_v2`.)

## Medical device: Generic Infusion Host Platform (GIHP)

The same pattern applies wherever a certified core must interoperate with independently
evolving peripherals under separate authority. Here a cleared host platform (the Baseline
Island) publishes pump state, alarm state and a device clock, and accepts one display
request. Three add-on modules make up the Unique Island:

| Subsystem | Native interface | What it needs |
| --- | --- | --- |
| `DoseAdvisor` | publish/subscribe | Flow in litres per hour, pressure in psi. |
| `BedsideDisplay` | publish/subscribe | The baseline's own units, a named pump mode. Sends `show_view` requests upward. |
| `AlarmRelay` | label words | Alarm code, priority and active flag, with a tight latency budget. |

Baseline v2 renames `volume_infused_ml` to `total_volume_ml`, changes line pressure from kPa
to mmHg, turns the numeric alarm code into a named value, doubles the alarm-state rate, adds
a battery-level element, and removes the alarm priority. The result has the same shape as
the aviation example: the mapping absorbs the changes, and the removal is reported to
`AlarmRelay`.

!!! note
    This example is a structural illustration. It makes no clinical claim, models no real
    device, and is not medical software. No real product or company is represented.
