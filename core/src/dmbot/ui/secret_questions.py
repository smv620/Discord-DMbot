"""🤫 Secrets to check (#4524 slice 2): the card that answers the questions DMbot asks instead of
taking knowledge away from the players by itself (`memory/secret_questions.py`, slice 1).

One question at a time, in the DM's private chat only, never in a shared channel (the card names
the item, and may spoil a backstory; the DM Bot channel gets counts only, #3874). Opened from
❓ Questions for you, an upload's note or the after-session line; only a DM of the campaign can
open it or answer, checked on every press. Each kind has its own two answers:
- **narrow:** "Keep it known" (nothing changes, never asked again) / "Make it secret";
- **replace:** "Keep both" / "End the old one";
- **reveal:** "Keep it secret" / "Let them know".
An answer that changes something is the DM's own change in the store (`answer_secret_question`),
with ↩️ Undo right after, which puts the question back.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

import discord

from dmbot.memory import link_words
from dmbot.memory import secret_questions as sq
from dmbot.memory.models import DM, MemoryRuleError, TooLateToUndo
from dmbot.memory.store import MemoryStore
from dmbot.ui.dmbot_commands import _answer_first, _Button, _Menu, _replace, _send, _tell
from dmbot.ui.family_ties import _dm_campaign
from dmbot.ui.names import _md, _memory

log = logging.getLogger(__name__)

HEADING = "🤫 **Secrets to check**"
LABEL = "🤫 Secrets to check"
WHY = "DMbot never takes something away from your players without asking you."
KEEP_KNOWN, MAKE_SECRET = "👁️ Keep it known", "🤫 Make it secret"
KEEP_BOTH, END_OLD = "🔗 Keep both", "⏹️ End the old one"
KEEP_SECRET, LET_KNOW = "🤫 Keep it secret", "👁️ Let them know"
UNDO = "↩️ Undo"
GONE_NOW = "Already answered. Showing what is left."
COULD_NOT = "DMbot couldn't change that, so it was left as it is. Showing what is left."
NOTHING_LEFT = "✅ Nothing left to answer. If DMbot finds more, they show in ❓ Questions for you."
NOT_AVAILABLE = (
    "Remembering names isn't switched on for this DMbot yet. Ask whoever runs DMbot to turn it on."
)
GONE_ITEM = "something that has since been removed"
NAMES_SHOWN = 5  # player characters named on one card; the rest are counted


@dataclass(frozen=True, slots=True)
class Shown:
    """One question as the card shows it: the item's words, ready for Discord, and what the answer
    would change. Built by `describe` from the store, so the card itself is pure."""

    question: sq.Question
    item: str  # "**Ash Morrow**", "**Ash Morrow** works for **Master Orlen**"
    what: str  # "name", "other name", "connection", "clue"
    owner: str = ""  # an other name's person, "**Ash Morrow**"
    knowers: tuple[str, ...] = ()  # the player characters a knowers change would take off
    new: str = ""  # a replace question's new connection, in words


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _names(names: tuple[str, ...]) -> str:
    shown = [f"**{n}**" for n in names[:NAMES_SHOWN]]
    if len(names) > NAMES_SHOWN:
        shown.append(_plural(len(names) - NAMES_SHOWN, "other"))
    if len(shown) == 1:
        return shown[0]
    return ", ".join(shown[:-1]) + " and " + shown[-1]


def _source(q: sq.Question) -> str:
    if q.label:
        return f'From "{_md(q.label)}".'
    return ""


def _make_secret_says(s: Shown) -> str:
    """What Make it secret does, in plain words."""
    change = s.question.change
    if "knowers_off" in change:
        who = _names(s.knowers) if s.knowers else "their characters"
        return f"{who} stop knowing it."
    if change.get("audience_limited"):
        return "only the players you chose for this clue see it."
    return f"your players stop seeing this {s.what}."


def card_text(s: Shown, waiting: int) -> str:
    """The question first, in one plain line, then where it came from, why DMbot asks and what
    each button does. Never posted in a shared channel."""
    q = s.question
    count = f" ({waiting} waiting)" if waiting > 1 else ""
    lines = [f"{HEADING}{count}"]
    if q.kind == sq.REPLACE:
        lines += [
            f"Did this change? Your players know {s.item}.",
            f"Your lore now says {s.new}." if s.new else "Your lore now says something else.",
        ]
        answers = (
            f"{KEEP_BOTH}: both stay, and DMbot won't ask this again. "
            f"{END_OLD}: the old one ends; {UNDO} takes it back."
        )
    elif q.kind == sq.REVEAL:
        lines.append(
            f"Your players knew the name {s.item}, but it is also a secret name of {s.owner}."
        )
        answers = (
            f"{KEEP_SECRET}: it stays the DM's, and DMbot won't ask this again. "
            f"{LET_KNOW}: your players see it as {s.owner}'s name; {UNDO} takes it back."
        )
    else:
        thing = f"{s.item}, another name of {s.owner}," if s.what == "other name" else s.item
        lines.append(f"{thing} sounds like a secret, but your players already know it.")
        answers = (
            f"{KEEP_KNOWN}: nothing changes, and DMbot won't ask this again. "
            f"{MAKE_SECRET}: {_make_secret_says(s)} {UNDO} takes it back."
        )
    source = _source(q)
    if source:
        lines.append(source)
    lines += [f"_{WHY}_", "", f"_{answers}_"]
    return "\n".join(lines)


def done_text(s: Shown, make_secret: bool) -> str:
    """What the DM's answer did."""
    q = s.question
    if q.kind == sq.REPLACE:
        if make_secret:
            return f"Done: {s.item} has ended. {UNDO} takes it back."
        return "Kept both. DMbot won't ask about this again."
    if q.kind == sq.REVEAL:
        if make_secret:
            return f"Kept secret. DMbot won't ask about {s.item} again."
        return f"Done: your players now see {s.item} as {s.owner}'s name. {UNDO} takes it back."
    if make_secret:
        return f"Done: {s.item} is a secret now. {UNDO} takes it back."
    return f"Kept known. DMbot won't ask about {s.item} again."


UNDONE = "Undone. The question is back, so you can answer it again."


async def _entry_name(memory: MemoryStore, g: int, c: str, entity_id: str) -> tuple[str, str]:
    """An entry's name for the card, bold and safe, and its kind; a gone one is said so."""
    e = await memory.entity(g, c, entity_id)
    if e is None:
        return GONE_ITEM, ""
    return f"**{_md(e.name)}**", e.type


async def describe(memory: MemoryStore, g: int, c: str, q: sq.Question) -> Shown:
    """The card's words for one question, read from the store (the DM's own view: secret names
    included). An item removed since is named as such; answering it then only records the
    answer (the store's rule)."""
    knowers: tuple[str, ...] = ()
    for entity_id in q.change.get("knowers_off") or ():
        e = await memory.entity(g, c, str(entity_id))
        if e is not None:
            knowers += (_md(e.name),)
    if q.item_table == "memory_entities":
        name, kind = await _entry_name(memory, g, c, q.item_id)
        what = "clue" if kind == "clue" else "name"
        return Shown(q, name, what, knowers=knowers)
    if q.item_table == "memory_aliases":
        alias = next(
            (a for a in await memory.aliases(g, c, include_secret=True) if a.id == q.item_id),
            None,
        )
        if alias is None:
            return Shown(q, GONE_ITEM, "other name", owner=GONE_ITEM, knowers=knowers)
        owner, _kind = await _entry_name(memory, g, c, alias.entity_id)
        return Shown(q, f"**{_md(alias.text)}**", "other name", owner=owner, knowers=knowers)
    item = await _connection(memory, g, c, q.item_id)
    new = ""
    if q.kind == sq.REPLACE and q.change.get("by"):
        new = await _connection(memory, g, c, str(q.change["by"]))
    return Shown(q, item, "connection", knowers=knowers, new=new)


async def _connection(memory: MemoryStore, g: int, c: str, relation_id: str) -> str:
    """A connection in words, "**Ash Morrow** works for **Master Orlen**"."""
    r = await memory.relation(g, c, relation_id)
    if r is None:
        return GONE_ITEM
    a, kind = await _entry_name(memory, g, c, r.subject_id)
    b, _ = await _entry_name(memory, g, c, r.object_id)
    words = link_words.link_words(r.predicate, r.detail, True, kind, feeling=True)
    return f"{a} {_md(words)} {b}"


def _buttons(kind: str) -> tuple[str, str]:
    """(the answer that changes nothing, the answer that changes something) for this kind; for
    a reveal the change is Let them know."""
    if kind == sq.REPLACE:
        return KEEP_BOTH, END_OLD
    if kind == sq.REVEAL:
        return KEEP_SECRET, LET_KNOW
    return KEEP_KNOWN, MAKE_SECRET


class SecretQuestions(_Menu):
    """One question at a time: the kind's two answers, and Undo right after a change."""

    def __init__(self, campaign_id: str, guild_id: int) -> None:
        super().__init__()
        self.campaign_id = campaign_id
        self.guild_id = guild_id  # from the campaign: a DM's private chat has no server (#3210)
        self.current: Shown | None = None
        self.undo_batch: int | None = None  # the last change, while Undo is offered

    async def _load(self, interaction: discord.Interaction) -> str | None:
        """The next question's words (its buttons drawn), or None when none is left."""
        memory = _memory(interaction)
        if memory is None:
            return None
        waiting = await memory.secret_questions_waiting(self.guild_id, self.campaign_id)
        self.clear_items()
        if not waiting:
            self.current = None
            if self.undo_batch is not None:
                self.add_item(_Button(self._undo, label=UNDO, style=discord.ButtonStyle.secondary))
            return None
        shown = await describe(memory, self.guild_id, self.campaign_id, waiting[0])
        self.current = shown
        stay, change = _buttons(shown.question.kind)
        self.add_item(_Button(self._stay, label=stay, style=discord.ButtonStyle.secondary))
        self.add_item(_Button(self._change, label=change, style=discord.ButtonStyle.primary))
        if self.undo_batch is not None:
            self.add_item(_Button(self._undo, label=UNDO, style=discord.ButtonStyle.secondary))
        return card_text(shown, len(waiting))

    async def _stay(self, interaction: discord.Interaction) -> None:
        await self._answer(interaction, "stay")

    async def _change(self, interaction: discord.Interaction) -> None:
        await self._answer(interaction, "change")

    async def _undo(self, interaction: discord.Interaction) -> None:
        await self._answer(interaction, "undo")

    async def _answer(self, interaction: discord.Interaction, what: str) -> None:
        await _answer_first(interaction, in_place=True)
        campaign = await _dm_campaign(interaction, self.campaign_id)  # asked again each press
        memory = _memory(interaction)
        if campaign is None or memory is None:
            return
        self.guild_id = campaign.guild_id  # (the campaign may have moved to another server)
        said = GONE_NOW
        shown = self.current
        try:
            if what == "undo" and self.undo_batch is not None:
                batch, self.undo_batch = self.undo_batch, None
                await memory.undo(self.guild_id, self.campaign_id, batch)
                said = UNDONE
            elif shown is not None and what in ("stay", "change"):
                self.undo_batch = None
                q = shown.question
                # (the store's `make_secret`: for a reveal, True keeps it secret, so Let them know
                # is False; for the others the change is True)
                changes = what == "change"
                make_secret = not changes if q.kind == sq.REVEAL else changes
                written = await memory.answer_secret_question(
                    self.guild_id, self.campaign_id, q.id, make_secret, source=DM
                )
                self.undo_batch = written.batch if changes else None
                said = done_text(shown, make_secret)
        except TooLateToUndo:
            said = GONE_NOW
        except MemoryRuleError as exc:
            said = GONE_NOW if str(exc) == sq.GONE else COULD_NOT
            if str(exc) != sq.GONE:
                log.info("A 🤫 answer was refused (%s)", type(exc).__name__)
        text = await self._load(interaction)
        if text is None:
            if self.undo_batch is None:
                self.stop()
            await _replace(
                interaction,
                f"{said}\n\n{NOTHING_LEFT}",
                self if self.undo_batch is not None else None,
            )
            return
        await _replace(interaction, f"{said}\n\n{text}", self)


class SecretQuestionsButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=r"dmbot:secrets:ask:(?P<campaign>[0-9a-f]{32})",
):
    """🤫 Secrets to check, from the Questions message, an upload's note or the after-session
    line; works after a restart (the campaign is in the button's ID). The card opens privately,
    for a DM only."""

    def __init__(self, campaign_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label=LABEL,
                style=discord.ButtonStyle.primary,
                custom_id=f"dmbot:secrets:ask:{campaign_id}",
            )
        )
        self.campaign_id = campaign_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> SecretQuestionsButton:
        return cls(match["campaign"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        await start_secret_questions(interaction, self.campaign_id)


async def start_secret_questions(interaction: discord.Interaction, campaign_id: str) -> None:
    # A new private message (the notice and the Questions message are never replaced).
    await interaction.response.defer(ephemeral=True, thinking=True)
    if _memory(interaction) is None:
        await _tell(interaction, NOT_AVAILABLE)
        return
    campaign = await _dm_campaign(interaction, campaign_id)
    if campaign is None:
        return
    view = SecretQuestions(campaign.id, campaign.guild_id)
    shown = await view._load(interaction)
    if shown is None:
        await _tell(interaction, NOTHING_LEFT)
        return
    await _send(interaction, shown, view)


def count_line(n: int) -> str:
    """The count line for an upload's note or after a session: counts only, never which (the DM
    Bot channel is read by the whole campaign); "" for none."""
    if not n:
        return ""
    return (
        f"\n🤫 **{_plural(n, 'thing')} your players know** sounded like "
        f"{'a secret' if n == 1 else 'secrets'}. DMbot kept "
        f"{'it' if n == 1 else 'them'} known and asks you in ❓ Questions for you."
    )


def notice_view(campaign_id: str) -> discord.ui.View:
    out = discord.ui.View(timeout=None)
    out.add_item(SecretQuestionsButton(campaign_id))
    return out


__all__ = [
    "SecretQuestions",
    "SecretQuestionsButton",
    "Shown",
    "card_text",
    "count_line",
    "describe",
    "done_text",
    "notice_view",
    "start_secret_questions",
]
