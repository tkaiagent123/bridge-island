# Related work

## Policy and oversight context

Modular open systems approaches are required for many U.S. defense programs by statute,
and recent oversight work reports on how that has gone. These sources describe the policy
setting. They do not describe this repository, and this repository does not implement any
program's approach.

* 10 U.S.C. § 4401, "Requirement for modular open system approach in major defense
  acquisition programs; definitions".
  <https://www.law.cornell.edu/uscode/text/10/4401>
* U.S. Government Accountability Office, *Weapon Systems Acquisition: DOD Needs Better
  Planning to Attain Benefits of Modular Open Systems*, GAO-25-106931, January 22, 2025.
  <https://www.gao.gov/products/gao-25-106931>

## Background on modularity and mediation

* D. L. Parnas, "On the criteria to be used in decomposing systems into modules,"
  *Communications of the ACM*, vol. 15, no. 12, pp. 1053-1058, December 1972.
  doi:10.1145/361598.361623. Decomposing around design decisions that are likely to change,
  not around processing steps. The Bridge applies this to the baseline's interface: the part
  that must follow the baseline is isolated from everything else.
* C. Y. Baldwin and K. B. Clark, *Design Rules, Volume 1: The Power of Modularity*. MIT
  Press, 2000. Design rules and hidden modules: independent teams can change what is hidden
  behind a stable interface.
* G. Wiederhold, "Mediators in the architecture of future information systems," *IEEE
  Computer*, vol. 25, no. 3, pp. 38-49, March 1992. doi:10.1109/2.121508. A mediator is a
  module between data resources and their users that holds the knowledge needed to bridge
  their differences. The Bridge is a mediator in this sense. (Some reference lists spell the
  title "Mediation in the Architecture of Future Information Systems".)
* M. E. Conway, "How do committees invent?," *Datamation*, vol. 14, no. 4, pp. 28-31,
  April 1968. Systems tend to mirror the communication structure of the organizations that
  build them.

## Related patterns, measures and standards

These were checked against their public pages. No claim is made that this project implements or
conforms to any standard listed here.

* **The anti-corruption layer.** The pattern places a layer between subsystems that do not share
  semantics, so that one can stay unchanged without compromising the design of the other. It was
  first described by Eric Evans in *Domain-Driven Design: Tackling Complexity in the Heart of
  Software*. "Anti-Corruption Layer Pattern", Azure Architecture Center, Microsoft Learn.
  <https://learn.microsoft.com/en-us/azure/architecture/patterns/anti-corruption-layer>.
  The Bridge is the closest industry analogue, and the same page lists the costs this project
  documents: added latency, an extra component to operate, enforcing input validation at a boundary
  between different trust levels, observability to diagnose translation failures, and keeping
  business rules out of the layer. It also says the pattern may not suit systems without significant
  semantic differences, which matches [the limits stated here](rationale.md).
* **Age of information.** S. Kaul, R. Yates and M. Gruteser, "Real-time status: How often should one
  update?," in *Proc. IEEE INFOCOM*, 2012, pp. 2731-2735. Introduced the age of information as a
  measure of freshness. It is why [this project](concepts.md#how-age-is-measured) reports the
  average and peak age over time, not only the age at arrival.
* **Valid time and transaction time.** C. S. Jensen and C. E. Dyreson (eds.), "The Consensus Glossary
  of Temporal Database Concepts - February 1998 Version." The vocabulary for what G3 does: the source
  time is the *valid time* of a fact, and the delivery time is the time the system learned of it.
  <https://infolab.usc.edu/csci599/Fall2001/paper/glossary.pdf> (a copy of the glossary).
* **Design structure and propagation cost.** A. MacCormack, J. Rusnak and C. Y. Baldwin, "Exploring
  the structure of complex software designs: An empirical study of open source and proprietary code,"
  *Management Science*, vol. 52, no. 7, pp. 1015-1030, 2006. doi:10.1287/mnsc.1060.0552. Uses design
  structure matrices of element-to-element dependencies, as the dependency edges counted in the
  [experiments](experiments.md) are, and defines a propagation-cost measure of how far a change can
  spread. This project counts one-step dependencies; it does not compute propagation cost.
* **Medical device integration.** ASTM F2761-09(2013), *Medical Devices and Medical Systems -
  Essential safety requirements for equipment comprising the patient-centric integrated clinical
  environment (ICE) - Part 1: General requirements and conceptual model*. A standard about
  integrating equipment from different manufacturers into one patient-centric system. The
  [medical example](examples.md) is a structural illustration only and makes no clinical claim.
* **A published open avionics architecture.** The FACE Approach, The Open Group. It defines a
  Reference Architecture, which includes a Transport Services Segment, and a Data Architecture.
  <https://www.opengroup.org/face>. It separates how data is moved from how it is represented, as
  this project separates the native-protocol ports from the Translator Module.

## Open mission-system standards (U.S. Air Force)

The U.S. Air Force publishes unclassified mission-system standards at <https://open-arsenal.gitlab.io/>,
with repositories on GitHub and GitLab. Three of them describe an element or pattern at the same boundary
the Bridge occupies. These were checked against the public files. No claim is made that this project
implements or conforms to them.

* U.S. Air Force, *OMS Standard*, OMSC-STD-001 Rev M, OMS v2.5, January 22, 2026. Defines the
  Isolator as an "isolation layer between Mission Package and an external system", and gives the aim
  of constraining the architecture to "bound/limit where and to what degree re-certification would be
  required upon change" (sections 3.4.6, Isolation, and 6.4, Isolators).
  <https://github.com/open-arsenal/oms>
* U.S. Air Force, *A-GRA Vehicle Interface Volume*, ASK 6.0a, July 31, 2026. Routes all communications
  from the mission package to a safety-critical enclave through a VI OMS Isolator, at what it calls the
  "Airworthiness Boundary", and assumes the Isolator contains a message validator and a platform
  adapter (section 1.1). <https://github.com/open-arsenal/a-gra>
* AFLCMC/WA, *AMS GRA Architecture Volumes, Executive Summary and Document Catalog*, Version 14.0,
  April 24, 2026. Says that interactions crossing its boundary may use "adaptation and isolation
  patterns" so that architectural updates do not trigger platform recertification (section 2.1). Its
  Compliance Assessment Approach assesses a legacy element and its adapters together as one unit.
  <https://github.com/open-arsenal/ams-gra>

The closest counterparts:

| This project | OMS / A-GRA | Note |
| --- | --- | --- |
| Bridge | Isolator (VI OMS Isolator in A-GRA) | closest counterpart |
| Baseline Island | Element external to the Mission Package; in A-GRA, the safety-critical enclave | behind the boundary |
| Unique Island | OMS Mission Package (Subsystems and Services) | the OMS-facing side |
| Translator Module | Isolator's format and protocol conversion | |
| Health monitor | Isolator status reporting (`ServiceStatus`, configurable period) | |
| Cyber monitor | Cybersecurity posture in the Service Contract; the VI message validator | |
| Mapping spec (YAML) | Service Contract | OMS-facing side only |

### Where they differ

* **Both sides specified.** The Isolator's Service Contract covers its OMS-facing side, and OMS says
  that exchanges facing the external elements "do not need to be documented in OMS documentation"
  (6.4). Here the mapping spec and the OIU and UID describe both sides, including a baseline that
  adopts no common standard.
* **Assumed standard.** OMS requires the Isolator to use OMS messages over the Platform's service bus
  through the Critical Abstraction Layer on its OMS-facing side (STD-000431). The Bridge assumes no
  shared standard on either side; translating between two native interfaces is its whole job.
* **The re-certification bound.** The sections cited here, and the Isolator Checklist Instructions
  (OMSC-INS-012), state the aim and say an Isolator may need certification, but do not specify a
  method for establishing the bound. They leave it to the adopting program. This project makes the
  extent of a change countable (guidelines G1 to G6 and the change-impact experiment), on invented
  numbers and without any claim about certification.

A Bridge whose Unique-Island side presents an OMS-facing interface would be, in OMS terms, an
Isolator, so the two can be used together and do not compete. [Concepts](concepts.md#the-bridge-and-what-is-in-it)
defines the Bridge's parts.
