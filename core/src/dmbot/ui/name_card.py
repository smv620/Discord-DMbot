"""Find a name and its card (docs/PLAN.md, "Names at scale", step 2; #126).

🔍 Find a name (a one-field form, or `/dmbot names find:` with Discord's type-ahead)
opens a **name card**: what it is, when it was last heard, its other names, its secret
names (the campaign's DMs only) and its connections, with ✏️ Fix spelling, Change what it
is and 🗑 Remove this name (asks first; Undo) first, then Also called…, Edit other
names (⭐ main name, 🤫 secret, ✖ not this name), 🔗 Same as…, 🧭 Connect to… and Show
all. Everything answers from the in-memory copy of the names, refreshed right after
DMbot's own changes. Only the campaign's DMs and server managers, privately; every press
checks again.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from typing import Any

import discord
from discord import app_commands

from dmbot.campaigns import Campaign
from dmbot.discord_limits import MESSAGE_MAX
from dmbot.memory.kinds import kind_phrase
from dmbot.memory.lookup import CampaignLookup
from dmbot.memory.models import (
    CONFIRMED,
    DM,
    DM_ONLY_TYPES,
    MERGED,
    REJECTED,
    Alias,
    Entity,
    MemoryRuleError,
    Relation,
    TooLateToUndo,
    Written,
    days,
    is_id,
    name_key,
)
from dmbot.memory.ontology import CORE_TYPES, FALLBACK_PREDICATE
from dmbot.memory.search import MAX_RESULTS, Match, find, said_lately
from dmbot.memory.store import SAID_AS_MAX, MemoryStore
from dmbot.mentions import NO_PINGS
from dmbot.transcript.respell import contains, done_note, left_note, recaps_note, undone_note
from dmbot.ui import logic
from dmbot.ui.dmbot_commands import (
    VIEW_TIMEOUT_S,
    _answer_first,
    _bot,
    _Button,
    _failed,
    _is_manager,
    _Menu,
    _replace,
    _Select,
    _send,
    _tell,
)
from dmbot.ui.names import (
    GONE,
    KIND_SHORT,
    KINDS,
    NOT_AVAILABLE,
    PC,
    _campaign_for,
    _md,
    _memory,
    _UserSelect,
    already_known,
    changed,
    known_as,
    sees_secrets,
    split_names,
)

log = logging.getLogger(__name__)
THREAD_KINDS = ("thread", "quest", "mystery", "hook", "goal", "promise")  # story threads (#1662)
ITEM = "item"  # only an item, or a kind under item, can be one of a kind
CORE_KINDS = frozenset(t.key for t in CORE_TYPES)
# Why DMbot will ask, for the checks that say so in plain words; any other is "an odd pair".
FLAG_WHY = {
    "place_loop": "That would put a place inside itself",
    "held_twice": "Someone else already has that, and there is only one",
    "acts_after_end": "They were dead or destroyed before this",
    "too_many": "That's more than one place at once",
}
ONLY_ONE = "☝️ Only one of these exists (you said so)."
ONLY_ONE_GUESS = "☝️ Taken as the only one (it has a proper name)."
ONLY_ONE_BUTTON = "☝️ Only one exists"
MORE_THAN_ONE_BUTTON = "Allow more than one"
THREAD_STATE_BUTTON = "🧵 Change state"  # a story thread's buttons (#1662)
THREAD_INVOLVE_BUTTON = "👥 Who's involved"
THREAD_LINK_BUTTON = "📌 Link an event"
LED_TO_BUTTON = "➡ Led to…"  # a clue, event or thread led to another (#1679)
PROMISE_BY_BUTTON = "🤝 Who promised"  # a promise's buttons (#1674)
PROMISE_TO_BUTTON = "🤝 Promised to"
PROMISE_DUE_BUTTON = "📅 Due"
BOOK_BUTTON = "📖 In my adventure…"  # the DM says who in the adventure this is (#1715)
DEED_BUTTON = "⚔️ Record a deed"  # a player character or the party did something (#1732)
STANDING_BUTTON = "🤝 Standing"  # how an NPC, group or place feels about the party (#1655)
CLUE_BUTTON = "🔒 Only some know this"  # a clue's list of who knows it (#1649)
ALSO_CALLED = "🏷️ Also called…"  # adds another name; its own icon, not Add a name's (#393)
NOTHING_CHANGED = (
    "Something went wrong and nothing was changed. Press 🔍 Find a name to open it and try again."
)

CARD_MAX = MESSAGE_MAX - 100  # margin kept from before: room for the words a sender adds around it
SHOWN = 3  # per section, then "… and N more"
# A state, not an action: it is a setting that stays until pressed again.
SECRET_OFF = "🤫 Secret: no"
SECRET_ON = "🤫 Secret: yes"
SECRET_FORCED = "You found it by a secret name, so this one is secret too."
SECRET_DENIED_PRESS = "Only the campaign's DM can keep a connection secret."
SECRET_DENIED_SAVE = (
    "Players can see this one, because only the campaign's DM can keep a connection secret."
)
SECRET_ALREADY = "It was already secret, so it stays secret. Remove it and add it again to show it."
SHOW_ALL = "Show all ▾"
NOTHING_MORE = "That list has changed. Open the name card again to see it."
NOTE_MAX = 600  # what just changed, above the card
NAME_LIMIT = 100  # what a form field may hold

# Connections in plain words (the fixed core of the memory rules).
CONNECTION_WORDS = {
    "located_in": "is in",
    "member_of": "is a member of",
    "ally_of": "is a friend or ally of",
    "enemy_of": "is an enemy of",
    "kin_of": "is family of",
    "owns": "owns",
    "knows": "knows",
    "serves": "works for",
    "carries": "carries",
}
# The same connection read from the other name's card ("Frostwolf tribe has as members
# Ulfgar"). Connections that read the same both ways use CONNECTION_WORDS.
CONNECTION_BACK = {
    "located_in": "is where you find",
    "member_of": "has as a member",
    "owns": "is owned by",
    "knows": "is known to",
    "serves": "is the boss of",
    "carries": "is carried by",
}
# On a card, where several names may follow ("members include Ulfgar, Kesh").
CARD_BACK = {**CONNECTION_BACK, "member_of": "members include"}
BOTH_WAYS = frozenset({"ally_of", "enemy_of", "kin_of"})
BACK = ":back"  # a "how" picked from the other name's side ("has as members…")


def connection_words(predicate: str) -> str:
    return CONNECTION_WORDS.get(predicate, predicate.replace("_", " "))


def fact_words(r: Relation) -> str:
    """How one fact reads between its two names. On the catch-all, the words it was said in
    ("sworn on the ashes of") are the fact's own label (#1303); otherwise the relationship's."""
    if r.predicate == FALLBACK_PREDICATE and r.detail:
        return _md(r.detail)  # transcript words: never let them format or ping
    return connection_words(r.predicate)


def sentence(lookup: CampaignLookup, r: Relation) -> str | None:
    """ "Ulfgar is a member of Frostwolf tribe", or None if either end is gone."""
    subject, obj = lookup.entities.get(r.subject_id), lookup.entities.get(r.object_id)
    if subject is None or obj is None:
        return None
    return f"{subject.name} {fact_words(r)} {obj.name}"


def connect_choices(name: str) -> list[tuple[str, str, str | None]]:
    """(value, label, description) for "How is it connected?": each connection from this
    name's side, each followed by the same one the other way round."""
    out: list[tuple[str, str, str | None]] = []
    for p, w in CONNECTION_WORDS.items():
        out.append((p, logic.shorten(f"{name} {w}…", logic.OPTION_LABEL_MAX), None))
        if p in CONNECTION_BACK:
            label = logic.shorten(f"{name} {CONNECTION_BACK[p]}…", logic.OPTION_LABEL_MAX)
            out.append((p + BACK, label, "The other way round"))
    return out


def cut(text: str, limit: int) -> str:
    """`text` within `limit` characters, cut after a whole entry (never inside **bold**)."""
    if len(text) <= limit:
        return text
    head = text[: limit - 1]
    at = max(head.rfind("\n"), head.rfind(", "), head.rfind("; "))
    return (head[:at] if at > 0 else head).rstrip(",; ") + "…"


def _who(names: CampaignLookup, entity_id: str) -> str:
    """A name with its kind, so two buttons never read the same: "Bell (NPC)"."""
    entity = names.entities.get(entity_id)
    return f"{entity.name} ({_kind(entity.kind_key)})" if entity is not None else "?"


def _kind(type_key: str) -> str:
    return KIND_SHORT.get(type_key, type_key)


def _more(items: list[str], sep: str = ", ") -> str:
    shown = sep.join(items[:SHOWN])
    return shown + (f" … and {len(items) - SHOWN} more" if len(items) > SHOWN else "")


def _groups(
    lookup: CampaignLookup, entity_id: str, connections: list[Relation], secrets: bool
) -> dict[str, list[str]]:
    """Connections grouped by how they're connected, read from this name's side: "is a member of
    **Frostwolf tribe**; knows **Bell**, **Kesh**". A secret one is left out unless `secrets`."""
    groups: dict[str, list[str]] = {}
    for r in connections:
        if r.status != CONFIRMED or (r.secret and not secrets):
            continue
        if r.predicate == "led_to":
            continue  # shown as the "Came from" / "Led to" lines instead (#1679)
        ahead = r.subject_id == entity_id or r.predicate in BOTH_WAYS
        other = lookup.entities.get(r.object_id if r.subject_id == entity_id else r.subject_id)
        if other is None or other.status != CONFIRMED or other.id == entity_id:
            continue
        words = (
            fact_words(r)
            if ahead
            else (
                f"is linked (“{_md(r.detail)}”) to"
                if r.predicate == FALLBACK_PREDICATE and r.detail
                else CARD_BACK.get(r.predicate, "is connected to")
            )
        )
        groups.setdefault(words, []).append(f"**{_md(other.name)}**" + (" 🤫" if r.secret else ""))
    return groups


# What Show all can open, in the card's order: key -> how the select names it.
NAMES, SECRETS, LINKS = "names", "secret", "links"
SECTION_WORDS = {NAMES: "Other names", SECRETS: "Secret names", LINKS: "Connections"}
PAGE_LINES = 25  # most lines in one Show all message, then Earlier / Later


def sections(
    lookup: CampaignLookup, entity_id: str, connections: list[Relation], *, secrets: bool
) -> dict[str, list[str]]:
    """Every entry of each section the card cuts, in the card's words and order. A section with
    nothing in it is left out, and the secret names and secret connections only when `secrets`
    (the campaign's DMs), the same check the card makes."""
    entity = lookup.entities.get(entity_id)
    if entity is None:
        return {}
    own = name_key(entity.name)
    mine = [e for e in lookup.names if e.entity_id == entity_id and e.confirmed]
    found = {
        NAMES: [_md(e.text) for e in mine if not e.secret and e.key != own],
        SECRETS: [_md(e.text) for e in mine if e.secret] if secrets else [],
        LINKS: [
            f"{words} {who}"
            for words, group in _groups(lookup, entity_id, connections, secrets).items()
            for who in group
        ],
    }
    return {key: lines for key, lines in found.items() if lines}


def cut_sections(
    lookup: CampaignLookup, entity_id: str, connections: list[Relation], *, secrets: bool
) -> list[tuple[str, str]]:
    """(key, "Other names (12)") for each section the card shows only the start of."""
    full = sections(lookup, entity_id, connections, secrets=secrets)
    groups = _groups(lookup, entity_id, connections, secrets)
    cut_keys = [
        key
        for key, lines in full.items()
        if (key != LINKS and len(lines) > SHOWN)
        or (key == LINKS and any(len(who) > SHOWN for who in groups.values()))
    ]
    return [(key, f"{SECTION_WORDS[key]} ({len(full[key])})") for key in cut_keys]


def card_text(
    lookup: CampaignLookup,
    entity_id: str,
    connections: list[Relation],
    *,
    secrets: bool,
    player: str | None = None,
    sheet: str | None = None,
    limit: int = CARD_MAX,
    item: bool | None = None,
    thread_lines: list[str] | None = None,
    book_line: str | None = None,
) -> str | None:
    """The card for one name, or None if it's gone. `player`: who plays it (a player's
    character); `sheet`: its D&D Beyond sheet line (#723). `item`: it is an item, of the core
    kind or one a campaign added under it (None: only the core kind counts)."""
    entity = lookup.entities.get(entity_id)
    if entity is None or entity.status != CONFIRMED:
        return None
    if entity.audience_limited and not secrets:
        return None  # a clue only some people know is the DM's alone (#1649)
    own = name_key(entity.name)
    mine = [e for e in lookup.names if e.entity_id == entity_id and e.confirmed]
    others = [
        _md(e.text) + (" (old spelling)" if e.kind == "old" else "")
        for e in mine
        if not e.secret and e.key != own
    ]
    # (a limited clue's own name is "secret" for the Cleaner, but it is the card's title)
    hidden = [
        _md(e.text) for e in mine if e.secret and not (entity.audience_limited and e.key == own)
    ]
    heard = lookup.heard.get(entity_id)
    when = (
        f"Last heard <t:{heard.last_session_at}:D>"
        if heard is not None and heard.last_session_at
        else "Not heard in a session yet"
    )
    phrase = kind_phrase(entity, lookup.links.get(entity_id, ()))
    what = f"{phrase} · played by **{_md(player)}**" if player else phrase
    lines = [f"🪪 **{_md(entity.name)}** · {what} · {when}"]
    if (entity.type == ITEM if item is None else item) and entity.is_unique:
        lines.append(ONLY_ONE if entity.unique_item else ONLY_ONE_GUESS)
    if thread_lines:  # a story thread (#1662): its state, who is involved, what moved it
        lines.extend(thread_lines)
    if book_line and secrets:  # who this is in the DM's adventure: the DM's alone (#1715)
        lines.append(book_line)
    if entity.audience_limited:
        knowers = sorted(
            (
                lookup.entities[r.subject_id].name
                for r in connections
                if r.predicate == "knows"
                and r.object_id == entity_id
                and r.status == CONFIRMED
                and r.to_session_at is None
                and r.subject_id in lookup.entities
            ),
            key=name_key,
        )
        who = _more([_md(n) for n in knowers]) if knowers else "—"
        lines.append(f"🔒 **Only some people know this:** {who}")
    if sheet:
        lines.append(sheet)
    if others:
        lines.append(f"**Also called:** {_more(others)}")
    if secrets and hidden:
        lines.append(f"**🤫 Secret:** {_more(hidden)} (hidden from players)")
    groups = _groups(lookup, entity_id, connections, secrets)
    if groups:
        said = "; ".join(f"{words} {_more(who)}" for words, who in groups.items())
        lines.append(f"**Connections:** {said}")
    text = "\n".join(lines)
    return cut(text, limit)


async def _names(interaction: discord.Interaction, campaign: Campaign) -> CampaignLookup | None:
    """The campaign's names from the in-memory copy, or None after saying why."""
    cache = _bot(interaction).lookup
    if cache is None:
        await _tell(interaction, NOT_AVAILABLE)
        return None
    try:  # (without the clues only some people know, unless this person is a DM, #1649)
        return (await cache.get(campaign.guild_id, campaign.id)).for_viewer(
            sees_secrets(campaign, interaction.user.id)
        )
    except Exception:
        log.exception("Couldn't load the campaign's names")
        await _tell(interaction, "DMbot couldn't load the names just now. Try again in a moment.")
        return None


def _a_person_or_place(onto: Any, type_key: str) -> bool:
    return any(onto.is_a(type_key, kind) for kind in ("character", "faction", "place"))


async def show_card(
    interaction: discord.Interaction,
    campaign_id: str,
    entity_id: str,
    *,
    replace: bool = False,
    note: str | None = None,
    undo_connection: int | None = None,
    undo_spelling: int | None = None,
    finish_past: int | None = None,
) -> None:
    """The card, as a new private message or in place of the one pressed; `note` goes
    on top (what just changed). `undo_connection`: the batch of a connection just removed,
    which puts an Undo on the card (#1566). `undo_spelling`: the batch of a spelling just fixed
    (#2170), which puts an Undo on the card that takes the name and the past transcripts back."""
    await _answer_first(interaction, in_place=replace)  # names may load slowly (#537)
    campaign = await _campaign_for(interaction, campaign_id)
    memory = _memory(interaction)
    if campaign is None:
        return
    if memory is None:  # never leave Discord's "thinking…" up
        await _tell(interaction, NOT_AVAILABLE)
        return
    names = await _names(interaction, campaign)
    if names is None:
        return
    secrets = sees_secrets(campaign, interaction.user.id)
    connections = await memory.relations(
        campaign.guild_id, campaign.id, entity_id=entity_id, include_secret=secrets
    )
    entity = names.entities.get(entity_id)
    player = sheet = None
    has_sheet = False
    if entity is not None and entity.played_by is not None:
        player = _bot(interaction).name_of(campaign.guild_id, entity.played_by)
        store = _bot(interaction).sheets
        if store is not None:
            from dmbot.ui.sheets import card_line

            found = await store.sheet(campaign.guild_id, campaign.id, entity_id)
            has_sheet = found is not None
            sheet = card_line(found, link=secrets)  # the address: the campaign's DMs only
    note = cut(note, NOTE_MAX) if note else None
    limit = CARD_MAX - (len(note) + 2 if note else 0)
    is_item = entity is not None and await _is_item(memory, campaign, entity.type)
    is_thread = entity is not None and entity.type in THREAD_KINDS
    lines_of_thread: list[str] | None = None
    if is_thread and secrets:  # a story thread is the DM's: its state, who is in it, what moved it
        from dmbot.ui.continuity import _session_numbers
        from dmbot.ui.threads import thread_lines

        here = [
            t
            for t in await memory.threads(
                campaign.guild_id,
                campaign.id,
                stalled_after=int(
                    getattr(_bot(interaction).settings, "thread_stalled_sessions", 3)
                ),
            )
            if t.thread.id == entity_id
        ]
        if here:
            lines_of_thread = thread_lines(
                here[0], await _session_numbers(interaction, campaign.id)
            )
    elif entity is not None and secrets and entity.type in ("clue", "event"):
        # A clue's or an event's "Led to" / "Came from" (#1679): the DM's word, so the DM's card.
        from dmbot.memory.threads import names_line

        came, went = await memory.chain(campaign.guild_id, campaign.id, entity_id)
        lines_of_thread = [
            line
            for line in (
                names_line("Came from", [_md(e.name) for e in came]),
                names_line("Led to", [_md(e.name) for e in went]),
            )
            if line
        ] or None
    book_line, book_ok = None, False
    if (
        entity is not None
        and secrets
        and campaign.story_mode != "off"
        and not entity.is_player_character
        and not entity.party
        and _a_person_or_place(await memory.ontology(campaign.guild_id, campaign.id), entity.type)
    ):
        from dmbot.memory import book_links

        if await book_links.has_plan(memory, campaign.guild_id, campaign.id):
            book_ok = True
            mine = await book_links.linked(memory, campaign.guild_id, campaign.id, entity_id)
            if mine:
                book_line = "📖 **In your adventure:** " + "; ".join(
                    _md(m.name[:60]) + (f" ({_md(m.chapter[:60])})" if m.chapter else "")
                    for m in mine[:3]
                )
    text = card_text(
        names,
        entity_id,
        connections,
        secrets=secrets,
        player=player,
        sheet=sheet,
        limit=limit,
        item=is_item,
        thread_lines=lines_of_thread,
        book_line=book_line,
    )
    if text is None or entity is None:
        if replace:  # never leave the message pressed on a step that's over
            await _replace(interaction, GONE, None)
        else:
            await _tell(interaction, GONE)
        return
    cuts = cut_sections(names, entity_id, connections, secrets=secrets)
    if not cuts and text != card_text(
        names, entity_id, connections, secrets=secrets, player=player, sheet=sheet,
        limit=10**6, item=is_item, thread_lines=lines_of_thread, book_line=book_line,
    ):  # fmt: skip
        # Every section is short, but the whole card still didn't fit one message (a long note,
        # a sheet line, many groups): offer every section that has anything in it.
        everything = sections(names, entity_id, connections, secrets=secrets)
        cuts = [(k, f"{SECTION_WORDS[k]} ({len(v)})") for k, v in everything.items()]
    own = name_key(entity.name)
    others = any(
        e.entity_id == entity_id and e.confirmed and e.key != own and (secrets or not e.secret)
        for e in names.names
    )
    if note:
        text = f"{note}\n\n{text}"
    # The narrator's spelling: DMs only, spoken recaps on, and not for a secret main name (the
    # narrator's dictionary goes to the voice company; the store refuses it too).
    say = (
        sees_secrets(campaign, interaction.user.id)
        and not any(e.entity_id == entity_id and e.key == own and e.secret for e in names.names)
        and await _spoken_recaps(interaction, campaign)
    )
    view = NameCard(
        campaign.id,
        entity_id,
        others=others,
        cut=cuts,
        sheet=has_sheet,
        item=is_item,
        unique=entity.is_unique,
        clue=entity.type == "clue",
        thread=is_thread and secrets,
        promise=entity.type == "promise" and secrets,
        led=secrets and (is_thread or entity.type in ("clue", "event")),
        book=book_ok,
        say=say,
        standing=(
            entity.type in ("character", "faction", "place")
            and not entity.is_player_character
            and not entity.party
            and sees_secrets(campaign, interaction.user.id)
        ),
        deed=(
            (entity.is_player_character or entity.party)
            and sees_secrets(campaign, interaction.user.id)
        ),
        undo_connection=undo_connection,
        undo_spelling=undo_spelling,
        finish_past=finish_past,
    )
    if replace:
        await _replace(interaction, text, view)
    else:
        await _send(interaction, text, view)


def section_pages(lines: list[str], head: str, limit: int = CARD_MAX) -> list[str]:
    """The section's lines in the card's order, a page at a time: at most PAGE_LINES lines and
    inside one Discord message, so no line is ever cut."""
    pages: list[list[str]] = [[]]
    size = len(head)
    for line in lines:
        line = cut(line, limit - len(head) - 2)
        if pages[-1] and (len(pages[-1]) >= PAGE_LINES or size + len(line) + 1 > limit):
            pages.append([])
            size = len(head)
        pages[-1].append(line)
        size += len(line) + 1
    return [f"{head}\n" + "\n".join(page) for page in pages]


async def show_section(
    interaction: discord.Interaction,
    campaign_id: str,
    entity_id: str,
    key: str,
    *,
    page: int = 0,
    replace: bool = False,
) -> None:
    """Show all on a cut section: every entry of it, as a private message of its own (or the next
    page in place). Read-only, and worked out again on every press, secrets check included."""
    await _answer_first(interaction, in_place=replace)
    campaign = await _campaign_for(interaction, campaign_id)
    memory = _memory(interaction)
    if campaign is None:
        return
    if memory is None:
        await _tell(interaction, NOT_AVAILABLE)
        return
    names = await _names(interaction, campaign)
    if names is None:
        return
    secrets = sees_secrets(campaign, interaction.user.id)
    connections = await memory.relations(
        campaign.guild_id, campaign.id, entity_id=entity_id, include_secret=secrets
    )
    entity = names.entities.get(entity_id)
    if entity is None or entity.status != CONFIRMED:
        await _tell(interaction, GONE)
        return
    lines = sections(names, entity_id, connections, secrets=secrets).get(key)
    if not lines:  # changed since the card was drawn, or a secret section for someone who can't
        await _tell(interaction, NOTHING_MORE)
        return
    head = f"**{_md(entity.name)} · {SECTION_WORDS[key].lower()} ({len(lines)})**"
    pages = section_pages(lines, head)
    page = min(max(page, 0), len(pages) - 1)
    view = AllPages(campaign_id, entity_id, key, page, len(pages)) if len(pages) > 1 else None
    footer = f"\n_Page {page + 1} of {len(pages)}._" if view is not None else ""
    if replace:
        await _replace(interaction, pages[page] + footer, view)
    else:
        await _send(interaction, pages[page] + footer, view)


class AllPages(_Menu):
    """Earlier / Later under a long Show all list. Each press works the list out again."""

    def __init__(self, campaign_id: str, entity_id: str, key: str, page: int, pages: int) -> None:
        super().__init__()
        self.campaign_id, self.entity_id, self.key, self.page = campaign_id, entity_id, key, page
        if page > 0:
            self.add_item(_Button(self._earlier, label="◀ Earlier"))
        if page < pages - 1:
            self.add_item(_Button(self._later, label="Later ▶"))

    async def _earlier(self, interaction: discord.Interaction) -> None:
        await self._go(interaction, self.page - 1)

    async def _later(self, interaction: discord.Interaction) -> None:
        await self._go(interaction, self.page + 1)

    async def _go(self, interaction: discord.Interaction, page: int) -> None:
        # Not stopped first: if the list changed meanwhile the message keeps working buttons.
        await show_section(
            interaction, self.campaign_id, self.entity_id, self.key, page=page, replace=True
        )


async def _is_item(memory: Any, campaign: Campaign, type_key: str) -> bool:
    """Is this kind an item, or a kind the campaign added under item (a potion, a weapon)?
    The core kinds are known without asking."""
    if type_key == ITEM:
        return True
    if type_key in CORE_KINDS:
        return False
    onto = await memory.ontology(campaign.guild_id, campaign.id)
    return bool(onto.is_a(type_key, ITEM))


async def _current(
    interaction: discord.Interaction, campaign_id: str, entity_id: str
) -> tuple[Campaign, str] | None:
    """The campaign and the entry's name, if both are still there (tells them if not)."""
    campaign = await _campaign_for(interaction, campaign_id)
    memory = _memory(interaction)
    if campaign is None or memory is None:
        return None
    entity = await memory.entity(campaign.guild_id, campaign.id, entity_id)
    if entity is None or entity.status != CONFIRMED:
        await _tell(interaction, GONE)
        return None
    return campaign, entity.name


class NameCard(_Menu):
    def __init__(
        self,
        campaign_id: str,
        entity_id: str,
        *,
        others: bool = False,
        cut: list[tuple[str, str]] | None = None,
        sheet: bool = False,
        item: bool = False,
        unique: bool = False,
        clue: bool = False,
        standing: bool = False,
        deed: bool = False,
        thread: bool = False,
        promise: bool = False,
        led: bool = False,
        book: bool = False,
        say: bool = False,
        undo_connection: int | None = None,
        undo_spelling: int | None = None,
        finish_past: int | None = None,
    ) -> None:
        super().__init__()
        self.campaign_id = campaign_id
        self.entity_id = entity_id
        self.unique = unique
        grey = discord.ButtonStyle.secondary
        if undo_connection is not None:  # last row but one: out of the way of the edits
            self.add_item(UndoConnectionButton(campaign_id, entity_id, undo_connection))
        if undo_spelling is not None:
            self.add_item(UndoSpellingButton(campaign_id, entity_id, undo_spelling))
        if finish_past is not None:
            self.add_item(FinishPastButton(campaign_id, entity_id, finish_past))
        # First row: the three edits the names panel names, in its words (#353). Remove
        # says what it does and still asks first.
        self.add_item(_Button(self._fix, label="✏️ Fix spelling", style=discord.ButtonStyle.primary))
        self.add_item(_Button(self._change_kind, label="Change what it is", style=grey))
        self.add_item(_Button(self._remove, label="🗑 Remove this name", style=grey))
        # "Also called", as the card says: right after Add a name, "Add another name"
        # read like "add the next name" (#353 review).
        self.add_item(_Button(self._add, label=ALSO_CALLED, style=grey, row=1))
        if others:
            self.add_item(_Button(self._edit_others, label="Edit other names", style=grey, row=1))
        self.add_item(_Button(self._same, label="🔗 Same as…", style=grey, row=1))
        # Row 2, so row 1's buttons don't clip on a phone (#393).
        self.add_item(_Button(self._connect, label="🧭 Connect to…", style=grey, row=2))
        if say:  # how the recap narrator says this name (#2171), the same as the website's field
            self.add_item(_Button(self._say, label=SAY_BUTTON, style=grey, row=2))
        if led:  # the DM's word that this led to a thread or an event (#1679)
            self.add_item(_Button(self._led_to, label=LED_TO_BUTTON, style=grey, row=2))
        if book:  # the DM's word that this is an entry in their adventure (#1715)
            self.add_item(_Button(self._book, label=BOOK_BUTTON, style=grey, row=2))
        if sheet:
            self.add_item(_Button(self._unlink_sheet, label="📜 Forget sheet", style=grey, row=2))
        if item:
            # The DM's word on an item: a named one (Heartseeker) is taken as one of a kind
            # until they say otherwise (#1267). Says what it will do: it flips.
            label = MORE_THAN_ONE_BUTTON if unique else ONLY_ONE_BUTTON
            self.add_item(_Button(self._one_of_a_kind, label=label, style=grey, row=3))
        if thread:  # a story thread (#1662): move it, say who is in it, link what moved it
            self.add_item(_Button(self._thread_state, label=THREAD_STATE_BUTTON, style=grey, row=3))
            if promise:  # who made it, to whom, when: instead of "who's involved"
                self.add_item(_Button(self._promise_by, label=PROMISE_BY_BUTTON, style=grey, row=3))
                self.add_item(_Button(self._promise_to, label=PROMISE_TO_BUTTON, style=grey, row=3))
                self.add_item(
                    _Button(self._promise_due, label=PROMISE_DUE_BUTTON, style=grey, row=3)
                )
            else:
                self.add_item(
                    _Button(self._thread_involve, label=THREAD_INVOLVE_BUTTON, style=grey, row=3)
                )
            self.add_item(_Button(self._thread_link, label=THREAD_LINK_BUTTON, style=grey, row=3))
        if standing:  # an NPC, group or place: how it feels about the party (#1655)
            self.add_item(_Button(self._standing, label=STANDING_BUTTON, style=grey, row=3))
        if deed:  # a player character or the party: a deed the DM records (#1732)
            self.add_item(_Button(self._deed, label=DEED_BUTTON, style=grey, row=3))
        if clue:  # a clue or secret: who knows it (#1649); an item has the row-3 button instead
            self.add_item(_Button(self._who_knows, label=CLUE_BUTTON, style=grey, row=3))
        if cut:
            # One select for every cut section, not a button each: Discord allows five rows.
            self.show_all = _Select(
                self._show_all,
                placeholder=SHOW_ALL,
                options=[discord.SelectOption(label=label, value=key) for key, label in cut],
                row=4,
            )
            self.add_item(self.show_all)

    async def _book(self, interaction: discord.Interaction) -> None:
        from dmbot.ui.book_link import show_picker

        await _answer_first(interaction, in_place=True)
        self.stop()
        await show_picker(interaction, self.campaign_id, self.entity_id)

    async def _thread_state(self, interaction: discord.Interaction) -> None:
        from dmbot.ui.threads import open_change_state

        await _answer_first(interaction, in_place=True)
        self.stop()
        await open_change_state(interaction, self.campaign_id, self.entity_id)

    async def _thread_involve(self, interaction: discord.Interaction) -> None:
        from dmbot.ui.threads import InvolveForm

        campaign = await _campaign_for(interaction, self.campaign_id)
        if campaign is None:
            return
        if not sees_secrets(campaign, interaction.user.id):
            await _tell(interaction, "Only the DM can change a story thread.")
            return
        await interaction.response.send_modal(InvolveForm(self.campaign_id, self.entity_id))

    async def _led_to(self, interaction: discord.Interaction) -> None:
        from dmbot.ui.threads import LedToForm

        campaign = await _campaign_for(interaction, self.campaign_id)
        if campaign is None:
            return
        if not sees_secrets(campaign, interaction.user.id):
            await _tell(interaction, "Only the DM can do that.")
            return
        await interaction.response.send_modal(LedToForm(self.campaign_id, self.entity_id))

    async def _promise_form(self, interaction: discord.Interaction, which: str) -> None:
        from dmbot.ui.threads import DueForm, PartyForm

        campaign = await _campaign_for(interaction, self.campaign_id)
        if campaign is None:
            return
        if not sees_secrets(campaign, interaction.user.id):
            await _tell(interaction, "Only the DM can change a story thread.")
            return
        form: Any = (
            DueForm(self.campaign_id, self.entity_id)
            if which == "due"
            else PartyForm(self.campaign_id, self.entity_id, to=which == "to")
        )
        await interaction.response.send_modal(form)

    async def _promise_by(self, interaction: discord.Interaction) -> None:
        await self._promise_form(interaction, "by")

    async def _promise_to(self, interaction: discord.Interaction) -> None:
        await self._promise_form(interaction, "to")

    async def _promise_due(self, interaction: discord.Interaction) -> None:
        await self._promise_form(interaction, "due")

    async def _thread_link(self, interaction: discord.Interaction) -> None:
        from dmbot.ui.threads import LinkForm

        campaign = await _campaign_for(interaction, self.campaign_id)
        if campaign is None:
            return
        if not sees_secrets(campaign, interaction.user.id):
            await _tell(interaction, "Only the DM can change a story thread.")
            return
        await interaction.response.send_modal(LinkForm(self.campaign_id, self.entity_id))

    async def _deed(self, interaction: discord.Interaction) -> None:
        from dmbot.ui.deeds import start

        await start(interaction, self.campaign_id, self.entity_id)

    async def _standing(self, interaction: discord.Interaction) -> None:
        from dmbot.ui.standing import show_standing

        await _answer_first(interaction, in_place=True)
        self.stop()
        await show_standing(interaction, self.campaign_id, self.entity_id)

    async def _who_knows(self, interaction: discord.Interaction) -> None:
        from dmbot.ui.clue_audience import show_audience

        await _answer_first(interaction, in_place=True)
        self.stop()
        await show_audience(interaction, self.campaign_id, self.entity_id)

    async def _one_of_a_kind(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        found = await _current(interaction, self.campaign_id, self.entity_id)
        memory = _memory(interaction)
        if found is None or memory is None:
            return
        campaign, name = found
        if not sees_secrets(campaign, interaction.user.id):  # only the campaign's DMs
            await _tell(interaction, "Only the DM can change that.")
            return
        now_unique = not self.unique
        try:
            await memory.set_unique(
                campaign.guild_id, campaign.id, self.entity_id, now_unique, source=DM
            )
        except MemoryRuleError as exc:
            await _tell(interaction, f"Couldn't change it. {exc}")
            return
        changed(interaction, campaign)
        note = (
            f"☝️ Noted: there is only one **{_md(name)}**. It goes in your after-session "
            "report if two characters carry it. You can change this any time."
            if now_unique
            else f"Noted: there can be more than one **{_md(name)}**."
        )
        await show_card(interaction, campaign.id, self.entity_id, replace=True, note=note)

    async def _unlink_sheet(self, interaction: discord.Interaction) -> None:
        from dmbot.ui.sheets import dm_unlink

        await _answer_first(interaction, in_place=True)
        found = await _current(interaction, self.campaign_id, self.entity_id)
        if found is None:
            return
        campaign, name = found
        self.stop()
        gone = await dm_unlink(interaction, campaign.guild_id, campaign.id, self.entity_id)
        note = (
            f"DMbot forgot **{_md(name)}**'s sheet. The player can link it again with 📜 My "
            "character sheet."
            if gone
            else None
        )
        await show_card(interaction, self.campaign_id, self.entity_id, replace=True, note=note)

    async def _show_all(self, interaction: discord.Interaction) -> None:
        # A new private message; the card stays as it is.
        await show_section(interaction, self.campaign_id, self.entity_id, self.show_all.values[0])

    async def _edit_others(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        found = await _current(interaction, self.campaign_id, self.entity_id)
        if found is None:
            return
        campaign, name = found
        listed = await _other_names(interaction, campaign, self.entity_id, name)
        if not listed:
            await _tell(
                interaction,
                f"**{_md(name)}** has no other names yet. Press {ALSO_CALLED} to add one.",
            )
            return
        self.stop()
        more = (
            f" Showing the first {logic.SELECT_OPTIONS_MAX}."
            if len(listed) > logic.SELECT_OPTIONS_MAX
            else ""
        )
        await _replace(
            interaction,
            f"**Other names for {_md(name)}:** pick one to change.{more}",
            OtherNames(self.campaign_id, self.entity_id, listed),
        )

    async def _same(self, interaction: discord.Interaction) -> None:
        found = await _current(interaction, self.campaign_id, self.entity_id)
        if found:
            self.origin = None  # the form's answer replaces this message; cancel keeps it
            await interaction.response.send_modal(
                PickForm(self.campaign_id, self.entity_id, SAME, f"{found[1]} is the same as…")
            )

    async def _connect(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        found = await _current(interaction, self.campaign_id, self.entity_id)
        if found is None:
            return
        campaign, name = found
        names = await _names(interaction, campaign)
        memory = _memory(interaction)
        if names is None or memory is None:
            return
        secrets = sees_secrets(campaign, interaction.user.id)
        removable = []
        for r in await memory.relations(
            campaign.guild_id,
            campaign.id,
            entity_id=self.entity_id,
            confirmed_only=True,
            include_secret=secrets,
        ):
            said = sentence(names, r)
            if said is not None:
                removable.append((r.id, said + (" 🤫" if r.secret else "")))
        self.stop()
        back = " Or take one back with Remove a connection." if removable else ""
        await _replace(
            interaction,
            f"**How is {_md(name)} connected?** Pick how, then the other name.{back}"
            + (" 🤫 hides it from players." if secrets else ""),
            Connect(self.campaign_id, self.entity_id, name, removable, secrets=secrets),
        )

    async def _fix(self, interaction: discord.Interaction) -> None:
        found = await _current(interaction, self.campaign_id, self.entity_id)
        if found:
            self.origin = None  # the form's answer replaces this message; cancel keeps it
            # The narrator's spelling is asked for only where spoken recaps are on for the
            # campaign (#1419); a form must open within 3 seconds, so a slow check means no field.
            spoken = await _spoken_recaps(interaction, found[0])
            said = None
            memory = _memory(interaction)
            if spoken and memory is not None:
                said = await memory.said_as(found[0].guild_id, found[0].id, self.entity_id)
            await interaction.response.send_modal(
                FixSpellingForm(
                    self.campaign_id, self.entity_id, found[1], spoken=spoken, said_as=said
                )
            )

    async def _say(self, interaction: discord.Interaction) -> None:
        found = await _current(interaction, self.campaign_id, self.entity_id)
        memory = _memory(interaction)
        if found is None or memory is None:
            return
        campaign, name = found
        if not sees_secrets(campaign, interaction.user.id):
            await _tell(interaction, "Only a DM of this campaign can do that.")
            return
        # No plan check here: a slow one would pass Discord's 3 seconds for opening a form. The
        # card shows this button only while spoken recaps are on, and the submit checks again.
        said = await memory.said_as(campaign.guild_id, campaign.id, self.entity_id)
        self.origin = None  # the form's answer replaces this message; cancel keeps it
        await interaction.response.send_modal(SayForm(self.campaign_id, self.entity_id, name, said))

    async def _add(self, interaction: discord.Interaction) -> None:
        found = await _current(interaction, self.campaign_id, self.entity_id)
        if found:
            self.origin = None  # the form's answer replaces this message; cancel keeps it
            secrets = sees_secrets(found[0], interaction.user.id)
            await interaction.response.send_modal(
                AnotherNameForm(self.campaign_id, self.entity_id, found[1], secrets=secrets)
            )

    async def _change_kind(self, interaction: discord.Interaction) -> None:
        found = await _current(interaction, self.campaign_id, self.entity_id)
        if found is None:
            return
        self.stop()
        view = KindChange(self.campaign_id, self.entity_id, found[1])
        await _replace(interaction, f"**What is {_md(found[1])}?**", view)

    async def _remove(self, interaction: discord.Interaction) -> None:
        found = await _current(interaction, self.campaign_id, self.entity_id)
        if found is None:
            return
        campaign, name = found
        memory = _memory(interaction)
        assert memory is not None
        own = name_key(name)
        others = [
            _md(a.text)
            for a in await memory.aliases(
                campaign.guild_id,
                campaign.id,
                entity_id=self.entity_id,
                include_secret=sees_secrets(campaign, interaction.user.id),
            )
            if a.key != own
        ]
        also = f" and its other names ({_more(others)})" if others else ""
        self.stop()
        await _replace(
            interaction,
            f"Forget **{_md(name)}**? DMbot stops listening for it{also}. Past transcripts "
            "don't change.",
            ConfirmRemove(self.campaign_id, self.entity_id),
        )


async def _spoken_recaps(interaction: discord.Interaction, campaign: Campaign) -> bool:
    """Whether the narrator's spelling is offered: spoken recaps are switched on and the
    campaign's plan includes them. False when it can't be told (the field is only a help)."""
    bot = interaction.client
    settings = getattr(bot, "settings", None)
    if settings is None or not getattr(settings, "recap_audio_on", False):
        return False
    try:
        async with asyncio.timeout(2):
            return bool(
                await bot.plan_allows("recap_audio", campaign.guild_id, campaign, fail_open=False)  # type: ignore[attr-defined]
            )
    except Exception:
        return False


SAID_AS_LABEL = "How to say it in the narration (optional)"
SAID_AS_HELP = "Spell it how it sounds, like Kah-zeth. Empty: as written."


def renamed_key(old: str, new: str) -> bool:
    return name_key(old) != name_key(new)


@dataclass(frozen=True, slots=True)
class PastNote:
    text: str = ""
    remaining: int = 0  # lines more than one run does: the Finish button carries on


async def _names_around(
    memory: MemoryStore, campaign: Campaign, entity_id: str, old: str
) -> tuple[bool, list[str], list[str]]:
    """(kept, longer, hidden) for a respelling of `old` (#2170). `kept`: the entry still has the
    old spelling as an old spelling (a clash drops it, and then nothing is rewritten). `longer`:
    the names that hold the old spelling as whole words ("Branok Hall"), never rewritten inside.
    `hidden`: names players must not read, which a respelling must not complete: every secret
    name, and the names of entries that are the DM's alone or known to only some people. They only
    stop a change; nothing is ever written from them. The names are read once."""
    old_key = name_key(old)
    everyone = await memory.aliases(campaign.guild_id, campaign.id, include_secret=True)
    kept = any(a.entity_id == entity_id and a.key == old_key and a.kind == "old" for a in everyone)
    longer = [a.text for a in everyone if a.key != old_key and contains(a.text, old)]
    entities = await memory.entities(campaign.guild_id, campaign.id)
    private = {e.id: e.name for e in entities if e.audience_limited or e.type in DM_ONLY_TYPES}
    hidden = [a.text for a in everyone if a.secret or a.entity_id in private]
    hidden += private.values()
    return kept, longer, hidden


async def _respell_past(
    interaction: discord.Interaction,
    memory: MemoryStore,
    campaign: Campaign,
    entity_id: str,
    old: str,
    written: Written[Entity],
) -> PastNote:
    """After a spelling fix: the cleaned transcripts of this campaign's past sessions and its
    recaps follow (#2170); the transcripts in the same Undo. Only when the old spelling was kept as
    an old spelling (a clash with another name or a secret one drops it, and nothing is
    rewritten), and never for an entry only the DM sees or only some people know: what it says
    would reach the whole server. The DM is told the counts. A failure here never undoes the fix;
    the DM is told what to do."""
    new = written.value.name
    bot = _bot(interaction)
    entity = written.value
    if (
        written.batch is None
        or not renamed_key(old, new)
        or entity.audience_limited
        or entity.type in DM_ONLY_TYPES
    ):
        return PastNote()
    notes: list[str] = []
    try:
        kept, longer, hidden = await _names_around(memory, campaign, entity_id, old)
        if not kept:
            return PastNote()
    except Exception:
        log.exception("Fix spelling: couldn't read the names")
        return PastNote("The past transcripts were not changed. Press Undo, then fix it again.")
    remaining = 0
    if bot.transcripts is not None:
        try:
            now = int(time.time())
            done = await bot.transcripts.respell_names(
                campaign.guild_id,
                campaign.id,
                written.batch,
                old,
                new,
                longer,
                hidden,
                now=now,
                forget_before=now - int(memory.keep_days * 86400),
            )
            log.info("Fix spelling: %d lines of %d past sessions", done.lines, done.sessions)
            remaining = done.remaining
            if done.lines:
                notes.append(done_note(_md(old), _md(new), done.lines, done.sessions))
            if remaining:
                notes.append(left_note(remaining))
        except Exception:
            log.exception("Fix spelling: couldn't carry it into the past transcripts")
            notes.append(
                "The past transcripts were not changed. Press Undo, then fix the spelling again."
            )
    if bot.recaps is not None:
        try:
            done_recaps = await bot.recaps.respell(
                campaign.guild_id, campaign.id, old, new, longer, hidden
            )
            log.info(
                "Fix spelling: %d waiting and %d posted recaps",
                done_recaps.waiting,
                done_recaps.posted,
            )  # counts only
            if done_recaps.waiting or done_recaps.posted or done_recaps.skipped:
                notes.append(
                    recaps_note(_md(old), _md(new), done_recaps, after_transcripts=bool(notes))
                )
        except Exception:
            log.exception("Fix spelling: couldn't carry it into the recaps")
            notes.append("The recaps were not changed. Press Undo, then fix the spelling again.")
    return PastNote(" ".join(notes), remaining)


class FixSpellingForm(discord.ui.Modal, title="Fix spelling"):
    name: discord.ui.TextInput[FixSpellingForm] = discord.ui.TextInput(
        label="Name", max_length=NAME_LIMIT
    )
    said_as: discord.ui.TextInput[FixSpellingForm] = discord.ui.TextInput(
        label=SAID_AS_LABEL, placeholder=SAID_AS_HELP, max_length=SAID_AS_MAX, required=False
    )

    def __init__(
        self,
        campaign_id: str,
        entity_id: str,
        current: str,
        *,
        spoken: bool = False,
        said_as: str | None = None,
    ) -> None:
        super().__init__(timeout=VIEW_TIMEOUT_S)
        self.campaign_id = campaign_id
        self.entity_id = entity_id
        self.name.default = current[:NAME_LIMIT]
        self.spoken = spoken
        self.before = said_as
        if spoken:
            self.said_as.default = (said_as or "")[:SAID_AS_MAX]
        else:
            self.remove_item(self.said_as)  # not part of this campaign's plan: no field

    async def on_error(  # type: ignore[override]  # a form's has no item (discord.py)
        self, interaction: discord.Interaction, error: Exception
    ) -> None:
        await _failed(interaction, error)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)  # saves, then reloads names
        found = await _current(interaction, self.campaign_id, self.entity_id)
        memory = _memory(interaction)
        if found is None or memory is None:
            return
        campaign, old = found
        new = " ".join(self.name.value.split())
        if name_key(new) != name_key(old):
            clash = await known_as(memory, campaign, new)
            if clash is not None and name_key(clash) != name_key(old):
                await _tell(interaction, already_known(new, clash))
                return
        try:
            written = await memory.rename_entity(
                campaign.guild_id, campaign.id, self.entity_id, new, source=DM
            )
        except MemoryRuleError as exc:
            await _tell(interaction, f"Couldn't change it. {exc}")
            return
        note = f"✏️ Now spelled **{_md(written.value.name)}**."
        past = await _respell_past(interaction, memory, campaign, self.entity_id, old, written)
        if past.text:
            note += " " + past.text
        if self.spoken:
            spelling = " ".join(self.said_as.value.split()) or None
            renamed = name_key(new) != name_key(old)
            if renamed and spelling is None and self.before:
                note += (
                    " The narration now says it as written; the old spelling was for the old name."
                )
            if spelling != (None if renamed else self.before):
                try:
                    await memory.set_said_as(
                        campaign.guild_id, campaign.id, self.entity_id, spelling, source=DM
                    )
                    note += (
                        f" The narration will say it as **{_md(spelling)}**."
                        if spelling
                        else " The narration will say it as written."
                    )
                except MemoryRuleError as exc:
                    note += (
                        f" The name is saved, but how recaps say it wasn't. {exc}"
                        " Open Fix spelling to try again."
                    )
        changed(interaction, campaign)
        await show_card(
            interaction,
            campaign.id,
            self.entity_id,
            replace=True,
            note=note,
            undo_spelling=written.batch if renamed_key(old, written.value.name) else None,
            finish_past=written.batch if past.remaining else None,
        )


SAY_BUTTON = "🔊 How to say it"
SAY_NOT_ON = "Spoken recaps aren't on for this campaign, so there's nothing to set."


class SayForm(discord.ui.Modal, title="How to say it"):
    """Sets or clears how the recap narrator says one name (#2171): the same field as the
    website's Names page and the one in ✏️ Fix spelling, without touching the name."""

    said_as: discord.ui.TextInput[SayForm] = discord.ui.TextInput(
        label=SAID_AS_LABEL, placeholder=SAID_AS_HELP, max_length=SAID_AS_MAX, required=False
    )

    def __init__(self, campaign_id: str, entity_id: str, name: str, said_as: str | None) -> None:
        super().__init__(title=logic.shorten(f"How to say {name}", 45), timeout=VIEW_TIMEOUT_S)
        self.campaign_id = campaign_id
        self.entity_id = entity_id
        self.said_as.default = (said_as or "")[:SAID_AS_MAX]

    async def on_error(  # type: ignore[override]  # a form's has no item (discord.py)
        self, interaction: discord.Interaction, error: Exception
    ) -> None:
        await _failed(interaction, error)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        found = await _current(interaction, self.campaign_id, self.entity_id)
        memory = _memory(interaction)
        if found is None or memory is None:
            return
        campaign, _ = found
        if not sees_secrets(campaign, interaction.user.id):  # after the wait, as on a press
            await _tell(interaction, "Only a DM of this campaign can do that.")
            return
        if not await _spoken_recaps(interaction, campaign):  # the plan may have lapsed meanwhile
            await _tell(interaction, SAY_NOT_ON)
            return
        spelling = " ".join(self.said_as.value.split()) or None
        try:
            await memory.set_said_as(
                campaign.guild_id, campaign.id, self.entity_id, spelling, source=DM
            )
        except MemoryRuleError as exc:
            await _tell(interaction, f"Couldn't change it. {exc}")
            return
        note = (
            f"🔊 The narrator will say it as **{_md(spelling)}**."
            if spelling
            else "🔊 The narrator will say it as written."
        )
        changed(interaction, campaign)
        await show_card(interaction, campaign.id, self.entity_id, replace=True, note=note)


class AnotherNameForm(discord.ui.Modal, title="Add another name"):
    other: discord.ui.TextInput[AnotherNameForm] = discord.ui.TextInput(
        label="Other names or nicknames",
        placeholder="Separate with commas. For example: Bell, the old knight",
        required=False,
        max_length=400,
    )
    secret: discord.ui.TextInput[AnotherNameForm] = discord.ui.TextInput(
        label="Disguises or secret identities (optional)",
        placeholder="Example: the hooded stranger. Players never learn who it really is.",
        required=False,
        max_length=400,
    )

    def __init__(self, campaign_id: str, entity_id: str, name: str, *, secrets: bool) -> None:
        super().__init__(title=logic.shorten(f"Other names for {name}", 45), timeout=VIEW_TIMEOUT_S)
        self.campaign_id = campaign_id
        self.entity_id = entity_id
        if not secrets:  # only the campaign's DMs deal in secret names
            self.remove_item(self.secret)
            self.other.required = True

    async def on_error(  # type: ignore[override]  # a form's has no item (discord.py)
        self, interaction: discord.Interaction, error: Exception
    ) -> None:
        await _failed(interaction, error)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)  # saves, then reloads names
        found = await _current(interaction, self.campaign_id, self.entity_id)
        memory = _memory(interaction)
        if found is None or memory is None:
            return
        campaign, name = found
        secret_ok = sees_secrets(campaign, interaction.user.id)
        plain = split_names(self.other.value)
        hidden = split_names(self.secret.value) if secret_ok else []
        if not plain and not hidden:
            await _tell(interaction, "Nothing added. Type at least one name.")
            return
        gid, cid = campaign.guild_id, campaign.id
        added: list[str] = []
        kept: list[str] = []
        problems: list[str] = []
        for text, secret in [*((t, False) for t in plain), *((t, True) for t in hidden)]:
            clash = None if secret else await known_as(memory, campaign, text)
            if clash is not None and name_key(clash) != name_key(name):
                problems.append(f"**{_md(text)}** is already {_md(clash)}'s")
                continue
            try:
                await memory.add_alias(
                    gid,
                    cid,
                    self.entity_id,
                    text,
                    kind="title" if secret else "nickname",
                    source=DM,
                    status=CONFIRMED,
                    secret=secret,
                )
            except MemoryRuleError as exc:
                problems.append(f"**{_md(text)}**: {exc}")
                continue
            (kept if secret else added).append(f"**{_md(text)}**")
        changed(interaction, campaign)
        lines = []
        if added:
            lines.append(f"✅ DMbot now also listens for {', '.join(added)} as **{_md(name)}**.")
        if kept:
            lines.append(
                f"🤫 Kept secret: {', '.join(kept)}. Hidden from players. DMbot never puts it "
                "in the transcript."
            )
        if problems:
            lines.append(f"Couldn't add {'; '.join(problems)}.")
        await show_card(interaction, cid, self.entity_id, replace=True, note="\n".join(lines))


class KindChange(_Menu):
    def __init__(self, campaign_id: str, entity_id: str, name: str) -> None:
        super().__init__()
        self.campaign_id = campaign_id
        self.entity_id = entity_id
        self.name = name
        self.pick = _Select(
            self._picked,
            placeholder="Pick one…",
            options=[discord.SelectOption(label=label, value=k) for k, label in KINDS.items()],
        )
        self.add_item(self.pick)
        self.add_item(_Button(self._back, label="Back", style=discord.ButtonStyle.secondary))

    async def _picked(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        campaign = await _campaign_for(interaction, self.campaign_id)
        memory = _memory(interaction)
        if campaign is None or memory is None:
            return
        kind = self.pick.values[0]
        if kind == PC:
            self.clear_items()
            self.add_item(_UserSelect(self._player_picked))
            self.add_item(_Button(self._back, label="Back", style=discord.ButtonStyle.secondary))
            await _replace(interaction, f"**Who plays {_md(self.name)}?**", self)
            return
        try:
            await memory.set_entity_type(
                campaign.guild_id, campaign.id, self.entity_id, kind, source=DM
            )
        except MemoryRuleError:
            await _tell(interaction, GONE)
            return
        changed(interaction, campaign)
        self.stop()
        await show_card(interaction, self.campaign_id, self.entity_id, replace=True)

    async def _player_picked(
        self, interaction: discord.Interaction, player: discord.User | discord.Member
    ) -> None:
        if player.bot:
            await _tell(interaction, "Pick a person, not a bot.")
            return
        await _answer_first(interaction, in_place=True)
        campaign = await _campaign_for(interaction, self.campaign_id)
        memory = _memory(interaction)
        if campaign is None or memory is None:
            return
        try:
            await memory.confirm_entity(
                campaign.guild_id, campaign.id, self.entity_id, PC, source=DM, played_by=player.id
            )
        except MemoryRuleError:
            await _tell(interaction, GONE)
            return
        changed(interaction, campaign)
        self.stop()
        await show_card(interaction, self.campaign_id, self.entity_id, replace=True)

    async def _back(self, interaction: discord.Interaction) -> None:
        self.stop()
        await show_card(interaction, self.campaign_id, self.entity_id, replace=True)


class ConfirmRemove(_Menu):
    def __init__(self, campaign_id: str, entity_id: str) -> None:
        super().__init__()
        self.campaign_id = campaign_id
        self.entity_id = entity_id
        self.add_item(_Button(self._forget, label="Forget it", style=discord.ButtonStyle.danger))
        self.add_item(_Button(self._keep, label="Keep it", style=discord.ButtonStyle.secondary))

    async def _forget(self, interaction: discord.Interaction) -> None:
        found = await _current(interaction, self.campaign_id, self.entity_id)  # still there?
        memory = _memory(interaction)
        if found is None or memory is None:
            return
        campaign, _ = found
        try:
            written = await memory.set_entity_status(
                campaign.guild_id, campaign.id, self.entity_id, REJECTED, source=DM
            )
        except MemoryRuleError:
            await _tell(interaction, GONE)
            return
        changed(interaction, campaign)
        self.stop()
        view = discord.ui.View(timeout=None)
        if written.batch is not None:
            view.add_item(UndoButton(campaign.id, self.entity_id, written.batch))
        await interaction.response.edit_message(
            content=f"Forgot **{_md(written.value.name)}**. Wrong? Press **Undo** within "
            f"{days(memory.keep_days)} to bring it back.",
            view=view,
        )

    async def _keep(self, interaction: discord.Interaction) -> None:
        self.stop()
        await show_card(interaction, self.campaign_id, self.entity_id, replace=True)


class UndoButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=r"dmbot:undo:(?P<campaign>[0-9a-f]{32}):(?P<entity>[0-9a-f]{32}):(?P<batch>[0-9]{1,18})",
):
    """Undo forgetting a name, or a 🔗 Same as… (`entity` is the name that went); keeps
    working after a restart (all it needs is in its ID, under Discord's 100 characters).
    Only acts while that name is still forgotten or joined to another."""

    def __init__(self, campaign_id: str, entity_id: str, batch: int) -> None:
        super().__init__(
            discord.ui.Button(
                label="Undo",
                style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:undo:{campaign_id}:{entity_id}:{batch}",
            )
        )
        self.campaign_id = campaign_id
        self.entity_id = entity_id
        self.batch = batch

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> UndoButton:
        return cls(match["campaign"], match["entity"], int(match["batch"]))

    async def callback(self, interaction: discord.Interaction) -> Any:
        await _answer_first(interaction, in_place=True)  # (#1803) two reads come before "Undoing…"
        campaign = await _campaign_for(interaction, self.campaign_id)
        memory = _memory(interaction)
        if campaign is None or memory is None:
            return
        entity = await memory.entity(campaign.guild_id, campaign.id, self.entity_id)
        if entity is None or entity.status not in (REJECTED, MERGED):
            await _tell(interaction, "Nothing to undo: that was already undone or changed.")
            return
        # Answer Discord at once (#351): a big undo can take longer than its 3 seconds.
        # Saying so in place also takes the button away, so it isn't pressed twice.
        await _replace(interaction, f"↩️ Undoing… bringing back **{_md(entity.name)}**.", None)
        try:
            await memory.undo(campaign.guild_id, campaign.id, self.batch)
        except MemoryRuleError as exc:
            now = await memory.entity(campaign.guild_id, campaign.id, self.entity_id)
            if now is not None and now.status not in (REJECTED, MERGED):  # a second press
                await _replace(interaction, f"↩️ **{_md(entity.name)}** is already back.", None)
                return
            reason = (
                str(exc)  # too old (#164)
                if isinstance(exc, TooLateToUndo)
                else f"Couldn't undo: **{_md(entity.name)}** was changed again since."
            )
            await _replace(
                interaction,
                f"{reason} "
                + (
                    "Add it again with ➕ Add a name on the names panel (its other names and "
                    "connections need adding again too)."
                    if entity.status == REJECTED
                    else "To split them, open the card's Edit other names, press ✖ Not this "
                    "name, add it again with ➕ Add a name, then fix any connections."
                ),
                None,
            )
            return
        except Exception:
            log.exception("Undo of batch %s failed", self.batch)
            await _replace(interaction, NOTHING_CHANGED, None)
            return
        changed(interaction, campaign)
        note = f"↩️ **{_md(entity.name)}** is back, with its other names and connections."
        await show_card(interaction, campaign.id, self.entity_id, replace=True, note=note)


UNDO_CONNECTION_SECONDS = 600  # a wrong pick is noticed at once; later, add it again


class UndoConnectionButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=r"dmbot:unconn:(?P<campaign>[0-9a-f]{32}):(?P<entity>[0-9a-f]{32}):(?P<batch>[0-9]{1,18})",
):
    """Undo 🧭 Remove a connection… for ten minutes (#1566). Keeps working after a restart:
    the batch of the removal is in its ID, and the connection comes back exactly as it was."""

    def __init__(self, campaign_id: str, entity_id: str, batch: int) -> None:
        super().__init__(
            discord.ui.Button(
                label="Undo",
                style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:unconn:{campaign_id}:{entity_id}:{batch}",
                row=3,
            )
        )
        self.campaign_id = campaign_id
        self.entity_id = entity_id
        self.batch = batch

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> UndoConnectionButton:
        return cls(match["campaign"], match["entity"], int(match["batch"]))

    async def callback(self, interaction: discord.Interaction) -> Any:
        # Checked on every press, like the removal itself; a secret connection also needs a DM.
        await _answer_first(interaction, in_place=True)
        found = await _current(interaction, self.campaign_id, self.entity_id)
        memory = _memory(interaction)
        if found is None or memory is None:
            return
        campaign = found[0]
        try:
            written = await memory.undo_connection(
                campaign.guild_id,
                campaign.id,
                self.batch,
                within=UNDO_CONNECTION_SECONDS,
                secrets=sees_secrets(campaign, interaction.user.id),
            )
        except TooLateToUndo:
            await _tell(
                interaction, "Too late to undo (10 minutes). Press 🧭 Connect to… to add it again."
            )
            return
        except MemoryRuleError:
            await _tell(interaction, f"Not undone. {NOTHING_MORE}")  # changed, or already undone
            return
        changed(interaction, campaign)
        names = await _names(interaction, campaign)
        said = sentence(names, written.value) if names is not None else None
        note = f"Put back: {_md(said or 'that connection')}."
        await show_card(interaction, campaign.id, self.entity_id, replace=True, note=note)


class UndoSpellingButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=r"dmbot:unspell:(?P<campaign>[0-9a-f]{32}):(?P<entity>[0-9a-f]{32}):(?P<batch>[0-9]{1,18})",
):
    """Undo ✏️ Fix spelling (#2170): the name goes back to how it was spelled and the past
    transcripts get the old spelling back where they were changed. Keeps working after a
    restart: the batch of the fix is in its ID. Only a DM of the campaign, checked on every
    press."""

    def __init__(self, campaign_id: str, entity_id: str, batch: int) -> None:
        super().__init__(
            discord.ui.Button(
                label="Undo",
                style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:unspell:{campaign_id}:{entity_id}:{batch}",
                row=4,
            )
        )
        self.campaign_id = campaign_id
        self.entity_id = entity_id
        self.batch = batch

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> UndoSpellingButton:
        return cls(match["campaign"], match["entity"], int(match["batch"]))

    async def callback(self, interaction: discord.Interaction) -> Any:
        await _answer_first(interaction, in_place=True)
        found = await _current(interaction, self.campaign_id, self.entity_id)
        memory = _memory(interaction)
        if found is None or memory is None:
            return
        campaign = found[0]
        if interaction.user.id not in campaign.dm_user_ids:  # on every press
            await _tell(interaction, "Only the DM can undo that.")
            return
        store = _bot(interaction).transcripts
        spellings = (
            await store.respell_spellings(campaign.guild_id, campaign.id, self.batch)
            if store is not None
            else None
        )
        refused = False
        try:
            await memory.undo(campaign.guild_id, campaign.id, self.batch)
        except TooLateToUndo as exc:
            await _tell(interaction, str(exc))
            return
        except MemoryRuleError:
            refused = True  # undone already, or changed since: the lines may still be waiting
        note = "↩️ Spelling put back."
        lines = sessions = 0
        if store is not None:
            try:
                lines, sessions = await store.undo_respell(
                    campaign.guild_id, campaign.id, self.batch
                )
            except Exception:
                log.exception("Undo of a fixed spelling: couldn't reach the past transcripts")
                note += " The past transcripts could not be put back; press Undo again."
        if refused and not lines:
            await _tell(interaction, f"Not undone. {NOTHING_MORE}")  # changed, or already undone
            return
        if refused:
            back = await _current(interaction, self.campaign_id, self.entity_id)
            if (
                spellings is not None
                and back is not None
                and name_key(back[1]) == name_key(spellings[0])
            ):
                pass  # the name was already put back (an earlier press): only the lines were left
            else:  # the name was changed again since: it stays; the lines still go back
                note = "The name was changed again since, so it stays as it is."
        if lines and spellings is not None:
            note += " " + undone_note(_md(spellings[0]), lines, sessions)
        changed(interaction, campaign)
        await show_card(interaction, campaign.id, self.entity_id, replace=True, note=note)


class FinishPastButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=r"dmbot:finish:(?P<campaign>[0-9a-f]{32}):(?P<entity>[0-9a-f]{32}):(?P<batch>[0-9]{1,9})",
):
    """[Finish the past transcripts] (#2170): a fix that reached more lines than one run does
    carries on with the rest, in session order. Each run has its own Undo. Keeps working after a
    restart: the fix is in its ID, and its two spellings are read from what it already wrote.
    Only a DM of the campaign, checked on every press."""

    def __init__(self, campaign_id: str, entity_id: str, batch: int) -> None:
        super().__init__(
            discord.ui.Button(
                label="Finish the past transcripts",
                style=discord.ButtonStyle.primary,
                custom_id=f"dmbot:finish:{campaign_id}:{entity_id}:{batch}",
                row=4,
            )
        )
        self.campaign_id = campaign_id
        self.entity_id = entity_id
        self.batch = batch

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> FinishPastButton:
        return cls(match["campaign"], match["entity"], int(match["batch"]))

    async def callback(self, interaction: discord.Interaction) -> Any:
        await _answer_first(interaction, in_place=True)
        found = await _current(interaction, self.campaign_id, self.entity_id)
        memory = _memory(interaction)
        store = _bot(interaction).transcripts
        if found is None or memory is None or store is None:
            return
        campaign = found[0]
        if interaction.user.id not in campaign.dm_user_ids:  # on every press
            await _tell(interaction, "Only the DM can do that.")
            return
        spellings = await store.respell_spellings(campaign.guild_id, campaign.id, self.batch)
        if spellings is None:
            await _tell(interaction, "Nothing more to do: it was undone, or it is all done.")
            return
        old, new = spellings
        try:
            entity = await memory.entity(campaign.guild_id, campaign.id, self.entity_id)
            kept, longer, hidden = await _names_around(memory, campaign, self.entity_id, old)
        except Exception:
            log.exception("Finish the past transcripts: couldn't read the names")
            await _tell(interaction, "That didn't work. Press the button again in a moment.")
            return
        if (
            entity is None
            or name_key(entity.name) != name_key(new)  # the name was changed again since
            or not kept
            or entity.audience_limited
            or entity.type in DM_ONLY_TYPES
        ):
            await _tell(interaction, "That fix is out of date, so nothing was changed.")
            return
        try:
            now = int(time.time())
            done = await store.respell_names(
                campaign.guild_id,
                campaign.id,
                self.batch,
                old,
                new,
                longer,
                hidden,
                now=now,
                forget_before=now - int(memory.keep_days * 86400),
            )
        except Exception:
            log.exception("Finish the past transcripts failed")
            await _tell(interaction, "That didn't work. Press the button again in a moment.")
            return
        note = done_note(_md(old), _md(new), done.lines, done.sessions) if done.lines else ""
        if not done.lines:
            note = "Nothing more to change."
        elif done.remaining:
            note += " " + left_note(done.remaining)
        view = discord.ui.View(timeout=None)
        if done.lines:
            view.add_item(UndoRunButton(self.campaign_id, self.entity_id, self.batch, done.run))
        if done.remaining:
            view.add_item(FinishPastButton(self.campaign_id, self.entity_id, self.batch))
        await interaction.edit_original_response(
            content=f"✏️ {note}", view=view, allowed_mentions=NO_PINGS
        )


class UndoRunButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=r"dmbot:unrun:(?P<campaign>[0-9a-f]{32}):(?P<entity>[0-9a-f]{32}):(?P<batch>[0-9]{1,9}):(?P<run>[0-9]{1,3})",
):
    """Undo one run of [Finish the past transcripts]: only the lines that run wrote. The name stays
    (its own Undo is on the card). Only a DM of the campaign, checked on every press."""

    def __init__(self, campaign_id: str, entity_id: str, batch: int, run: int) -> None:
        super().__init__(
            discord.ui.Button(
                label="Undo this part",
                style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:unrun:{campaign_id}:{entity_id}:{batch}:{run}",
            )
        )
        self.campaign_id = campaign_id
        self.entity_id = entity_id
        self.batch = batch
        self.run = run

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> UndoRunButton:
        return cls(match["campaign"], match["entity"], int(match["batch"]), int(match["run"]))

    async def callback(self, interaction: discord.Interaction) -> Any:
        await _answer_first(interaction, in_place=True)
        found = await _current(interaction, self.campaign_id, self.entity_id)
        store = _bot(interaction).transcripts
        if found is None or store is None:
            return
        campaign = found[0]
        if interaction.user.id not in campaign.dm_user_ids:  # on every press
            await _tell(interaction, "Only the DM can undo that.")
            return
        spellings = await store.respell_spellings(campaign.guild_id, campaign.id, self.batch)
        try:
            lines, sessions = await store.undo_respell(
                campaign.guild_id, campaign.id, self.batch, self.run
            )
        except Exception:
            log.exception("Undo of one run of a fixed spelling failed")
            await _tell(interaction, "That didn't work. Press the button again in a moment.")
            return
        old = spellings[0] if spellings is not None else "the old spelling"
        text = undone_note(_md(old), lines, sessions) if lines else "Nothing was put back."
        view = discord.ui.View(timeout=None)
        if lines:  # the part is open again: it can be done again
            view.add_item(FinishPastButton(self.campaign_id, self.entity_id, self.batch))
        await interaction.edit_original_response(
            content=f"↩️ {text}", view=view, allowed_mentions=NO_PINGS
        )


# ---- other names: ⭐ main name, 🤫 secret, ✖ not this name -------------------------------


async def _other_names(
    interaction: discord.Interaction, campaign: Campaign, entity_id: str, name: str
) -> list[Alias]:
    """The entry's confirmed other names this person may see (secret ones only for the
    campaign's DMs), not its main name."""
    memory = _memory(interaction)
    assert memory is not None
    own = name_key(name)
    return [
        a
        for a in await memory.aliases(
            campaign.guild_id,
            campaign.id,
            entity_id=entity_id,
            include_secret=sees_secrets(campaign, interaction.user.id),
        )
        if a.status == CONFIRMED and a.key != own
    ]


class OtherNames(_Menu):
    def __init__(self, campaign_id: str, entity_id: str, listed: list[Alias]) -> None:
        super().__init__()
        self.campaign_id = campaign_id
        self.entity_id = entity_id
        self.pick = _Select(
            self._picked,
            placeholder="Pick a name to change…",
            options=[
                discord.SelectOption(
                    label=logic.shorten(a.text, logic.OPTION_LABEL_MAX),
                    value=a.id,
                    description="🤫 Secret: hidden from players" if a.secret else "Another name",
                )
                for a in listed[: logic.SELECT_OPTIONS_MAX]
            ],
        )
        self.add_item(self.pick)
        self.add_item(_Button(self._back, label="Back", style=discord.ButtonStyle.secondary))

    async def _picked(self, interaction: discord.Interaction) -> None:
        found = await _one_name(interaction, self.campaign_id, self.entity_id, self.pick.values[0])
        if found is None:
            return
        campaign, alias, name = found
        self.stop()
        secrets = sees_secrets(campaign, interaction.user.id)
        hidden = (
            " 🤫 Secret: hidden from players, so it can't be the main name." if alias.secret else ""
        )
        await _replace(
            interaction,
            f"**{_md(alias.text)}** is another name for **{_md(name)}**.{hidden}",
            OneName(self.campaign_id, self.entity_id, alias, secrets=secrets),
        )

    async def _back(self, interaction: discord.Interaction) -> None:
        self.stop()
        await show_card(interaction, self.campaign_id, self.entity_id, replace=True)


async def _one_name(
    interaction: discord.Interaction, campaign_id: str, entity_id: str, alias_id: str
) -> tuple[Campaign, Alias, str] | None:
    """The campaign, the other name and the entry's name, checked again (tells them if
    it's gone). A secret name is never found for someone who may not see it."""
    found = await _current(interaction, campaign_id, entity_id)
    if found is None:
        return None
    campaign, name = found
    for alias in await _other_names(interaction, campaign, entity_id, name):
        if alias.id == alias_id:
            return campaign, alias, name
    await _tell(interaction, "That name isn't there any more.")
    return None


class OneName(_Menu):
    def __init__(self, campaign_id: str, entity_id: str, alias: Alias, *, secrets: bool) -> None:
        super().__init__()
        self.campaign_id = campaign_id
        self.entity_id = entity_id
        self.alias_id = alias.id
        grey = discord.ButtonStyle.secondary
        self.add_item(
            _Button(
                self._main,
                label="⭐ Make it the main name",
                style=discord.ButtonStyle.primary,
                disabled=alias.secret,  # the main name is the one everyone sees
            )
        )
        if secrets:  # only the campaign's DMs deal in secret names
            label = "👁️ Stop keeping it secret" if alias.secret else "🤫 Keep it secret"
            self.add_item(_Button(self._secret, label=label, style=grey))
        self.add_item(_Button(self._not_this, label="✖ Not this name", style=grey))
        self.add_item(_Button(self._back, label="Back", style=grey))

    async def _main(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        found = await _one_name(interaction, self.campaign_id, self.entity_id, self.alias_id)
        memory = _memory(interaction)
        if found is None or memory is None:
            return
        campaign, alias, old = found
        try:
            await memory.set_main_name(
                campaign.guild_id, campaign.id, self.entity_id, alias.id, source=DM
            )
        except MemoryRuleError as exc:
            await _tell(interaction, f"Couldn't change it. {exc}")
            return
        changed(interaction, campaign)
        self.stop()
        note = (
            f"⭐ **{_md(alias.text)}** is the main name now. **{_md(old)}** is still one of "
            "its other names."
        )
        await show_card(interaction, campaign.id, self.entity_id, replace=True, note=note)

    async def _secret(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        found = await _one_name(interaction, self.campaign_id, self.entity_id, self.alias_id)
        memory = _memory(interaction)
        if found is None or memory is None:
            return
        campaign, alias, _ = found
        if not sees_secrets(campaign, interaction.user.id):
            await _tell(interaction, "Only the campaign's DMs can change secret names.")
            return
        try:
            await memory.update_alias(
                campaign.guild_id, campaign.id, alias.id, secret=not alias.secret, source=DM
            )
        except MemoryRuleError:
            await _tell(interaction, "That name isn't there any more.")
            return
        changed(interaction, campaign)
        self.stop()
        note = (
            f"👁️ **{_md(alias.text)}** isn't secret any more. Players can see it."
            if alias.secret
            else f"🤫 **{_md(alias.text)}** is secret now: hidden from players from now on, "
            "and DMbot leaves it out of new transcripts. Past transcripts don't change."
        )
        await show_card(interaction, campaign.id, self.entity_id, replace=True, note=note)

    async def _not_this(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        found = await _one_name(interaction, self.campaign_id, self.entity_id, self.alias_id)
        memory = _memory(interaction)
        if found is None or memory is None:
            return
        campaign, alias, name = found
        try:
            await memory.update_alias(
                campaign.guild_id, campaign.id, alias.id, status=REJECTED, source=DM
            )
        except MemoryRuleError:
            await _tell(interaction, "That name isn't there any more.")
            return
        changed(interaction, campaign)
        self.stop()
        note = (
            f"✖ DMbot stops listening for **{_md(alias.text)}** as **{_md(name)}**. Wrong? "
            f"Press {ALSO_CALLED} to add it back."
        )
        await show_card(interaction, campaign.id, self.entity_id, replace=True, note=note)

    async def _back(self, interaction: discord.Interaction) -> None:
        self.stop()
        await show_card(interaction, self.campaign_id, self.entity_id, replace=True)


# ---- 🔗 Same as… and 🧭 Connect to…: pick the other name --------------------------------

SAME = "same"


class PickForm(discord.ui.Modal, title="Find the other name"):
    """Type the other name, for Same as… (`how` is SAME) or Connect to… (`how` is the
    connection, maybe ending in BACK)."""

    typed: discord.ui.TextInput[PickForm] = discord.ui.TextInput(
        label="The other name",
        placeholder="Type part of a name, a nickname, or how it sounds",
        max_length=NAME_LIMIT,
    )

    def __init__(
        self, campaign_id: str, entity_id: str, how: str, title: str, *, secret: bool = False
    ) -> None:
        super().__init__(title=logic.shorten(title, 45), timeout=VIEW_TIMEOUT_S)
        self.campaign_id = campaign_id
        self.entity_id = entity_id
        self.how = how
        self.secret = secret  # 🤫 Keep it secret was on (the DM's toggle on Connect to…)

    async def on_error(  # type: ignore[override]  # a form's has no item (discord.py)
        self, interaction: discord.Interaction, error: Exception
    ) -> None:
        await _failed(interaction, error)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await show_picks(
            interaction, self.campaign_id, self.entity_id, self.how, self.typed.value,
            secret=self.secret,
        )  # fmt: skip


def _asked(name: str, how: str) -> str:
    """The question being answered: "Belleros is the same as…", "Ulfgar is a member of…"."""
    if how == SAME:
        return f"{name} is the same as…"
    predicate = how.removesuffix(BACK)
    words = CONNECTION_BACK.get(predicate) if how.endswith(BACK) else None
    return f"{name} {words or connection_words(predicate)}…"


async def show_picks(
    interaction: discord.Interaction,
    campaign_id: str,
    entity_id: str,
    how: str,
    typed: str,
    *,
    secret: bool = False,
) -> None:
    await _answer_first(interaction, in_place=True)
    found = await _current(interaction, campaign_id, entity_id)
    if found is None:
        return
    campaign, name = found
    names = await _names(interaction, campaign)
    if names is None:
        return
    matches = [
        m
        for m in find(names, typed, secrets=sees_secrets(campaign, interaction.user.id))
        if m.entity_id != entity_id
    ]
    options = [discord.SelectOption(label=_label(names, m), value=m.entity_id) for m in matches]
    asked = f"**{_md(_asked(name, how))}**" + (" 🤫 Secret." if secret else "")
    if not matches:
        text = (
            f"{asked}\nNo other name like **{_md(typed)}** yet. Search again, or add it "
            "with ➕ Add a name on the names panel and come back."
        )
    else:
        text = f"{asked} Pick one."
    # A name found through a secret name makes the connection secret by itself: saying it out
    # loud on the other name's card would give the secret name away.
    hidden = frozenset(m.entity_id for m in matches if m.secret)
    await _replace(
        interaction,
        text,
        PickOther(campaign_id, entity_id, how, name, options, secret=secret, hidden=hidden),
    )


class PickOther(_Menu):
    def __init__(
        self,
        campaign_id: str,
        entity_id: str,
        how: str,
        name: str,
        options: list[discord.SelectOption],
        *,
        secret: bool = False,
        hidden: frozenset[str] = frozenset(),
    ) -> None:
        super().__init__()
        self.campaign_id = campaign_id
        self.entity_id = entity_id
        self.how = how
        self.name = name
        self.secret = secret
        self.hidden = hidden  # entries that were found through a secret name
        if options:
            self.pick = _Select(self._picked, placeholder="Pick the other name…", options=options)
            self.add_item(self.pick)
        self.add_item(
            _Button(self._again, label="🔍 Search again", style=discord.ButtonStyle.secondary)
        )
        self.add_item(_Button(self._back, label="Back", style=discord.ButtonStyle.secondary))

    async def _picked(self, interaction: discord.Interaction) -> None:
        other_id = self.pick.values[0]
        await _answer_first(interaction, in_place=True)
        if self.how == SAME:
            found = await _current(interaction, self.campaign_id, self.entity_id)
            other = await _current(interaction, self.campaign_id, other_id) if found else None
            if found is None or other is None:
                return
            found_names = await _names(interaction, found[0])
            if found_names is None:
                return
            self.stop()
            name, other_name = found[1], other[1]
            await _replace(
                interaction,
                f"Make **{_md(name)}** and **{_md(other_name)}** one? Their other names and "
                "connections go together. Which name should it keep?\n"
                "Is one of them a disguise? Press **No**, then press "
                f"{ALSO_CALLED} on the real one to add it as a secret name.",
                SameConfirm(
                    self.campaign_id,
                    self.entity_id,
                    other_id,
                    _who(found_names, self.entity_id),
                    _who(found_names, other_id),
                ),
            )
            return
        self.stop()
        await connect(
            interaction, self.campaign_id, self.entity_id, self.how, other_id,
            secret=self.secret or other_id in self.hidden, forced=other_id in self.hidden,
        )  # fmt: skip

    async def _again(self, interaction: discord.Interaction) -> None:
        if await _current(interaction, self.campaign_id, self.entity_id):
            self.origin = None  # the form's answer replaces this message; cancel keeps it
            await interaction.response.send_modal(
                PickForm(
                    self.campaign_id,
                    self.entity_id,
                    self.how,
                    _asked(self.name, self.how),
                    secret=self.secret,
                )
            )

    async def _back(self, interaction: discord.Interaction) -> None:
        self.stop()
        await show_card(interaction, self.campaign_id, self.entity_id, replace=True)


class SameConfirm(_Menu):
    def __init__(
        self, campaign_id: str, entity_id: str, other_id: str, name: str, other_name: str
    ) -> None:
        super().__init__()
        self.campaign_id = campaign_id
        self.entity_id = entity_id
        self.other_id = other_id
        blue = discord.ButtonStyle.primary
        for handler, label in ((self._keep_this, name), (self._keep_other, other_name)):
            self.add_item(
                _Button(handler, label=logic.shorten(f"Keep the name {label}", 80), style=blue)
            )
        self.add_item(
            _Button(self._back, label="No, they're different", style=discord.ButtonStyle.secondary)
        )

    async def _keep_this(self, interaction: discord.Interaction) -> None:
        await self._join(interaction, self.entity_id, self.other_id)

    async def _keep_other(self, interaction: discord.Interaction) -> None:
        await self._join(interaction, self.other_id, self.entity_id)

    async def _join(self, interaction: discord.Interaction, keep_id: str, gone_id: str) -> None:
        kept = await _current(interaction, self.campaign_id, keep_id)
        gone = await _current(interaction, self.campaign_id, gone_id) if kept else None
        memory = _memory(interaction)
        if kept is None or gone is None or memory is None:
            return
        campaign = kept[0]
        # Answer Discord at once (#351): a big merge can take longer than its 3 seconds.
        # Saying so in place also takes the buttons away, so nothing is pressed twice.
        self.stop()
        await _replace(interaction, f"🔗 Joining **{_md(gone[1])}** into **{_md(kept[1])}**…", None)
        try:
            written = await memory.merge(
                campaign.guild_id, campaign.id, keep_id, gone_id, source=DM, dm_said_same=True
            )
        except MemoryRuleError as exc:
            await _replace(interaction, f"Couldn't join them. {exc} Nothing was changed.", None)
            return
        except Exception:
            log.exception("Joining %s into %s failed", gone_id, keep_id)
            await _replace(interaction, NOTHING_CHANGED, None)
            return
        changed(interaction, campaign)
        note = f"🔗 Done: **{_md(gone[1])}** is now another name for **{_md(kept[1])}**."
        await show_card(interaction, campaign.id, keep_id, replace=True, note=note)
        if written.batch is not None:
            # Its own message, so Undo outlives the card's menu (and a restart).
            view = discord.ui.View(timeout=None)
            view.add_item(UndoButton(campaign.id, gone_id, written.batch))
            await interaction.followup.send(
                f"Wrong? Press **Undo** within {days(memory.keep_days)} to split "
                f"**{_md(gone[1])}** and **{_md(kept[1])}** again.",
                view=view,
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )

    async def _back(self, interaction: discord.Interaction) -> None:
        self.stop()
        await show_card(interaction, self.campaign_id, self.entity_id, replace=True)


class Connect(_Menu):
    def __init__(
        self,
        campaign_id: str,
        entity_id: str,
        name: str,
        removable: list[tuple[str, str]],
        *,
        secrets: bool = False,
    ) -> None:
        super().__init__()
        self.campaign_id = campaign_id
        self.entity_id = entity_id
        self.name = name
        self.secret = False
        self.how = _Select(
            self._how_picked,
            placeholder=logic.shorten(f"How is {name} connected?", 150),
            options=[
                discord.SelectOption(label=label, value=v, description=d)
                for v, label, d in connect_choices(name)
            ],
        )
        self.add_item(self.how)
        if removable:
            self.remove = _Select(
                self._remove_picked,
                placeholder="Remove a connection…",
                options=[
                    discord.SelectOption(label=logic.shorten(said, logic.OPTION_LABEL_MAX), value=r)
                    for r, said in removable[: logic.SELECT_OPTIONS_MAX]
                ],
                row=1,
            )
            self.add_item(self.remove)
        self.add_item(_Button(self._back, label="Back", style=discord.ButtonStyle.secondary, row=2))
        if secrets:  # only the campaign's DMs deal in secrets, as with secret names
            # After Back, so a thumb on the left edge doesn't flip a privacy setting.
            self.toggle = _Button(
                self._toggle_secret, label=SECRET_OFF, style=discord.ButtonStyle.secondary, row=2
            )
            self.add_item(self.toggle)

    async def _toggle_secret(self, interaction: discord.Interaction) -> None:
        campaign = await _campaign_for(interaction, self.campaign_id)
        if campaign is None:
            return
        if not sees_secrets(campaign, interaction.user.id):  # checked on every press
            await _tell(interaction, SECRET_DENIED_PRESS)
            return
        self.secret = not self.secret
        self.toggle.label = SECRET_ON if self.secret else SECRET_OFF
        self.toggle.style = (
            discord.ButtonStyle.success if self.secret else discord.ButtonStyle.secondary
        )
        await interaction.response.edit_message(view=self)

    async def _how_picked(self, interaction: discord.Interaction) -> None:
        how = self.how.values[0]
        if await _current(interaction, self.campaign_id, self.entity_id):
            self.origin = None  # the form's answer replaces this message; cancel keeps it
            await interaction.response.send_modal(
                PickForm(
                    self.campaign_id,
                    self.entity_id,
                    how,
                    _asked(self.name, how),
                    secret=self.secret,
                )
            )

    async def _remove_picked(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        found = await _current(interaction, self.campaign_id, self.entity_id)
        memory = _memory(interaction)
        if found is None or memory is None:
            return
        campaign = found[0]
        names = await _names(interaction, campaign)
        if names is None:
            return
        wanted = self.remove.values[0]
        mine = await memory.relations(
            campaign.guild_id,
            campaign.id,
            entity_id=self.entity_id,
            confirmed_only=True,
            include_secret=sees_secrets(campaign, interaction.user.id),
        )
        relation = next((r for r in mine if r.id == wanted), None)
        if relation is None:
            await _tell(interaction, "That connection isn't there any more.")
            return
        try:
            written = await memory.update_relation(
                campaign.guild_id, campaign.id, relation.id, status=REJECTED, source=DM
            )
        except MemoryRuleError:
            await _tell(interaction, "That connection isn't there any more.")
            return
        changed(interaction, campaign)
        self.stop()
        said = sentence(names, relation) or "that connection"
        note = f"Removed: {_md(said)}. Wrong? Press **Undo** within 10 minutes."
        await show_card(
            interaction,
            campaign.id,
            self.entity_id,
            replace=True,
            note=note,
            undo_connection=written.batch,
        )

    async def _back(self, interaction: discord.Interaction) -> None:
        self.stop()
        await show_card(interaction, self.campaign_id, self.entity_id, replace=True)


async def connect(
    interaction: discord.Interaction,
    campaign_id: str,
    entity_id: str,
    how: str,
    other_id: str,
    *,
    secret: bool = False,
    forced: bool = False,
) -> None:
    """Save the connection the DM picked (confirmed: the DM said it), then the card. `secret`:
    hidden from players (🤫); `forced`: it is secret because one end was found through a secret
    name. Only a campaign's DM can keep one secret, checked again here."""
    await _answer_first(interaction, in_place=True)
    found = await _current(interaction, campaign_id, entity_id)
    other = await _current(interaction, campaign_id, other_id) if found else None
    memory = _memory(interaction)
    if found is None or other is None or memory is None:
        return
    campaign = found[0]
    predicate = how.removesuffix(BACK)
    if predicate not in CONNECTION_WORDS:
        await _tell(interaction, "Pick how they're connected again.")
        return
    denied = secret and not sees_secrets(campaign, interaction.user.id)
    if denied and forced:
        # Found through a secret name, and no longer a DM: saving it in the open would point at
        # the secret name, so nothing is saved.
        await _tell(interaction, SECRET_DENIED_PRESS)
        return
    if denied:
        secret = False
    subject, obj = (other, found) if how.endswith(BACK) else (found, other)
    subject_id, object_id = (other_id, entity_id) if how.endswith(BACK) else (entity_id, other_id)
    try:
        written = await memory.add_relation(
            campaign.guild_id,
            campaign.id,
            subject_id,
            predicate,
            object_id,
            source=DM,
            confidence=1.0,
            status=CONFIRMED,
            secret=secret,
        )
    except MemoryRuleError as exc:
        await _tell(interaction, f"Couldn't connect them. {exc}")
        return
    changed(interaction, campaign)
    relation, flags = written.value
    said = f"**{_md(subject[1])}** {connection_words(predicate)} **{_md(obj[1])}**"
    if relation.secret:  # what is stored: a connection already secret is never made public
        note = f"🧭 Saved: {said} 🤫 Secret: players can't see it."
        if forced:
            note += f" {SECRET_FORCED}"
        elif not secret:
            note += f" {SECRET_ALREADY}"
    else:
        note = f"🧭 Saved: {said}."
        if denied:
            note += f" {SECRET_DENIED_SAVE}"
    if flags:
        why = FLAG_WHY.get(flags[0].kind) or (
            f'It\'s an odd pair for "{connection_words(predicate)}"'
        )
        note += f" {why}, so DMbot will ask you about it after the session."
    await show_card(interaction, campaign.id, entity_id, replace=True, note=note)


# ---- finding a name --------------------------------------------------------------------


class FindForm(discord.ui.Modal, title="Find a name"):
    typed: discord.ui.TextInput[FindForm] = discord.ui.TextInput(
        label="Name",
        placeholder="Type part of a name, a nickname, or how it sounds",
        max_length=NAME_LIMIT,
    )

    def __init__(self, campaign_id: str) -> None:
        super().__init__(timeout=VIEW_TIMEOUT_S)
        self.campaign_id = campaign_id

    async def on_error(  # type: ignore[override]  # a form's has no item (discord.py)
        self, interaction: discord.Interaction, error: Exception
    ) -> None:
        await _failed(interaction, error)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await show_matches(interaction, self.campaign_id, self.typed.value)


def _label(names: CampaignLookup, m: Match) -> str:
    entity = names.entities[m.entity_id]
    matched = "" if name_key(m.matched) == name_key(entity.name) else f' (matched "{m.matched}")'
    secret = " 🤫" if m.secret else ""
    return logic.shorten(f"{entity.name} · {_kind(entity.kind_key)}{matched}{secret}", 100)


async def show_matches(interaction: discord.Interaction, campaign_id: str, typed: str) -> None:
    await _answer_first(interaction)  # names may load slowly (#537)
    campaign = await _campaign_for(interaction, campaign_id)
    if campaign is None:
        return
    names = await _names(interaction, campaign)
    if names is None:
        return
    matches = find(names, typed, secrets=sees_secrets(campaign, interaction.user.id))
    if len(matches) == 1:
        await show_card(interaction, campaign.id, matches[0].entity_id)
        return
    options = [discord.SelectOption(label=_label(names, m), value=m.entity_id) for m in matches]
    view = Matches(campaign.id, typed, options)
    if not matches:
        text = f"No name like **{_md(typed)}**."
    elif len(matches) >= MAX_RESULTS:
        text = (
            f"**Names like {_md(typed)}:** the closest {MAX_RESULTS}. Type more of the name to "
            "narrow it."
        )
    else:
        text = f"**Names like {_md(typed)}:** pick one."
    await _send(interaction, text, view)


class Matches(_Menu):
    def __init__(self, campaign_id: str, typed: str, options: list[discord.SelectOption]) -> None:
        super().__init__()
        self.campaign_id = campaign_id
        self.typed = typed
        if options:
            self.pick = _Select(
                self._picked, placeholder="✏️ Fix, change or remove a name…", options=options
            )
            self.add_item(self.pick)
        primary, secondary = discord.ButtonStyle.primary, discord.ButtonStyle.secondary
        self.add_item(
            _Button(self._again, label="🔍 Search again", style=primary if options else secondary)
        )
        self.add_item(
            _Button(
                self._add,
                label="➕ Add it as a new name",
                style=secondary if options else primary,
            )
        )

    async def _picked(self, interaction: discord.Interaction) -> None:
        self.stop()
        await show_card(interaction, self.campaign_id, self.pick.values[0], replace=True)

    async def _again(self, interaction: discord.Interaction) -> None:
        if await _campaign_for(interaction, self.campaign_id):
            await interaction.response.send_modal(FindForm(self.campaign_id))

    async def _add(self, interaction: discord.Interaction) -> None:
        from dmbot.ui.names import AddNameForm

        campaign = await _campaign_for(interaction, self.campaign_id)
        if campaign:
            form = AddNameForm(
                self.campaign_id, secrets=sees_secrets(campaign, interaction.user.id)
            )
            form.name.default = self.typed[:NAME_LIMIT]
            await interaction.response.send_modal(form)


# ---- the type-ahead for /dmbot names find: ---------------------------------------------


async def typeahead_campaign(interaction: discord.Interaction) -> Campaign | None:
    """The campaign the type-ahead searches, or None (it suggests nothing): the one
    being played, else the person's only campaign. Same check as the panel."""
    guild = interaction.guild
    if guild is None:
        return None
    bot = _bot(interaction)
    mine = logic.runnable(
        await bot.campaigns.list_campaigns(guild.id), interaction.user.id, _is_manager(interaction)
    )
    playing = bot.active_campaign_id(guild.id)
    for campaign in mine:
        if campaign.id == playing:
            return campaign
    return mine[0] if len(mine) == 1 else None


# How long type-ahead waits for a campaign's names copy that isn't ready: Discord drops an
# answer after 3 seconds, and the campaign look-up comes first (#581).
TYPEAHEAD_WAIT_S = 1.5


async def find_typeahead(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    """Discord asks this as someone types; it runs for anyone who can see the command,
    so it suggests nothing to anyone who isn't the campaign's DM or a server manager."""
    try:
        campaign = await typeahead_campaign(interaction)
        cache = _bot(interaction).lookup
        if campaign is None or cache is None:
            return []
        names = await cache.get_within(campaign.guild_id, campaign.id, TYPEAHEAD_WAIT_S)
    except Exception:
        log.exception("Name type-ahead failed")
        return []
    if names is None:  # still loading: nothing this time, ready for the next keystroke
        return []
    secrets = sees_secrets(campaign, interaction.user.id)
    names = names.for_viewer(secrets)
    if current.strip():
        matches = find(names, current, secrets=secrets)
    else:
        matches = [
            Match(e, names.entities[e].name, False, 0) for e in said_lately(names, MAX_RESULTS)
        ]
    return [
        app_commands.Choice(name=_label(names, m), value=m.entity_id) for m in matches[:MAX_RESULTS]
    ]


async def open_found(interaction: discord.Interaction, campaign_id: str, value: str) -> None:
    """What `/dmbot names find:` does with what was picked (an ID) or typed (search)."""
    if is_id(value):
        await show_card(interaction, campaign_id, value)
    else:
        await show_matches(interaction, campaign_id, value)
