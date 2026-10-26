import importlib.util
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "release_check.py"
spec = importlib.util.spec_from_file_location("release_check", SCRIPT)
assert spec and spec.loader
release_check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release_check)


def _repo(tmp_path: Path, text: str) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "release_check.toml").write_text(
        'exclude = ["release_check.toml"]\nterms = ["Zorblax"]\npatterns = ["\\\\d+% effort"]\n'
    )
    (tmp_path / "doc.md").write_text(text)
    return tmp_path


def test_clean_repo_has_no_hits(tmp_path: Path) -> None:
    assert release_check.scan(_repo(tmp_path, "nothing to see\n")) == []


def test_term_and_pattern_hits(tmp_path: Path) -> None:
    hits = release_check.scan(_repo(tmp_path, "ok\nthe zorblax unit\nsaved 40% effort\n"))
    assert [h.split(":")[:2] for h in hits] == [["doc.md", "2"], ["doc.md", "3"]]


def test_whole_word_only(tmp_path: Path) -> None:
    assert release_check.scan(_repo(tmp_path, "Zorblaxes\n")) == []
