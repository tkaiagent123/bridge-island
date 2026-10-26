# Contributing

Thanks for your interest. Bug reports, questions, new invented examples and
documentation fixes are welcome.

## Ground rules

1. **Invented content only.** Every platform, message, field, rate and number must be
   invented and generic. Do not add real aircraft, program, supplier, company or product
   names, real interface control documents, bus addresses, data rates or program metrics.
   `scripts/release_check.py` enforces this and runs in CI.
2. Label any numeric output "synthetic, illustrative".
3. Use [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`,
   `docs:`, `test:`, `chore:`).
4. Disclose AI assistance in `docs/AI_USAGE.md` if you used it.

## Development setup

```powershell
uv sync --all-groups
uv run pre-commit install
uv run pytest
uv run ruff check . ; uv run ruff format --check .
uv run mypy
uv run python scripts/release_check.py
```

Everything is cross-platform. Helper tasks are Python scripts, not shell scripts.

## Pull requests

Keep them small and focused. Add or update tests. Tests for the design guidelines are
named after them (for example `test_g3_source_time_preserved`).
