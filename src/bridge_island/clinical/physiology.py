"""An invented glucose-insulin model and a virtual cohort. Synthetic, illustrative.

Not a validated patient model. Not medical software. Not for clinical use.

Structure: the Bergman minimal model (Bergman, Ider, Bowden and Cobelli, "Quantitative estimation of
insulin sensitivity", Am J Physiol 236(6): E667-E677, 1979, doi:10.1152/ajpendo.1979.236.6.E667),
extended with a plasma-insulin compartment for the pump's insulin, a two-compartment subcutaneous
delay in front of it, and a two-compartment meal absorption. Time ``t`` is in minutes::

    dG/dt  = -(p1 + X) G + p1 Gb + Ra(t) / V        G  plasma glucose, mg/dL
    dX/dt  = -p2 X + p3 (I - Ib)                     X  remote insulin action, 1/min
    dI/dt  = -n I + U2 / (tau_i VI)                  I  plasma insulin, uU/mL
    dU1/dt = u(t) - U1 / tau_i                       U1 insulin under the skin, uU
    dU2/dt = (U1 - U2) / tau_i                       U2 insulin under the skin, second depot, uU
    dQ1/dt = -Q1 / tau_m + D(t)                      Q1 meal glucose, first compartment, mg
    dQ2/dt = (Q1 - Q2) / tau_m                       Q2 meal glucose, second compartment, mg
    Ra(t)  = f Q2 / tau_m                            rate of appearance of meal glucose, mg/min

``u(t)`` is the insulin the pump delivers (uU/min; 1 U = 10^6 uU), ``D(t)`` the carbohydrate eaten
(mg/min). At a constant basal ``u_b`` the depots settle at ``U1 = U2 = u_b tau_i`` and plasma
insulin at ``Ib = u_b / (n VI)``, so the model rests at ``G = Gb`` on basal insulin with no food.

The subcutaneous delay is an addition to the plasma-insulin form ``dI/dt = -n I + u(t)/VI``: without
it, insulin acts within minutes while a meal appears over hours, and no meal bolus can match a meal.

Integration: classic Runge-Kutta (RK4) with a fixed 1-minute step. The fastest time constant is
``1/n`` (8 to 12 minutes), well inside RK4's stability region at a 1-minute step; a fixed step keeps
runs bit-for-bit repeatable. Inputs change on minute boundaries: insulin or food given during a
minute is applied over the next whole minute.

The ``PatientModel`` protocol is the seam for a validated simulator: anything with these methods can
stand in for :class:`Patient` without changing the Bridge, the devices or the controller. No such
adapter exists here.
"""

from __future__ import annotations

import functools
import random
from dataclasses import dataclass, field
from typing import Protocol

UU_PER_U = 1_000_000.0

# Invented, illustrative ranges for the virtual cohort (not population data).
RANGES: dict[str, tuple[float, float]] = {
    "weight_kg": (55.0, 95.0),
    "Gb": (110.0, 150.0),  # mg/dL, glucose at rest on basal insulin
    "p1": (0.003, 0.02),  # 1/min, glucose effectiveness (the smallest values are rejected)
    "p2": (0.015, 0.035),  # 1/min
    "SI": (6.0e-4, 1.4e-3),  # p3/p2, (1/min) per (uU/mL)
    "n": (0.08, 0.12),  # 1/min, plasma insulin clearance
    "tau_i": (40.0, 70.0),  # min, subcutaneous insulin absorption
    "basal_u_h": (0.5, 1.4),
    "tau_m": (30.0, 60.0),  # min, meal absorption
    "f": (0.8, 0.95),  # meal bioavailability
}
V_DL_PER_KG = 1.6
VI_ML_PER_KG = 120.0


class PatientModel(Protocol):
    """What the devices need from a patient model. Times in seconds of simulated time."""

    def glucose_at(self, t_s: float) -> float: ...
    def set_basal(self, t_s: float, u_per_h: float) -> None: ...
    def bolus(self, t_s: float, units: float) -> None: ...
    def eat(self, t_s: float, carbs_g: float) -> None: ...


@dataclass(frozen=True)
class Params:
    id: str
    weight_kg: float
    Gb: float
    p1: float
    p2: float
    SI: float
    n: float
    tau_i: float
    basal_u_h: float
    tau_m: float
    f: float

    @property
    def p3(self) -> float:
        return self.SI * self.p2

    @property
    def V(self) -> float:
        return V_DL_PER_KG * self.weight_kg

    @property
    def VI(self) -> float:
        return VI_ML_PER_KG * self.weight_kg

    @property
    def basal_uu_min(self) -> float:
        return self.basal_u_h * UU_PER_U / 60.0

    @property
    def Ib(self) -> float:
        return self.basal_uu_min / (self.n * self.VI)


@dataclass
class Patient:
    """One virtual patient, integrated on demand. Implements :class:`PatientModel`."""

    params: Params
    trace: list[float] = field(default_factory=list)  # G at each whole minute, from t = 0
    delivered_u: float = 0.0  # all insulin delivered so far, U

    def __post_init__(self) -> None:
        p = self.params
        depot = p.basal_uu_min * p.tau_i
        self.state = [p.Gb, 0.0, p.Ib, depot, depot, 0.0, 0.0]  # G X I U1 U2 Q1 Q2
        self.minute = 0
        self._basal = p.basal_uu_min
        self._bolus = 0.0
        self._carbs = 0.0
        self.trace.append(p.Gb)

    @property
    def G(self) -> float:
        return self.state[0]

    # ---- inputs (applied over the next whole minute) ----------------------------------

    def set_basal(self, t_s: float, u_per_h: float) -> None:
        self.advance_to(t_s)
        self._basal = max(0.0, u_per_h) * UU_PER_U / 60.0

    def bolus(self, t_s: float, units: float) -> None:
        self.advance_to(t_s)
        self._bolus += max(0.0, units) * UU_PER_U

    def eat(self, t_s: float, carbs_g: float) -> None:
        self.advance_to(t_s)
        self._carbs += max(0.0, carbs_g) * 1000.0

    # ---- integration ------------------------------------------------------------------

    def _step(self) -> None:
        p = self.params
        u = self._basal + self._bolus
        D = self._carbs
        self.delivered_u += u / UU_PER_U
        self._bolus = self._carbs = 0.0
        p1, p2, p3, Gb, n, ti, tm = p.p1, p.p2, p.p3, p.Gb, p.n, p.tau_i, p.tau_m
        V, VI, Ib, f = p.V, p.VI, p.Ib, p.f

        def d(s: list[float]) -> list[float]:
            G, X, Ins, U1, U2, Q1, Q2 = s
            return [
                -(p1 + X) * G + p1 * Gb + f * Q2 / tm / V,
                -p2 * X + p3 * (Ins - Ib),
                -n * Ins + U2 / (ti * VI),
                u - U1 / ti,
                (U1 - U2) / ti,
                -Q1 / tm + D,
                (Q1 - Q2) / tm,
            ]

        s = self.state
        k1 = d(s)
        k2 = d([a + 0.5 * b for a, b in zip(s, k1, strict=True)])
        k3 = d([a + 0.5 * b for a, b in zip(s, k2, strict=True)])
        k4 = d([a + b for a, b in zip(s, k3, strict=True)])
        self.state = [
            a + (b1 + 2 * b2 + 2 * b3 + b4) / 6
            for a, b1, b2, b3, b4 in zip(s, k1, k2, k3, k4, strict=True)
        ]
        self.state[0] = max(self.state[0], 1.0)  # numerical floor only
        self.minute += 1
        self.trace.append(self.state[0])

    def advance_to(self, t_s: float) -> None:
        """Integrate every whole minute that ends at or before ``t_s``."""
        while (self.minute + 1) * 60.0 <= t_s + 1e-9:
            self._step()

    def glucose_at(self, t_s: float) -> float:
        """Plasma glucose at the last whole minute at or before ``t_s``."""
        self.advance_to(t_s)
        return self.state[0]


# ---- the cohort --------------------------------------------------------------------------


def draw(rng: random.Random, pid: str) -> Params:
    return Params(pid, **{k: rng.uniform(*r) for k, r in RANGES.items()})


def settles(params: Params, kick: float = 60.0, hours: float = 8.0, tol: float = 1.0) -> bool:
    """True if, on basal insulin and no food, glucose returns to within ``tol`` of Gb.

    Starts ``kick`` mg/dL above Gb. A set that has not settled after ``hours`` has no usable
    resting state for a 24-hour run and is rejected.
    """
    p = Patient(params)
    p.state[0] = params.Gb + kick
    p.advance_to(hours * 3600.0)
    return abs(p.G - params.Gb) < tol


def cohort(seed: int = 1, size: int = 30) -> tuple[list[Params], int]:
    """``size`` kept parameter sets VP01.. drawn from ``seed``, and how many draws were rejected."""
    rng = random.Random(f"cohort-{seed}")
    kept: list[Params] = []
    rejected = 0
    while len(kept) < size:
        params = draw(rng, f"VP{len(kept) + 1:02d}")
        if settles(params):
            kept.append(params)
        else:
            rejected += 1
    return kept, rejected


# ---- therapy settings: what a clinician would have set, derived from the model ------------


@dataclass(frozen=True)
class Therapy:
    """The controller's per-patient settings (an invented derivation, not a clinical method)."""

    basal_u_h: float  # programmed basal
    isf_mg_dl_per_u: float  # largest glucose drop after 1 U, from rest
    carb_ratio_g_per_u: float  # grams of carbohydrate one unit covers


def _excess_area(params: Params, carbs: float, units: float, hours: float = 5.0) -> float:
    q = Patient(params)
    q.eat(0.0, carbs)
    q.bolus(0.0, units)
    q.advance_to(hours * 3600.0)
    return sum(g - params.Gb for g in q.trace)


@functools.lru_cache(maxsize=256)
def therapy_for(params: Params) -> Therapy:
    p = Patient(params)
    p.bolus(0.0, 1.0)
    p.advance_to(6 * 3600.0)
    isf = params.Gb - min(p.trace)
    lo, hi = 0.0, 40.0
    for _ in range(16):  # the bolus whose 5-hour glucose excess for a 50 g meal is zero
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if _excess_area(params, 50.0, mid) > 0 else (lo, mid)
    return Therapy(params.basal_u_h, isf, 50.0 / ((lo + hi) / 2))
