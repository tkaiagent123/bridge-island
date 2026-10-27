# Concepts

## The two islands

| Term | Meaning |
| --- | --- |
| **Baseline Island** | The host platform. It is immutable to the integrator and certified by another authority. Only its published interface may be used. |
| **Unique Island** | The integrator's own mission systems. Each has its own native interface, protocol, units and rates, and evolves independently. |
| **Bridge** | The single mediation layer. It is the only path between the islands. |
| **Point-to-point (P2P)** | The comparison architecture: each unique system is wired directly to the baseline elements it needs. |

The baseline is certified and owned elsewhere, so the integrator cannot change it. The
unique systems are owned and changed by different teams on different schedules. Without a
mediator, every unique system carries its own copy of the knowledge of how to talk to the
baseline. That knowledge is spread over as many places as there are systems, and each copy
must be updated when the baseline changes.

## The Bridge, and what is in it

The Bridge is one object (`bridge_island.bridge.Bridge`) with five parts.

| Part | Role |
| --- | --- |
| **OEM Interface Unit (OIU)** | Faces the baseline. It follows the baseline's published interface exactly, and acts as a *remote terminal* toward the baseline. |
| **Translator Module (TM)** | Holds all format, content and protocol conversion, and the time-tag handling. |
| **Unique Interface Distributor (UID)** | Keeps a separate connection to each unique system, in that system's own native format. It acts as the *controlling side* toward the unique systems. |
| **Health monitor** | A background service. It detects stale data and stall-and-resume discontinuities, and annunciates them to the affected systems. |
| **Cyber monitor** | A background service. It enforces which data may cross, and in which direction. |

```mermaid
sequenceDiagram
    participant BL as Baseline (bus controller)
    participant OIU
    participant TM as Translator Module
    participant UID
    participant U as Unique system
    BL->>OIU: message, valid at time t (commanded transfer)
    OIU->>TM: message (published elements only)
    TM->>UID: datum in the system's units, still tagged t
    UID->>U: native protocol (bus, label words or pub/sub)
    Note over U: age = now - t
    U->>UID: request (native format)
    UID->>TM: cyber monitor allows? then translate
    TM->>OIU: baseline message
    BL->>OIU: poll for pending input
    OIU-->>BL: the message
```

### Role asymmetry

The baseline's bus has a *bus controller* (BC) that starts every transfer, and *remote
terminals* (RTs) that only answer. The OIU is an RT. It cannot push data to the baseline;
it can only hold a message until the baseline polls for it. The baseline stays in control of
its own bus, and the Bridge fits into the role the baseline already defines.

Toward the unique side the roles flip. The UID is the controlling side: bus controller on a
command/response bus, sole transmitter on a label-word bus, publisher on a pub/sub bus. The
Bridge therefore decides when each unique system hears from it, and a unique system cannot
start a conversation with the baseline.

## The six design guidelines

Each guideline is implemented as runtime behavior and checked by tests whose names start
with the guideline number (for example `test_g3_source_time_preserved`).
`bridge-island check-rules` runs the same checks on an example and prints pass or fail.

**G1. Complete retargeting.** No unique system has a direct link to the baseline. The
baseline platform refuses to attach anything that does not declare itself part of a Bridge,
and a network registry records every link so that a bypass can be found by inspection.
Python cannot make a bypass literally impossible for code that lies about what it is, so
the guarantee is "impossible through the public API, and detected otherwise". The P2P
module turns the refusal off on purpose, and the registry then reports one bypass link per
subsystem.

**G2. Translate only where needed.** When the baseline and unique representations of an
element have the same encoding, units and frame, the datum passes through unchanged under
an identity transform. The code generator rejects a mapping that lists a conversion where
none is needed, and rejects one whose conversions do not reach the target representation.

**G3. Time-tag integrity.** Each datum carries the *source* time of validity, never the
Bridge's delivery time. The Bridge adds latency, and a consumer can always compute data age
as `now - source_time`. The Translator Module has a deliberately naive `delivery` mode that
stamps arrival time instead, to show the error: a datum held inside the Bridge for a second
arrives looking brand new.

**G4. Baseline immutability.** The OIU accepts only messages and elements the baseline
publishes, and emits only messages the baseline is specified to accept. Anything else is
rejected and logged inside the Bridge. The Bridge refuses to start if its mapping refers to
an element the baseline does not publish.

**G5. Compiled mapping.** The baseline-to-unique mapping is a YAML spec. `bridge-island
codegen` turns it into a Python module at build time, with straight-line conversion code and
no run-time tables. Every generated file begins with a header naming the spec and its
SHA-256:

```python
# GENERATED FILE - DO NOT EDIT.
# Generated by bridge-island codegen from spec: mapping_v2.yaml
# Spec SHA-256: <hex digest of the spec>
```

The only conversions a spec can name are the permitted transforms: identity, scale and
offset, unit conversion, enum mapping, bit-field pack and unpack, binary-angle encode and
decode, and range checks.

**G6. Single-unit realization.** One `Bridge` object composes the OIU, TM, UID and monitors.
Mixed criticality is handled by separation *inside* the object: each part sits in a named
partition (baseline-facing, translation, unique-facing, monitor), and the parts hold no
direct references to each other. They communicate only through callbacks owned by the
Bridge.

## Data age, rates and what the Bridge does not do

* A subsystem's declared rate is a *cap*. The Bridge drops samples to stay at or below it,
  and never invents samples if the baseline is slower. A subsystem that wants 50 Hz and
  gets a 20 Hz source sees 20 Hz, with honest ages.
* A baseline element that is removed is not guessed at. The generated mapping records it,
  the Bridge logs it, and the UID tells the affected subsystem that the input is
  unavailable. See the [aviation example](examples.md).
* The buses are simplified behavioral models inspired by public avionics bus concepts
  (command/response, label words, publish/subscribe). They are not implementations of any
  standard.
