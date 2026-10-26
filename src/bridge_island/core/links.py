"""Which node sits on which island, and which nodes are linked (used to prove G1)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class BypassError(Exception):
    """A unique system tried to connect to the baseline without going through the Bridge."""


class IslandMember(Protocol):
    name: str
    island: str  # "baseline" | "bridge" | "unique"


@dataclass(frozen=True)
class Link:
    a: str
    b: str
    protocol: str


class Network:
    """Registry of nodes and the links wired between them."""

    def __init__(self) -> None:
        self.islands: dict[str, str] = {}
        self.links: list[Link] = []

    def add(self, member: IslandMember) -> None:
        self.islands[member.name] = member.island

    def connect(self, a: str, b: str, protocol: str) -> None:
        for n in (a, b):
            if n not in self.islands:
                raise KeyError(f"unknown node {n!r}")
        self.links.append(Link(a, b, protocol))

    def bypass_links(self) -> list[Link]:
        """Links that join a baseline node directly to a unique node."""
        return [
            link
            for link in self.links
            if {self.islands[link.a], self.islands[link.b]} == {"baseline", "unique"}
        ]

    def problems(self) -> list[str]:
        """G1 violations: bypass links, and unique nodes with no link to the Bridge."""
        found = [f"bypass link {lk.a} <-> {lk.b}" for lk in self.bypass_links()]
        for name, island in self.islands.items():
            if island != "unique":
                continue
            peers = {lk.b if lk.a == name else lk.a for lk in self.links if name in (lk.a, lk.b)}
            if not any(self.islands[p] == "bridge" for p in peers):
                found.append(f"{name} has no link to the Bridge")
        return found
