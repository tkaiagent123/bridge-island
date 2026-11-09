"""Build the in-browser app bundle: docs/online/bridge_island_web.zip.

The zip holds the package, the examples and the glue module, so the page can unpack it into
Pyodide. It is a build product (git-ignored); the docs workflow runs this before mkdocs.
"""

import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "online" / "bridge_island_web.zip"


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
        for path in sorted((ROOT / "src" / "bridge_island").rglob("*.py")):
            if "__pycache__" not in path.parts:
                z.write(path, path.relative_to(ROOT / "src").as_posix())
        for path in sorted((ROOT / "examples").rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                z.write(path, path.relative_to(ROOT).as_posix())
        z.write(ROOT / "docs" / "online" / "bridge_web.py", "bridge_web.py")
    print(f"wrote {OUT.relative_to(ROOT)} ({OUT.stat().st_size // 1024} KiB)")


if __name__ == "__main__":
    main()
