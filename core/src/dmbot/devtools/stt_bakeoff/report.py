"""Turning results into numbers and the decision. No transcribed text is included, so
the report can be committed to the public repo."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from dmbot.devtools.stt_bakeoff import data
from dmbot.devtools.stt_bakeoff.data import LINE_BY_N
from dmbot.devtools.stt_bakeoff.runner import (
    CLEAN,
    LEARNED,
    MAIN,
    RELISTEN,
    TIMING,
    Record,
)
from dmbot.devtools.stt_bakeoff.score import score

# Pay-as-you-go list prices per hour of audio (docs/STT_BAKEOFF.md; check the dashboards).
PRICE_PER_HOUR = {
    "sm-standard": 0.24,
    "sm-enhanced": 0.43,
    "dg-nova3": 0.462,
    "dg-nova3+keyterms": 0.462 + 0.078,
}


def percentile(values: Iterable[float], p: float) -> float | None:
    v = sorted(values)
    if not v:
        return None
    k = (len(v) - 1) * p / 100
    lo, hi = math.floor(k), math.ceil(k)
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


@dataclass(slots=True)
class Summary:
    requests: int = 0
    errors: int = 0
    names_expected: int = 0
    names_correct: int = 0
    false_names: int = 0
    rules_expected: int = 0
    rules_correct: int = 0
    word_errors: int = 0
    ref_words: int = 0
    plain_errors: int = 0  # off-topic and everyday lines only
    plain_words: int = 0
    missed_low_conf: int = 0
    missed_conf_known: int = 0
    audio_s: float = 0.0

    @property
    def name_acc(self) -> float | None:
        return 100 * self.names_correct / self.names_expected if self.names_expected else None

    @property
    def rules_acc(self) -> float | None:
        return 100 * self.rules_correct / self.rules_expected if self.rules_expected else None

    @property
    def wer(self) -> float | None:
        return 100 * self.word_errors / self.ref_words if self.ref_words else None

    @property
    def plain_wer(self) -> float | None:
        return 100 * self.plain_errors / self.plain_words if self.plain_words else None

    @property
    def low_conf_share(self) -> float | None:
        if not self.missed_conf_known:
            return None
        return 100 * self.missed_low_conf / self.missed_conf_known


def summarize(records: Iterable[Record]) -> Summary:
    s = Summary()
    for r in records:
        s.requests += 1
        s.audio_s += r.audio_s
        if r.error:
            s.errors += 1
            continue
        ls = score(r.line, r.words)
        s.names_expected += ls.names_expected
        s.names_correct += ls.names_correct
        s.false_names += len(ls.false_names)
        s.rules_expected += ls.rules_expected
        s.rules_correct += ls.rules_correct
        s.word_errors += ls.word_errors
        s.ref_words += ls.ref_words
        if LINE_BY_N[r.line].category in (data.OFF_TOPIC, data.EVERYDAY):
            s.plain_errors += ls.word_errors
            s.plain_words += ls.ref_words
        s.missed_low_conf += ls.missed_low_conf
        s.missed_conf_known += ls.missed_conf_known
    return s


def _f(v: float | None, digits: int = 1, suffix: str = "") -> str:
    return "–" if v is None else f"{v:.{digits}f}{suffix}"


@dataclass(slots=True)
class Speed:
    final_p50: float | None
    final_p95: float | None
    connect_p50: float | None
    connect_p95: float | None


def speed(records: list[Record]) -> Speed:
    ok = [r for r in records if not r.error]
    return Speed(
        percentile((r.final_s for r in ok), 50),
        percentile((r.final_s for r in ok), 95),
        percentile((r.connect_s for r in ok), 50),
        percentile((r.connect_s for r in ok), 95),
    )


@dataclass(slots=True)
class Relisten:
    tried: int
    recovered: int
    p95_s: float | None

    @property
    def rate(self) -> float | None:
        return 100 * self.recovered / self.tried if self.tried else None


def relisten_stats(records: list[Record]) -> Relisten:
    ok = [r for r in records if not r.error]
    recovered = sum(1 for r in ok if r.target not in score(r.line, r.words).missed)
    return Relisten(len(records), recovered, percentile((r.connect_s + r.final_s for r in ok), 95))


@dataclass(slots=True)
class Decision:
    choice: str
    reasons: list[str]


def decide(
    sm: Summary, dg: Summary, sm_speed: Speed, sm_relisten: Relisten, sm_name: str
) -> Decision:
    """The rule fixed before the run (docs/STT_BAKEOFF.md, "Deciding")."""
    reasons: list[str] = []
    ok = True
    if sm.name_acc is None or dg.name_acc is None:
        return Decision("undecided", ["missing name results"])
    if sm.name_acc < dg.name_acc - 2:
        ok = False
        reasons.append(f"names {sm.name_acc:.1f}% vs Deepgram {dg.name_acc:.1f}% (> 2 points)")
    if sm.false_names > dg.false_names:
        ok = False
        reasons.append(f"false names {sm.false_names} vs Deepgram {dg.false_names}")
    if sm_speed.final_p95 is None or sm_speed.final_p95 > 1.0:
        ok = False
        reasons.append(f"end → final p95 {_f(sm_speed.final_p95, 2)} s (limit 1.0 s)")
    if sm_relisten.p95_s is not None and sm_relisten.p95_s > 1.5:
        ok = False
        reasons.append(f"re-listen p95 {sm_relisten.p95_s:.2f} s (limit 1.5 s)")
    if ok:
        return Decision(sm_name, ["every condition met"])
    return Decision("dg-nova3", reasons)


def build(records: list[Record]) -> str:
    groups: dict[tuple[str, str, str, str], list[Record]] = defaultdict(list)
    for r in records:
        if r.phase in (MAIN, CLEAN, LEARNED):
            groups[(r.phase, r.setup, r.wordlist, r.variant)].append(r)
    lines = [
        "# Speech-to-text bake-off results (#128)",
        "",
        "Numbers only; per-clip results stay on the server. Plan: docs/STT_BAKEOFF.md.",
        "",
        "## Accuracy",
        "",
        "| Phase | Setup | Word list | Audio | Names ✓ | False names | Rules ✓ | WER | "
        "WER (plain lines) | Missed names marked low-confidence | Errors |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for (phase, setup, wl, variant), recs in sorted(groups.items()):
        s = summarize(recs)
        lines.append(
            f"| {phase} | {setup} | {wl} | {variant} | {_f(s.name_acc, suffix='%')} | "
            f"{s.false_names} | {_f(s.rules_acc, suffix='%')} | {_f(s.wer, suffix='%')} | "
            f"{_f(s.plain_wer, suffix='%')} | {_f(s.low_conf_share, 0, '%')} | {s.errors} |"
        )
    lines += [
        "",
        "## Speed (seconds)",
        "",
        "| Setup | Runs | End → final p50 | p95 | Connect p50 | p95 |",
        "|---|---|---|---|---|---|",
    ]
    by_setup: dict[str, list[Record]] = defaultdict(list)
    for r in records:
        if r.phase == TIMING:
            by_setup[f"{r.setup} ({'cold' if r.cold else 'warm'} dictionary)"].append(r)
        elif r.phase == MAIN:
            by_setup[f"{r.setup} (all main runs)"].append(r)
    for name, recs in sorted(by_setup.items()):
        sp = speed(recs)
        lines.append(
            f"| {name} | {len(recs)} | {_f(sp.final_p50, 2)} | {_f(sp.final_p95, 2)} | "
            f"{_f(sp.connect_p50, 2)} | {_f(sp.connect_p95, 2)} |"
        )
    lines += [
        "",
        "## Re-listen (focused list of up to 5 names, audio sent at once)",
        "",
        "| Setup | Missed names re-sent | Recovered | Round trip p95 (s) |",
        "|---|---|---|---|",
    ]
    rl: dict[str, list[Record]] = defaultdict(list)
    for r in records:
        if r.phase == RELISTEN:
            rl[r.setup].append(r)
    for setup, recs in sorted(rl.items()):
        st = relisten_stats(recs)
        lines.append(f"| {setup} | {st.tried} | {_f(st.rate, 0, '%')} | {_f(st.p95_s, 2)} |")
    lines += [
        "",
        "## Audio sent and list-price estimate",
        "",
        "| Setup | Audio minutes | Estimate |",
        "|---|---|---|",
    ]
    audio: dict[str, float] = defaultdict(float)
    for r in records:
        audio[r.setup] += r.audio_s
    for setup, secs in sorted(audio.items()):
        price = PRICE_PER_HOUR.get(setup)
        est = "–" if price is None else f"${secs / 3600 * price:.2f}"
        lines.append(f"| {setup} | {secs / 60:.1f} | {est} |")
    lines += [
        "",
        "Compare with each dashboard's billed amount: a big gap means a minimum "
        "charge per connection.",
        "",
    ]

    def pick(phase: str, setup: str, wl: str) -> Summary:
        recs = [
            r for (p, s, w, _v), rs in groups.items() if (p, s, w) == (phase, setup, wl) for r in rs
        ]
        return summarize(recs)

    dg = pick(MAIN, "dg-nova3", data.SCENE)
    lines += ["## Decision", ""]
    for sm_name in ("sm-enhanced", "sm-standard"):
        sm = pick(MAIN, sm_name, data.SCENE_SL)
        sp = speed(
            by_setup.get(f"{sm_name} (warm dictionary)", [])
            or by_setup.get(f"{sm_name} (all main runs)", [])
        )
        d = decide(sm, dg, sp, relisten_stats(rl.get(sm_name, [])), sm_name)
        lines.append(f"- **{sm_name} vs dg-nova3:** {d.choice}: {'; '.join(d.reasons)}")
    lines.append("")
    return "\n".join(lines)
