# Cassettes

Recorded model responses for the `ai-mapping` experiment, one `<provider>__<model>.jsonl` per model.
Each line holds the response text, token counts and latency, keyed by a hash of the provider, model,
settings, trial and prompt. The prompt text and any key are never stored.

Replay them offline: `uv run bridge-island experiment ai-mapping --provider replay`.
Record new ones with a live provider; see `docs/experiments.md`, section 5.
A cassette is not edited by hand: if the prompt code changes, its hashes stop matching and the
requests are skipped with a warning until it is recorded again.
