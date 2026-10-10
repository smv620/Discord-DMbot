"""Re-checks what a replayed live test showed the DM: rules cards and sidebar answers (#1026).

`test_library run --with-rules` runs the real card spotter over the transcript lines, saved and
today's, and compares the cards each would show: no AI, no cost. `--with-sidebar` asks every
sidebar question saved in the session again through the real `Sidebar` answer path (today's
prompt and tier, the real AI, so it costs money) and compares each new answer with the saved
one: its source, "sure" or "not sure", the length rule, and how many of its words are the same.
It judges no style. Both are off by default.

Everything here is numbers and short comparisons; the words of a session are players' words and
never go in the public log (the caller logs only `Counts`).
"""

from __future__ import annotations

import os
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field

from dmbot.ai import FEATURE_TIERS, AnthropicClient, Feature, Reply
from dmbot.campaigns.models import Campaign
from dmbot.config import ConfigError, parse_ai_models
from dmbot.devtools.costs import model as cost_model
from dmbot.devtools.costs import prices
from dmbot.devtools.replay.score import words
from dmbot.devtools.session_replay import Shown
from dmbot.rules.spotter import Spotter
from dmbot.sidebar import brevity

SAME_OVERLAP = 0.8  # this share of the words in common (and the same source) is "same"
SCENE_LINES = 8  # the table lines before the question that the sidebar is given as the scene
QUESTION_KIND, ANSWER_KIND = "sidebar question", "sidebar answer"
_SUFFIX = re.compile(r" \((?P<source>.+?)(?:, (?P<sure>not sure|sure))?\)$")


@dataclass(frozen=True, slots=True)
class Counts:
    """What goes in the log: only how many."""

    same: int = 0
    changed: int = 0
    worse: int = 0
    better: int = 0
    gained: int = 0  # cards only
    lost: int = 0  # cards only
    failed: int = 0  # sidebar only: refused or could not be answered

    def __add__(self, other: Counts) -> Counts:
        return Counts(
            self.same + other.same,
            self.changed + other.changed,
            self.worse + other.worse,
            self.better + other.better,
            self.gained + other.gained,
            self.lost + other.lost,
            self.failed + other.failed,
        )

    def line(self) -> str:
        parts = [f"{n} {name}" for name, n in vars_of(self) if n]
        return ", ".join(parts) or "nothing to compare"


def vars_of(counts: Counts) -> list[tuple[str, int]]:
    return [
        ("same", counts.same),
        ("changed", counts.changed),
        ("worse", counts.worse),
        ("better", counts.better),
        ("gained", counts.gained),
        ("lost", counts.lost),
        ("failed", counts.failed),
    ]


# ---- rules cards ------------------------------------------------------------------------


def cards(lines: Sequence[str], spotter: Spotter) -> dict[tuple[str, str], str]:
    """The cards the live rules pass would show for these lines: each name once (the first
    time it is said), by entry, with the words that made it."""
    shown: dict[tuple[str, str], str] = {}
    for line in lines:
        for mention in spotter.find(line):
            shown.setdefault(mention.key, mention.said)
    return shown


def compare_cards(
    then: dict[tuple[str, str], str], now: dict[tuple[str, str], str]
) -> tuple[Counts, list[str]]:
    """Cards gained, lost or changed (same entry, said differently). The detail lines name
    the entries (free rules text, not a player's words)."""
    same = changed = 0
    detail: list[str] = []
    for key in then.keys() & now.keys():
        if then[key] == now[key]:
            same += 1
        else:
            changed += 1
            detail.append(f"card changed: {key[1]} (said {then[key]!r}, now {now[key]!r})")
    lost = sorted(then.keys() - now.keys())
    gained = sorted(now.keys() - then.keys())
    detail += [f"card lost: {name}" for _, name in lost]
    detail += [f"card gained: {name}" for _, name in gained]
    return Counts(same=same, changed=changed, gained=len(gained), lost=len(lost)), detail


# ---- sidebar answers --------------------------------------------------------------------


def pairs(shown: Sequence[Shown]) -> list[tuple[int, str, str]]:
    """Each saved sidebar question (when it was asked, what it was) with the answer that
    followed it (empty if none did)."""
    out: list[tuple[int, str, str]] = []
    asked: tuple[int, str] | None = None
    answer = ""
    for item in shown:
        if item.kind == QUESTION_KIND:
            if asked is not None:
                out.append((asked[0], asked[1], answer))
            asked, answer = (item.at_ms, item.text), ""
        elif item.kind == ANSWER_KIND and asked is not None and not answer:
            answer = item.text
    if asked is not None:
        out.append((asked[0], asked[1], answer))
    return out


def split_answer(text: str) -> tuple[str, str | None, str | None]:
    """The answer, its source and its "sure" or "not sure" (None when not there)."""
    match = _SUFFIX.search(text)
    if match is None:
        return text, None, None
    return text[: match.start()], match["source"], match["sure"]


def overlap(a: str, b: str) -> float:
    """Words in common, as a share of the words in either (1.0 for the same words)."""
    one, two = set(words(a)), set(words(b))
    return len(one & two) / len(one | two) if one | two else 1.0


@dataclass(frozen=True, slots=True)
class AnswerCheck:
    verdict: str  # same, changed, worse or better
    source_same: bool
    sure_same: bool
    length_then: bool
    length_now: bool
    overlap: float


def compare_answer(then: str, now: str) -> AnswerCheck:
    body_then, source_then, sure_then = split_answer(then)
    body_now, source_now, sure_now = split_answer(now)
    ok_then, ok_now = brevity.within_limit(body_then), brevity.within_limit(body_now)
    shared = overlap(body_then, body_now)
    source_same, sure_same = source_then == source_now, sure_then == sure_now
    if (ok_then and not ok_now) or (sure_then == "sure" and sure_now != "sure"):
        verdict = "worse"
    elif not ok_then and ok_now:
        verdict = "better"
    elif source_same and sure_same and shared >= SAME_OVERLAP:
        verdict = "same"
    else:
        verdict = "changed"
    return AnswerCheck(verdict, source_same, sure_same, ok_then, ok_now, shared)


class CountingAI:
    """Wraps the real AI client and adds up what the questions cost."""

    def __init__(self, inner: object) -> None:
        self._inner = inner
        self.model: str = getattr(inner, "model", "")
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0

    async def complete(self, system: str, text: str, *, max_tokens: int = 8000) -> Reply:
        reply: Reply = await self._inner.complete(system, text, max_tokens=max_tokens)  # type: ignore[attr-defined]
        self.calls += 1
        self.input_tokens += reply.input_tokens
        self.output_tokens += reply.output_tokens
        self.model = reply.model or self.model
        return reply

    def budget(self) -> str:
        """The AI use in a line: calls and tokens, and dollars when the price is on file."""
        line = f"AI {self.calls} calls, {self.input_tokens} tokens in, {self.output_tokens} out"
        usage = cost_model.Usage(
            self.calls, self.input_tokens, self.output_tokens, self.model, estimated=False
        )
        try:
            return f"{line} (${cost_model.ai_dollars(usage):.4f})"
        except prices.UnknownPrice:
            return f"{line} (no price on file for this model)"


AnswerFn = Callable[[str, str], Awaitable[str | None]]  # (question, scene) -> text, or None


@dataclass(slots=True)
class SidebarResult:
    counts: Counts = field(default_factory=Counts)
    detail: list[str] = field(default_factory=list)


async def recheck_sidebar(
    transcript: Sequence[tuple[int, str]],
    asks: list[tuple[int, str, str]],
    answer: AnswerFn,
) -> SidebarResult:
    """Ask each saved question again. `asks`: (at_ms, question, saved answer) in order;
    `transcript`: (at_ms, text) table lines, for the scene. A question that could not be
    answered is counted as failed and the rest go on."""
    result = SidebarResult()
    for number, (at_ms, question, saved) in enumerate(asks, start=1):
        scene = " ".join(t for ms, t in transcript if ms < at_ms)[-1500:]
        new = await answer(question, scene)
        if new is None:
            result.counts += Counts(failed=1)
            result.detail.append(f"sidebar question {number}: could not be answered")
            continue
        if not saved:
            # An answer that was not about the game is not saved: nothing to compare, so not
            # counted either way.
            result.detail.append(f"sidebar question {number}: no saved answer to compare")
            continue
        check = compare_answer(saved, new)
        result.counts += Counts(**{check.verdict: 1})
        if check.verdict != "same":
            result.detail.append(
                f"sidebar question {number}: {check.verdict} "
                f"(source {'same' if check.source_same else 'differs'}, "
                f"sure {'same' if check.sure_same else 'differs'}, "
                f"{check.overlap:.0%} of the words shared)"
            )
    return result


def make_ai(env: dict[str, str] | None = None) -> tuple[AnthropicClient, CountingAI]:
    """The real AI, as the bot's sidebar uses it (tier and model from the settings), counted.
    Raises SystemExit with plain words if the key is not set."""
    values = env if env is not None else dict(os.environ)
    key = values.get("ANTHROPIC_API_KEY", "")
    if not key:
        raise SystemExit("--with-sidebar needs ANTHROPIC_API_KEY (the server's key) to be set.")
    try:
        models, _ = parse_ai_models(lambda name: values.get(name, ""))
    except ConfigError as exc:
        raise SystemExit(str(exc)) from None
    client = AnthropicClient(key, models)
    return client, CountingAI(client.tier(FEATURE_TIERS[Feature.SIDEBAR]))


def sample_campaign() -> Campaign:
    """The campaign the answers are asked about: the newest rules, 2014 as the fallback, no
    house rules (a session does not save them)."""
    from dmbot.devtools.sidebar_check import sample_campaign as sample

    return sample()
