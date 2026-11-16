"""The invented patient model, cohort, therapy, units and transforms (synthetic, illustrative)."""

from __future__ import annotations

import random

import pytest

from bridge_island.clinical.physiology import (
    RANGES,
    Params,
    Patient,
    cohort,
    draw,
    settles,
    therapy_for,
)
from bridge_island.core import units
from bridge_island.core.elements import Representation
from bridge_island.core.transforms import Quantize, ScaleOffset, TransformError, from_spec

COHORT, REJECTED = cohort(1)


def test_cohort_is_thirty_named_sets_drawn_from_the_seed() -> None:
    assert [p.id for p in COHORT] == [f"VP{i:02d}" for i in range(1, 31)]
    assert cohort(1) == (COHORT, REJECTED)
    assert cohort(2)[0] != COHORT
    assert REJECTED > 0  # some draws do not settle and are rejected


def test_every_parameter_is_inside_its_stated_range() -> None:
    for p in COHORT:
        for key, (lo, hi) in RANGES.items():
            assert lo <= getattr(p, key) <= hi


@pytest.mark.parametrize("params", COHORT, ids=lambda p: p.id)
def test_every_kept_set_rests_at_its_basal_steady_state(params: Params) -> None:
    patient = Patient(params)
    patient.advance_to(24 * 3600.0)
    assert max(abs(g - params.Gb) for g in patient.trace) < 1e-6
    assert settles(params)


def test_a_set_that_settles_too_slowly_is_rejected() -> None:
    slow = draw(random.Random(0), "VPxx")
    slow = Params(**{**vars(slow), "p1": 0.0005})
    assert not settles(slow)


def test_a_meal_raises_glucose_and_insulin_lowers_it() -> None:
    params = COHORT[0]
    fed = Patient(params)
    fed.eat(0.0, 50.0)
    fed.advance_to(3 * 3600.0)
    assert max(fed.trace) > params.Gb + 30
    dosed = Patient(params)
    dosed.bolus(0.0, 2.0)
    dosed.advance_to(3 * 3600.0)
    assert min(dosed.trace) < params.Gb - 10
    assert dosed.delivered_u == pytest.approx(2.0 + params.basal_u_h * 3.0, rel=1e-6)


def test_inputs_apply_from_the_next_minute_and_basal_can_be_changed() -> None:
    p = Patient(COHORT[0])
    p.set_basal(30.0, 0.0)
    p.advance_to(60.0)
    assert p.minute == 1
    p.advance_to(59.0)  # never backwards
    assert p.minute == 1
    p.advance_to(3 * 3600.0)
    assert COHORT[0].Gb < p.G  # no insulin: glucose rises
    assert p.glucose_at(3 * 3600.0) == p.G


def test_therapy_settings_are_plausible_and_cover_a_meal() -> None:
    for params in COHORT:
        th = therapy_for(params)
        assert th.basal_u_h == params.basal_u_h
        assert 10 < th.isf_mg_dl_per_u < 120
        assert 4 < th.carb_ratio_g_per_u < 40


def test_glucose_units_convert_with_the_stated_factor() -> None:
    assert units.convert(1.0, "mmol/L", "mg/dL") == pytest.approx(18.0182)
    assert units.convert(180.182, "mg/dL", "mmol/L") == pytest.approx(10.0)
    assert units.convert(1.0, "U/h", "mU/min") == pytest.approx(1000 / 60)
    with pytest.raises(units.UnitError):
        units.convert(1.0, "U", "mg/dL")


def test_glucose_conversion_is_a_permitted_transform() -> None:
    t = from_spec({"unit": ["mmol/L", "mg/dL"]})
    rep = Representation("float64", "mmol/L", "glucose", "pubsub")
    assert t.output(rep).units == "mg/dL"
    assert t.apply(5.5) == pytest.approx(99.1001)


def test_quantize_rounds_units_to_pump_pulses() -> None:
    q = from_spec({"quantize": {"scale": 20, "encoding": "uint16", "units": "pulse"}})
    assert isinstance(q, Quantize)
    assert q.apply(2.0) == 40 and q.apply(0.024) == 0 and q.apply(0.026) == 1
    assert eval(q.expr("x"), {}, {"x": 1.0}) == 20
    rep = q.output(Representation("float64", "U", "dose", "command_response"))
    assert (rep.encoding, rep.units) == ("uint16", "pulse")
    assert q.inverse().apply(40) == pytest.approx(2.0)


@pytest.mark.parametrize(
    "spec",
    [
        {"quantize": {"scale": 20, "encoding": "uint16"}},
        {"quantize": {"scale": -1, "encoding": "uint16", "units": "pulse"}},
        {"quantize": {"scale": 20, "encoding": "float64", "units": "pulse"}},
        {"quantize": [20]},
        {"scale_offset": {"scale": 1, "to_encoding": 5}},
    ],
)
def test_bad_quantize_and_scale_specs_are_refused(spec: dict) -> None:
    with pytest.raises(TransformError):
        from_spec(spec)


def test_scale_offset_can_change_the_encoding() -> None:
    t = from_spec({"scale_offset": {"scale": 0.05, "to_units": "U", "to_encoding": "float64"}})
    assert isinstance(t, ScaleOffset)
    out = t.output(Representation("uint16", "pulse", "dose", "command_response"))
    assert (out.encoding, out.units) == ("float64", "U")
    assert t.apply(40) == pytest.approx(2.0)
