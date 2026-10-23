"""A list of names in plain text: 📥 Add many and 📤 Download all (docs/PLAN.md, "Names
at scale", step 3; #126). Pure: no Discord, no database.

One name per line: `name | kind | other names | secret names`. Only the name is needed;
other names and secret names are separated by `,` or `;` (as in the Add a name form).
Lines starting with `#` are notes and are skipped, so the template's instructions and a
download's header can stay in the file when it's uploaded again.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from dmbot.memory.models import NAME_MAX, name_key
from dmbot.memory.ontology import PERSON_NOT_LISTED, SPELL_NOT_KEPT, UNNAMED_MONSTER, is_dm_word
from dmbot.memory.scan import alike, sound_keys
from dmbot.rules import index

MAX_FILE_BYTES = 256 * 1024
MAX_LINES = 2000
MAX_NAMES = 10_000  # a campaign holds up to about this many
MAX_WORDS = 8  # more than this is a description, not a name
# Other names, and secret names, on one line (#598): a worst-case file stays bounded.
# More go on another line for the same name.
MAX_PER_LINE = 20
TOO_MANY_OTHERS = (
    f"more than {MAX_PER_LINE} other names. Put the rest on a new line that starts with "
    "the name again"
)
TOO_MANY_SECRETS = (
    f"more than {MAX_PER_LINE} secret names. Put the rest on a new line: the name, then "
    "| | |, then the rest"
)
# The most other names, and the most secret names, one list may give one name (#598),
# counted with all its lines joined.
MAX_PER_NAME = 50
# The most other and secret names one list may add in all (#598). For both caps only the
# names a list adds count, so a Download all file always uploads again.
MAX_ADDED = 5_000
PC = "player_character"
# A player or DM row (the campaign's DMs only, #1559): `table names | player or DM | other
# names | | who | plays`. `who` is the Discord user id; `plays` the characters they play.
MAX_PEOPLE = 50  # people in one list: each is checked against the server one by one
WHO_DIGITS = re.compile(r"^[0-9]{15,19}$")  # a Discord user id (a snowflake), as a number
WHO_MAX = 2**63 - 1  # (and a snowflake fits a signed 64-bit number)
NEEDS_WHO = (
    "a player or DM needs their Discord id in the 5th part (how to get it is in the notes at "
    "the top)"
)
NO_SECRETS_FOR_PEOPLE = "players and DMs can't have secret names. Leave the 4th part empty"
ONLY_PEOPLE_HAVE = (
    "the 5th and 6th parts are only for a player or DM. Leave them empty, or change the kind "
    "to player or DM"
)
TOO_MANY_PEOPLE = (
    f"only {MAX_PEOPLE} players and DMs fit in one list. Put the rest in a second file"
)

# Kinds in plain words → the memory rules' kinds. Anything else "needs a look".
KIND_WORDS: dict[str, str] = {
    "npc": "npc",
    "character": "npc",
    "player": "person",  # a real person at the table (#1188); see PERSON_KIND
    "dm": "person",
    "person": "person",
    "player or dm": "person",
    "pc": "player_character",
    "player character": "player_character",
    "player's character": "player_character",
    "place": "place",
    "location": "place",
    "town": "place",
    "city": "place",
    "group": "faction",
    "faction": "faction",
    "guild": "faction",
    "creature": "creature",
    "monster": "creature",
    "beast": "creature",
    "item": "item",
    "object": "item",
    "weapon": "item",
    "god": "deity",
    "deity": "deity",
    "spell": "spell",  # refused: a spell is not kept in campaign memory (SPELL_NOT_KEPT)
    "magic": "spell",
    "event": "event",
    "clue": "clue",
    "secret": "clue",
    "people": "people",
    "folk": "people",
    "quest": "quest",
    "mystery": "mystery",
    "hook": "hook",
    "goal": "goal",
    "promise": "promise",
    "thread": "thread",
    "story thread": "thread",
    "idea": "concept",
    "other": "concept",
    "thing": "concept",
}
# How each kind is written in a download (read back by KIND_WORDS).
KIND_OUT: dict[str, str] = {
    "npc": "NPC",
    PC: "player's character",
    "character": "character",
    "place": "place",
    "faction": "group",
    "creature": "creature",
    "item": "item",
    "deity": "god",
    "spell": "spell",
    "event": "event",
    "concept": "other",
    "clue": "clue",
    "people": "people",
    "thread": "story thread",
    "quest": "quest",
    "mystery": "mystery",
    "hook": "hook",
    "goal": "goal",
    "promise": "promise",
    "person": "player",
}


def header(*, secrets: bool, download: bool = False) -> str:
    """The instructions at the top of the template and of a download. `secrets`: for the
    campaign's DMs, who may add secret names (the fourth part). `download`: a file DMbot
    wrote from a campaign's names has no examples to change and can be too long to paste,
    so it ends with how to add it again instead (#878)."""
    parts = "name | kind | other names | secret names" if secrets else "name | kind | other names"
    lines = [
        "### DMbot names list",
        "###",
        "### One name per line. Put a | (a straight up-and-down line) between the parts:",
        f"###     {parts}",
        "###",
        "### - Only the name is needed. To skip a part, leave it empty: Ulfgar | | Ulf",
        "### - kind: NPC, place, group, creature, item, god, event or other.",
        "###   These work too: person or character (NPC); town, city or location (place);",
        "###   faction or guild (group); monster or beast (creature); object or weapon",
        "###   (item); deity (god); idea or thing (other). A named creature, an NPC and a",
        "###   god are all characters. Spells aren't kept here: look them up with /dmbot rule.",
        "###   Another word (like wizard), or no kind? After you add the list, DMbot asks",
        '###   you once what every "wizard" is. If there are many words, the rest wait in',
        "###   Check new names.",
        "### - other names: nicknames, titles or short forms people say.",
        "###   Put a , or ; between them: Bell, the old knight.",
        "###   Up to 20 other names on a line. For more, write the name again on a new",
        "###   line with the rest. One name can get up to 50 new other names per list.",
    ]
    if secrets:
        lines += [
            "### - secret names: disguises or secret identities (who it really is), with a ,",
            "###   or ; between them. Up to 20 on a line and 50 for one name, as for other",
            "###   names.",
            "###   Only the campaign's DM can add them. Players never see them.",
            "### - A player or DM is written like this (only the campaign's DM can add these):",
            "###     table names | player or DM | more table names | | who | plays",
            "###   who: their Discord id, a long number. To get it, turn on Developer Mode",
            "###   (Settings > Advanced). Then open the person's profile (right-click on a",
            "###   PC, tap their name on a phone) and choose Copy User ID. Paste it here.",
            "###   plays: the characters they play, with ; between them.",
            "###   table names: what the table calls them (Mike, Mikey).",
            "###   Example: Mike, Mikey | player | | | 123456789012345678 | Ulfgar; Bell",
            "###   Names are added to what DMbot knows, never replaced.",
        ]
    lines += [
        "### - Names only: no descriptions or notes. Each name is up to 100 characters.",
        "### - Player's characters: add them with Add a player's character instead.",
        "### - Lines starting with # are notes. DMbot skips them, so you can leave these.",
        "### - A name DMbot already knows gets any new other names from its line.",
        "###   Spelled almost like a known name? DMbot asks if they're the same.",
        "###   A different kind than DMbot has? DMbot keeps its kind and asks you.",
        "###   DMbot never joins or changes names on its own. Up to 2,000 lines and",
        f"###   {MAX_ADDED:,} new {'other and secret' if secrets else 'other'} names in all.",
        "### - If a line doesn't fit, DMbot tells you, and lets you add the rest or have its",
        "###   AI tidy the list.",
        "###",
    ]
    if download:
        lines += [
            "### To add it again: edit it if you like, save it as a .txt file, then press",
            "### Add many > Upload a file.",
        ]
    else:
        lines += [
            "### To use it: change the examples to your own names and save it as a .txt file",
            "### (Notepad on Windows; TextEdit on a Mac: Format > Make Plain Text).",
            '### Then in Discord type /dmbot names and add the file in the "file" box.',
            "### On a phone? Copy the lines instead, then press Add many > Paste a list.",
        ]
    return "\n".join(lines) + "\n"


def template(*, secrets: bool) -> str:
    """The file to fill in: the instructions, then examples to change."""
    belleros = "Belleros | NPC | Bell, the old knight"
    return (
        header(secrets=secrets)
        + "###\n### Examples (change or delete them):\n"
        + (belleros + " | the hooded stranger" if secrets else belleros)
        + "\nBryn Shander | place | Bryn\nFrostwolf tribe | group | the Frostwolves\n"
        "Heartseeker | item\nAuril | god | the Frostmaiden\nUlfgar\n"
    )


HEADER = header(secrets=True)
TEMPLATE = template(secrets=True)
_SEPARATORS = re.compile(r"[;,]")
# A web address is never a name (#264): a pasted link must be read, not added.
_LINK = re.compile(r"://|^www\.|^[a-z0-9-]+(?:\.[a-z0-9-]+)+/", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class ListLine:
    number: int  # line number in the file, for messages
    name: str
    kind: str | None  # a memory kind, or None when missing or unclear
    kind_word: str  # as written ("" if none)
    others: tuple[str, ...]
    secrets: tuple[str, ...]
    said_as: str = ""  # a bracket that is clearly how to say the name (#2447): "TAV-in"
    ask: str = ""  # a bracket that may be how to say it: the DM is asked once
    whole: str = ""  # the name with its bracket, when the bracket was split off as an aside


@dataclass(frozen=True, slots=True)
class PersonLine:
    """A player or DM row (#1559): the person's Discord user id, the names the table calls them
    and the characters they play, by name."""

    number: int
    who: int
    names: tuple[str, ...]
    plays: tuple[str, ...]


@dataclass(slots=True)
class Parsed:
    lines: list[ListLine] = field(default_factory=list)
    people: list[PersonLine] = field(default_factory=list)
    refused: list[tuple[int, str]] = field(default_factory=list)  # (line, why), plain words
    repeated: int = 0  # the same name twice in the list


# Words that mean a bracket is plain English (an aside), never a respelling of the name.
PLAIN_WORDS = frozenset(
    {"the", "of", "a", "an", "and", "or", "to", "in", "on", "at", "for", "with", "from", "by"}
    | {"who", "is", "was", "are", "his", "her", "their", "its", "my", "our", "your", "this"}
    | {"that", "these", "those", "as", "but", "not", "no"}
)
_BRACKET = re.compile(
    r"^(?P<name>.+?)\s*(?:\((?P<round>[^()\[\]]{1,80})\)|\[(?P<square>[^()\[\]]{1,80})\])$"
)
_STRESS = re.compile("[-\u02c8\u02cc\u00b4`]")  # hyphens and stress marks
SOUND_SAME = 0.8  # this alike once hyphens, stress and capitals are gone: a respelling
SOUND_SHARED = 0.7  # with a shared sound code
SOUND_NEAR = 0.5  # at least this alike, or a shared sound code: ask the DM
SOUND, ASIDE, NEAR_SOUND = "sound", "aside", "near"


def split_bracket(cell: str) -> tuple[str, str] | None:
    """`Tavin (TAV-in)` gives ("Tavin", "TAV-in"); a name with no closing bracket at its end gives
    None."""
    found = _BRACKET.match(" ".join(cell.split()))
    if found is None:
        return None
    name = found["name"].strip()
    inside = " ".join((found["round"] or found["square"] or "").split())
    return (name, inside) if name and inside else None


def judge_bracket(name: str, inside: str) -> str:
    """What the bracket after a name is (#2447): SOUND (a respelling: after dropping hyphens, stress
    marks and capitals its sound key is close to the name's), NEAR_SOUND (maybe: ask the DM once) or
    ASIDE (any other words: kept as another name). A plain English word in it ("the", "of", "a")
    makes it an aside, whatever it sounds like. Pure code, no AI."""
    if {w.strip(".,;:!?'\"").lower() for w in inside.split()} & PLAIN_WORDS:
        return ASIDE
    plain = _STRESS.sub("", inside).lower()
    closeness = alike(name, plain)
    shared = bool(set(sound_keys(name)) & set(sound_keys(plain)))
    close = closeness >= SOUND_SAME or (shared and closeness >= SOUND_SHARED)
    if close and _respelled(inside):
        return SOUND
    if close or shared or closeness >= SOUND_NEAR:
        return NEAR_SOUND  # sounds alike but doesn't look like a respelling ("Kaela (Kael)"): ask
    return ASIDE


def _respelled(inside: str) -> bool:
    """Does the bracket look like a sound being spelled out: a hyphen or stress mark in it, or a
    shouted syllable ("kah-ZETH", "TAV-in")? A plain look-alike ("Kael" after "Kaela") does not."""
    return bool(_STRESS.search(inside)) or any(c.isupper() for c in inside[1:])


def _names(raw: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for part in _SEPARATORS.split(raw):
        text = " ".join(part.split())
        if text and name_key(text) not in seen:
            seen.add(name_key(text))
            out.append(text)
    return out


def _without(names: list[str], taken: set[str]) -> list[str]:
    """`names` minus any already said for this line (the name itself, its other names)."""
    return [n for n in names if name_key(n) not in taken]


def _unreadable(text: str) -> bool:
    """Control characters, and the line and paragraph separators U+2028/U+2029: a name with
    one would be split in two by splitlines(), so the line count (and the upload's own
    check) would come out low (#878)."""
    return any(unicodedata.category(c) in ("Cc", "Zl", "Zp") for c in text)


def _problem(text: str) -> str | None:
    if _unreadable(text):
        return "it has characters DMbot can't read. Save the file as .txt and try again"
    if len(text) > NAME_MAX or len(name_key(text)) > NAME_MAX:
        return f"a name is longer than {NAME_MAX} characters"
    if len(text.split()) > MAX_WORDS:
        return f"more than {MAX_WORDS} words. Names only, no descriptions"
    if _LINK.search(text):
        return "a link, not a name (to read a link, use 📥 Add many > 🔗 Paste a link)"
    return None


# Why one name typed on its own can't be saved (`check_name`).
EMPTY, BAR, UNREADABLE, TOO_LONG, TOO_MANY_WORDS, LINK = (
    "empty",
    "bar",
    "unreadable",
    "too long",
    "too many words",
    "link",
)


def check_name(text: str, limit: int = NAME_MAX) -> str | None:
    """Why one name typed on its own can't be saved, with the list's rules (#503): one
    of EMPTY, BAR, UNREADABLE, TOO_LONG, TOO_MANY_WORDS or LINK; None if it's fine."""
    if not text.strip():
        return EMPTY
    if "|" in text:
        return BAR
    if _unreadable(text):
        return UNREADABLE
    if len(text) > limit or len(name_key(text)) > limit:
        return TOO_LONG
    if len(text.split()) > MAX_WORDS:
        return TOO_MANY_WORDS
    if _LINK.search(text):
        return LINK
    return None


def kind_of(word: str) -> str | None:
    """A kind in plain words ("Places", "cities", "god") → the memory rules' kind."""
    word = " ".join(word.casefold().split())
    if not word:
        return None
    for guess in (word, word.removesuffix("s"), word.removesuffix("ies") + "y"):
        if guess in KIND_WORDS:
            return KIND_WORDS[guess]
    return None


@dataclass(slots=True)
class _Taking:
    """One name while a list is read: its lines join here, so a name written on many
    lines costs no more than its names (#598)."""

    number: int
    name: str
    kind: str | None
    kind_word: str
    others: list[str]
    secrets: list[str]
    said: set[str]  # name keys already given for it: the name, other and secret names
    said_as: str = ""
    ask: str = ""
    whole: str = ""

    def add(self, names: list[str], into: list[str]) -> None:
        for n in names:
            if (key := name_key(n)) not in self.said:
                self.said.add(key)
                into.append(n)


def parse(text: str, *, secrets: bool, people: bool = True) -> Parsed:
    """The names in a list. `secrets`: whether this person may add secret names (the
    campaign's DMs); otherwise a line with secret names is refused, never half-saved. `people`:
    whether player or DM rows may be read (never from an AI's reply, #1559)."""
    out = Parsed()
    taking: dict[str, _Taking] = {}  # name key → the name, in the order first given
    found_people: dict[int, tuple[int, list[str], list[str]]] = {}  # who → line, names, plays
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip().lstrip("\ufeff")
        if not line or line.startswith("#"):
            continue
        cells = [c.strip() for c in line.split("|")]
        if len(cells) > 6:
            out.refused.append(
                (number, "more than 6 parts. Use | only between name, kind, other names, "
                 "secret names (and, for a player or DM, who and plays)")
            )  # fmt: skip
            continue
        cells += [""] * (6 - len(cells))
        name = " ".join(cells[0].split())
        if not name:
            out.refused.append((number, "no name before the first |"))
            continue
        if kind_of(" ".join(cells[1].split())) == "person" and secrets and people:
            _person(out, found_people, number, cells)
            continue
        said_as = ask = whole = ""
        aside: list[str] = []
        if (bracket := split_bracket(name)) is not None:
            base, inside = bracket
            match judge_bracket(base, inside):
                case "sound":  # SOUND
                    name, said_as = base, inside
                case "near":  # NEAR_SOUND
                    name, ask = base, inside
                case _:  # an aside: Tavin (the rogue) is Tavin, also called "Tavin the rogue"
                    whole, name, aside = name, base, [f"{base} {inside}"]
        if (cells[4] or cells[5]) and kind_of(" ".join(cells[1].split())) != "person":
            out.refused.append((number, ONLY_PEOPLE_HAVE))
            continue
        key = name_key(name)
        others, hidden = (
            _names(" ; ".join([*aside, cells[2]]) if aside else cells[2]),
            _names(cells[3]),
        )
        # Counted as kept: without the name, or a secret name said already. Before the
        # checks on each name, so a long line is refused without reading all of it.
        others = _without(others, {key})
        hidden = _without(hidden, {key, *map(name_key, others)})
        if len(others) > MAX_PER_LINE:
            why: str | None = TOO_MANY_OTHERS
        elif len(hidden) > MAX_PER_LINE:
            why = TOO_MANY_SECRETS
        else:
            why = next((p for t in (name, *others, *hidden) if (p := _problem(t))), None)
        if why:
            out.refused.append((number, why))
            continue
        if hidden and not secrets:
            out.refused.append(
                (number, "only the campaign's DM can add secret names. Remove the last part "
                 "and add it again")
            )  # fmt: skip
            continue
        word = " ".join(cells[1].split())
        if kind_of(word) == "spell":
            out.refused.append((number, SPELL_NOT_KEPT))
            continue
        if kind_of(word) == "person":
            out.refused.append((number, PERSON_NOT_LISTED))
            continue
        if kind_of(word) == "creature" and index.srd().is_monster(name):
            out.refused.append((number, UNNAMED_MONSTER))
            continue
        first = taking.get(key)
        if first is None:
            taking[key] = first = _Taking(
                number, name, kind_of(word), word, [], [], {key}, said_as, ask, whole
            )
        else:  # the same name again: keep the first line, with every other name
            out.repeated += 1
            later = kind_of(word)
            # A kind given later counts when the first line gave none (or "other").
            if first.kind in (None, "concept") and later not in (None, "concept"):
                first.kind, first.kind_word = later, word
            if not first.said_as and said_as:
                first.said_as, first.ask = said_as, ""
            elif not first.said_as and not first.ask and ask:
                first.ask = ask
        first.add(others, first.others)
        first.add(hidden, first.secrets)
    out.lines = [
        ListLine(
            t.number,
            t.name,
            t.kind,
            t.kind_word,
            tuple(t.others),
            tuple(t.secrets),
            t.said_as,
            t.ask,
            t.whole,
        )
        for t in taking.values()
    ]
    out.people = [
        PersonLine(n, who, tuple(names), tuple(plays))
        for who, (n, names, plays) in found_people.items()
    ]
    return out


def _person(
    out: Parsed,
    people: dict[int, tuple[int, list[str], list[str]]],
    number: int,
    cells: list[str],
) -> None:
    """A player or DM row from a campaign DM: `who` is needed; the names are what the table
    calls them. A row for someone already given in this list adds to that one."""
    who_text = cells[4].replace(" ", "")
    if not WHO_DIGITS.match(who_text):
        out.refused.append((number, NEEDS_WHO))
        return
    if cells[3]:
        out.refused.append((number, NO_SECRETS_FOR_PEOPLE))
        return
    names = _names(" ; ".join([cells[0], cells[2]]))
    plays = _names(cells[5])
    if len(names) > MAX_PER_LINE or len(plays) > MAX_PER_LINE:
        out.refused.append((number, TOO_MANY_OTHERS))
        return
    if why := next((p for t in (*names, *plays) if (p := _problem(t))), None):
        out.refused.append((number, why))
        return
    # The DM words are never a name (#1208); the person is still listed.
    names = [n for n in names if not is_dm_word(n)]
    who = int(who_text)
    if who > WHO_MAX:
        out.refused.append((number, NEEDS_WHO))
        return
    if who not in people and len(people) >= MAX_PEOPLE:
        out.refused.append((number, TOO_MANY_PEOPLE))
        return
    _, have_names, have_plays = people.setdefault(who, (number, [], []))
    for n in names:
        if name_key(n) not in {name_key(h) for h in have_names}:
            have_names.append(n)
    for n in plays:
        if name_key(n) not in {name_key(h) for h in have_plays}:
            have_plays.append(n)
    if len(have_names) > MAX_PER_NAME or len(have_plays) > MAX_PER_NAME:  # (all the rows joined)
        del have_names[MAX_PER_NAME:], have_plays[MAX_PER_NAME:]
        out.refused.append((number, f"more than {MAX_PER_NAME} names for one person"))


@dataclass(frozen=True, slots=True)
class OutName:
    name: str
    kind: str
    others: tuple[str, ...]
    secrets: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class OutPerson:
    """A player or DM for a download (#1559). `names` are what the table calls them (never a
    name read from Discord); `placeholder` is the person entry's own name (`Player 1234`),
    written when there is no table name, and skipped again on the way in."""

    who: int
    names: tuple[str, ...]
    plays: tuple[str, ...]
    placeholder: str


_SEPARATOR_CHARS = str.maketrans({"\u2028": " ", "\u2029": " "})


def _one_line(text: str) -> str:
    return text.translate(_SEPARATOR_CHARS)


def lines_for(name: str, kind: str, others: Sequence[str], secrets: Sequence[str]) -> list[str]:
    """One name as list lines. More other or secret names than a line holds go on more
    lines with the same name, as a person would write them (#598). A name saved before
    U+2028/U+2029 were refused is written with a space instead, so one name is never two
    lines in the file (#878)."""
    name, others, secrets = (
        _one_line(name),
        [_one_line(o) for o in others],
        [_one_line(s) for s in secrets],
    )
    out = []
    for at in range(0, max(len(others), len(secrets), 1), MAX_PER_LINE):
        cells = [
            name,
            kind,
            "; ".join(others[at : at + MAX_PER_LINE]),
            "; ".join(secrets[at : at + MAX_PER_LINE]),
        ]
        while len(cells) > 1 and not cells[-1]:
            cells.pop()
        out.append(" | ".join(cells))
    return out


def person_lines(p: OutPerson) -> list[str]:
    """A player or DM as list lines (#1559): their table names (the first is the name part),
    the Discord id and the characters they play. More than a line holds goes on more lines
    with the same id, which the way in joins."""
    names = [_one_line(n) for n in p.names] or [p.placeholder]
    plays = [_one_line(c) for c in p.plays]
    out = []
    for at in range(0, max(len(names) - 1, len(plays), 1), MAX_PER_LINE):
        cells = [
            names[0],
            "player or DM",
            "; ".join(names[1:][at : at + MAX_PER_LINE]),
            "",
            str(p.who),
            "; ".join(plays[at : at + MAX_PER_LINE]),
        ]
        while len(cells) > 5 and not cells[-1]:
            cells.pop()  # the id is always written
        out.append(" | ".join(cells))
    return out


def _prelude(*, campaign: str, secrets: bool, part: tuple[int, int] | None = None) -> list[str]:
    """The lines every download file starts with: the instructions (so each file is valid
    on its own), then what it holds. `part` is (this file, how many) when a download comes
    as several files, and each then says it can be added on its own."""
    which = f" (file {part[0]} of {part[1]})" if part else ""
    lines = [
        header(secrets=secrets, download=True).rstrip("\n"),
        "###",
        f"### Names DMbot knows for {' '.join(campaign.split())}{which}",
    ]
    if secrets:
        lines.append("### This file includes secret names. Don't share it with players.")
    if part:
        lines.append(
            f"### This is one of {part[1]} files. Each can be added on its own, in any order."
        )
    return lines


def render(
    names: Iterable[OutName],
    *,
    campaign: str,
    secrets: bool,
    people: Sequence[OutPerson] = (),
) -> str:
    """A download: the instructions, then one line per name, A to Z. Uploading it again
    adds nothing that's already known. A name with more other or secret names than a
    line holds goes on more lines, as a person would write it."""
    lines = _prelude(campaign=campaign, secrets=secrets)
    # Players and the DM are DMbot's own entries and can't be uploaded again (#1188).
    names = [n for n in names if n.kind != "person"]
    for n in sorted(names, key=lambda n: name_key(n.name)):
        hidden = n.secrets if secrets else ()
        lines += lines_for(n.name, KIND_OUT.get(n.kind, "other"), n.others, hidden)
    if secrets:  # players and DMs are listed for the campaign's DMs only (#1559)
        for person in sorted(people, key=lambda q: q.who):
            lines += person_lines(person)
    return "\n".join(lines) + "\n"


def render_files(
    names: Iterable[OutName],
    *,
    campaign: str,
    secrets: bool,
    people: Sequence[OutPerson] = (),
) -> list[str]:
    """A download as one or more files, each one the upload's own limits allow (#685):
    at most MAX_LINES lines (the # notes count) and MAX_FILE_BYTES, so every file can be
    added again. One file when it all fits, exactly as render() writes it. A name's lines
    (more than a line's worth of other names goes on more lines) never split across files,
    and every file starts with the instructions, so each is valid on its own and the
    files can be added in any order."""
    ordered = sorted(names, key=lambda n: name_key(n.name))
    groups = [
        lines_for(n.name, KIND_OUT.get(n.kind, "other"), n.others, n.secrets if secrets else ())
        for n in ordered
    ]
    adds = [len(n.others) + (len(n.secrets) if secrets else 0) for n in ordered]
    person_at: set[int] = set()  # which groups are people: a file holds MAX_PEOPLE of them
    if secrets:  # after the names; each person's lines stay together
        listed = sorted(people, key=lambda q: q.who)
        person_at = set(range(len(groups), len(groups) + len(listed)))
        groups += [person_lines(q) for q in listed]
        adds += [len(q.names) + len(q.plays) for q in listed]

    def budget(prelude: list[str]) -> tuple[int, int]:
        joined = "\n".join(prelude)  # the instructions are many lines in one string
        return joined.count("\n") + 1, len(joined.encode("utf-8")) + 1

    def pack(part: tuple[int, int] | None) -> list[list[str]]:
        # A file is full at the upload's line, size or names-added limit (#712), whichever
        # comes first; a lone name is always accepted (its own size is capped elsewhere).
        base_lines, base_bytes = budget(_prelude(campaign=campaign, secrets=secrets, part=part))
        chunks: list[list[str]] = [[]]
        used_lines, used_bytes, used_adds, used_people = base_lines, base_bytes, 0, 0
        for at, (group, add) in enumerate(zip(groups, adds, strict=True)):
            size = sum(len(line.encode("utf-8")) + 1 for line in group)
            is_person = at in person_at
            if chunks[-1] and (
                used_lines + len(group) > MAX_LINES
                or used_bytes + size > MAX_FILE_BYTES
                or used_adds + add > MAX_ADDED
                or (is_person and used_people >= MAX_PEOPLE)  # the way in takes this many
            ):
                chunks.append([])
                used_lines, used_bytes, used_adds, used_people = base_lines, base_bytes, 0, 0
            chunks[-1].extend(group)
            used_lines += len(group)
            used_bytes += size
            used_adds += add
            used_people += is_person
        return chunks

    chunks = pack(None)
    if len(chunks) == 1:  # fits whole: exactly what render() writes
        return [render(ordered, campaign=campaign, secrets=secrets, people=people)]
    # The two "file i of n" lines make each file a little longer: plan with the widest
    # numbers (99,999 files, far past what 10,000 names can make), then write the real ones.
    chunks = pack((99_999, 99_999))
    return [
        "\n".join(_prelude(campaign=campaign, secrets=secrets, part=(i, len(chunks))) + body) + "\n"
        for i, body in enumerate(chunks, 1)
    ]
