"""🤫 Known or secret? (#4524 slice 2): the card that answers the questions DMbot asks instead of
taking knowledge away from the players by itself (`memory/secret_questions.py`, slice 1).

One question at a time, in a private reply only the DM sees (as 👪 Family ties), never in a shared
channel (the card names
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
from dataclasses import dataclass, replace
from typing import Any

import discord

from dmbot.discord_limits import shorten
from dmbot.memory import link_words, public_guard, world_reveal
from dmbot.memory import secret_questions as sq
from dmbot.memory.models import DM, MemoryRuleError, TooLateToUndo
from dmbot.memory.store import MemoryStore
from dmbot.ui.dmbot_commands import _answer_first, _Button, _Menu, _replace, _send, _tell
from dmbot.ui.family_ties import _dm_campaign
from dmbot.ui.names import _md, _memory
from dmbot.upload_notices import UNDO_DAYS

log = logging.getLogger(__name__)

HEADING = "🤫 **Known or secret?**"
# (not "Secrets to check": its button sits under counts in the channel the players read, and a
# label that says secrets exist teases them; ux-critic)
LABEL = "🤫 Known or secret?"
WHY = "DMbot never changes what your players know without asking you."
KEEP_KNOWN, MAKE_SECRET = "👁️ Keep it known", "🤫 Make it secret"
KEEP_BOTH, END_OLD = "🔗 Keep both", "⏹️ End the old one"
KEEP_SECRET, LET_KNOW = "🤫 Keep it secret", "👁️ Let them know"
# A 🌍 World read's reveal, asked instead of made (#4590): one is the older reveal's "Let them
# know" (never "everyone", which reads as a post to the server; ux-critic). The same person is
# Supervisor's ruled words for A. Every one the same upload asked is one change with one Undo, and
# keeping them all secret is offered beside it, so revealing is never the only quick answer.
SAME_YES = "👁️ Yes, let everyone know"


def same_for_all(n: int) -> str:
    return f"👁️ Same for all {n}"


def keep_all(n: int) -> str:
    return f"🤫 Keep all {n} secret"


LORE = "Your 🌍 Common knowledge lore"
UNDO = "↩️ Undo"
# (#4908: a question can also be gone because its speaker stopped being recorded or its session's
# transcript was deleted, not only answered: one sentence true of both, in Supervisor's words)
GONE_NOW = "This question isn't waiting any more. Showing what is left."
TOO_LATE = "It's too late to undo that one here. Showing what is left."
NOTHING_TO_DO = "That had already changed, so nothing was done. Showing what is left."
CANT_SHOW_YET = (
    "DMbot couldn't show that yet: something it needs is still hidden from your players (a name "
    "at one end, or the person). Show that first, or keep it secret. Showing what is left."
)
CANT_SHOW_SOME = "Some couldn't be shown yet, so they wait for you."
COULD_NOT = (
    "DMbot couldn't change that, so it was left as it is. To change it yourself, open the name's "
    "card. Showing what is left."
)
NOTHING_LEFT = (
    "✅ Nothing left to answer. If DMbot finds more, they'll show up in ❓ Questions for you."
)
GONE_LINE = "This one was about something since removed. Either answer just clears it."
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
    from_upload: int = 0  # 🌍 questions waiting from the same upload, this one included (#4590)


def is_world(q: sq.Question) -> bool:
    """A 🌍 World read's reveal, asked instead of made (#4590), answered by `world_reveal`."""
    return q.kind == sq.REVEAL and bool(q.change.get("common"))


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
    """What Make it secret does, in plain words: DMbot's record, never the game itself."""
    change = s.question.change
    if "knowers_off" in change:
        who = _names(s.knowers) if s.knowers else "some player characters"
        return f"DMbot stops showing {who} as knowing it."
    if public_guard.limits_audience(change):
        return "only the players this clue was meant for see it."
    return f"this {s.what} is hidden from your players in DMbot."


def card_text(s: Shown, waiting: int) -> str:
    """The question first, in one plain line, then where it came from, why DMbot asks and what
    each button does, one line each (easier on a phone). Never posted in a shared channel."""
    q = s.question
    count = f" ({waiting} waiting)" if waiting > 1 else ""
    lines = [f"{HEADING}{count}"]
    if GONE_ITEM in (s.item, s.owner):
        lines.append(GONE_LINE)
        answers: tuple[str, ...] = ()
    elif is_world(q):
        lines.append(_world_line(s))
        if q.change.get("lone"):
            answers = (
                f"{KEEP_SECRET}: it stays secret (the same person, or someone else), and DMbot "
                "won't ask this again.",
                f"{SAME_YES}: your players learn that {s.item} is {s.owner}. {UNDO} takes it back.",
            )
        else:
            answers = (
                f"{KEEP_SECRET}: it stays hidden, and DMbot won't ask this again.",
                f"{LET_KNOW}: your players can see it in DMbot, and every player character knows "
                f"it, including ones who join later. DMbot doesn't post it anywhere. {UNDO} takes "
                "it back.",
            )
        if s.from_upload > 1:
            n = s.from_upload
            answers += (
                f"{same_for_all(n)}: your players can see all {n} from this upload. A secret "
                "name that may be someone else's is still asked on its own. "
                f"{UNDO} takes them all back.",
                f"{keep_all(n)}: none of the {n} from this upload change, and DMbot won't ask "
                "about them again.",
            )
    elif q.kind == sq.REPLACE:
        lines += [
            f"Did this change? Your players know {s.item}.",
            f"Now: {s.new}." if s.new else "Something newer says otherwise.",
        ]
        answers = (
            f"{KEEP_BOTH}: both stay true, and DMbot won't ask this again.",
            f"{END_OLD}: the old one becomes how things were. {UNDO} takes it back.",
        )
    elif q.kind == sq.REVEAL:
        # (a secret identity is the biggest spoiler DMbot holds: said plainly, never softened)
        lines.append(f"Your players know the name {s.item}. Only you know it is {s.owner}.")
        answers = (
            f"{KEEP_SECRET}: they don't learn it is {s.owner}, and DMbot won't ask this again.",
            f"{LET_KNOW}: your players learn that {s.item} is {s.owner}. {UNDO} takes it back.",
        )
    else:
        thing = f"{s.item}, another name of {s.owner}," if s.what == "other name" else s.item
        lines.append(f"{thing} sounds like a secret, but your players already know it.")
        answers = (
            f"{KEEP_KNOWN}: nothing changes, and DMbot won't ask this again.",
            f"{MAKE_SECRET}: {_make_secret_says(s)} {UNDO} takes it back.",
        )
    source = _source(q)
    if source:
        lines.append(source)
    lines += [f"_{WHY}_", "", *(f"_{a}_" for a in answers)]
    return "\n".join(lines).rstrip()


def _world_line(s: Shown) -> str:
    """A 🌍 question's line: what the lore said that the players don't know yet."""
    q = s.question
    if q.change.get("lone"):
        return (
            f"{LORE} names {s.item}, which is also a secret name of {s.owner}. Is it the same "
            "person?"
        )
    if s.what == "other name":
        return (
            f"{LORE} gives {s.item} as another name of {s.owner}, which is hidden from your "
            "players."
        )
    if s.what == "connection":
        return f"{LORE} says {s.item}, which is hidden from your players."
    return f"{LORE} names {s.item}, which is hidden from your players."


def done_text(s: Shown, make_secret: bool) -> str:
    """What the DM's answer did."""
    q = s.question
    if is_world(q) and not make_secret:
        if q.change.get("lone"):
            return f"Done: your players now know {s.item} is {s.owner}. {UNDO} takes it back."
        return (
            f"Done: your players can see {s.item} now, and every player character knows it. "
            f"{UNDO} takes it back."
        )
    if q.kind == sq.REPLACE:
        if make_secret:
            return f"Done: {s.item} is now how things were. {UNDO} takes it back."
        return "Kept both. DMbot won't ask about this again."
    if q.kind == sq.REVEAL:
        if make_secret:
            return "Kept secret. DMbot won't ask about this again."
        return f"Done: your players now know {s.item} is {s.owner}. {UNDO} takes it back."
    if make_secret:
        return f"Done: {s.item} is a secret now. {UNDO} takes it back."
    return "Left as known. DMbot won't ask about this again."


def all_done_text(n: int) -> str:
    return f"Done: your players can see all {n} from this upload now. {UNDO} takes them all back."


def all_kept_text(n: int) -> str:
    return f"Kept all {n} secret. DMbot won't ask about them again."


def _same_upload(q: sq.Question, waiting: list[sq.Question]) -> list[sq.Question]:
    """The 🌍 questions still waiting from the same upload as `q` (the run that asked them): what
    "Same for all" and "Keep all secret" answer. Never another upload's."""
    # (never a lone secret name's "same person?": only the DM can say whose it is, one by one.
    # By the run that asked, never the label: two uploads may share a name and a day)
    run = q.change.get(sq.RUN)
    return [
        x
        for x in waiting
        if is_world(x) and not x.change.get("lone") and run and x.change.get(sq.RUN) == run
    ]


UNDONE = "Undone. The question is back, so you can answer it again."


async def _entry_name(
    memory: MemoryStore, g: int, c: str, entity_id: str, cut: int | None = None
) -> tuple[str, str]:
    """An entry's name for the card, bold and safe (cut to `cut` characters before escaping,
    where a list must fit one message), and its kind; a gone one is said so."""
    e = await memory.entity(g, c, entity_id)
    if e is None:
        return GONE_ITEM, ""
    return f"**{_md(_cut(e.name, cut))}**", e.type


def _cut(text: str, limit: int | None) -> str:
    return text if limit is None else shorten(text, limit)


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


async def _connection(
    memory: MemoryStore, g: int, c: str, relation_id: str, cut: int | None = None
) -> str:
    """A connection in words, "**Ash Morrow** works for **Master Orlen**"."""
    r = await memory.relation(g, c, relation_id)
    if r is None:
        return GONE_ITEM
    a, kind = await _entry_name(memory, g, c, r.subject_id, cut)
    b, _ = await _entry_name(memory, g, c, r.object_id, cut)
    words = link_words.link_words(r.predicate, r.detail, True, kind, feeling=True)
    return f"{a} {_md(_cut(words, cut))} {b}"


def _buttons(q: sq.Question) -> tuple[str, str]:
    """(the answer that changes nothing, the answer that changes something) for this question;
    for a reveal the change is the reveal."""
    kind = q.kind
    if is_world(q):
        return KEEP_SECRET, SAME_YES if q.change.get("lone") else LET_KNOW
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
        # the last answer's changes, newest last, while Undo is offered (a 🌍 reveal is several)
        self.undo_batches: list[int] = []

    async def _load(self, interaction: discord.Interaction) -> str | None:
        """The next question's words (its buttons drawn), or None when none is left."""
        memory = _memory(interaction)
        if memory is None:
            return None
        waiting = await memory.secret_questions_waiting(self.guild_id, self.campaign_id)
        self.clear_items()
        if not waiting:
            self.current = None
            if self.undo_batches:
                self.add_item(_Button(self._undo, label=UNDO, style=discord.ButtonStyle.secondary))
            return None
        shown = await describe(memory, self.guild_id, self.campaign_id, waiting[0])
        if is_world(shown.question) and not shown.question.change.get("lone"):
            same = _same_upload(shown.question, waiting)
            shown = replace(shown, from_upload=len(same))
        self.current = shown
        stay, change = _buttons(shown.question)
        self.add_item(_Button(self._stay, label=stay, style=discord.ButtonStyle.secondary))
        # (both grey: a highlighted answer would look like DMbot's advice, and the DM decides)
        self.add_item(_Button(self._change, label=change, style=discord.ButtonStyle.secondary))
        if shown.from_upload > 1:
            n = shown.from_upload
            grey = discord.ButtonStyle.secondary
            self.add_item(_Button(self._all, label=same_for_all(n), style=grey))
            self.add_item(_Button(self._keep_all, label=keep_all(n), style=grey))
        if self.undo_batches:
            self.add_item(_Button(self._undo, label=UNDO, style=discord.ButtonStyle.secondary))
        return card_text(shown, len(waiting))

    async def _stay(self, interaction: discord.Interaction) -> None:
        await self._answer(interaction, "stay")

    async def _change(self, interaction: discord.Interaction) -> None:
        await self._answer(interaction, "change")

    async def _undo(self, interaction: discord.Interaction) -> None:
        await self._answer(interaction, "undo")

    async def _all(self, interaction: discord.Interaction) -> None:
        await self._answer(interaction, "all")

    async def _keep_all(self, interaction: discord.Interaction) -> None:
        await self._answer(interaction, "keep all")

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
            if what == "undo" and self.undo_batches:
                said = UNDONE
                while self.undo_batches:  # (newest first; one that can't be undone stays offered)
                    try:
                        await memory.undo(self.guild_id, self.campaign_id, self.undo_batches[-1])
                    except (TooLateToUndo, MemoryRuleError):
                        said = TOO_LATE
                        break
                    self.undo_batches.pop()
            elif shown is not None and is_world(shown.question) and what == "keep all":
                self.undo_batches = []
                waiting = await memory.secret_questions_waiting(self.guild_id, self.campaign_id)
                kept = 0
                for x in _same_upload(shown.question, waiting):  # (this upload's only)
                    try:
                        await memory.answer_secret_question(
                            self.guild_id, self.campaign_id, x.id, True, source=DM
                        )
                        kept += 1
                    except MemoryRuleError:  # (answered meanwhile elsewhere)
                        continue
                said = all_kept_text(kept) if kept else GONE_NOW
            elif shown is not None and is_world(shown.question) and what in ("change", "all"):
                self.undo_batches = []
                q = shown.question
                ids = [q.id]
                if what == "all":  # (only this upload's: never another upload's, #4590 C)
                    waiting = await memory.secret_questions_waiting(self.guild_id, self.campaign_id)
                    ids = [x.id for x in _same_upload(q, waiting)] or ids
                made = await world_reveal.answer(memory, self.guild_id, self.campaign_id, ids)
                if made is None:
                    said = GONE_NOW
                else:
                    self.undo_batches = list(made.batches)
                    if not made.answered:
                        said = CANT_SHOW_YET if made.refused else NOTHING_TO_DO
                    elif len(ids) > 1:
                        said = all_done_text(made.answered)
                    else:
                        said = done_text(shown, False)
                    if made.refused and made.answered:
                        said += f" {CANT_SHOW_SOME}"
            elif shown is not None and what in ("stay", "change"):
                self.undo_batches = []
                q = shown.question
                # (the store's `make_secret`: for a reveal, True keeps it secret, so Let them know
                # is False; for the others the change is True)
                changes = what == "change"
                make_secret = not changes if q.kind == sq.REVEAL else changes
                written = await memory.answer_secret_question(
                    self.guild_id, self.campaign_id, q.id, make_secret, source=DM
                )
                self.undo_batches = [written.batch] if changes and written.batch else []
                # (a change with nothing left to change, the item gone or merged since, has no
                # batch and no Undo: never say it was done)
                nothing = changes and written.batch is None
                said = NOTHING_TO_DO if nothing else done_text(shown, make_secret)
        except TooLateToUndo:
            said = TOO_LATE
        except MemoryRuleError as exc:
            said = GONE_NOW if str(exc) == sq.GONE else COULD_NOT
            if str(exc) != sq.GONE:
                log.info("A 🤫 answer was refused (%s)", type(exc).__name__)
        text = await self._load(interaction)
        if text is None:
            if not self.undo_batches:
                self.stop()
            await _replace(
                interaction,
                f"{said}\n\n{NOTHING_LEFT}",
                self if self.undo_batches else None,
            )
            return
        await _replace(interaction, f"{said}\n\n{text}", self)


class SecretQuestionsButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=r"dmbot:secrets:ask:(?P<campaign>[0-9a-f]{32})",
):
    """🤫 Known or secret?, from the Questions message, an upload's note or the after-session
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
    """The count line for an upload's note or after a session, in the DM Bot channel the whole
    campaign reads: counts only, never which, and addressed to the DM. Neutral words, as a reveal
    question kept a secret and the others kept something known; "" for none."""
    if not n:
        return ""
    return (
        f"\n🤫 DM: **{_plural(n, 'thing')}** the players know sounded secret. DMbot left "
        f"{'it as it is' if n == 1 else 'them as they are'}; check "
        f"{'it' if n == 1 else 'them'} in ❓ Questions for you."
    )


UNDO_UPLOAD = "↩️ **Undo this upload**"
REVEALED_SHOWN = 10
# The list stays well inside one Discord message with the notice above it (2,000 characters):
# each name and connection's words cut to `NAME_CUT`, the lines to `REVEALED_BUDGET` in all, and
# what doesn't fit is counted in "and N more" (as `look_alikes.MESSAGE_BUDGET` does)
NAME_CUT = 40
REVEALED_BUDGET = 900  # names a private list gives; the rest are counted (Supervisor's ruling B)


async def revealed_names(
    memory: MemoryStore, g: int, c: str, revealed: world_reveal.Revealed
) -> list[str]:
    """What a World read revealed by itself, in words, for the DMs only (#4590 B): each entry's
    name, each connection ("**Ash** works for **Orlen**") and each other name with its person.
    Built only for a private message and never logged."""
    cut = NAME_CUT
    lines = [(await _entry_name(memory, g, c, e, cut))[0] for e in revealed.entry_ids]
    lines += [await _connection(memory, g, c, r, cut) for r in revealed.link_ids]
    if revealed.alias_ids:
        wanted = set(revealed.alias_ids)
        for a in await memory.aliases(g, c, include_secret=True):
            if a.id in wanted:
                owner, _ = await _entry_name(memory, g, c, a.entity_id, cut)
                lines.append(f"{owner} is also called **{_md(_cut(a.text, cut))}**")
    return [line for line in lines if line != GONE_ITEM]


def revealed_text(lines: list[str]) -> str:
    """The private lines under an upload's notice (#4590 B): which hidden things the players can
    now see, one per line, at most `REVEALED_SHOWN`, then how many more, and how to keep one
    secret. "" for none. Never for a shared channel."""
    if not lines:
        return ""
    one = len(lines) == 1
    shown: list[str] = []
    used = 0
    for line in lines[:REVEALED_SHOWN]:
        if shown and used + len(line) > REVEALED_BUDGET:
            break
        shown.append(line)
        used += len(line) + 3
    more = len(lines) - len(shown)
    return (
        f"\n🌍 _Only you see this._ Your players can now see {'this' if one else 'these'} in "
        f"DMbot, and their characters know {'it' if one else 'them'}:\n"
        + "\n".join(f"• {line}" for line in shown)
        + (f"\n…and {more} more." if more else "")
        + f"\nShould {'it' if one else 'one'} stay secret? Open its name card and hide it, or "
        f"{UNDO_UPLOAD} takes back the whole upload (within {UNDO_DAYS} days)."
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
