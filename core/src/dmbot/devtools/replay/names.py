"""The after-session name scan, scored (#395): after a replay, run the bot's real scan
(`dmbot.memory.scan.find_new_names`) on what was heard, with a campaign's names known as
the bot would know them, and count how many of the story's names it suggests (recall),
how many suggestions aren't story names at all (speech-to-text artefacts), and what it
misses.

The campaign's names come from a names-list file (the Add many format), built into the
same in-memory lookup the bot uses, so the real Cleaner fixes misheard known names
exactly as it does at a table. The scan runs twice: on the text as heard, which is what
the bot scans today, and on the cleaned text, which is what #394 changes it to.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from dmbot.devtools.replay.score import words
from dmbot.devtools.stt_bakeoff.data import NAMES as BAKEOFF_NAMES
from dmbot.memory import scan
from dmbot.memory.lookup import CampaignLookup, LookupData
from dmbot.memory.models import CONFIRMED, Alias, Entity, name_key
from dmbot.memory.name_list import parse
from dmbot.memory.scene import SceneTracker, mentions
from dmbot.memory.sounds import sound_codes
from dmbot.transcript.cleaner import Vocabulary, clean

SPEAKER = 1001  # the twin's one made-up speaker
# The spells the story uses as spells, scored as story names too.
SPELLS = ("Detect Magic", "Cure Wounds", "Fireball", "Bardic Inspiration")


@dataclass(frozen=True, slots=True)
class Known:
    """A campaign's names, as the bot would hold them."""

    lookup: CampaignLookup
    keys: frozenset[str]  # every name and other name: what the scan skips
    names: tuple[str, ...]  # main names, for hints

    @classmethod
    def none(cls) -> Known:
        return cls(CampaignLookup.build(LookupData(1, (), (), (), ())), frozenset(), ())


def known_from_text(text: str) -> Known:
    """Names in the Add many format. A markdown file (a setup note) is read from its
    first ``` block."""
    if "```" in text:
        text = text.split("```")[1]
    parsed = parse(text, secrets=False)
    if parsed.refused:
        number, why = parsed.refused[0]
        raise ValueError(f"names file, line {number}: {why}")
    entities: list[Entity] = []
    aliases: list[Alias] = []
    for index, line in enumerate(parsed.lines):
        eid = f"{index:032x}"
        entities.append(
            Entity(eid, line.kind or "concept", line.name, "", CONFIRMED, None, "dm", 0)
        )
        for n, spelling in enumerate((line.name, *line.others)):
            aid = f"{index:016x}{n:016x}"
            aliases.append(
                Alias(
                    aid, eid, spelling, name_key(spelling), "full", None, False, CONFIRMED,
                    sound_codes(spelling), "dm", 0,
                )
            )  # fmt: skip
    lookup = CampaignLookup.build(LookupData(1, tuple(entities), tuple(aliases), (), ()))
    return Known(lookup, frozenset(a.key for a in aliases), tuple(e.name for e in entities))


def load_known(path: Path) -> Known:
    return known_from_text(path.read_text(encoding="utf-8"))


def clean_lines(known: Known, heard: Sequence[tuple[float, str]]) -> list[str]:
    """Each line as the transcript channel would show it: misheard known names fixed by
    the real Cleaner, with the session's vocabulary and scene built up line by line as
    the bot builds them (`bot._deliver_transcript`). `heard`: (seconds, text) in order."""
    vocabulary = Vocabulary()
    tracker = SceneTracker()
    out = []
    for now, text in heard:
        cleaned = clean(
            known.lookup, text, vocabulary=vocabulary, scene=tracker.scene(now).keys()
        ).text
        tracker.note(mentions(known.lookup, cleaned), SPEAKER, now)
        vocabulary.note(SPEAKER, text)  # after cleaning: a line is never evidence about itself
        out.append(cleaned)
    return out


@dataclass(frozen=True, slots=True)
class StoryName:
    name: str
    spellings: tuple[str, ...]

    @property
    def forms(self) -> frozenset[str]:
        return frozenset("".join(words(s)) for s in self.spellings)


def story_names() -> tuple[StoryName, ...]:
    """Every name in the bake-off scripts and the story (#367), with accepted spellings."""
    return (
        *(StoryName(n.canonical, n.forms) for n in BAKEOFF_NAMES),
        *(StoryName(s, (s,)) for s in SPELLS),
    )


@dataclass(slots=True)
class ScanScore:
    offered: int  # suggestions the DM would see (the bot shows at most 10)
    unknown: int  # story names the campaign doesn't know yet
    found: list[str] = field(default_factory=list)  # story names suggested
    missed: list[str] = field(default_factory=list)  # unknown story names not suggested
    false: list[str] = field(default_factory=list)  # suggestions that aren't story names
    # Misheard known names suggested as new ones: what #394's pre-filled match is for.
    again: list[str] = field(default_factory=list)


@contextmanager
def _no_limit() -> Iterator[None]:
    saved = scan.MAX_SUGGESTIONS
    scan.MAX_SUGGESTIONS = 10**6
    try:
        yield
    finally:
        scan.MAX_SUGGESTIONS = saved


def _sounds_known(known: Known, name: str) -> bool:
    """It sounds like a name the campaign knows (as the bot's own check does): what #394
    will offer as a pre-filled match."""
    key = name_key(name)
    return any(
        entry.key != key
        for code in sound_codes(name)
        for entry in known.lookup.by_sound.get(code, ())
    )


def score_scan(
    lines: Sequence[str], known: Known, names: Sequence[StoryName], *, limit: bool = True
) -> ScanScore:
    """Run the bot's scan on these lines, skipping what the campaign knows (as the bot's
    `known_keys` does), and compare what it suggests with the story's names."""
    if limit:
        found = scan.find_new_names(lines, known.keys)
    else:
        with _no_limit():
            found = scan.find_new_names(lines, known.keys)
    unknown = [n for n in names if not {name_key(s) for s in n.spellings} & known.keys]
    result = ScanScore(offered=len(found), unknown=len(unknown))
    hit: set[str] = set()
    for suggestion in found:
        form = "".join(words(suggestion.name))
        match = next((n for n in names if form in n.forms), None)
        if match is None:
            if _sounds_known(known, suggestion.name):
                result.again.append(suggestion.name)  # "Belle Ross" for Belleros
            else:
                result.false.append(suggestion.name)
        elif match in unknown:
            hit.add(match.name)
        else:
            result.again.append(suggestion.name)
    result.found = [n.name for n in unknown if n.name in hit]
    result.missed = [n.name for n in unknown if n.name not in hit]
    return result


def scan_record(
    as_heard: ScanScore, cleaned: ScanScore, unlimited: ScanScore, known: int
) -> list[str]:
    """Numbers only, for the record and --log: never what was heard."""

    def line(label: str, s: ScanScore) -> str:
        return (
            f"{label}: {s.offered} suggested; {len(s.found)} of {s.unknown} new story names "
            f"found, {len(s.again)} known names suggested again, {len(s.false)} not story names"
        )

    return [
        f"name scan (campaign knows {known} names; the bot suggests at most "
        f"{scan.MAX_SUGGESTIONS}):",
        "  " + line("as heard (what the bot scans today)", as_heard),
        "  " + line("cleaned (what #394 will scan)", cleaned),
        "  " + line("cleaned, without the limit", unlimited),
        "  pre-filled matches: not until #394",
    ]


def scan_details(score: ScanScore) -> list[str]:
    """The misses and false names, for the screen only."""
    return [
        f"missed: {', '.join(score.missed) or 'none'}",
        f"known names suggested again: {', '.join(score.again) or 'none'}",
        f"not story names: {', '.join(score.false) or 'none'}",
    ]
