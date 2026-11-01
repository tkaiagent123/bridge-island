# Design rationale and limitations

## Why this shape

The baseline is fixed and certified elsewhere. The mission systems change at their own pace.
The pattern puts the knowledge of how to bridge the two in one place, so that the part that
must follow the baseline closely (the OIU and the mapping) is separate from the parts that
follow each mission system. The six guidelines are what keep that separation honest. The
architecture also follows the way the work is divided between the organizations, which is
the observation behind [Conway's paper](related-work.md): a structure that matches the
authority boundaries is cheaper to keep than one that fights them.

## What the Bridge does not do

**The Bridge concentrates complexity. It does not remove it.** All the conversion, timing
and protocol knowledge that used to be spread across subsystems now sits in one component.
That component has to be correct, tested and kept current. The change-impact experiment
shows the counts moving, not the work disappearing: the Bridge is re-verified after every
baseline change, and the removed-element case still touches a unique subsystem. When a
system has one or two subsystems and a stable baseline, point-to-point may well be cheaper.
The experiments make no case for the Bridge at that scale.

**The Bridge is a single point of failure and needs analysis.** Every exchange passes through
it. A stall, a crash or a wrong mapping affects every unique system at once. The health
monitor detects a stall after the fact, as the timing experiment shows, but detection is
not tolerance. This repository contains no redundancy, failover or fault-tree analysis of
the Bridge. Anyone applying the pattern to a real system needs those, and a safety case for
the Bridge itself.

**The numbers are synthetic.** All platforms, interfaces and rates are invented. The latency
budgets, bus latencies and signal functions are arbitrary choices that make the behavior
visible. The experiment counts follow a stated counting model, which is an assumption about
where tests and adapters live, not a measurement of any real program. Do not quote the
outputs as evidence about a real system.

**It is not flight software or medical software, and it is not certified.** It is a
simulation for teaching, testing and discussion. It has not been analysed against any safety
or certification standard and must not be used where failure could cause harm.

## Modelling limits

* **The buses are simplified.** The command/response, label-word and publish/subscribe models
  capture who may start a transfer, what the rate structure looks like, and a fixed latency.
  They have no arbitration, error detection, retries, jitter or bandwidth limits, and they
  are not implementations of any standard.
* **Python cannot enforce G1 absolutely.** Code that misrepresents its own island can attach
  to the baseline. The design makes a bypass impossible through the public API, and the
  link registry makes any bypass visible. See [Concepts](concepts.md).
* **Neither rate policy meets a need the baseline cannot supply.** A subsystem that wants 50
  Hz of data from a 20 Hz source cannot be fully served. `cap` delivers the real data at the
  rate the baseline has. `extrapolate` delivers the rate the interface expects, but part of it
  is predicted, and it adds latency. Either one keeps the existing interface working; neither
  meets the subsystem's full need. See [Concepts](concepts.md#data-age-rates-and-what-the-bridge-does-not-do).
  The extrapolator is deliberately simple (linear for floats, hold for other encodings, a
  short time limit) and is not a state estimator.
* **Time is simulated.** The scheduler is deterministic and seeded. There is no wall-clock
  time, no clock drift between islands, and no clock synchronization problem. A real
  integration has all three.
* **The stall model is simple.** A stalled Bridge keeps the latest message of each kind and
  drops uplinks. Other stall behaviors, such as queueing everything, would give different
  numbers.
* **Verification effort is counted, not costed.** The counting model treats a test as a unit
  and an adapter as a module. It does not weigh the sizes of these things.

## Where the pattern fits less well

It does not help when the baseline can be changed freely, when there is only one consumer,
or when the unique systems can simply adopt the baseline's representation (guideline G2
still applies there: the Bridge passes the data through unchanged). It adds a component, a
latency and a failure mode, and it is worth that only when the cost of coupling many
independent systems to an immutable core is higher.

## Critical review: threats to validity

This section lists what the evidence in this repository does and does not show. All numbers
here and elsewhere are synthetic, illustrative.

- **The platforms are invented, by design.** Nothing here measures a real integration. The
  examples show that the behaviours are implemented and testable, not that the pattern pays off
  on any real system.
- **The change-impact model is a counting model.** Its tests-per-dependency figure is an
  assumption, and the result depends on it (the sensitivity table shows how). The `n` it counts
  is baseline data elements, not baseline systems.
- **Latency is simulated.** Delays come from a seeded random model, not from a measured bus. The
  average and peak ages are exact for the simulated deliveries, but the delay distribution is
  invented.
- **Neither rate policy meets a subsystem's full need.** `cap` lowers the rate and `extrapolate`
  fills the gap with estimates (marked `estimated`). Both only support the existing interface.
- **Age is measured four ways** because arrival age alone hides how long a value is held. The
  time-average and peak figures are the ones to read when a source is slow or the Bridge stalls.
- **Tests are checked by deliberate breakage.** `scripts/mutation_check.py` changes the code on
  purpose and reports the changes no test notices. A sample run exposed gaps (an unconverted
  constant, exact counters, health thresholds); each was fixed or covered by a test. Some
  surviving mutants are equivalent changes that cannot be detected, so the score is not 100%.
- **One implementation, one author's model.** The guidelines G1 to G6 are checked against this
  code, not against independent implementations.
