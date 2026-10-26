"""The permitted transforms. Nothing outside this set can appear in a mapping spec (G5).

Each transform can run (``apply``), describe itself as a Python expression for the code
generator (``expr``), and say how it changes a representation (``output``).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
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


def from_spec(spec: Any) -> Transform:
    """Build a transform from one YAML mapping-spec entry. Anything else is not permitted."""
    if spec == "identity":
        return Identity()
    if isinstance(spec, dict) and len(spec) == 1:
        ((kind, arg),) = spec.items()
        if kind == "unit":
            return UnitConversion(str(arg[0]), str(arg[1]))
        if kind == "scale_offset":
            return ScaleOffset(
                float(arg["scale"]), float(arg.get("offset", 0.0)), arg.get("to_units")
            )
        if kind == "enum":
            return EnumMapping(tuple(arg["map"].items()), arg.get("to_encoding"))
        if kind == "bam_decode":
            return BamDecode(int(arg))
        if kind == "bam_encode":
            return BamEncode(int(arg))
        if kind in ("bit_unpack", "bit_pack"):
            fields = tuple((str(n), int(w)) for n, w in arg)
            return BitUnpack(fields) if kind == "bit_unpack" else BitPack(fields)
        if kind == "valid_range":
            return ValidityCheck(float(arg[0]), float(arg[1]))
    raise TransformError(f"transform not permitted: {spec!r}")
