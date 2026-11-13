from hypothesis import given
from hypothesis import strategies as st

from bridge_island.aimap.parse import entry_ids, parse_response
from bridge_island.aimap.task import plain_task, spec_text

GOOD = """\
Here is the mapping.
```yaml
mapping:
  name: x
  baseline_version: 2
  downlinks:
    - {from: A.b, to: S.c}
  uplinks:
    - from: S.out
      to: Msg
      fields:
        - {from: f, to: g}
confidence:
  - {entry: "A.b -> S.c", confidence: HIGH, reason: same unit}
  - {entry: "S.out.f -> Msg.g", confidence: low, reason: unsure}
  - {entry: "bad", confidence: certain, reason: not a level}
```
"""


def test_a_fenced_reply_gives_spec_and_confidences() -> None:
    got = parse_response(GOOD)
    assert got.error == "" and got.spec is not None
    assert got.spec["baseline_version"] == 2
    assert got.confidence == {
        "A.b -> S.c": ("high", "same unit"),
        "S.out.f -> Msg.g": ("low", "unsure"),
    }  # levels are case-insensitive; an unknown level is dropped
    assert entry_ids(got.spec) == ["A.b -> S.c", "S.out.f -> Msg.g"]


def test_the_spec_is_valid_input_to_the_generator() -> None:
    task = plain_task("aviation")
    got = parse_response(
        "mapping:\n" + "".join("  " + ln + "\n" for ln in spec_text(task.truth).splitlines())
    )
    assert got.spec == task.truth and got.confidence == {}


def test_a_bare_spec_is_accepted_without_confidences() -> None:
    got = parse_response("name: x\nbaseline_version: 2\ndownlinks: []\n")
    assert got.spec is not None and got.confidence == {}


def test_bad_replies_are_outcomes_not_errors() -> None:
    for text in ("", "I cannot do that.", "[1, 2]", "a: [unclosed", "mapping: 3", "x: &a 1\ny: *a"):
        got = parse_response(text)
        assert got.spec is None and got.error, text


@given(st.text(max_size=300))
def test_nothing_makes_the_parser_raise(text: str) -> None:
    got = parse_response(text)
    assert (got.spec is None) == bool(got.error)


def test_the_first_confidence_for_an_entry_wins() -> None:
    got = parse_response(
        "mapping: {baseline_version: 2}\nconfidence:\n"
        "  - {entry: a, confidence: low, reason: first}\n"
        "  - {entry: a, confidence: high, reason: second}\n"
    )
    assert got.confidence == {"a": ("low", "first")}
