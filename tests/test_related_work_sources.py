"""The Air Force standards entry in docs/related-work.md keeps its sources, with no percentages."""

from pathlib import Path

DOC = Path(__file__).resolve().parent.parent / "docs" / "related-work.md"


def test_air_force_standards_entry_cites_its_sources() -> None:
    text = DOC.read_text(encoding="utf-8")
    for url in (
        "https://github.com/open-arsenal/oms",
        "https://github.com/open-arsenal/a-gra",
        "https://github.com/open-arsenal/ams-gra",
    ):
        assert url in text
    assert "OMSC-STD-001" in text and "ASK 6.0a" in text
    section = text.split("## Open mission-system standards", 1)[1]
    assert not [ln for ln in section.splitlines() if "%" in ln]


def test_mosa_policy_entry_is_cited() -> None:
    text = DOC.read_text(encoding="utf-8")
    section = text.split("## Policy and oversight context", 1)[1].split("\n## ", 1)[0]
    assert "Minimum Expectations" in section
    assert "https://www.linkedin.com/posts/asd-acquisition_establishment-dow-mosa" in section
    assert not [ln for ln in section.splitlines() if "%" in ln]
