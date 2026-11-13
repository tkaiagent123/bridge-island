"""The ``ai-mapping`` experiment: trials, repair rounds, CSV files, the summary and the plot."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bridge_island import experiments as ex
from bridge_island.aimap import cassette, controls, score
from bridge_island.aimap.evaluate import ACCEPTED, Evaluation, evaluate
from bridge_island.aimap.prompt import build_prompt
from bridge_island.aimap.providers import (
    LIVE,
    Completion,
    Provider,
    ProviderError,
    RecordingProvider,
    ReplayProvider,
    Request,
    cassette_name,
    live_provider,
)
from bridge_island.aimap.stats import bootstrap, ratio
from bridge_island.aimap.task import Task, plain_task
from bridge_island.aimap.variants import VARIANTS, make_tasks
from bridge_island.examples import examples_dir

SHARED = tuple(score.CLASSES)


def cassette_dir(override: Path | None = None) -> Path:
    return override or examples_dir().parent / "experiments" / "ai_mapping" / "cassettes"


def repair_prompt(first: str, reply: str, error: str) -> str:
    """The first prompt again, with the model's last reply and what was wrong with it."""
    return (
        f"{first}\n\n## Your previous reply\n{reply[:6000]}\n\n"
        f"## What was wrong with it\n{error}\n\n"
        "Reply again with the complete corrected YAML document (`mapping` and `confidence`) and "
        "nothing else."
    )


@dataclass
class Trial:
    source: str  # provider name, or the control's name
    model: str
    task: Task
    index: int
    rounds: int  # model calls made, 0 for a control
    evaluation: Evaluation
    tokens_in: int = 0
    tokens_out: int = 0
    latency_s: float = 0.0

    def counts(self) -> Counter[str]:
        return Counter(r.cls for r in self.evaluation.results)


def run_model_trial(task: Task, provider: Provider, index: int, repairs: int) -> Trial:
    first = build_prompt(task)
    prompt, calls = first, 0
    done = Completion("")
    t_in = t_out = 0
    latency = 0.0
    while True:
        done = provider.complete(Request(task.id, index, calls, prompt))
        calls += 1
        t_in, t_out, latency = (
            t_in + done.tokens_in,
            t_out + done.tokens_out,
            latency + done.latency_s,
        )
        ev = evaluate(task, done.text)
        if ev.status == ACCEPTED or calls > repairs:
            return Trial(
                provider.provider, provider.model, task, index, calls, ev, t_in, t_out, latency
            )
        prompt = repair_prompt(first, done.text, ev.error)


def run_control(name: str, task: Task) -> Trial:
    return Trial(name, "control", task, 0, 0, evaluate(task, controls.control_reply(name, task)))


# ---- rows --------------------------------------------------------------------------------------


def trial_row(t: Trial) -> dict[str, Any]:
    counts = t.counts()
    ev = t.evaluation
    row: dict[str, Any] = {
        "provider": t.source,
        "model": t.model,
        "variant": t.task.variant,
        "case": t.task.id,
        "trial": t.index,
        "outcome": ev.status,
        "repair_rounds": max(0, t.rounds - 1),
        **{c: counts.get(c, 0) for c in SHARED},
        "proposal_entries": ev.proposal_entries,
        "review_entries": ev.review_entries,
        "system_differences": len(ev.system_differences),
        "tokens_in": t.tokens_in,
        "tokens_out": t.tokens_out,
        "latency_s": round(t.latency_s, 3),
        "first_error": ev.error.splitlines()[0][:200] if ev.error else "",
    }
    return row


def entry_rows(t: Trial) -> list[dict[str, Any]]:
    return [
        {
            "provider": t.source,
            "model": t.model,
            "variant": t.task.variant,
            "case": t.task.id,
            "trial": t.index,
            "entry": r.entry,
            "class": r.cls,
            "confidence": r.confidence,
            "reason": r.reason,
        }
        for r in t.evaluation.results
    ]


def _sum(rows: Sequence[dict[str, Any]], *keys: str) -> int:
    return sum(r[k] for r in rows for k in keys)


def _entries(r: dict[str, Any]) -> int:
    return int(sum(r[c] for c in SHARED if c not in ("extra",)))


STATS = {
    "correct": lambda rows: ratio(
        _sum(rows, "correct", "removed_handled"), sum(_entries(r) for r in rows)
    ),
    "rejected_by_contract": lambda rows: ratio(
        _sum(rows, "rejected_by_contract"), sum(_entries(r) for r in rows)
    ),
    "unparseable": lambda rows: ratio(_sum(rows, "unparseable"), sum(_entries(r) for r in rows)),
    "wrong_unflagged": lambda rows: ratio(
        _sum(rows, "wrong_unflagged"), sum(_entries(r) for r in rows)
    ),
    "review_share": lambda rows: ratio(
        _sum(rows, "review_entries"), _sum(rows, "proposal_entries")
    ),
    "escape_rate": lambda rows: ratio(
        _sum(rows, "wrong_unflagged"), _sum(rows, "wrong_flagged", "wrong_unflagged")
    ),
    "trials_with_an_escape": lambda rows: ratio(
        sum(1 for r in rows if r["wrong_unflagged"]), len(rows)
    ),
}
DESCRIBE = {
    "correct": "share of entries correct (a handled removal counts as correct)",
    "rejected_by_contract": "share of entries in proposals the contract check rejected",
    "unparseable": "share of entries in replies that were not valid YAML",
    "wrong_unflagged": "share of entries wrong, passing the contract, marked high or unrated",
    "review_share": "share of proposed entries marked low or medium (what a reviewer reads)",
    "escape_rate": "share of wrong, accepted entries a reviewer reading only flagged ones misses",
    "trials_with_an_escape": "share of trials with at least one such missed entry",
}


def summary_rows(trials: Sequence[Trial], seed: int) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for t in trials:
        groups.setdefault((t.source, t.model, t.task.variant), []).append(trial_row(t))
    rows: list[dict[str, Any]] = []
    for (source, model, variant), items in sorted(groups.items()):
        result = bootstrap(items, STATS, seed)
        for name, (value, lo, hi) in result.items():
            rows.append(
                {
                    "provider": source,
                    "model": model,
                    "variant": variant,
                    "metric": name,
                    "value": "" if value is None else round(value, 4),
                    "ci95_low": "" if lo is None else round(lo, 4),
                    "ci95_high": "" if hi is None else round(hi, 4),
                    "trials": len(items),
                    "meaning": DESCRIBE[name],
                }
            )
    return rows


def print_table(rows: Sequence[dict[str, Any]]) -> None:
    by: dict[tuple[str, str, str], dict[str, str]] = {}
    for r in rows:
        key = (r["provider"], r["model"], r["variant"])
        value = "-" if r["value"] == "" else f"{100 * r['value']:.1f}%"
        by.setdefault(key, {"trials": str(r["trials"])})[r["metric"]] = value
    head = [
        "provider",
        "variant",
        "trials",
        "correct",
        "contract-rej",
        "unflagged-wrong",
        "review",
        "escape",
    ]
    table: list[list[str]] = [head]
    for (source, model, variant), m in by.items():
        label = source if model == "control" else f"{source}:{model}"
        table.append(
            [
                label,
                variant,
                m["trials"],
                m["correct"],
                m["rejected_by_contract"],
                m["wrong_unflagged"],
                m["review_share"],
                m["escape_rate"],
            ]
        )
    ex._table(table)


def plot(trials: Sequence[Trial], path: Path) -> Path | None:
    plt = ex._plt()
    if plt is None:
        return None
    groups: dict[str, Counter[str]] = {}
    for t in trials:
        label = (
            f"{t.source}\n{t.task.variant}"
            if t.model == "control"
            else f"{t.model}\n{t.task.variant}"
        )
        groups.setdefault(label, Counter()).update(t.counts())
    labels = list(groups)
    fig, axis = plt.subplots(figsize=(max(5, 1.1 * len(labels)), 4))
    bottom = [0.0] * len(labels)
    for cls in SHARED:
        values = [100 * groups[g][cls] / max(1, sum(groups[g].values())) for g in labels]
        if any(values):
            axis.bar(labels, values, bottom=bottom, label=cls)
            bottom = [b + v for b, v in zip(bottom, values, strict=True)]
    axis.set_ylabel("share of entries (%)")
    axis.set_title(f"AI-proposed mappings: entry classes ({ex.LABEL})")
    axis.legend(fontsize=7, loc="upper left", bbox_to_anchor=(1, 1))
    return ex._save(fig, path)


# ---- the experiment ----------------------------------------------------------------------------


def _replay_providers(directory: Path, model: str | None) -> list[Provider]:
    found: list[Provider] = []
    for path in sorted(directory.glob("*.jsonl")):
        records = cassette.load(path)
        if not records:
            continue
        first = next(iter(records.values()))
        if model and first["model"] != model:
            continue
        found.append(ReplayProvider(first["provider"], first["model"], path))
    return found


def ai_mapping(
    out: Path,
    seed: int,
    example: str,
    provider: str = "replay",
    model: str | None = None,
    trials: int = 1,
    repairs: int = 2,
    variants: Sequence[str] = ("plain",),
    perturbed_n: int = 20,
    cassettes: Path | None = None,
) -> dict[str, Path]:
    bad = [v for v in variants if v not in VARIANTS]
    if bad:
        raise ValueError(f"unknown variant {bad[0]!r}; choose from {', '.join(VARIANTS)}")
    base = plain_task(example)
    tasks = [t for v in variants for t in make_tasks(base, v, seed, perturbed_n)]
    directory = cassette_dir(cassettes)
    if provider == "replay":
        providers = _replay_providers(directory, model)
        if not providers:
            print(f"  no cassettes in {directory}: only the controls were run")
    elif provider in LIVE:
        live = live_provider(provider, model)
        providers = [RecordingProvider(live, directory / cassette_name(live.provider, live.model))]
    else:
        raise ProviderError(f"unknown provider {provider!r}")

    results: list[Trial] = []
    for task in tasks:
        results += [run_control(name, task) for name in controls.CONTROLS]
    skipped = 0
    for prov in providers:
        for task in tasks:
            for i in range(trials):
                try:
                    results.append(run_model_trial(task, prov, i, repairs))
                except cassette.CassetteMiss:
                    skipped += 1
    if skipped:
        print(f"  {skipped} request(s) had no recorded response and were skipped (replay)")
    files = {
        "trials": ex._write_csv(out / "ai_mapping_trials.csv", [trial_row(t) for t in results]),
        "entries": ex._write_csv(
            out / "ai_mapping_entries.csv", [r for t in results for r in entry_rows(t)]
        ),
    }
    summary = summary_rows(results, seed)
    files["summary"] = ex._write_csv(out / "ai_mapping_summary.csv", summary)
    png = plot(results, out / "ai_mapping.png")
    if png:
        files["plot"] = png
    print(f"AI-proposed mappings, {example} ({ex.LABEL}); seed {seed}")
    print_table(summary)
    return files
