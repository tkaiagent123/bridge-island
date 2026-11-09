"""Adversarial tests: arbitrary input must produce a clear error or a correct result, never a crash.

These are the tests a sceptical reviewer writes: fuzz every parser with arbitrary data, feed
hostile YAML, send malformed HTTP, and check system-level invariants over random scenarios.
"""

import contextlib
import copy
import json
import socket
import time
import urllib.parse
from pathlib import Path
from typing import Any

import pytest
import world
import yaml
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from bridge_island.codegen import CodegenError, dictionary_for_spec, generate
from bridge_island.core.elements import Representation
from bridge_island.core.transforms import Transform, TransformError, from_spec
from bridge_island.examples import examples_dir, load_world
from bridge_island.scenario import (
    ScenarioError,
    fault_messages,
    parse_scenario,
    parse_scenario_text,
    run_scenario,
)
from bridge_island.summary import summarize
from bridge_island.ui import MAX_BODY, App, make_server
from bridge_island.yamlutil import YamlError, load_yaml

JSON = st.recursive(
    st.none()
    | st.booleans()
    | st.integers(-(10**6), 10**6)
    | st.floats(allow_nan=True, allow_infinity=True)
    | st.text(max_size=12),
    lambda inner: (
        st.lists(inner, max_size=4) | st.dictionaries(st.text(max_size=8), inner, max_size=4)
    ),
    max_leaves=12,
)
FAST = settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.too_slow])


# --- transforms and specs -----------------------------------------------------------------


@FAST
@given(JSON)
def test_from_spec_raises_only_transform_error(spec: Any) -> None:
    try:
        transform = from_spec(spec)
    except TransformError:
        return
    assert isinstance(transform, Transform)
    # whatever was accepted must also behave when asked what it does to a representation
    with contextlib.suppress(TransformError):
        transform.output(Representation("float64", "ft", "msl", "p"))


@pytest.mark.parametrize(
    "spec",
    [
        {"unit": ["ft", "furlong"]},  # a unit that does not exist
        {"unit": ["ft", "kt"]},  # different dimensions
        {"unit": "ftm"},  # a string is not a pair (it used to read as 'f' and 't')
        {"unit": ["ft"]},
        {"bam_decode": 0},
        {"bam_decode": 99},
        {"bam_encode": True},
        {"valid_range": [2, 1]},
        {"valid_range": [float("nan"), 1]},
        {"valid_range": [0, float("inf")]},
        {"enum": {"map": {}}},
        {"enum": {"map": {"a": [1]}}},
        {"enum": {"map": {"a": 1}, "extra": 1}},
        {"bit_pack": [["a", 0]]},
        {"bit_unpack": [["a", 40], ["b", 40]]},
        {"bit_unpack": [["a", 1], ["a", 1]]},
        {"scale_offset": {"scale": float("inf")}},
        {"scale_offset": {"scale": 1, "typo": 2}},
        {"unit": ["ft", "m"], "extra": 1},
        ["unit"],
        None,
    ],
)
def test_malformed_transforms_are_rejected_at_construction(spec: Any) -> None:
    with pytest.raises(TransformError):
        from_spec(spec)


BASE_SPEC_PATH = world.HERE / "mapping_v1.yaml"
BASE_SPEC = load_yaml(BASE_SPEC_PATH.read_text(encoding="utf-8"))
DELETE = object()


def all_paths(obj: Any, prefix: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    found = [prefix]
    if isinstance(obj, dict):
        for k, v in obj.items():
            found += all_paths(v, (*prefix, k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            found += all_paths(v, (*prefix, i))
    return found


PATHS = all_paths(BASE_SPEC)


def mutate(path: tuple[Any, ...], new: Any) -> Any:
    root = copy.deepcopy(BASE_SPEC)
    if not path:
        return {} if new is DELETE else new
    cur = root
    for step in path[:-1]:
        cur = cur[step]
    if new is DELETE:
        del cur[path[-1]]
    else:
        cur[path[-1]] = new
    return root


@settings(max_examples=250, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(st.sampled_from(PATHS), JSON | st.just(DELETE))
def test_any_corruption_of_a_valid_spec_gives_codegen_error_or_valid_python(
    path: tuple[Any, ...], new: Any
) -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        spec = Path(tmp) / "spec.yaml"
        spec.write_text(yaml.safe_dump(mutate(path, new)), encoding="utf-8")
        try:
            text = generate(spec, world.dictionary(1))
        except CodegenError:
            return
    compile(text, "generated.py", "exec")  # whatever is accepted is syntactically valid Python


def test_the_valid_spec_still_generates() -> None:
    assert generate(BASE_SPEC_PATH, world.dictionary(1)).startswith("# GENERATED FILE")


def test_specs_with_missing_files_or_bad_world_are_codegen_errors(tmp_path: Path) -> None:
    with pytest.raises(CodegenError, match="cannot read"):
        generate(tmp_path / "nope.yaml", world.dictionary(1))
    bad = tmp_path / "s.yaml"
    bad.write_text("name: x\nbaseline_version: 1\nworld: missing_world.py\n")
    with pytest.raises(CodegenError, match="does not exist"):
        dictionary_for_spec(bad)
    (tmp_path / "world.py").write_text("")  # a world file exists, so the version is what is wrong
    bad.write_text("name: x\nbaseline_version: one\n")
    with pytest.raises(CodegenError, match="must be an integer"):
        dictionary_for_spec(bad)
    bad.write_bytes(b"\xff\xfe\x00bad bytes")
    with pytest.raises(CodegenError, match="cannot read"):
        generate(bad, world.dictionary(1))


def test_generated_code_quotes_names_safely() -> None:
    text = generate(BASE_SPEC_PATH, world.dictionary(1))
    assert "raise MalformedError('show_page: unexpected or missing fields')" in text


# --- YAML -----------------------------------------------------------------------------------

LAUGHS = "a: &a [lol, lol, lol, lol, lol, lol, lol, lol, lol]\n" + "".join(
    f"{n}: &{n} [{', '.join(['*' + p] * 9)}]\n" for p, n in zip("abcdefgh", "bcdefghi", strict=True)
)


def test_a_billion_laughs_bomb_is_rejected_instantly_not_expanded() -> None:
    start = time.perf_counter()
    with pytest.raises(YamlError, match="anchors and aliases"):
        load_yaml(LAUGHS)
    assert time.perf_counter() - start < 1.0
    with pytest.raises(ScenarioError, match="not valid YAML"):
        parse_scenario_text(LAUGHS)


@pytest.mark.parametrize(
    "text, message",
    [
        ("a: 1\na: 2\n", "duplicate key 'a'"),
        ("a: &x 1\nb: *x\n", "anchors and aliases"),
        ("base: &b {x: 1}\nd:\n  <<: *b\n", "anchors and aliases"),
        ("? [1, 2]\n: v\n", "plain value"),
        ("a: [unclosed", ""),
        ("{{{", ""),
    ],
)
def test_strict_yaml_rejections(text: str, message: str) -> None:
    with pytest.raises(YamlError, match=message or None):
        load_yaml(text)


@FAST
@given(st.text(max_size=200))
def test_load_yaml_never_raises_anything_but_yaml_error(text: str) -> None:
    with contextlib.suppress(YamlError):
        load_yaml(text)


def test_duplicate_keys_in_a_scenario_are_not_silently_resolved() -> None:
    with pytest.raises(ScenarioError, match="duplicate key 'duration_s'"):
        parse_scenario_text("example: aviation\nduration_s: 5\nduration_s: 500\n")


# --- scenarios ------------------------------------------------------------------------------


@FAST
@given(JSON)
def test_parse_scenario_raises_only_scenario_error(data: Any) -> None:
    with contextlib.suppress(ScenarioError):
        parse_scenario(data)


@FAST
@given(
    st.dictionaries(
        st.sampled_from(
            ["name", "description", "example", "version", "seed", "duration_s", "bridge", "events"]
        ),
        JSON | st.sampled_from(["aviation", "medical", 5, 2.5]),
        max_size=8,
    )
)
def test_scenario_like_dicts_never_crash_the_parser(data: dict[str, Any]) -> None:
    with contextlib.suppress(ScenarioError):
        parse_scenario(data)


@pytest.mark.parametrize("duration", [".inf", "-.inf", ".nan", "1e9", "3601", "0", "-1"])
def test_unbounded_or_non_finite_durations_cannot_hang_the_simulator(duration: str) -> None:
    with pytest.raises(ScenarioError, match="duration_s"):
        parse_scenario_text(f"example: aviation\nduration_s: {duration}\n")


@pytest.mark.parametrize(
    "extra",
    [
        "example: [aviation]",
        "events: [{at: .nan, stall: 1}]",
        "events: [{at: 1, send: {from: [x], message: m, values: {}}}]",
        "events: [{at: 1, send: {from: X, message: 5, values: {}}}]",
        "events: [{at: 1, send: {from: X, message: m, values: {1: 2}}}]",
        "bridge: {processing_delay_s: .inf}",
        "seed: 99999999999999999999.5",
    ],
)
def test_wrongly_typed_scenario_fields_are_explained(extra: str) -> None:
    base = {"example": "aviation", "duration_s": 5}
    text = yaml.safe_dump({k: v for k, v in base.items() if not extra.startswith(k)}) + extra + "\n"
    with pytest.raises(ScenarioError):
        parse_scenario_text(text)


# --- system-level invariants over random scenarios ------------------------------------------


@st.composite
def scenarios(draw: Any) -> dict[str, Any]:
    example = draw(st.sampled_from(["aviation", "medical"]))
    duration = draw(st.floats(1.0, 6.0))
    sender = load_world(example).CONTAINMENT["sender"]
    events: list[dict[str, Any]] = []
    for _ in range(draw(st.integers(0, 2))):
        events.append({"at": draw(st.floats(0, duration)), "stall": draw(st.floats(0.1, 2.0))})
    for _ in range(draw(st.integers(0, 3))):
        key = draw(st.sampled_from(sorted(fault_messages(load_world(example), 1))))
        _, message, values = fault_messages(load_world(example), 1)[key]
        events.append(
            {
                "at": draw(st.floats(0, duration)),
                "send": {"from": sender, "message": message, "values": values},
            }
        )
    return {
        "example": example,
        "version": draw(st.sampled_from([1, 2])),
        "seed": draw(st.integers(0, 10**6)),
        "duration_s": duration,
        "bridge": {
            "time_tag": draw(st.sampled_from(["source", "delivery"])),
            "rate_policy": draw(st.sampled_from(["cap", "extrapolate"])),
            "processing_delay_s": draw(st.floats(0.0, 0.05)),
        },
        "events": events,
    }


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(scenarios())
def test_invariants_hold_for_any_scenario(data: dict[str, Any]) -> None:
    scenario = parse_scenario(data)
    system, _ = run_scenario(scenario, load_world(scenario.example))
    summary = summarize(system)
    json.dumps(summary)  # always serializable
    assert system.network.problems() == []  # G1, whatever happens
    assert system.platform.rejected == []  # the Bridge shields the baseline from every fault
    sends = sum(1 for e in scenario.events if "send" in e)
    assert len(system.platform.accepted) <= sends
    for name, sub in system.subsystems.items():
        per_input: dict[str, list[Any]] = {}
        for r in sub.received:
            assert r.age >= -1e-9, (name, r.native_name)  # information is never from the future
            per_input.setdefault(r.native_name, []).append(r)
        for native, recs in per_input.items():
            real = [r.datum.source_time for r in recs if not r.datum.estimated]
            assert real == sorted(real), (name, native)  # real data never goes backwards in time
        s = summary["subsystems"][name]
        assert s["late"] <= s["received"]
        if s["time_avg_age_ms"] is not None:
            assert s["time_avg_age_ms"] <= s["peak_age_ms"] + 1e-6
    again, _ = run_scenario(scenario, load_world(scenario.example))
    assert summarize(again) == summary  # and fully deterministic


# --- the app: arbitrary forms and malformed HTTP --------------------------------------------

PORT = 8765
HOST = {"Host": f"127.0.0.1:{PORT}"}
JUNK = st.one_of(
    st.sampled_from(
        [
            "",
            "abc",
            "-1",
            "0",
            "1",
            "2.5",
            "8",
            "1e9",
            "1e400",
            "nan",
            "inf",
            "999",
            "<b>x</b>",
            "\x00",
            "ÿ",
        ]
    ),
    st.text(max_size=8),
)
FIELDS = [
    "example",
    "version",
    "duration",
    "seed",
    "time_tag",
    "rate_policy",
    "stall",
    "stall_at",
    "stall_for",
    "fault",
    "fault_at",
    "mode",
    "name",
    "yaml",
    "trials",
    "extra",
]


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    st.sampled_from(["/run", "/run-scenario", "/report", "/rules", "/experiments", "/nowhere"]),
    st.dictionaries(st.sampled_from(FIELDS), JUNK, max_size=8),
)
def test_no_form_input_can_crash_the_app(path: str, fields: dict[str, str]) -> None:
    if (
        fields.get("trials", "10").isascii()
        and fields.get("trials", "10").isdigit()
        and int(fields.get("trials", "10")) > 60
    ):
        fields["trials"] = "20"  # keep the test fast; the bound itself is tested elsewhere
    if (
        fields.get("duration", "5").isascii()
        and fields.get("duration", "5").replace(".", "", 1).isdigit()
        and float(fields.get("duration", "5")) > 8
    ):
        fields["duration"] = "8"
    app = App(PORT)
    fields["t"] = app.token
    resp = app.handle("POST", path, HOST, urllib.parse.urlencode(fields).encode())
    assert resp.status in (200, 404), (path, fields, resp.status)
    body = resp.body.decode("utf-8", "replace")
    assert "Traceback" not in body and "Something went wrong" not in body


@settings(max_examples=60, deadline=None)
@given(st.text(max_size=60), st.dictionaries(st.text(max_size=8), st.text(max_size=8), max_size=3))
def test_odd_urls_and_hosts_never_crash_the_app(path: str, headers: dict[str, str]) -> None:
    app = App(PORT)
    resp = app.handle("GET", "/" + urllib.parse.quote(path), {**headers, **HOST})
    assert resp.status in (200, 404)
    assert app.handle("GET", "/" + path, headers).status == 403 or "Host" in headers


def test_an_unexpected_exception_returns_a_500_page_and_the_app_keeps_serving(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    app = App(PORT)
    original = App._get

    def boom(self: App, path: str, query: Any) -> Any:
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr(App, "_get", boom)
    resp = app.handle("GET", "/", HOST)
    assert (
        resp.status == 500 and b"secret internal detail" not in resp.body
    )  # not leaked to the page
    assert (
        "secret internal detail" in capsys.readouterr().err
    )  # but logged where the user can see it
    monkeypatch.setattr(App, "_get", original)
    assert app.handle("GET", "/", HOST).status == 200


def test_missing_example_files_give_a_friendly_page(monkeypatch: pytest.MonkeyPatch) -> None:
    def gone() -> Path:
        raise FileNotFoundError("examples/ not found")

    monkeypatch.setattr("bridge_island.examples.examples_dir", gone)
    monkeypatch.setattr("bridge_island.ui.examples_dir", gone)
    app = App(PORT)
    resp = app.handle(
        "POST",
        "/rules",
        HOST,
        urllib.parse.urlencode({"t": app.token, "example": "aviation"}).encode(),
    )
    assert resp.status == 200 and b"example files were not found" in resp.body


def raw_request(port: int, request: bytes) -> bytes:
    with socket.create_connection(("127.0.0.1", port), timeout=10) as conn:
        conn.sendall(request)
        chunks = []
        try:
            while chunk := conn.recv(65536):
                chunks.append(chunk)
        except TimeoutError:
            pass
        return b"".join(chunks)


def test_malformed_and_hostile_http_is_refused_without_hanging() -> None:
    import threading

    server, running = make_server(0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    host = f"Host: 127.0.0.1:{running.port}\r\n".encode()
    try:
        bad_length = raw_request(
            running.port, b"POST /run HTTP/1.1\r\n" + host + b"Content-Length: abc\r\n\r\n"
        )
        assert b" 400 " in bad_length.split(b"\r\n", 1)[0]
        negative = raw_request(
            running.port, b"POST /run HTTP/1.1\r\n" + host + b"Content-Length: -5\r\n\r\n"
        )
        assert b" 400 " in negative.split(b"\r\n", 1)[0]
        start = time.perf_counter()
        huge = raw_request(
            running.port,
            b"POST /run HTTP/1.1\r\n" + host + f"Content-Length: {10**9}\r\n\r\nx=1".encode(),
        )
        assert (
            b" 413 " in huge.split(b"\r\n", 1)[0] and time.perf_counter() - start < 5
        )  # no waiting
        assert MAX_BODY < 10**9
        ok = raw_request(
            running.port, b"GET /help HTTP/1.1\r\n" + host + b"Connection: close\r\n\r\n"
        )
        assert b" 200 " in ok.split(b"\r\n", 1)[0]  # still serving after all of that
    finally:
        server.shutdown()
        server.server_close()


def test_shipped_scenarios_contain_nothing_the_strict_loader_rejects() -> None:
    for path in (examples_dir() / "scenarios").glob("*.yaml"):
        parse_scenario_text(path.read_text(encoding="utf-8"), str(path))
    for path in examples_dir().glob("*/mapping_v*.yaml"):
        load_yaml(path.read_text(encoding="utf-8"))
