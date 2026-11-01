"""Strict YAML loading for specs and scenarios.

PyYAML's safe loader is safe from code execution but has two traps for configuration files:

* it silently keeps the *last* of two duplicate keys, so a repeated ``transforms:`` quietly wins;
* anchors and aliases can expand exponentially (the "billion laughs" memory bomb).

These files are small and hand-written, so both are rejected with a clear message instead.
"""

from __future__ import annotations

from typing import Any

import yaml


class YamlError(ValueError):
    """The text is not valid, or uses a YAML feature these files do not allow."""


class _StrictLoader(yaml.SafeLoader):
    def compose_node(self, parent: Any, index: Any) -> Any:
        event = self.peek_event()  # type: ignore[no-untyped-call]
        if getattr(event, "anchor", None) is not None:
            raise YamlError("YAML anchors and aliases (&name, *name, <<) are not allowed here")
        return super().compose_node(parent, index)

    def construct_mapping(self, node: Any, deep: bool = False) -> dict[Any, Any]:
        seen: set[Any] = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=True)
            try:
                duplicate = key in seen
                seen.add(key)
            except TypeError:
                raise YamlError(f"a mapping key must be a plain value, got {key!r}") from None
            if duplicate:
                raise YamlError(f"duplicate key {key!r} (line {key_node.start_mark.line + 1})")
        mapping: dict[Any, Any] = super().construct_mapping(node, deep)
        return mapping


def load_yaml(text: str) -> Any:
    """Parse YAML text, rejecting duplicate keys, anchors and aliases."""
    try:
        return yaml.load(text, Loader=_StrictLoader)  # noqa: S506  (a SafeLoader subclass)
    except yaml.YAMLError as exc:
        raise YamlError(str(exc)) from exc
