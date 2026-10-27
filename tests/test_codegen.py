from dataclasses import replace
from pathlib import Path

import pytest
import world

from bridge_island.codegen import (
    CodegenError,
    Dictionary,
    dictionary_for_spec,
    generate,
    generate_file,
    load_module,
)
from bridge_island.core.transforms import MalformedError, UnmappedError, ValidityError

LOGGER_LINE = "  - {from: PowerStatus.bus_voltage_v, to: StatusLogger.bus_voltage_v}\n"
LON_LINE = "  - {from: NavigationState.longitude, to: PlanningDisplay.nav_lon_deg}\n"
BARO_LINE = (
    "  - {from: AirData.baro_altitude_ft, to: CueingProcessor.baro_alt_m, "
    "transforms: [{unit: [ft, m]}]}\n"
)


def variant(spec_v1: Path, tmp_path: Path, old: str, new: str) -> Path:
    text = spec_v1.read_text(encoding="utf-8")
    assert old in text
    path = tmp_path / "variant.yaml"
    path.write_text(text.replace(old, new), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "old, new, message",
    [
        (
            LON_LINE,
            LON_LINE.replace("}\n", ", transforms: [{scale_offset: {scale: 1.0}}]}\n"),
            "G2",
        ),
        (BARO_LINE, BARO_LINE.replace("[ft, m]", "[ft, km]"), "but the target needs"),
        (LOGGER_LINE, "", "no mapping entry"),
        (LON_LINE, LON_LINE * 2, "mapped twice"),
        (
            LOGGER_LINE,
            LOGGER_LINE.replace("bus_voltage_v, to", "bus_voltage_x, to"),
            "not published",
        ),
        (LON_LINE, LON_LINE.replace("}\n", ", status: removed}\n"), "still publishes"),
        (LON_LINE, LON_LINE.replace("}\n", ", status: pending}\n"), "unknown status"),
        (
            LON_LINE,
            LON_LINE.replace("PlanningDisplay.nav_lon_deg", "PlanningDisplay.nope"),
            "not a declared input",
        ),
        (LON_LINE, LON_LINE.replace("}\n", ", transforms: [{python: 'x+1'}]}\n"), "not permitted"),
        ("baseline_version: 1", "baseline_version: 2", "baseline v2"),
        (
            "from: PlanningDisplay.show_page",
            "from: PlanningDisplay.other",
            "does not declare",
        ),
        ("to: DisplayRequest", "to: EngineCommand", "does not accept"),
        (
            "      - from: priority\n        to: priority",
            "      - from: priority\n        to: page_id",
            "filled exactly once",
        ),
    ],
)
def test_spec_errors_are_loud(
    spec_v1: Path, tmp_path: Path, old: str, new: str, message: str
) -> None:
    path = variant(spec_v1, tmp_path, old, new)
    with pytest.raises(CodegenError, match=message):
        generate(path, world.dictionary(1))


def test_spec_must_be_a_mapping_with_a_name(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("- just a list\n")
    with pytest.raises(CodegenError, match="needs 'name'"):
        generate(path, world.dictionary(1))


def test_uplink_must_map_every_unique_field(spec_v1: Path, tmp_path: Path) -> None:
    old = (
        "      - from: priority\n        to: priority\n"
        "        transforms: [{valid_range: [0, 3]}]\n"
    )
    path = variant(spec_v1, tmp_path, old, "")
    with pytest.raises(CodegenError, match="mapped exactly once"):
        generate(path, world.dictionary(1))


def test_removed_element_is_marked_not_silently_dropped(spec_v1: Path, tmp_path: Path) -> None:
    dic = world.dictionary(1)
    pub = tuple(
        replace(m, elements=tuple(e for e in m.elements if e.name != "baro_altitude_ft"))
        if m.name == "AirData"
        else m
        for m in dic.interface.published
    )
    without = Dictionary(replace(dic.interface, published=pub), dic.needs, dic.outputs)
    # Still mapped as if published: the build fails loudly.
    with pytest.raises(CodegenError, match=r"not published.*status: removed"):
        generate(spec_v1, without)
    # Marked removed: the build succeeds and the removal is recorded in the module.
    removed = BARO_LINE.replace("transforms: [{unit: [ft, m]}]", "status: removed, reason: gone")
    path = variant(spec_v1, tmp_path, BARO_LINE, removed)
    module_path = generate_file(path, without, tmp_path / "m.py")
    module = load_module(module_path)
    assert module.UNAVAILABLE == (
        ("AirData.baro_altitude_ft", "CueingProcessor", "baro_alt_m", "gone"),
    )
    assert all(d[0] != "AirData.baro_altitude_ft" for d in module.DOWNLINKS)


def test_generated_module_translates_and_rejects(spec_v1: Path, tmp_path: Path) -> None:
    mod = load_module(generate_file(spec_v1, world.dictionary(1), tmp_path / "m.py"))
    nav = {
        "latitude": 41.0,
        "longitude": -72.0,
        "altitude": 1000.0,
        "heading": 16384,
        "groundspeed_kt": 100.0,
    }
    out, errors = mod.translate("NavigationState", nav)
    by = {(sub, native): value for _, sub, native, value, _ in out}
    assert errors == []
    assert by[("PlanningDisplay", "nav_alt_m")] == pytest.approx(304.8)
    assert by[("PlanningDisplay", "nav_heading_deg")] == pytest.approx(90.0)
    assert by[("StatusLogger", "altitude_ft")] == 1000.0
    out, errors = mod.translate("NavigationState", {**nav, "latitude": 95.0})
    assert [ref for ref, _ in errors] == ["NavigationState.latitude"]
    assert (
        "outside" in errors[0][1] and len(out) == len(mod.translate("NavigationState", nav)[0]) - 1
    )
    out, errors = mod.translate("NavigationState", {"latitude": 1.0})  # missing fields
    assert errors and mod.translate("Unmapped", {}) == ([], [])
    assert mod.translate_up("PlanningDisplay", "show_page", {"page": "MAP", "priority": 1}) == (
        "DisplayRequest",
        {"page_id": 1, "priority": 1},
    )
    with pytest.raises(MalformedError):
        mod.translate_up("PlanningDisplay", "show_page", {"page": "NOPE", "priority": 1})
    with pytest.raises(MalformedError):
        mod.translate_up("PlanningDisplay", "show_page", {"page": "MAP"})
    with pytest.raises(MalformedError):
        mod.translate_up("PlanningDisplay", "show_page", {"page": "MAP", "priority": "high"})
    with pytest.raises(ValidityError):
        mod.translate_up("PlanningDisplay", "show_page", {"page": "MAP", "priority": 9})
    with pytest.raises(UnmappedError):
        mod.translate_up("PlanningDisplay", "set_gear", {})
    with pytest.raises(UnmappedError):
        mod.translate_up("CueingProcessor", "show_page", {"page": "MAP", "priority": 1})


def test_generation_is_deterministic(spec_v1: Path, tmp_path: Path) -> None:
    a = generate(spec_v1, world.dictionary(1))
    assert a == generate(spec_v1, world.dictionary(1))
    assert generate_file(spec_v1, world.dictionary(1), tmp_path / "x" / "y.py").read_text() == a


def test_dictionary_for_spec_loads_the_world(spec_v1: Path) -> None:
    dic = dictionary_for_spec(spec_v1)
    assert dic.interface.version == 1 and "StatusLogger" in dic.needs
