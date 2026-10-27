# Tutorial: integrate a new unique subsystem in 15 minutes

You will add an invented subsystem, `RangeEstimator`, to the aviation example. It wants the
airspeed in metres per second and the bus voltage, over publish/subscribe. You will touch
three things: the subsystem's declared inputs, the mapping spec, and one command. You will
not touch the baseline, the Bridge, or any other subsystem.

Work in a checkout of the repository, in `examples/aviation_invented/`. (Every step below is
executed by the test suite, in `tests/test_tutorial.py`, so these snippets stay correct.)

## 1. Declare what the subsystem needs

In `world.py`, add its inputs to `_needs()`, in the subsystem's *own* units and protocol.
Each `n(...)` is: native name, encoding, units, frame, protocol, highest useful rate in Hz,
latency budget in seconds, and how old data may be when used, in seconds.

```python
        "RangeEstimator": (
            n("airspeed_ms", "float64", "m/s", "air", PS, 1, 1.0, 5.0),
            n("bus_voltage_v", "float64", "V", "bus", PS, 1, 1.0, 5.0),
        ),
```

Then register its native protocol in `_PROTOCOLS`:

```python
_PROTOCOLS = {..., "StatusLogger": CR, "RangeEstimator": PS}
```

## 2. Say how each input maps from the baseline

In both `mapping_v1.yaml` and `mapping_v2.yaml`, add two lines under `downlinks:`:

```yaml
  - {from: AirData.airspeed_kt, to: RangeEstimator.airspeed_ms, transforms: [{unit: [kt, m/s]}]}
  - {from: PowerStatus.bus_voltage_v, to: RangeEstimator.bus_voltage_v}
```

The first converts knots to metres per second. The second needs no conversion: the voltage
representation matches, so the entry has no `transforms` and the value passes through
unchanged (guideline G2).

## 3. Compile the mapping

```powershell
uv run bridge-island codegen --spec examples/aviation_invented/mapping_v1.yaml
uv run bridge-island codegen --spec examples/aviation_invented/mapping_v2.yaml
```

The generator checks the spec against the baseline interface and your declared inputs, and
writes `generated/mapping_v*.py`. It fails loudly if something does not fit. For example, if
you did step 1 but forgot step 2:

```text
codegen failed: RangeEstimator.airspeed_ms is a declared input but has no mapping entry
```

Other build-time failures: an element the baseline does not publish, a conversion where
none is needed, a chain of conversions that does not reach the target units, or a transform
that is not in the permitted set.

## 4. Run it

```powershell
uv run bridge-island demo aviation
uv run bridge-island check-rules
```

The demo table now has a `RangeEstimator` row, with its own received count and data age. All
six guideline checks still pass. Nothing else changed: the other subsystems show the same
numbers as before.

## What you did not have to do

* You did not change the baseline interface. It is immutable to you.
* You did not write protocol code. The UID opened a publish/subscribe connection for the
  new subsystem because `_PROTOCOLS` says so.
* You did not write time-tag handling. Every value arrives with its source time, and the
  subsystem's data-age checks work immediately (`RangeEstimator.check()` reports missing or
  stale inputs).
* You did not touch the other four subsystems or re-test them. When the baseline moves from
  v1 to v2, only the mapping lines change.

## If your subsystem sends data to the baseline

Declare an `Output` (see `PlanningDisplay.show_page` in `world.py`) and add an `uplinks:`
entry that maps each field to a field of a message the baseline accepts. The cyber monitor
will allow exactly that message from that subsystem and block everything else, and the
Translator Module will reject out-of-range or malformed values before they reach the
baseline. See the [containment experiment](experiments.md#4-fault-containment).
