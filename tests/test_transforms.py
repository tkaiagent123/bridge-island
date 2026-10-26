import pytest
from hypothesis import given
from hypothesis import strategies as st

from bridge_island.core.elements import Representation
from bridge_island.core.transforms import (
    BamDecode,
    BamEncode,
    BitPack,
    BitUnpack,
    Chain,
    EnumMapping,
    Identity,
    MalformedError,
    ScaleOffset,
    TransformError,
    UnitConversion,
    ValidityCheck,
    ValidityError,
    chain,
    check_type,
    from_spec,
)

FLOATS = st.floats(-1e6, 1e6, allow_nan=False)


def run_expr(t: object, value: object) -> object:
    """Evaluate a transform's generated expression the way generated code does."""
    from bridge_island.core.transforms import check_range, enum_fail

    return eval(t.expr("x"), {"x": value, "check_range": check_range, "enum_fail": enum_fail})  # type: ignore[attr-defined]


@given(FLOATS)
def test_unit_conversion_round_trip(x: float) -> None:
    t = UnitConversion("ft", "m")
    assert t.inverse().apply(t.apply(x)) == pytest.approx(x, abs=1e-6)  # type: ignore[union-attr]


@given(FLOATS)
def test_temperature_round_trip(x: float) -> None:
    t = UnitConversion("degF", "degC")
    assert t.inverse().apply(t.apply(x)) == pytest.approx(x, abs=1e-6)  # type: ignore[union-attr]


@given(FLOATS)
def test_scale_offset_round_trip(x: float) -> None:
    t = ScaleOffset(2.5, -4.0)
    assert t.inverse().apply(t.apply(x)) == pytest.approx(x, abs=1e-6)  # type: ignore[union-attr]


@given(st.integers(0, 65535))
def test_bam_round_trip_is_exact(n: int) -> None:
    assert BamEncode(16).apply(BamDecode(16).apply(n)) == n


@given(st.floats(0, 360, exclude_max=True))
def test_bam_encode_error_is_below_half_a_step(deg: float) -> None:
    back = BamDecode(16).apply(BamEncode(16).apply(deg))
    diff = abs(back - deg)
    assert min(diff, 360 - diff) <= 360 / 65536 / 2 + 1e-9


@given(st.integers(0, 2**12 - 1))
def test_bitfield_round_trip(word: int) -> None:
    fields = (("a", 3), ("b", 5), ("c", 4))
    assert BitPack(fields).apply(BitUnpack(fields).apply(word)) == word


def test_enum_mapping_and_inverse() -> None:
    t = EnumMapping((("MAP", 1), ("STATUS", 2)))
    assert t.apply("STATUS") == 2
    assert t.inverse().apply(2) == "STATUS"  # type: ignore[union-attr]
    with pytest.raises(MalformedError):
        t.apply("NOPE")
    assert EnumMapping((("A", 1), ("B", 1))).inverse() is None


def test_validity_check() -> None:
    t = ValidityCheck(0, 10)
    assert t.apply(5) == 5
    with pytest.raises(ValidityError):
        t.apply(11)


@pytest.mark.parametrize(
    "t, value",
    [
        (UnitConversion("ft", "m"), 123.4),
        (UnitConversion("degF", "degC"), 77.0),
        (ScaleOffset(3.0, 1.0), 2.0),
        (BamDecode(16), 1234),
        (BamEncode(16), 123.456),
        (EnumMapping((("A", 1), ("B", 2))), "B"),
        (BitUnpack((("a", 3), ("b", 5))), 0b10110101),
        (BitPack((("a", 3), ("b", 5))), {"a": 5, "b": 22}),
        (ValidityCheck(0, 5), 3.0),
        (Identity(), 7),
    ],
)
def test_generated_expression_matches_apply(t: object, value: object) -> None:
    assert run_expr(t, value) == t.apply(value)  # type: ignore[attr-defined]


def test_chain_drops_identity_and_composes() -> None:
    ch = chain(Identity(), UnitConversion("ft", "m"), Identity())
    assert len(ch.steps) == 1 and not ch.is_identity
    assert chain(Identity()).is_identity
    assert ch.apply(10.0) == pytest.approx(3.048)
    inv = ch.inverse()
    assert inv is not None and inv.apply(3.048) == pytest.approx(10.0)
    assert Chain((EnumMapping((("A", 1), ("B", 1))),)).inverse() is None


def test_representation_flow() -> None:
    rep = Representation("float64", "ft", "msl", "command_response")
    assert UnitConversion("ft", "m").output(rep).units == "m"
    with pytest.raises(TransformError):
        UnitConversion("kt", "km/h").output(rep)
    bam = Representation("bam16", "deg", "true_north", "command_response")
    decoded = BamDecode(16).output(bam)
    assert (decoded.encoding, decoded.units) == ("float64", "deg")
    assert BamEncode(16).output(decoded).encoding == "bam16"
    with pytest.raises(TransformError):
        BamDecode(16).output(rep)
    with pytest.raises(TransformError):
        BamEncode(16).output(bam)
    assert EnumMapping((("A", 1),), "uint8").output(rep).encoding == "uint8"
    assert ScaleOffset(2.0, 0.0, "m").output(rep).units == "m"
    assert BitUnpack((("a", 3),)).output(rep).encoding == "bitfield"
    assert BitPack((("a", 3), ("b", 5))).output(rep).encoding == "uint8"
    assert ValidityCheck(0, 1).output(rep) == rep


def test_from_spec_accepts_only_permitted_kinds() -> None:
    assert from_spec("identity").is_identity
    assert isinstance(from_spec({"unit": ["ft", "m"]}), UnitConversion)
    assert isinstance(from_spec({"scale_offset": {"scale": 2}}), ScaleOffset)
    assert isinstance(from_spec({"enum": {"map": {"A": 1}}}), EnumMapping)
    assert isinstance(from_spec({"bam_decode": 16}), BamDecode)
    assert isinstance(from_spec({"bam_encode": 16}), BamEncode)
    assert isinstance(from_spec({"bit_unpack": [["a", 3]]}), BitUnpack)
    assert isinstance(from_spec({"bit_pack": [["a", 3]]}), BitPack)
    assert isinstance(from_spec({"valid_range": [0, 1]}), ValidityCheck)
    for bad in ({"python": "x + 1"}, "lookup_table", {"unit": ["a", "b"], "extra": 1}, 5):
        with pytest.raises(TransformError, match="not permitted"):
            from_spec(bad)


def test_check_type() -> None:
    check_type(1.5, "float64")
    check_type(3, "uint8")
    check_type(True, "bool")
    check_type("MAP", "enum")
    for value, enc in [
        (True, "float64"),
        ("x", "float64"),
        (300, "uint8"),
        (-1, "uint8"),
        (1, "bool"),
    ]:
        with pytest.raises(MalformedError):
            check_type(value, enc)
