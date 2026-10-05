"""Turning results into numbers and the decision. No transcribed text is included, so
the report can be committed to the public repo."""

from __future__ import annotations

import math
import random
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
    "dg-nova3 + keyterms": 0.462 + 0.078,
}
MAX_ERROR_SHARE = 0.05  # more failed requests than this and nothing is decided
NAME_MARGIN = 2.0  # points
TOO_CLOSE_WIDTH = 8.0  # a 95% interval wider than ±4 points can't settle names
FALSE_NAME_MARGIN = 2  # false names: Speechmatics may not have 2 or more extra
SPEED_LIMIT_S = 1.0  # end of speech -> final text, slowest 5%
RELISTEN_LIMIT_S = 1.5  # re-listen round trip, slowest 5%


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
    correct_low_conf: int = 0
    correct_conf_known: int = 0
    audio_s: float = 0.0
    keyterms: str = ""

    @staticmethod
    def _pct(num: int, den: int) -> float | None:
        return 100 * num / den if den else None

    @property
    def name_acc(self) -> float | None:
        return self._pct(self.names_correct, self.names_expected)

    @property
    def rules_acc(self) -> float | None:
        return self._pct(self.rules_correct, self.rules_expected)

    @property
    def wer(self) -> float | None:
        return self._pct(self.word_errors, self.ref_words)

    @property
    def plain_wer(self) -> float | None:
        return self._pct(self.plain_errors, self.plain_words)

    @property
    def error_share(self) -> float:
        return self.errors / self.requests if self.requests else 0.0


def summarize(records: Iterable[Record]) -> Summary:
    s = Summary()
    keyterms: set[str] = set()
    for r in records:
        s.requests += 1
        s.audio_s += r.audio_s
        keyterms.update(p.split(" ", 1)[1] for p in r.note.split("; ") if p.startswith("keyterms"))
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
        s.correct_low_conf += ls.correct_low_conf
        s.correct_conf_known += ls.correct_conf_known
    s.keyterms = ", ".join(sorted(keyterms))
    return s


def _f(v: float | None, digits: int = 1, suffix: str = "") -> str:
    return "–" if v is None else f"{v:.{digits}f}{suffix}"


# ---- paired comparison -------------------------------------------------------------


@dataclass(slots=True)
class Paired:
    """A vs B on the clips both returned without error."""

    clips: int
    names: int
    gap: float | None  # A minus B, in points
    low: float | None  # 95% bootstrap interval for the gap
    high: float | None
    false_a: int
    false_b: int


def paired(a: list[Record], b: list[Record], seed: int = 7, rounds: int = 2000) -> Paired:
    """Name accuracy gap with a 95% interval, resampling by *name* (each name is said
    several times, so its results aren't independent)."""
    by_a = {(r.reader, r.line): r for r in a if not r.error}
    by_b = {(r.reader, r.line): r for r in b if not r.error}
    common = sorted(by_a.keys() & by_b.keys())
    per_name: dict[str, list[tuple[int, int]]] = defaultdict(list)
    false_a = false_b = 0
    for key in common:
        sa, sb = score(key[1], by_a[key].words), score(key[1], by_b[key].words)
        false_a += len(sa.false_names)
        false_b += len(sb.false_names)
        for name in LINE_BY_N[key[1]].names:
            per_name[name].append((int(name in sa.correct), int(name in sb.correct)))
    pairs = [p for ps in per_name.values() for p in ps]
    if not pairs:
        return Paired(len(common), 0, None, None, None, false_a, false_b)

    def gap(groups: list[list[tuple[int, int]]]) -> float:
        flat = [p for g in groups for p in g]
        return 100 * sum(x - y for x, y in flat) / len(flat)

    groups = list(per_name.values())
    rng = random.Random(seed)
    boots = sorted(gap([rng.choice(groups) for _ in groups]) for _ in range(rounds))
    low = boots[int(0.025 * rounds)]
    high = boots[int(0.975 * rounds) - 1]
    return Paired(len(common), len(pairs), gap(groups), low, high, false_a, false_b)


# ---- speed and re-listen ------------------------------------------------------------


@dataclass(slots=True)
class Speed:
    runs: int
    final_p50: float | None  # end of speech -> final text
    final_p95: float | None
    connect_p50: float | None
    connect_p95: float | None


def speed(records: list[Record]) -> Speed:
    ok = [r for r in records if not r.error]
    finals = [r.speech_to_final_s for r in ok]
    return Speed(
        len(ok),
        percentile(finals, 50),
        percentile(finals, 95),
        percentile((r.connect_s for r in ok), 50),
        percentile((r.connect_s for r in ok), 95),
    )


@dataclass(slots=True)
class Relisten:
    tried: int  # requests that returned (errors are counted separately)
    errors: int
    shortlist_hits: int
    recovered: int  # the right name came back
    new_false_names: int
    p95_s: float | None  # connection open -> final text

    @property
    def hit_rate(self) -> float | None:
        return 100 * self.shortlist_hits / self.tried if self.tried else None

    @property
    def rate(self) -> float | None:
        return 100 * self.recovered / self.tried if self.tried else None


def relisten_stats(records: list[Record]) -> Relisten:
    real = [r for r in records if r.wordlist == "shortlist"]
    ok = [r for r in real if not r.error]
    recovered = false = 0
    for r in ok:
        s = score(r.line, r.words)
        recovered += r.target in s.correct
        false += len(s.false_names)
    return Relisten(
        len(ok),
        len(real) - len(ok),
        sum(1 for r in ok if r.shortlist_hit),
        recovered,
        false,
        percentile((r.total_s for r in ok), 95),
    )


def control_false_names(records: list[Record]) -> tuple[int, int]:
    ctrl = [r for r in records if r.wordlist == "control" and not r.error]
    return sum(len(score(r.line, r.words).false_names) for r in ctrl), len(ctrl)


# ---- the decision --------------------------------------------------------------------


@dataclass(slots=True)
class Decision:
    choice: str
    reasons: list[str]


def decide(
    names: Paired,
    errors: tuple[float, float],
    sm_speed: Speed,
    sm_relisten: Relisten,
    sm_name: str,
) -> Decision:
    """The rule fixed before the run (docs/STT_BAKEOFF.md, "Deciding")."""
    if max(errors) > MAX_ERROR_SHARE:
        return Decision("undecided", [f"too many failed requests ({max(errors):.0%})"])
    if names.gap is None or names.low is None or names.high is None:
        return Decision("undecided", ["no paired name results"])
    if sm_speed.runs == 0:
        return Decision("undecided", ["no timing results: run the timing phase"])
    reasons: list[str] = []
    ok = True
    too_close = names.high - names.low > TOO_CLOSE_WIDTH
    if too_close:
        reasons.append(
            f"names too close to call ({names.gap:+.1f}, 95% {names.low:+.1f} to "
            f"{names.high:+.1f}); decided on false names, speed and cost"
        )
    elif names.low < -NAME_MARGIN:
        ok = False
        reasons.append(
            f"names {names.gap:+.1f} points (95% {names.low:+.1f} to "
            f"{names.high:+.1f}): may be more than {NAME_MARGIN:.0f} worse"
        )
    else:
        reasons.append(f"names {names.gap:+.1f} points (95% {names.low:+.1f} to {names.high:+.1f})")
    if names.false_a >= names.false_b + FALSE_NAME_MARGIN:
        ok = False
        reasons.append(f"false names {names.false_a} vs {names.false_b}")
    if sm_speed.final_p95 is None or sm_speed.final_p95 > SPEED_LIMIT_S:
        ok = False
        reasons.append(
            f"end of speech → final p95 {_f(sm_speed.final_p95, 2)} s (limit {SPEED_LIMIT_S} s)"
        )
    if sm_relisten.tried and (sm_relisten.p95_s or 0) > RELISTEN_LIMIT_S:
        ok = False
        reasons.append(f"re-listen p95 {sm_relisten.p95_s:.2f} s (limit {RELISTEN_LIMIT_S} s)")
    if sm_relisten.errors and not sm_relisten.tried:
        return Decision("undecided", ["every re-listen request failed"])
    return Decision(sm_name if ok else "dg-nova3", reasons)


def standard_vs_enhanced(p: Paired) -> str:
    if p.low is None or p.gap is None:
        return "undecided (no paired results)"
    if p.low >= -NAME_MARGIN:
        return f"Standard is good enough ({p.gap:+.1f} points vs Enhanced, 95% from {p.low:+.1f})"
    return f"Enhanced ({p.gap:+.1f} points for Standard, 95% from {p.low:+.1f})"


# ---- the report -----------------------------------------------------------------------


def _cost_label(r: Record) -> str:
    if r.setup == "dg-nova3" and r.vocab_size:
        return "dg-nova3 + keyterms"
    return r.setup


def build(records: list[Record]) -> str:
    groups: dict[tuple[str, str, str, str], list[Record]] = defaultdict(list)
    for r in records:
        if r.phase in (MAIN, CLEAN, LEARNED):
            groups[(r.phase, r.setup, r.wordlist, r.variant)].append(r)
    out = [
        "# Speech-to-text bake-off results (#128)",
        "",
        "Numbers only; per-clip results stay on the server. Plan: docs/STT_BAKEOFF.md.",
        "Speechmatics ran with max_delay 1.0 s and ForceEndOfUtterance at the end of each "
        "clip; Deepgram with Finalize. Speed counts from the end of speech.",
        "",
        "## Accuracy",
        "",
        "| Phase | Setup | Word list | Audio | Names ✓ | False names | Rules ✓ | WER | "
        "WER (plain lines) | Missed names flagged | Correct names flagged | Keyterms used "
        "| Errors |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for (phase, setup, wl, variant), recs in sorted(groups.items()):
        s = summarize(recs)
        missed_flag = Summary._pct(s.missed_low_conf, s.missed_conf_known)
        correct_flag = Summary._pct(s.correct_low_conf, s.correct_conf_known)
        out.append(
            f"| {phase} | {setup} | {wl} | {variant} | {_f(s.name_acc, suffix='%')} | "
            f"{s.false_names} | {_f(s.rules_acc, suffix='%')} | {_f(s.wer, suffix='%')} | "
            f"{_f(s.plain_wer, suffix='%')} | {_f(missed_flag, 0, '%')} | "
            f"{_f(correct_flag, 0, '%')} | {s.keyterms or '–'} | {s.errors} |"
        )
    out += [
        "",
        '"Flagged" = confidence below 0.7. Good: most missed names flagged, few correct ones.',
        "",
    ]

    out += [
        "## Speed (seconds, end of speech → final text)",
        "",
        "| Setup | Runs | p50 | p95 | Connect p50 | Connect p95 |",
        "|---|---|---|---|---|---|",
    ]
    timing: dict[str, list[Record]] = defaultdict(list)
    for r in records:
        if r.phase == TIMING:
            timing[f"{r.setup} ({'cold' if r.cold else 'warm'} dictionary)"].append(r)
    for name, recs in sorted(timing.items()):
        sp = speed(recs)
        out.append(
            f"| {name} | {sp.runs} | {_f(sp.final_p50, 2)} | {_f(sp.final_p95, 2)} | "
            f"{_f(sp.connect_p50, 2)} | {_f(sp.connect_p95, 2)} |"
        )
    out.append("")

    rl: dict[str, list[Record]] = defaultdict(list)
    for r in records:
        if r.phase == RELISTEN:
            rl[r.setup].append(r)
    out += [
        "## Re-listen (shortlist of 5 names closest to what was heard, audio sent at once)",
        "",
        "| Setup | Re-sent | Right name in shortlist | Recovered | New false names | "
        "Round trip p95 (s) | Controls: names forced onto trap lines | Errors |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for setup, recs in sorted(rl.items()):
        st = relisten_stats(recs)
        forced, n = control_false_names(recs)
        out.append(
            f"| {setup} | {st.tried} | {_f(st.hit_rate, 0, '%')} | "
            f"{_f(st.rate, 0, '%')} | {st.new_false_names} | {_f(st.p95_s, 2)} | "
            f"{forced} of {n} | {st.errors} |"
        )
    out.append("")

    out += [
        "## Audio sent and list-price estimate",
        "",
        "| Setup | Audio minutes | Estimate |",
        "|---|---|---|",
    ]
    audio: dict[str, float] = defaultdict(float)
    for r in records:
        audio[_cost_label(r)] += r.audio_s
    for label, secs in sorted(audio.items()):
        price = PRICE_PER_HOUR.get(label)
        est = "–" if price is None else f"${secs / 3600 * price:.2f}"
        out.append(f"| {label} | {secs / 60:.1f} | {est} |")
    out += [
        "",
        "Compare with each dashboard's billed amount: a big gap means a minimum "
        "charge per connection.",
        "",
    ]

    def main_recs(setup: str, wl: str) -> list[Record]:
        return [
            r for (p, s, w, _v), rs in groups.items() if (p, s, w) == (MAIN, setup, wl) for r in rs
        ]

    dg = main_recs("dg-nova3", data.SCENE)
    out += ["## Decision", ""]
    for sm_name in ("sm-enhanced", "sm-standard"):
        sm = main_recs(sm_name, data.SCENE_SL)
        if not sm or not dg:
            continue
        names = paired(sm, dg)
        warm = timing.get(f"{sm_name} (warm dictionary)")
        speed_note = ""
        if not warm:  # only Enhanced gets the timing phase; Standard uses its numbers
            warm = timing.get("sm-enhanced (warm dictionary)", [])
            speed_note = " (speed from sm-enhanced)"
        d = decide(
            names,
            (summarize(sm).error_share, summarize(dg).error_share),
            speed(warm),
            relisten_stats(rl.get(sm_name, [])),
            sm_name,
        )
        out.append(
            f"- **{sm_name} (Scene + sounds like) vs dg-nova3 (Scene):** "
            f"**{d.choice}**. {'; '.join(d.reasons)}{speed_note}. Paired on "
            f"{names.clips} clips, {names.names} names."
        )
    std, enh = main_recs("sm-standard", data.SCENE_SL), main_recs("sm-enhanced", data.SCENE_SL)
    if std and enh:
        out.append(f"- **Standard vs Enhanced:** {standard_vs_enhanced(paired(std, enh))}.")
    out.append("")
    return "\n".join(out)
