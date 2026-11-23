"""How noisy the sample run is (#3545; Supervisor's ruling of 2026-11-15): each count over the
runs of one deploy as its median, lowest, highest and spread, and the rule for comparing two
deploys. Pure: counts in, lines out.

The rule: a count moved between two deploys only when the medians differ by more than the larger
of the two spreads. Anything smaller is noise and is said to be noise.

The sure step is plain code. Its counts vary only when what it is given varies, so a spread in a
sure count is a bug only when the lore read (and, for the session, the import) it was given had no
spread: `sure_flags` names those, for an issue.
"""

from __future__ import annotations

import json
import re
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

Metrics = Mapping[str, float]

# A step's own name starts each measure ("sure (lore): accepted"); these are the sure steps and
# the steps that feed them (`plan.STEPS_DECIDE`)
# (the session's sure step comes after the AI's lore decisions and the session import, which vary
# by design, so it is flagged only when none of them varied either)
SURE_INPUTS = {
    "sure (lore)": ("lore read",),
    "sure (session)": ("lore read", "sure (lore)", "AI decides (lore)", "session import"),
}
_MONEY = re.compile(r"^\$(\d+(?:\.\d+)?)$")


@dataclass(frozen=True, slots=True)
class Stat:
    median: float
    low: float
    high: float
    runs: int

    @property
    def spread(self) -> float:
        return self.high - self.low


def number(value: object) -> float | None:
    """A count as a number: an int, a float, or "$0.04"; None for words (a stage name)."""
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str) and (m := _MONEY.match(value.strip())):
        return float(m.group(1))
    return None


def step_metrics(step: str, counts: Mapping[str, object]) -> dict[str, float]:
    """One step's numeric counts, named "step: count"."""
    out: dict[str, float] = {}
    for key, value in counts.items():
        n = number(value)
        if n is not None:
            out[f"{step}: {key}"] = n
    return out


def summarize(runs: Sequence[Metrics]) -> dict[str, Stat]:
    """Each measure over the runs that have it, in first-seen order. A run that stopped early
    simply has fewer measures."""
    names: list[str] = []
    for run in runs:
        names += [n for n in run if n not in names]
    out: dict[str, Stat] = {}
    for name in names:
        values = [run[name] for run in runs if name in run]
        out[name] = Stat(statistics.median(values), min(values), max(values), len(values))
    return out


def _n(x: float) -> str:
    return f"{x:g}" if x != int(x) else str(int(x))


def render(stats: Mapping[str, Stat]) -> list[str]:
    """One line a measure: "name: median M (low L to high H, spread S, over N runs)"."""
    return [
        f"{name}: median {_n(s.median)} ({_n(s.low)} to {_n(s.high)}, spread {_n(s.spread)}, "
        f"{s.runs} run{'s' if s.runs != 1 else ''})"
        for name, s in stats.items()
    ]


def compare(now: Mapping[str, Stat], before: Mapping[str, Stat]) -> list[str]:
    """Each measure both deploys have: "changed" only when the medians differ by more than the
    larger spread, else "noise"; unchanged medians are left out."""
    out: list[str] = []
    for name, s in now.items():
        b = before.get(name)
        if b is None or s.median == b.median:
            continue
        moved = s.median - b.median
        word = "changed" if abs(moved) > max(s.spread, b.spread) else "noise"
        out.append(
            f"{name}: {_n(b.median)} → {_n(s.median)} ({'+' if moved > 0 else ''}{_n(moved)}; "
            f"spreads {_n(b.spread)} and {_n(s.spread)}): {word}"
        )
    return out


def sure_flags(stats: Mapping[str, Stat]) -> list[str]:
    """Sure-step measures with a spread although every count of the steps that feed them had
    none: a bug (Supervisor: open an issue)."""
    out: list[str] = []
    for step, inputs in SURE_INPUTS.items():
        fed = [s for n, s in stats.items() if n.split(": ", 1)[0] in inputs]
        if not fed or any(s.spread for s in fed):
            continue
        out += [
            name
            for name, s in stats.items()
            if name.split(": ", 1)[0] == step and s.spread and s.runs > 1
        ]
    return out


def save(path: Path, stats: Mapping[str, Stat], *, commit: str, version: str = "typed") -> None:
    """The deploy's summary, counts only, for the next deploy to compare with. `version`: which
    version of the sample session it scored (docs/design/session_names.md, section 8)."""
    path.write_text(
        json.dumps(
            {
                "commit": commit,
                "version": version,
                "stats": {k: asdict(v) for k, v in stats.items()},
            },
            indent=1,
        )
    )


def version_of(path: Path) -> str:
    """The session version a saved summary scored: "typed" for every summary written before the
    versions existed (they had no other)."""
    data = json.loads(path.read_text())
    return str(data.get("version") or "typed")


def load(path: Path) -> tuple[str, dict[str, Stat]]:
    data = json.loads(path.read_text())
    return str(data.get("commit", "")), {
        k: Stat(float(v["median"]), float(v["low"]), float(v["high"]), int(v["runs"]))
        for k, v in data["stats"].items()
    }
