"""The after-session name scan, scored (#395): after a replay, run the bot's real scan
(`dmbot.memory.scan.find_new_names`) on what was heard, with a campaign's names known as
the bot would know them, and count how many of the story's names it suggests (recall),
what else it suggests, and what it misses.

The campaign's names come from a names-list file (the Add many format), built into the
same in-memory lookup the bot uses, so the real Cleaner fixes misheard known names
exactly as it does at a table, and the hints are the bot's own. The scan runs on the text
as heard, which is what the bot scans today, and on the cleaned text, which is what #394
changes it to. The script's own text is scanned too: the most the scan could find if
every word were heard right.

Not modelled: people at the table (the twin has no display names, so none are skipped).
"""

from __future__ import annotations

import re
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from dmbot.devtools.replay.score import words
from dmbot.devtools.stt_bakeoff.data import NAMES as BAKEOFF_NAMES
from dmbot.memory import scan
from dmbot.memory.lookup import CampaignLookup, LookupData
from dmbot.memory.models import (
    CONFIRMED,
    PROPOSED,
    Alias,
    Entity,
    MemoryRuleError,
    clean_text,
    lookup_key,
    name_key,
)
from dmbot.memory.name_list import parse
from dmbot.memory.scene import SceneTracker, mentions, prepare, scene_hints
from dmbot.memory.sounds import sound_codes
from dmbot.transcript.cleaner import Vocabulary, clean

SPEAKER = 1001  # the twin's one made-up speaker
# Rules words the scripts say with capitals. Not story names: a scan that suggests them is
# offering the DM a spell as a new name, and they take up its 10 places.
RULES_WORDS = (
    "Detect Magic",
    "Cure Wounds",
    "Fireball",
    "Bardic Inspiration",
    "Bardic",
    "Insight",
    "Wisdom",
)
_FENCE = re.compile(r"```[^\n]*\n(.*?)```", re.S)


@dataclass(frozen=True, slots=True)
class Known:
    """A campaign's names, as the bot would hold them."""

    lookup: CampaignLookup
    keys: frozenset[str]  # every name and other name: what the scan skips (`known_keys`)
    count: int  # names (not counting other names)

    @classmethod
    def none(cls) -> Known:
        return cls(CampaignLookup.build(LookupData(1, (), (), (), ())), frozenset(), 0)

    def hints(self) -> list[str]:
        """What the bot sends the speech-to-text at the start of a session: its own
        `scene_hints`, up to three spellings per name, never a secret one."""
        now = time.time()
        return scene_hints(self.lookup, prepare(self.lookup, now), SceneTracker(), now)


def _names_block(text: str) -> str:
    """A names list, or a setup note's first code block that holds one."""
    for block in _FENCE.findall(text):
        if "|" in block:
            return str(block)
    return text


def known_from_text(text: str) -> Known:
    """Names in the Add many format, saved as Add many saves them: a line with no kind,
    or one that sounds like a name already listed, waits as a proposed name (which the
    Cleaner never fixes towards, and which the hints put last); a repeated name is skipped."""
    parsed = parse(_names_block(text), secrets=False)
    if parsed.refused:
        number, why = parsed.refused[0]
        raise ValueError(f"names file, line {number}: {why}")
    entities: list[Entity] = []
    aliases: list[Alias] = []
    taken: set[str] = set()
    confirmed_codes: set[str] = set()
    for index, line in enumerate(parsed.lines):
        try:
            name = clean_text(line.name)
            key = lookup_key(name)
            others = [clean_text(o) for o in line.others]
        except MemoryRuleError as exc:
            raise ValueError(f"names file, line {line.number}: {exc}") from exc
        if key in taken:
            continue
        sounds_known = bool(set(sound_codes(name)) & confirmed_codes)
        status = PROPOSED if line.kind is None or sounds_known else CONFIRMED
        eid = f"{index:032x}"
        entities.append(Entity(eid, line.kind or "concept", name, "", status, None, "dm", 0))
        for n, spelling in enumerate((name, *others)):
            alias_key = lookup_key(spelling)
            if alias_key in taken:
                continue
            taken.add(alias_key)
            kind = "full" if n == 0 else "nickname"
            codes = sound_codes(spelling)
            aliases.append(
                Alias(
                    f"{index:016x}{n:016x}", eid, spelling, alias_key, kind, None, False,
                    status, codes, "dm", 0,
                )
            )  # fmt: skip
            if status == CONFIRMED:
                confirmed_codes.update(codes)
    lookup = CampaignLookup.build(LookupData(1, tuple(entities), tuple(aliases), (), ()))
    return Known(lookup, frozenset(taken), len(entities))


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
    return tuple(StoryName(n.canonical, n.forms) for n in BAKEOFF_NAMES)


@dataclass(slots=True)
class ScanScore:
    offered: int  # suggestions (the bot shows the DM at most 10)
    unknown: int  # story names the campaign doesn't know yet
    found: list[str] = field(default_factory=list)  # story names suggested
    missed: list[str] = field(default_factory=list)  # unknown story names not suggested
    # Known names suggested again: misheard (by sound, as the bot checks) or a part of a
    # longer known name (#399). What #394's pre-filled match is for.
    again: list[str] = field(default_factory=list)
    misheard: list[str] = field(default_factory=list)  # sounds like an unknown story name
    part: list[str] = field(default_factory=list)  # a word of an unknown story name
    rules: list[str] = field(default_factory=list)  # spells and other rules words
    junk: list[str] = field(default_factory=list)  # none of the above


@contextmanager
def _no_limit() -> Iterator[None]:
    # The scan has no limit argument (scan.py is #394's), so its module setting is
    # swapped for one call. No await inside: nothing else can run meanwhile.
    saved = scan.MAX_SUGGESTIONS
    scan.MAX_SUGGESTIONS = 10**6
    try:
        yield
    finally:
        scan.MAX_SUGGESTIONS = saved


def _sounds_like(name: str, codes: set[str]) -> bool:
    return bool(set(sound_codes(name)) & codes)


def _known_sound_codes(known: Known) -> set[str]:
    """Sound codes of confirmed, not secret names: the bot's own check (`_sounds_known`)."""
    return {
        code
        for code, entries in known.lookup.by_sound.items()
        if any(e.confirmed and not e.secret for e in entries)
    }


def score_scan(
    lines: Sequence[str], known: Known, names: Sequence[StoryName], *, limit: bool = True
) -> ScanScore:
    """Run the bot's scan on these lines, skipping what the campaign knows (as the bot's
    `known_keys` does), and sort what it suggests."""
    if limit:
        found = scan.find_new_names(lines, known.keys)
    else:
        with _no_limit():
            found = scan.find_new_names(lines, known.keys)
    unknown = [n for n in names if not {name_key(s) for s in n.spellings} & known.keys]
    known_codes = _known_sound_codes(known)
    unknown_codes = {code for n in unknown for s in n.spellings for code in sound_codes(s)}
    rules = {"".join(words(r)) for r in RULES_WORDS}
    known_words = {w for key in known.keys if " " in key for w in key.split()}
    unknown_words = {w for n in unknown for s in n.spellings if " " in s for w in words(s)}
    result = ScanScore(offered=len(found), unknown=len(unknown))
    hit: set[str] = set()
    for suggestion in found:
        form = "".join(words(suggestion.name))
        match = next((n for n in names if form in n.forms), None)
        if match is not None and match in unknown:
            hit.add(match.name)
        elif match is not None or _sounds_like(suggestion.name, known_codes):
            result.again.append(suggestion.name)  # "Gorak", "Belle Ross"
        elif form in rules:
            result.rules.append(suggestion.name)
        elif form in known_words:
            result.again.append(suggestion.name)  # "Oskar" when Oskar Vane is known
        elif form in unknown_words:
            result.part.append(suggestion.name)
        elif _sounds_like(suggestion.name, unknown_codes):
            result.misheard.append(suggestion.name)  # "Val Zimmer"
        else:
            result.junk.append(suggestion.name)
    result.found = [n.name for n in unknown if n.name in hit]
    result.missed = [n.name for n in unknown if n.name not in hit]
    return result


def scan_record(
    *,
    script: ScanScore,
    as_heard: ScanScore,
    cleaned: ScanScore,
    unlimited: ScanScore,
    script_unlimited: ScanScore,
    known: int,
) -> list[str]:
    """Numbers only, for the record and --log: never what was heard."""

    def line(label: str, s: ScanScore) -> str:
        other = len(s.misheard) + len(s.part) + len(s.junk)
        return (
            f"  {label}: {len(s.found)} of {s.unknown} new story names; also {len(s.again)} "
            f"known names again, {len(s.rules)} rules words, {other} other ({s.offered} "
            "suggested)"
        )

    return [
        f"name scan (campaign knows {known} names). What the DM sees, at most "
        f"{scan.MAX_SUGGESTIONS}:",
        line("as heard (what the bot scans today)", as_heard),
        line("cleaned (what #394 will scan)", cleaned),
        line("script text, heard perfectly", script),
        "name scan without the limit (the real recall):",
        line("cleaned", unlimited),
        line("script text, heard perfectly (the most possible)", script_unlimited),
        "  pre-filled matches: not until #394",
    ]


def scan_details(score: ScanScore) -> list[str]:
    """What it missed and what else it suggested, for the screen only."""

    def names(items: list[str]) -> str:
        return ", ".join(items) or "none"

    return [
        f"missed: {names(score.missed)}",
        f"known names suggested again: {names(score.again)}",
        f"rules words suggested: {names(score.rules)}",
        f"misheard story names: {names(score.misheard)}",
        f"part of a story name: {names(score.part)}",
        f"other: {names(score.junk)}",
    ]
