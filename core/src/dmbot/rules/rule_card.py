"""The rule card the DM sees when they look something up (#908): what the free rules (the
SRD) say about a spell, a condition or a creature, word for word, with where it comes from.

Built here without Discord (only its text escaping), so it can be tested on its own, and
so the rules advisor's alerts can reuse it later. Nothing here decides anything: a house
rule that names the thing is shown first, the book comes after, and the DM decides what
applies. Nothing is guessed: a card is only made for a name the index knows; otherwise
`no_match_text` says so and offers close names to pick from.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

import discord

from dmbot.discord_limits import CHOICE_VALUE_MAX, MESSAGE_MAX
from dmbot.rules import facts as rule_facts
from dmbot.rules import index
from dmbot.rules.book_rank import BOOK_TAG, LEGACY_TAG
from dmbot.rules.followed import FollowedRule
from dmbot.rules.homebrew import Homebrew, credit_label
from dmbot.rules.house import HouseRule
from dmbot.rules.index import Entry, Hit, monster_keys, normalize

PART_MAX = MESSAGE_MAX - 100  # the rest is for what never counts
CONTINUED_MAX = 120  # room for a later part's heading
HOUSE_LINE_MAX = 250  # one house rule, after escaping
HOUSE_SHOWN = 3  # most house rules shown; the rest are counted
FACTS_MAX = 400
NAME_MAX = 100
MIN_TEXT_ROOM = 200  # a first part with less room than this is only the heading

FREE_RULES = "the free rules (SRD)"
NOT_A_RULING = (
    "_From the free rules (SRD). DMbot shows the rules as written; you decide what applies._"
)
OLDER_NOTE = "_From the older 2014 rules (the newer free rules don't have it)._"
TAG_WORDS = {"[2024]": "[Newer 2024 rules]"}  # a tag as the DM reads it
ONLY_FREE_RULES = "_Only the free rules are in DMbot so far, not your own books._"
SEE_ALSO_MAX = 3  # a longer list would push a card into one more message
KIND_WORDS = {
    "spell": "spell",
    "condition": "condition",
    "monster": "creature",
    "rule": "rule",
    "feature": "feature",
    "item": "item",
    "feat": "feat",
    "species": "species",
    "background": "background",
    "weapon": "weapon",
    "armor": "armor",
    "gear": "gear",
    "tool": "tool",
    "poison": "poison",
    "trap": "trap",
    "curse": "curse",
    "disease": "disease",
}


def _md(text: str) -> str:
    return discord.utils.escape_markdown(text)


def _fit(text: str, limit: int) -> str:
    """Shorten text to `limit` characters, never ending on half an escape."""
    if len(text) <= limit:
        return text
    cut = text[: limit - 1].rstrip()
    if (len(cut) - len(cut.rstrip("\\"))) % 2:
        cut = cut[:-1]
    return cut + "…"


def facts(entry: Entry) -> str:
    """The short line under the name: a spell's level, school, casting time, range,
    components and duration; a creature's size and type, AC, HP, speed and CR; nothing for
    a condition. The fields come from `dmbot.rules.facts` (#1063)."""
    return _fit(" · ".join(_md(part) for part in rule_facts.card_parts(entry)), FACTS_MAX)


def house_matches(rules: Iterable[HouseRule], names: Iterable[str]) -> list[HouseRule]:
    """The house rules that name the thing: any of `names` (case, punctuation and
    apostrophes ignored) is a whole word or phrase in the rule or in what it replaces.
    From the rules given, which are one campaign's: this never looks further. A short name
    ("Light", "Fly") matches loosely on purpose: the DM sees every house rule that might
    apply, and decides."""
    wanted = {n for n in (normalize(name) for name in names) if len(n) >= 2}
    found = []
    for rule in rules:
        text = f" {normalize(rule.rule)} {normalize(rule.supersedes or '')} "
        if any(f" {name} " in text for name in wanted):
            found.append(rule)
    # Own first, then rules merged in from a set, then the followed set's: the order they win.
    return sorted(found, key=lambda r: (isinstance(r, FollowedRule), bool(r.from_set), r.number))


def house_lines(matches: Sequence[HouseRule], line_max: int = HOUSE_LINE_MAX) -> list[str]:
    """ "🏠 House rule 12: ..." for each (the first few, each cut to `line_max`), and a count
    of the rest. With no room (`line_max` 0), only a count."""
    if not matches:
        return []
    if line_max <= 0:
        plural = "s" if len(matches) != 1 else ""
        return [
            f"🏠 {len(matches)} house rule{plural} mention this. "
            "See them all with `/dmbot houserules`."
        ]
    lines = []
    for rule in matches[:HOUSE_SHOWN]:
        words = _md(rule.rule)
        if rule.supersedes:
            words += f" (instead of: {_md(rule.supersedes)})"
        if isinstance(rule, FollowedRule):
            label = f"**{_md(rule.cite)}, rule {rule.number}:**"
        elif rule.from_set:
            label = (
                f"**House rule {rule.number} (from {_md(rule.from_set)}; the campaign's own win):**"
            )
        else:
            label = f"**House rule {rule.number}:**"
        lines.append(_fit(f"🏠 {label} {words}", line_max))
    extra = len(matches) - HOUSE_SHOWN
    if extra > 0:
        plural = "s" if extra != 1 else ""
        lines.append(
            f"🏠 …and {extra} more house rule{plural} mention this. "
            "See them all with `/dmbot houserules`."
        )
    return lines


def names_for(entry: Entry, typed: str) -> list[str]:
    """What a house rule might call this entry: its name, the name typed, and (a creature)
    its name without the bracket or turned round."""
    names = [entry.name, typed]
    if entry.kind == "monster":
        names += monster_keys(entry.name)
    return names


def split_text(text: str, first: int, rest: int) -> list[str]:
    """The text in parts that fit: the first part up to `first` characters, the others up
    to `rest`. Parts end between paragraphs when they can, else between sentences or
    words; no word is cut (unless one is longer than a whole part). Where a part ends inside
    a paragraph the break was a space, and a break between paragraphs was a new line."""
    atoms: list[tuple[str, str]] = []  # (what came before it, the piece)
    limit = max(1, min(first, rest) if first >= MIN_TEXT_ROOM else rest)
    for line in text.split("\n"):
        for number, piece in enumerate(_atoms(line, limit)):
            atoms.append(("\n" if number == 0 else " ", piece))
    parts: list[str] = []
    current = ""
    for before, atom in atoms:
        budget = first if not parts else rest
        joined = f"{current}{before}{atom}" if current else atom
        if len(joined) <= budget:
            current = joined
            continue
        if current:
            parts.append(current)
        current = atom
    if current or not parts:
        parts.append(current)
    return parts


def _atoms(line: str, limit: int) -> list[str]:
    """A line, or if it is longer than `limit` the pieces of it: sentences, then words."""
    if len(line) <= limit:
        return [line]
    pieces: list[str] = []
    current = ""
    for sentence in re.split(r"(?<=[.!?])\s+", line):
        for word in _words(sentence, limit):
            joined = f"{current} {word}" if current else word
            if len(joined) <= limit:
                current = joined
            else:
                if current:
                    pieces.append(current)
                current = word
    if current:
        pieces.append(current)
    return pieces


def _words(sentence: str, limit: int) -> list[str]:
    """The sentence whole if it fits, else its words (a word longer than `limit` is cut)."""
    if len(sentence) <= limit:
        return [sentence]
    out = []
    for word in sentence.split(" "):
        while len(word) > limit:
            out.append(word[:limit])
            word = word[limit:]
        out.append(word)
    return out


def source(hit: Hit) -> str:
    """Where it comes from, with the tag in words a DM reads ("[Newer 2024 rules]")."""
    tag = TAG_WORDS.get(hit.tag, hit.tag)
    return f"{hit.entry.citation} {tag}" if tag else hit.entry.citation


def header(
    hit: Hit,
    typed: str,
    rules: Sequence[HouseRule],
    *,
    house_max: int = HOUSE_LINE_MAX,
    player: bool = False,
    also: Sequence[str] = (),
    older: str = "",
) -> str:
    """Everything above the text: house rules first, the name and facts, where it comes
    from, and any note about how it was found."""
    entry = hit.entry
    lines = house_lines(house_matches(rules, names_for(entry, typed)), house_max)
    kind = KIND_WORDS.get(entry.kind, entry.kind)
    called = _fit(_md(entry.name), NAME_MAX)
    if entry.details.get(
        "tag"
    ):  # a glossary entry's own tag, inside the brackets: "(rule: Action)"
        kind = f"{kind}: {_md(str(entry.details['tag']))}"
    elif (
        entry.kind == index.FEATURE
    ):  # "(feature: Rogue 7)"; a subclass's feature adds its subclass
        kind = f"{kind}: {_md(feature_place(entry))}"
    elif entry.kind == index.ITEM:  # "(item: Wondrous Item, uncommon)"
        kind = f"{kind}: {_md(item_place(entry))}"
    elif entry.kind in (index.GEAR, index.TOOL):  # "(gear: 5 GP)", "(tool: Dexterity)"
        kind = f"{kind}: {_md(equipment_place(entry))}"
    elif entry.kind == index.POISON:  # "(poison: Injury)"
        kind = f"{kind}: {_md(str(entry.details.get('type', '')))}"
    elif (
        entry.kind in (index.FEAT, index.WEAPON, index.ARMOR)
        and entry.details.get("category")  # (the 2014 Grappler has none)
        and entry.details.get("category") != entry.name
    ):  # "(feat: Origin)", "(weapon: Martial Melee)"
        kind = f"{kind}: {_md(str(entry.details.get('category', '')))}"
    lines.append(f"📖 **{called}** ({kind})")
    line = facts(entry)
    if line:
        lines.append(line)
    lines.append(f"_Source: {_md(source(hit))}_")
    if also:  # a feature other classes have too: the card shows one, and says so
        lines.append(f"_Other classes with this: {_md(', '.join(also))}._")
    if older:  # a newer species or background took the name of an older creature
        name = older.split(" (creature)")[0]
        lines.append(f"_The older {_md(older)} has the same name: type “{_md(name)} (creature)”._")
    asked = _fit(_md(" ".join(typed.split())), NAME_MAX)
    if hit.renamed and asked:
        lines.append(f"_{asked} is now called {called} in the newer rules._")
    elif asked and normalize(typed) not in {normalize(n) for n in names_shown(entry)}:
        lines.append(f"_Showing {called} (you typed {asked})._")
    if hit.tag and hit.from_fallback and entry.edition == index.LEGACY:
        lines.append(OLDER_NOTE)  # when 2014 is the campaign's own choice, the tag is enough
    if not player:  # a player's card ends with PLAYER_LINE instead: "you decide" is the DM's
        lines.append(NOT_A_RULING)
    return "\n".join(lines)


ALERT_FACTS_MAX = 200
ALERT_HEARD_MAX = 80
ALERT_HOUSE_MAX = 120
ALERT_FOOTER = "_Free rules (SRD), as written. You decide._"


def alert_text(hit: Hit, said: str, heard: str, rules: Sequence[HouseRule]) -> str:
    """The short card DMbot puts on the DM screen when a name is said at the table: about
    five lines to glance at. A house rule that names it first (one line, the rest counted),
    the name, the facts, the source with its tag, what was heard, and a short reminder that
    the DM decides. **Read it all** gives the lookup card with its full heading and text."""
    entry = hit.entry
    matches = house_matches(rules, names_for(entry, said))
    lines = house_lines(matches[:1], ALERT_HOUSE_MAX) if matches else []
    if len(matches) > 1:
        more = len(matches) - 1
        plural = "s" if more != 1 else ""
        lines.append(f"🏠 {more} more house rule{plural}: `/dmbot houserules`")
    called = _fit(_md(entry.name), NAME_MAX)
    lines.append(f"📖 **{called}** ({KIND_WORDS.get(entry.kind, entry.kind)})")
    line = facts(entry)
    if line:
        lines.append(_fit(line, ALERT_FACTS_MAX))
    lines.append(f"_Source: {_md(source(hit))}_")
    asked = _fit(_md(" ".join(said.split())), NAME_MAX)
    if hit.renamed and asked:
        lines.append(f"_{asked} is now called {called} in the newer rules._")
    lines.append(f"_Heard: “{_fit(_md(heard), ALERT_HEARD_MAX)}”_")
    lines.append(ALERT_FOOTER)
    return "\n".join(lines)


def card_parts(
    hit: Hit,
    typed: str,
    rules: Sequence[HouseRule],
    *,
    player: bool = False,
    also: Sequence[str] = (),
    older: str = "",
) -> list[str]:
    """The card as messages, each short enough for Discord: the first has the heading and
    as much of the text as fits; the others carry on with the text. Many long house rules
    are cut shorter, then only counted, so the heading never fills a message."""
    head = ""
    for house_max in (HOUSE_LINE_MAX, 120, 60, 0):
        head = header(hit, typed, rules, house_max=house_max, player=player, also=also, older=older)
        if len(head) <= PART_MAX - MIN_TEXT_ROOM:
            break
    text = _md(hit.entry.text)
    see_also = hit.entry.details.get("see_also")
    if see_also:  # a glossary entry points to others by name (plain words, no links)
        text += "\n\nSee also: " + ", ".join(
            _md(str(name)) for name in list(see_also)[:SEE_ALSO_MAX]
        )
    room = PART_MAX - len(head) - 1
    rest = PART_MAX - CONTINUED_MAX
    if room < MIN_TEXT_ROOM:
        chunks = ["", *split_text(text, rest, rest)]
    else:
        chunks = split_text(text, room, rest)
    parts = [f"{head}\n{chunks[0]}" if chunks[0] else head]
    title = _fit(_md(hit.entry.name), NAME_MAX)
    total = len(chunks)
    for number, chunk in enumerate(chunks[1:], start=2):
        parts.append(f"📖 **{title}** (part {number} of {total})\n{chunk}")
    return parts


HOMEBREW_TAG = "[Homebrew]"
HOMEBREW_FOOTER = "_Your own homebrew, as you wrote it. You decide._"
PACK_FOOTER = "_From a homebrew pack this campaign follows, as published. You decide._"


PLAYER_LINE = "Your DM decides what applies at your table."


def for_player(parts: list[str]) -> list[str]:
    """A card read by a player ends with one short line: a rules card is information, and the
    DM decides what applies (CLAUDE.md, DM authority). It joins the last part if it fits."""
    if parts and len(parts[-1]) + len(PLAYER_LINE) + 1 <= MESSAGE_MAX:
        return [*parts[:-1], f"{parts[-1]}\n{PLAYER_LINE}"]
    return [*parts, PLAYER_LINE]


def homebrew_citation(entry: Homebrew, campaign_name: str) -> str:
    """Where homebrew comes from, as every answer names its source: `Frostmaiden's homebrew:
    Hollow King`. A followed pack's entry names its pack: `Frost Pack homebrew, v2: Hollow King`."""
    # A page's entry is "from" its site, a website's upload "credited to" it (#1635), a person's
    # upload "by" their name (#1646). (The caller escapes the whole line for Discord.)
    label = credit_label(entry)
    site = f" ({label})" if label else ""
    if entry.source:
        return f"{entry.source}: {entry.name}{site}"
    if entry.from_book:  # the book's title is never kept (CLAUDE.md, IP rule): the campaign's copy
        edition = f" ({entry.book_edition})" if entry.book_edition in ("2024", "2014") else ""
        return f"{campaign_name}'s shared book{edition}: {entry.name}"
    return f"{campaign_name}'s homebrew: {entry.name}{site}"


def _tags(own: Homebrew, legacy: bool) -> str:
    """`[Homebrew]`; for a shared book's entry `[Shared book]`, with `[Legacy 2014]` when it is
    for the older rules and the campaign plays the newer (#1563)."""
    if own.from_book:
        return f"{BOOK_TAG} {LEGACY_TAG}" if legacy else BOOK_TAG
    return HOMEBREW_TAG


def homebrew_parts(
    own: Homebrew, campaign_name: str, *, player: bool = False, legacy: bool = False
) -> list[str]:
    """The card for one of the campaign's own homebrew entries, for the DM (private): tagged
    `[Homebrew]`, its source named, then its words. It goes above the free
    rules' card and below any house rule (the order is house rules, homebrew, the target
    ruleset, the fallback)."""
    head = "\n".join(
        [
            f"📚 **{_fit(_md(own.name), NAME_MAX)}** ({own.kind.capitalize()}) "
            f"{_tags(own, legacy)}",
            f"_Source: {_md(_fit(homebrew_citation(own, campaign_name), 200))}_",
        ]
    )
    text = _md(own.text)
    room = PART_MAX - len(head) - 1
    rest = PART_MAX - CONTINUED_MAX
    chunks = (
        split_text(text, room, rest)
        if room >= MIN_TEXT_ROOM
        else ["", *split_text(text, rest, rest)]
    )
    parts = [f"{head}\n{chunks[0]}" if chunks[0] else head]
    title = _fit(_md(own.name), NAME_MAX)
    for number, chunk in enumerate(chunks[1:], start=2):
        parts.append(f"📚 **{title}** (part {number} of {len(chunks)})\n{chunk}")
    return parts


def homebrew_alert_text(
    own: Homebrew,
    campaign_name: str,
    said: str,
    heard: str,
    rules: Sequence[HouseRule],
    *,
    legacy: bool = False,
) -> str:
    """The short card on the DM screen when the table names one of the campaign's own homebrew
    entries (#1524): a house rule that names it first, then the entry (tagged, with its one-line
    summary), its source and what was heard. The DM screen only. **Read it all** gives the whole
    entry."""
    matches = house_matches(rules, [own.name, said])
    lines = house_lines(matches[:1], ALERT_HOUSE_MAX) if matches else []
    if len(matches) > 1:
        more = len(matches) - 1
        plural = "s" if more != 1 else ""
        lines.append(f"🏠 {more} more house rule{plural}: `/dmbot houserules`")
    name = _fit(_md(own.name), NAME_MAX)
    lines.append(f"📚 **{name}** ({own.kind.capitalize()}) {_tags(own, legacy)}")
    lines.append(_fit(_md(own.shown()), ALERT_FACTS_MAX))
    lines.append(f"_Source: {_md(_fit(homebrew_citation(own, campaign_name), 200))}_")
    lines.append(f"_Heard: “{_fit(_md(heard), ALERT_HEARD_MAX)}”_")
    lines.append(PACK_FOOTER if own.source else HOMEBREW_FOOTER)
    return "\n".join(lines)


def no_match_text(
    typed: str, rules: Sequence[HouseRule], has_suggestions: bool, *, player: bool = False
) -> str:
    """When the index has no entry for the name: say so plainly, show any house rule that
    names it, and say what to do next. Nothing is guessed."""
    asked = _fit(_md(" ".join(typed.split())), NAME_MAX)
    lines = house_lines(house_matches(rules, [typed]))
    lines.append(f"DMbot couldn't find **{asked}** in {FREE_RULES}.")
    if has_suggestions:
        lines.append("**Did you mean…?** Press a name to read it.")
    elif player:  # (the same words whether or not the DM keeps something by that name hidden)
        lines.append("Check the spelling, or ask your DM.")
    else:
        lines.append("Check the spelling, or try a shorter name.")
    if not player:
        lines.append(ONLY_FREE_RULES)
    return "\n".join(lines)


def names_shown(entry: Entry) -> list[str]:
    """The ways of typing an entry that need no "Showing ..." note: its name, and for a class
    feature its name behind its class ("Rogue Evasion", which the list fills in)."""
    names = [entry.name]
    if entry.kind == index.FEATURE:
        names.append(f"{entry.details.get('class', '')} {entry.name}")
    return names


def item_place(entry: Entry) -> str:
    """What kind of item it is and how rare: `Wondrous Item, uncommon`."""
    d = entry.details
    rarity = rule_facts.short_rarity(str(d.get("rarity", "")))
    return f"{d.get('category', '')}, {rarity}".strip(", ")


def equipment_place(entry: Entry) -> str:
    """What a list or heading says after "gear:" or "tool:": a gear entry's price ("5 GP"; a
    mount or a vehicle says which, "Mount, 75 GP"), a tool's ability ("Dexterity")."""
    d = entry.details
    if entry.kind == index.TOOL and d.get("ability"):
        return str(d["ability"])
    group = str(d.get("group", ""))
    cost = str(d.get("cost", ""))
    if entry.kind == index.TOOL:  # (the SRD 5.1's tools have no ability: the price, and no group)
        group = "Adventuring Gear"
    if cost == "Varies":
        cost = "price varies"
    elif ";" in cost and (first := re.search(r"[\d,]+ [GSC]P", cost)):
        cost = f"from {first.group()}"  # the Spell Scroll's two prices; the card's text has both
    return cost if group == "Adventuring Gear" else f"{group}, {cost}"


def feature_place(entry: Entry) -> str:
    """Where a class feature is gained: `Rogue 7`, or `Barbarian 3, Path of the Berserker` for a
    subclass's feature."""
    place = f"{entry.details.get('class', '')} {entry.details.get('level', '')}".strip()
    subclass = entry.details.get("subclass")
    return f"{place}, {subclass}" if subclass else place


def choice_label(entry: Entry) -> str:
    """How a name shows in the list that opens as the DM types: `Fireball (spell)`, and
    `Orc (creature) [Legacy 2014]` for a name only the 2014 rules have."""
    kind = KIND_WORDS.get(entry.kind, entry.kind)
    if entry.kind == index.FEATURE:  # "Evasion (feature: Rogue 7)": the class tells two apart
        kind = f"{kind}: {entry.details.get('class', '')} {entry.details.get('level', '')}".rstrip()
    if entry.details.get("tag"):  # "Topple (rule: Mastery Property)"
        if entry.details["tag"] not in entry.name:  # (not "Reach (Weapon Property) (rule: ...)")
            kind = f"{kind}: {entry.details['tag']}"
    elif entry.kind == index.ITEM:  # "Bag of Holding (item: Wondrous Item, uncommon)"
        kind = f"{kind}: {item_place(entry)}"
    elif entry.kind in (index.GEAR, index.TOOL):  # "Healer's Kit (gear: 5 GP)"
        kind = f"{kind}: {equipment_place(entry)}"
    elif entry.kind == index.POISON:  # "Purple Worm Poison (poison: Injury)"
        kind = f"{kind}: {entry.details.get('type', '')}"
    elif (
        entry.kind in (index.FEAT, index.WEAPON, index.ARMOR)
        and entry.details.get("category")  # (the 2014 Grappler has none)
        and entry.details.get("category") != entry.name  # (a Shield is not "armor: Shield")
    ):  # "Longsword (weapon: Martial Melee)"
        kind = f"{kind}: {entry.details.get('category', '')}"
    older = f" [Legacy {entry.edition}]" if entry.edition == index.LEGACY else ""
    label = f"{entry.name} ({kind}){older}"
    return label if len(label) <= NAME_MAX else label[: NAME_MAX - 1] + "…"


def choice_value(srd: index.Index, entry: index.Entry) -> str:
    """What is typed into the box when a name is picked. A feature several classes share is
    picked with its class in front ("Rogue Evasion"); one a class has at two levels ("Improved
    Brutal Strike") with its level after, which `_features` reads back."""
    if entry.kind in index.NAMED_KINDS and (
        srd.lookup(entry.name, entry.edition) is not None
        or any(  # a gear entry and a magic item can share a name ("Spell Scroll"): #1927
            srd.lookup(entry.name, entry.edition, kind=kind) is not None
            for kind in index.NAMED_KINDS
            if kind != entry.kind
        )
        or (
            entry.kind != index.RULE  # a feat shares its name with a class feature
            and srd.lookup_all(entry.name, entry.edition, kind=index.FEATURE)
        )
    ):
        return f"{entry.name} ({entry.kind})"[
            :CHOICE_VALUE_MAX
        ]  # also a creature's name: say which
    if entry.kind == "monster" and any(
        srd.lookup(entry.name, edition, kind=kind) is not None
        for edition in ("2024", "2014")
        for kind in (index.ITEM, *index.ORIGIN_KINDS, *index.EQUIPMENT_KINDS, *index.HAZARD_KINDS)
    ):
        return f"{entry.name} (creature)"[
            :CHOICE_VALUE_MAX
        ]  # the species or background has the name too
    if entry.kind != index.FEATURE:
        return entry.name[:CHOICE_VALUE_MAX]
    klass = str(entry.details.get("class", ""))
    same = [
        h
        for h in srd.lookup_all(entry.name, entry.edition, kind=index.FEATURE, prefer=klass)
        if h.entry.details.get("class") == klass
    ]
    level = f" {entry.details.get('level')}" if len(same) > 1 else ""
    return f"{klass} {entry.name}{level}"[:CHOICE_VALUE_MAX]
