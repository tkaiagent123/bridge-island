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
