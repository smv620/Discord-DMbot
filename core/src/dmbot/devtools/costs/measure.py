"""Measures sessions for the cost estimate (#945): `python -m dmbot.devtools.replay --measure`.

No network and no key. For each recording in docs/test-scripts the twin cuts the speech the
way core does (the Segmenter, ears' end-of-speech quiet, the 15 s cut) and counts what would
go to speech-to-text (pieces under the minimum are not sent). Scripts without a recording are
timed from their words. The AI features are then run on that speech with their real prompts
and a recording stand-in for the AI service, which counts the size of what is sent:

- **off-topic filter**: the real window rules (`TopicWindow`) and prompt (`topic_ai`);
- **DM sidebar**: the real engine (`Sidebar`) on the 17 table questions, with the replies
  those test cases give.

Token numbers are estimates by size (`cm.estimate_tokens`), not the AI service's own
counts; docs/costs.md says so, and what run 8 should confirm.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

from dmbot.ai import DEFAULT_MODELS, Reply
from dmbot.audio.segmenter import Segmenter, Utterance
from dmbot.devtools.common import REPO, TEST_SCRIPTS
from dmbot.devtools.costs import model as cm
from dmbot.devtools.costs import report
from dmbot.devtools.replay import audio
from dmbot.devtools.replay.script import Script, load_script
from dmbot.ears.protocol import AudioFrame
from dmbot.transcript import topic_ai
from dmbot.transcript.topics import TopicWindow, Waiting, obviously_game
from dmbot.transcription.base import MIN_UTTERANCE_S

GUILD, SPEAKER = 1, 1001
WORDS_PER_SECOND = 2.5  # a script read at 150 words a minute (the speed of ordinary talk)
GAP_S = 1.0  # between lines, for a script nobody recorded
OUTPUT_TOKENS_PER_LABEL = 3  # "1 game\n": the filter answers one short line per line
FILTER = cm.FILTER
PAIRS = {  # recording -> its script
    "DMOnlyAudio.m4a": "dm-only.md",
    "dm-and-player.m4a": "dm-and-player.md",
}
SCRIPTS_WITHOUT_RECORDING = ("two-voices.md", "bakeoff-story.md")
CASES = REPO / "core" / "tests" / "sidebar_brevity_cases.py"
SAMPLE_SCENE = (
    "Mia: I draw my bow and step behind the pillar.\nSam: The goblin chief snarls and points.\n"
    "Dee: Can I try to calm it down?\nSam: Make a Persuasion check."
)


@dataclass(frozen=True, slots=True)
class Line:
    start_s: float
    seconds: float
    text: str


class RecordingAI:
    """Stands in for the AI service: counts the size of every request and answers from a
    list. Nothing leaves the machine."""

    model = DEFAULT_MODELS.fast

    def __init__(self, replies: Sequence[str] = ("",)) -> None:
        self._replies = list(replies)
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0

    async def complete(self, system: str, text: str, *, max_tokens: int = 8000) -> Reply:
        answer = self._replies[min(self.calls, len(self._replies) - 1)]
        self.calls += 1
        sent = cm.estimate_tokens(system) + cm.estimate_tokens(text)
        got = min(cm.estimate_tokens(answer), max_tokens)
        self.input_tokens += sent
        self.output_tokens += got
        return Reply(answer, False, sent, got)

    def usage(self, *, estimated: bool = True) -> cm.Usage:
        return cm.Usage(self.calls, self.input_tokens, self.output_tokens, self.model, estimated)


# ---- sessions from recordings and scripts ----------------------------------------------


def utterances(pcm: bytes) -> list[Utterance]:
    """The recording cut into what core would send: pieces of speech as ears ends them,
    cut again at 15 seconds."""
    segmenter = Segmenter(GUILD)
    silence = audio.silence_dbfs_for(audio.frame_levels(pcm))
    out: list[Utterance] = []
    for piece in audio.pieces(pcm, silence_dbfs=silence):
        for at_ms, frame in piece.frames:
            if full := segmenter.add(AudioFrame(GUILD, SPEAKER, at_ms, frame)):
                out.append(full)
        if ended := segmenter.end(SPEAKER):
            out.append(ended)
    return out


def sent_lines(lines: Sequence[Line]) -> list[Line]:
    """The lines that reach speech-to-text, and so the filter: shorter ones are counted by
    core but never written down."""
    return [x for x in lines if x.seconds >= MIN_UTTERANCE_S]


def words_for(script: Script, seconds: Sequence[float]) -> list[str]:
    """The script's words dealt out to the pieces by how long each lasts."""
    words = [w.text for w in script.words]
    total = sum(seconds) or 1.0
    lines: list[str] = []
    at = 0
    for index, s in enumerate(seconds):
        count = (
            len(words) - at if index == len(seconds) - 1 else round(len(script.words) * s / total)
        )
        lines.append(" ".join(words[at : at + count]))
        at += count
    return lines


def session_from_recording(path: Path, script: Script) -> cm.Session:
    heard = utterances(audio.decode(path))
    if not heard:
        raise ValueError(f"{path.name}: no speech found")
    lengths = [u.duration_s for u in heard]
    texts = words_for(script, lengths)
    start = heard[0].start_ms / 1000
    lines = [
        Line(u.start_ms / 1000 - start, u.duration_s, t) for u, t in zip(heard, texts, strict=True)
    ]
    table_s = heard[-1].end_ms / 1000 - start
    sent = sent_lines(lines)
    return cm.Session(
        path.name,
        table_s,
        sum(x.seconds for x in sent),
        len(sent),
        {FILTER: filter_usage(sent)},
        measured_audio=True,
    )


_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def session_from_script(script: Script) -> cm.Session:
    """A script nobody recorded: sentences read at ordinary speed with a pause between."""
    sentences = [s for s in _SENTENCE.split(" ".join(w.text for w in script.words)) if s]
    lines: list[Line] = []
    at = 0.0
    for sentence in sentences:
        seconds = len(sentence.split()) / WORDS_PER_SECOND
        lines.append(Line(at, seconds, sentence))
        at += seconds + GAP_S
    sent = sent_lines(lines)
    return cm.Session(
        script.name,
        at - GAP_S,
        sum(x.seconds for x in sent),
        len(sent),
        {FILTER: filter_usage(sent)},
        measured_audio=False,
    )


def filter_usage(lines: Sequence[Line]) -> cm.Usage:
    """What the off-topic filter would cost on these lines: the real rules for what is
    asked about (a line plainly about the game isn't) and when (six lines, or 20 seconds),
    with no campaign names loaded, so no line is spared for naming one. That makes it a
    little high for a real campaign."""
    window = TopicWindow()
    calls = input_tokens = output_tokens = 0

    def ask(waiting: list[Waiting]) -> None:
        nonlocal calls, input_tokens, output_tokens
        texts = [w.text for w in waiting]
        calls += 1
        input_tokens += cm.estimate_tokens(topic_ai.SYSTEM) + cm.estimate_tokens(
            topic_ai.prompt(texts)
        )
        output_tokens += OUTPUT_TOKENS_PER_LABEL * len(texts)

    for i, line in enumerate(lines):
        if window.due(line.start_s):  # the window's timer fired before this line came
            ask(window.take())
        if obviously_game(line.text):
            continue
        waiting = Waiting(SPEAKER, i, line.text, line.seconds)
        if window.add(waiting, line.start_s):
            ask(window.take())
    if window.lines:  # the window's timer asks about what is left
        ask(window.take())
    return cm.Usage(calls, input_tokens, output_tokens, DEFAULT_MODELS.fast, True)


def audio_check_ceiling() -> cm.Usage:
    """The most the audio check can ask in an hour for one speaker: once a minute
    (`audio_check.AI_EVERY_S`), only while their audio is breaking up, each time with the
    last minute of their lines (taken here as ten lines of twelve words). Normal play asks
    for none, so this is a ceiling, not an estimate of use."""
    from dmbot import audio_check

    lines = [" ".join(["word"] * 12) for _ in range(10)]
    per_call_in = cm.estimate_tokens(audio_check.SYSTEM) + cm.estimate_tokens(
        "\n".join(f"{n}. {x}" for n, x in enumerate(lines, 1))
    )
    calls = 3600 / audio_check.AI_EVERY_S
    return cm.Usage(calls, calls * per_call_in, calls * audio_check.ANSWER_TOKENS)


# ---- the sidebar, on its 17 questions ---------------------------------------------------


def CASES_MODULE() -> ModuleType:
    from dmbot.devtools import sidebar_check

    return sidebar_check.load_cases(CASES)


async def sidebar_usage() -> tuple[cm.Usage, int]:
    """AI use of one sidebar question, averaged over the 17 table questions (retries
    included): the real engine, the sample campaign and house rule of the sidebar check,
    and the replies the test cases give."""
    from dmbot.devtools import sidebar_check
    from dmbot.rules.index import srd
    from dmbot.sidebar.answer import Sidebar

    cases = CASES_MODULE().CASES
    total = RecordingAI()
    for case in cases:
        ai = RecordingAI(case.replies)

        async def gate(campaign: object, user: int) -> None:
            return None

        async def houses(campaign: object) -> list[object]:
            return [sidebar_check.SAMPLE_HOUSE_RULE]

        async def names(campaign: object) -> None:
            return None

        sidebar = Sidebar(ai, srd(), gate=gate, houses=houses, names=names)  # type: ignore[arg-type]
        await sidebar.answer(
            sidebar_check.sample_campaign(), case.question, asker_id=1, scene=SAMPLE_SCENE
        )
        total.calls += ai.calls
        total.input_tokens += ai.input_tokens
        total.output_tokens += ai.output_tokens
    n = len(cases)
    return total.usage().scaled(1 / n), n


# ---- the whole measurement ----------------------------------------------------------------


def measure_sessions(scripts: Path = TEST_SCRIPTS) -> list[cm.Session]:
    sessions = [
        session_from_recording(scripts / recording, load_script(scripts / name))
        for recording, name in PAIRS.items()
    ]
    sessions += [
        session_from_script(load_script(scripts / name)) for name in SCRIPTS_WITHOUT_RECORDING
    ]
    return sessions


async def run(args: argparse.Namespace) -> int:
    if not TEST_SCRIPTS.is_dir() or not CASES.is_file():
        print(
            "replay --measure: run this from a repo checkout (pip install -e), which has "
            "docs/test-scripts and core/tests",
            file=sys.stderr,
        )
        return 2
    sessions = measure_sessions()
    sidebar, questions = await sidebar_usage()
    cases = cm.table_hour(
        sessions,
        sidebar=sidebar,
        hosting_monthly=args.hosting_monthly,
        table_hours_per_month=args.table_hours_per_month,
    )
    text = report.render(sessions, sidebar, questions, cases, args, audio_check_ceiling())
    if args.write:
        args.write.write_text(text, encoding="utf-8")
        print(f"Wrote {args.write}")
    else:
        print(text)
    return 0


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="replay --measure", description="Cost per table-hour from the twin's sessions (#945)."
    )
    parser.add_argument(
        "--hosting-monthly", type=float, help="the server's monthly cost, in dollars"
    )
    parser.add_argument(
        "--table-hours-per-month", type=float, help="table-hours served a month (to split hosting)"
    )
    parser.add_argument("--write", type=Path, help="write the report here (docs/costs.md)")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(run(parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
