"""Age-of-information metrics: how old is the data a consumer holds, over time?

"Age at arrival" (the mean of ``delivered - source`` over deliveries) says how stale a datum was
when it landed. It says nothing about the time *between* deliveries, when the consumer is still
holding the last datum and it keeps getting older. The standard measures (Kaul, Yates and
Gruteser, 2012) look at the age a consumer would see at any instant:

* **time-average age**: the age, averaged over time, from the first delivery to the end;
* **peak age**: the oldest the data ever got, which is just before an update arrives.

Age at time ``t`` is ``t - b`` where ``b`` is the time the *freshest information delivered so far*
was true at. An update only resets the age if it is fresher than what is already held, so a late,
older datum arriving out of order does not make the data look younger. For a stream delivered
every ``T`` seconds with delay ``d``, the time-average age is ``d + T/2`` and the peak age is
``d + T``.
"""

from __future__ import annotations

from collections.abc import Sequence


def age_profile(
    deliveries: Sequence[tuple[float, float]], end: float
) -> tuple[float, float] | None:
    """Time-average age and peak age, in seconds, of one stream.

    ``deliveries`` are ``(delivered_at, basis)`` pairs, where ``basis`` is the time the
    information was true at (the source time, or for an estimate the time of the real sample
    it is based on). ``end`` is the end of the observation. Returns ``None`` for no deliveries.
    """
    if not deliveries:
        return None
    ordered = sorted(deliveries, key=lambda d: d[0])
    best = ordered[0][1]
    integral = peak = 0.0
    for i, (start, basis) in enumerate(ordered):
        best = max(best, basis)
        stop = max(ordered[i + 1][0], start) if i + 1 < len(ordered) else max(end, start)
        a_start, a_stop = start - best, stop - best
        integral += (stop - start) * (a_start + a_stop) / 2.0
        peak = max(peak, a_start, a_stop)
    span = max(end, ordered[-1][0]) - ordered[0][0]
    average = integral / span if span > 0 else ordered[0][0] - ordered[0][1]
    return average, peak


def time_above(deliveries: Sequence[tuple[float, float]], end: float, threshold: float) -> float:
    """Total time, in seconds, that the age of the data was greater than ``threshold``.

    This is the consumer's own freshness requirement made measurable: a receiver that tolerates
    data up to ``threshold`` seconds old was working from data older than it accepts for this long.
    """
    if not deliveries:
        return 0.0
    ordered = sorted(deliveries, key=lambda d: d[0])
    best = ordered[0][1]
    total = 0.0
    for i, (start, basis) in enumerate(ordered):
        best = max(best, basis)
        stop = max(ordered[i + 1][0], start) if i + 1 < len(ordered) else max(end, start)
        total += max(0.0, stop - max(start, best + threshold))
    return total
