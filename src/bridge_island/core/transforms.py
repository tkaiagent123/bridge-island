"""The permitted transforms. Nothing outside this set can appear in a mapping spec (G5).

Each transform can run (``apply``), describe itself as a Python expression for the code
generator (``expr``), and say how it changes a representation (``output``).
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any, NoReturn

from bridge_island.core import units
from bridge_island.core.elements import Representation


class TransformError(Exception):
    """A value cannot be transformed."""


class ValidityError(TransformError):
    """A value is outside its permitted range."""


class MalformedError(TransformError):
    """A value or message has the wrong shape or type."""


class UnmappedError(TransformError):
    """No mapping exists for this element or message."""


def check_range(value: float, lo: float, hi: float) -> float:
    if not lo <= value <= hi:
        raise ValidityError(f"{value!r} outside [{lo}, {hi}]")
    return value


def check_type(value: Any, encoding: str) -> None:
    """Shallow type check of a value against an encoding name."""
    if encoding.startswith(("float",)):
        ok = isinstance(value, (int, float)) and not isinstance(value, bool)
    elif encoding.startswith(("uint", "bam")):
        bits = int("".join(c for c in encoding if c.isdigit()) or 0)
        ok = isinstance(value, int) and not isinstance(value, bool)
        ok = ok and (bits == 0 or 0 <= value < 2**bits)
    elif encoding == "bool":
        ok = isinstance(value, bool)
    else:
        ok = True
    if not ok:
        raise MalformedError(f"{value!r} is not a valid {encoding}")


def enum_fail(value: Any) -> NoReturn:
    raise MalformedError(f"no enum mapping for {value!r}")


class Transform(ABC):
    @property
    def kind(self) -> str:
        return type(self).__name__

    @property
    def is_identity(self) -> bool:
        return False

    @abstractmethod
    def apply(self, value: Any) -> Any: ...

    @abstractmethod
    def expr(self, x: str) -> str:
        """A Python expression computing ``apply(x)``; ``x`` is a simple variable name."""

    @abstractmethod
    def inverse(self) -> Transform | None: ...

    def output(self, rep: Representation) -> Representation:
        return rep


@dataclass(frozen=True)
class Identity(Transform):
    @property
    def is_identity(self) -> bool:
        return True

    def apply(self, value: Any) -> Any:
        return value

    def expr(self, x: str) -> str:
        return x

    def inverse(self) -> Transform:
        return self


@dataclass(frozen=True)
class ScaleOffset(Transform):
    scale: float
    offset: float = 0.0
    to_units: str | None = None

    def apply(self, value: float) -> float:
        return value * self.scale + self.offset

    def expr(self, x: str) -> str:
        return f"({x} * {self.scale!r} + {self.offset!r})"

    def inverse(self) -> Transform | None:
        if self.scale == 0:
            return None
        return ScaleOffset(1.0 / self.scale, -self.offset / self.scale)

    def output(self, rep: Representation) -> Representation:
        return replace(rep, units=self.to_units) if self.to_units else rep


@dataclass(frozen=True)
class UnitConversion(Transform):
    src: str
    dst: str

    def __post_init__(self) -> None:
        try:  # fail where the units are written, not later when the code is generated
            units.coefficients(self.src, self.dst)
        except units.UnitError as exc:
            raise TransformError(str(exc)) from exc

    def apply(self, value: float) -> float:
        scale, offset = units.coefficients(self.src, self.dst)
        return value * scale + offset

    def expr(self, x: str) -> str:
        scale, offset = units.coefficients(self.src, self.dst)
        return f"({x} * {scale!r} + {offset!r})"

    def inverse(self) -> Transform:
        return UnitConversion(self.dst, self.src)

    def output(self, rep: Representation) -> Representation:
        if rep.units != self.src:
            raise TransformError(f"expected units {self.src!r}, got {rep.units!r}")
        return replace(rep, units=self.dst)


@dataclass(frozen=True)
class EnumMapping(Transform):
    pairs: tuple[tuple[Any, Any], ...]
    to_encoding: str | None = None

    def apply(self, value: Any) -> Any:
        for key, out in self.pairs:
            if value == key:
                return out
        return enum_fail(value)

    def expr(self, x: str) -> str:
        tail = f"enum_fail({x})"
        for key, out in reversed(self.pairs):
            tail = f"({out!r} if {x} == {key!r} else {tail})"
        return tail

    def inverse(self) -> Transform | None:
        outs = [o for _, o in self.pairs]
        if len(set(map(repr, outs))) != len(outs):
            return None
        return EnumMapping(tuple((o, k) for k, o in self.pairs))

    def output(self, rep: Representation) -> Representation:
        return replace(rep, encoding=self.to_encoding) if self.to_encoding else rep


@dataclass(frozen=True)
class BamDecode(Transform):
    """Binary angular measurement counts to degrees (float)."""

    bits: int = 16

    @property
    def _step(self) -> float:
        return 360.0 / (1 << self.bits)

    def apply(self, value: int) -> float:
        return value * self._step

    def expr(self, x: str) -> str:
        return f"({x} * {self._step!r})"

    def inverse(self) -> Transform:
        return BamEncode(self.bits)

    def output(self, rep: Representation) -> Representation:
        if rep.encoding != f"bam{self.bits}":
            raise TransformError(f"expected encoding bam{self.bits}, got {rep.encoding!r}")
        return replace(rep, encoding="float64", units="deg")


@dataclass(frozen=True)
class BamEncode(Transform):
    """Degrees to binary angular measurement counts (rounded, wrapped)."""

    bits: int = 16

    @property
    def _step(self) -> float:
        return 360.0 / (1 << self.bits)

    def apply(self, value: float) -> int:
        return round(value / self._step) % (1 << self.bits)

    def expr(self, x: str) -> str:
        return f"(round({x} / {self._step!r}) % {(1 << self.bits)})"

    def inverse(self) -> Transform:
        return BamDecode(self.bits)

    def output(self, rep: Representation) -> Representation:
        if (rep.encoding, rep.units) != ("float64", "deg"):
            raise TransformError("BAM encoding needs float64 degrees")
        return replace(rep, encoding=f"bam{self.bits}")


def _layout(fields: Sequence[tuple[str, int]]) -> list[tuple[str, int, int]]:
    out, shift = [], 0
    for name, width in fields:
        out.append((name, shift, (1 << width) - 1))
        shift += width
    return out


@dataclass(frozen=True)
class BitUnpack(Transform):
    """Integer word to a dict of named bit fields (least-significant field first)."""

    fields: tuple[tuple[str, int], ...]

    def apply(self, value: int) -> dict[str, int]:
        return {n: (value >> s) & m for n, s, m in _layout(self.fields)}

    def expr(self, x: str) -> str:
        items = ", ".join(f"{n!r}: ({x} >> {s}) & {m}" for n, s, m in _layout(self.fields))
        return "{" + items + "}"

    def inverse(self) -> Transform:
        return BitPack(self.fields)

    def output(self, rep: Representation) -> Representation:
        return replace(rep, encoding="bitfield")


@dataclass(frozen=True)
class BitPack(Transform):
    fields: tuple[tuple[str, int], ...]

    def apply(self, value: Mapping[str, int]) -> int:
        return sum((value[n] & m) << s for n, s, m in _layout(self.fields))

    def expr(self, x: str) -> str:
        return (
            "("
            + " | ".join(f"(({x}[{n!r}] & {m}) << {s})" for n, s, m in _layout(self.fields))
            + ")"
        )

    def inverse(self) -> Transform:
        return BitUnpack(self.fields)

    def output(self, rep: Representation) -> Representation:
        width = sum(w for _, w in self.fields)
        return replace(rep, encoding=f"uint{width}")


@dataclass(frozen=True)
class ValidityCheck(Transform):
    lo: float
    hi: float

    def apply(self, value: float) -> float:
        return check_range(value, self.lo, self.hi)

    def expr(self, x: str) -> str:
        return f"check_range({x}, {self.lo!r}, {self.hi!r})"

    def inverse(self) -> Transform:
        return self


@dataclass(frozen=True)
class Chain(Transform):
    steps: tuple[Transform, ...]

    @property
    def is_identity(self) -> bool:
        return all(s.is_identity for s in self.steps)

    def apply(self, value: Any) -> Any:
        for step in self.steps:
            value = step.apply(value)
        return value

    def expr(self, x: str) -> str:
        raise NotImplementedError("generate one statement per step instead")

    def inverse(self) -> Transform | None:
        inv = [s.inverse() for s in reversed(self.steps)]
        return None if any(i is None for i in inv) else Chain(tuple(i for i in inv if i))

    def output(self, rep: Representation) -> Representation:
        for step in self.steps:
            rep = step.output(rep)
        return rep


def chain(*steps: Transform) -> Chain:
    """Compose transforms, dropping identities."""
    return Chain(tuple(s for s in steps if not s.is_identity))


def _finite(value: Any, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise TransformError(f"{what} must be a finite number, got {value!r}")
    return float(value)


def _pair(arg: Any, what: str) -> tuple[Any, Any]:
    if not isinstance(arg, (list, tuple)) or len(arg) != 2:
        raise TransformError(f"{what} needs exactly two values in a list, got {arg!r}")
    return arg[0], arg[1]


def _build_unit(arg: Any) -> Transform:
    src, dst = _pair(arg, "unit")
    if not isinstance(src, str) or not isinstance(dst, str):
        raise TransformError(f"unit needs two unit names, got {arg!r}")
    return UnitConversion(src, dst)


def _build_scale_offset(arg: Any) -> Transform:
    if (
        not isinstance(arg, dict)
        or "scale" not in arg
        or set(arg) - {"scale", "offset", "to_units"}
    ):
        raise TransformError(
            f"scale_offset needs 'scale' (and optionally 'offset', 'to_units'): {arg!r}"
        )
    to_units = arg.get("to_units")
    if to_units is not None and not isinstance(to_units, str):
        raise TransformError(f"to_units must be text, got {to_units!r}")
    return ScaleOffset(
        _finite(arg["scale"], "scale"), _finite(arg.get("offset", 0.0), "offset"), to_units
    )


def _build_enum(arg: Any) -> Transform:
    scalar = (str, int, float, bool)
    mapping = arg.get("map") if isinstance(arg, dict) else None
    if not isinstance(mapping, dict) or not mapping or set(arg) - {"map", "to_encoding"}:
        raise TransformError(
            f"enum needs a non-empty 'map' (and optionally 'to_encoding'): {arg!r}"
        )
    if not all(isinstance(k, scalar) and isinstance(v, scalar) for k, v in mapping.items()):
        raise TransformError("enum keys and values must be text, numbers or booleans")
    to_encoding = arg.get("to_encoding")
    if to_encoding is not None and not isinstance(to_encoding, str):
        raise TransformError(f"to_encoding must be text, got {to_encoding!r}")
    return EnumMapping(tuple(mapping.items()), to_encoding)


def _bits(arg: Any) -> int:
    if isinstance(arg, bool) or not isinstance(arg, int) or not 1 <= arg <= 32:
        raise TransformError(f"the number of bits must be an integer from 1 to 32, got {arg!r}")
    return arg


def _build_fields(arg: Any) -> tuple[tuple[str, int], ...]:
    if not isinstance(arg, list) or not arg:
        raise TransformError("bit fields need a non-empty list of [name, width] pairs")
    fields = []
    for item in arg:
        name, width = _pair(item, "a bit field")
        if (
            not isinstance(name, str)
            or isinstance(width, bool)
            or not isinstance(width, int)
            or width < 1
        ):
            raise TransformError(f"a bit field is [name, width in bits >= 1], got {item!r}")
        fields.append((name, width))
    if len({n for n, _ in fields}) != len(fields) or sum(w for _, w in fields) > 64:
        raise TransformError("bit field names must be unique and the total width at most 64 bits")
    return tuple(fields)


def _build_valid_range(arg: Any) -> Transform:
    lo, hi = _pair(arg, "valid_range")
    lo, hi = _finite(lo, "the lower limit"), _finite(hi, "the upper limit")
    if lo > hi:
        raise TransformError(f"valid_range lower limit {lo} is above the upper limit {hi}")
    return ValidityCheck(lo, hi)


_BUILDERS: dict[str, Callable[[Any], Transform]] = {
    "unit": _build_unit,
    "scale_offset": _build_scale_offset,
    "enum": _build_enum,
    "bam_decode": lambda arg: BamDecode(_bits(arg)),
    "bam_encode": lambda arg: BamEncode(_bits(arg)),
    "bit_unpack": lambda arg: BitUnpack(_build_fields(arg)),
    "bit_pack": lambda arg: BitPack(_build_fields(arg)),
    "valid_range": _build_valid_range,
}


def from_spec(spec: Any) -> Transform:
    """Build a transform from one YAML mapping-spec entry.

    Raises :class:`TransformError`, and nothing else, for anything that is not a permitted,
    well-formed transform.
    """
    if isinstance(spec, str) and spec == "identity":
        return Identity()
    if isinstance(spec, dict) and len(spec) == 1:
        ((kind, arg),) = spec.items()
        builder = _BUILDERS.get(kind) if isinstance(kind, str) else None
        if builder is not None:
            return builder(arg)
    raise TransformError(f"transform not permitted: {spec!r}")
