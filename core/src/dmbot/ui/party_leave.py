"""A goodbye or a death, in Discord: "Still in the party?" (#4537, party knowledge slice 6a;
`docs/design/party_knowledge.md` §4 "Readers only suggest", §6 and §11; Supervisor's ruling on
slice 6, 2026-11-21).

- **The question**, one private message to each DM, never during play (`send_staying_questions`,
  from the after-session steps beside the join question): "Okthandom is marked dead. Mark
  Okthandom as no longer in the party?" (the ruling's words, the same whether the death came from
  play or from a card edit between sessions), or, for a goodbye heard at the table (slice 6b),
  "Okthandom said goodbye to the party in the story. Has Okthandom left the party?".
- **The answers** (each redraws the message with whoever in it still waits): "Left the party"
  closes the character's stretch with the card's own call, one
  change with its ↩️ Undo (which asks again). "Still in the party" writes nothing and is a plain
  answer, never a warning: characters come back in this game (Revivify, Raise Dead). "Later"
  keeps it in ❓ Questions for you.
- **Nothing here closes a stretch by itself** (§11): only a DM's press of "Left the party".

The buttons carry the campaign and the character, never a name; who may press is read again on
every press, and only a campaign's DMs may. The character is named, never with a pronoun (ruling
C). Counts-only logs.
"""

from __future__ import annotations

import contextlib
import logging
import re
from collections.abc import Sequence
from typing import Any

import discord

from dmbot.memory.join_questions import DIED, AlreadyAnswered
from dmbot.memory.models import MemoryRuleError, TooLateToUndo
from dmbot.ui.dmbot_commands import _answer_first, _bot
from dmbot.ui.party_join import _dm_campaign, _edit, _followup, _names

log = logging.getLogger(__name__)

DIED_LINE = "**{name} is marked dead.** Mark {name} as no longer in the party?"
LEAVE_LINE = "**{name} said goodbye to the party in the story.** Has {name} left the party?"
LEFT_BUTTON = "Left the party"
STAYS_BUTTON = "Still in the party"
LATER_BUTTON = "Later"
UNDO = "↩️ Undo"
LEFT_DONE = "✅ {name} has left the party. DMbot keeps what {name} knew, in case {name} comes back."
STAYS_DONE = "✅ {name} is still in the party."
LATER_DONE = "Kept for later in ❓ Questions for you."
UNDONE = "↩️ Undone. {name} is in the party again."
ANSWERED = "Already answered (by you or another DM). Nothing changed."
# (#4908: the question was removed, not answered: its speaker stopped being recorded or its
# session's transcript was deleted; Supervisor's words)
NOT_WAITING = "This question isn't waiting any more."
FAILED = "Couldn't do that just now. Nothing changed. Try again in a moment."
CANT_UNDO = (
    "Can't undo this one: something changed since. Open {name}'s card to put {name} back in the "
    "party."
)
OPEN_LABEL = "🧭 Still in the party?"
WAIT_FOR_PLAY = "A session is running. DMbot will ask who's still in the party when it ends."
NOTHING_LEFT = "No characters to ask about right now."
MORE_THERE = "Buttons for the first {n} here. The rest are in ❓ Questions for you."
SHORT_NAME = 16  # (a name on a button, so the action is never the part a phone cuts off)
MOST_SHOWN = 4  # (characters in one message: two buttons each, and "Later", within Discord's 25)

_ID = r"[0-9a-f]{32}"
_LEFT = rf"dmbot:pleavel:(?P<campaign>{_ID}):(?P<entity>{_ID})"
_STAYS = rf"dmbot:pleaves:(?P<campaign>{_ID}):(?P<entity>{_ID})"
_LATER = rf"dmbot:pleavek:(?P<campaign>{_ID})"
_UNDO = rf"dmbot:pleaveu:(?P<campaign>{_ID}):(?P<entity>{_ID}):(?P<batch>[0-9]{{1,18}})"
_OPEN = rf"dmbot:pleaveo:(?P<campaign>{_ID})"


def line_for(kind: str, name: str) -> str:
    """One character's question, in the ruling's words. Pure."""
    return (DIED_LINE if kind == DIED else LEAVE_LINE).format(name=name)


def _label(base: str, name: str, many: bool) -> str:
    """A button's words: the action alone, or with several characters in one message the name
    (cut short, so the action always shows on a phone) and the action in one word. Pure."""
    if not many:
        return base
    short = name if len(name) <= SHORT_NAME else name[: SHORT_NAME - 1] + "…"
    return f"{short}: {'left' if base == LEFT_BUTTON else 'stays'}"


def question_view(
    campaign_id: str, people: Sequence[tuple[str, str]], *, later: bool = True
) -> discord.ui.View:
    """The buttons of one message: for each (entity id, name as written, not escaped) its own
    row, "Left the party" and "Still in the party" (neither one leaned on: both are plain
    answers), then one "Later" on the last row."""
    view = discord.ui.View(timeout=None)
    many = len(people) > 1
    shown = people[:MOST_SHOWN]
    for row, (entity_id, name) in enumerate(shown):
        view.add_item(LeftButton(campaign_id, entity_id, _label(LEFT_BUTTON, name, many), row))
        view.add_item(StaysButton(campaign_id, entity_id, _label(STAYS_BUTTON, name, many), row))
    if later:
        view.add_item(LaterButton(campaign_id, len(shown)))
    return view


async def _raw_names(bot: Any, campaign: Any, ids: Sequence[str]) -> list[str]:
    """The names as written, for buttons (Discord shows a label as it is, no markdown)."""
    out = []
    for entity_id in ids:
        entity = await bot.memory.entity(campaign.guild_id, campaign.id, entity_id)
        out.append(entity.name if entity is not None else "A character")
    return out


async def waiting_in(bot: Any, campaign: Any, group_id: str) -> tuple[str, discord.ui.View] | None:
    """The text and buttons for the characters of one message still waiting, or None when none
    is (also what ❓ Questions for you shows)."""
    g, c = campaign.guild_id, campaign.id
    waiting = [
        q for q in await bot.memory.join_question_group(g, c, group_id) if q.status == "waiting"
    ]
    if not waiting:
        return None
    ids = [q.entity_id for q in waiting]
    names = await _names(bot.memory, g, c, ids)
    lines = [line_for(q.kind, n) for q, n in zip(waiting, names, strict=True)]
    if len(waiting) > MOST_SHOWN:
        lines.append(MORE_THERE.format(n=MOST_SHOWN))
    raw = await _raw_names(bot, campaign, ids)
    return "\n".join(lines), question_view(campaign.id, list(zip(ids, raw, strict=True)))


async def _after_answer(bot: Any, campaign: Any, entity_id: str, done: str) -> tuple[str, Any]:
    """The message once one character is answered: the confirmation on top, and the characters
    of the same message still waiting below it with their buttons, so none seems to vanish."""
    group = await bot.memory.staying_group_for(campaign.guild_id, campaign.id, entity_id)
    rest = None if group is None else await waiting_in(bot, campaign, group)
    if rest is None:
        return done, None
    return done + "\n\n" + rest[0], rest[1]


async def send_staying_questions(bot: Any, guild_id: int, campaign_id: str) -> int:
    """Send the campaign's goodbye and death questions not sent yet, one message per kind to each
    DM privately: never while a session is running (the claim takes none then). Returns how many
    characters it asked about. A message no DM could get is given back, to go next time. Never
    raises. No switch: a death needs no AI (Supervisor's ruling U); a goodbye is only ever asked
    once the goodbye reader (slice 6b, behind its own switch) has found one."""
    memory = getattr(bot, "memory", None)
    if memory is None:
        return 0
    asked = 0
    for _ in range(2):  # (one message per kind: deaths, then goodbyes, or the reverse)
        group_id = None
        try:
            claimed = await memory.staying_questions_to_send(guild_id, campaign_id)
            if not claimed:
                break
            group_id = claimed[0].group_id
            campaign = await bot.campaigns.get(guild_id, campaign_id)
            if campaign is None or group_id is None:
                break
            shown = await waiting_in(bot, campaign, group_id)
            if shown is None:
                break
            text, view = shown
            if not await bot.ask_privately(guild_id, campaign_id, text, view):
                await memory.unclaim_join_questions(guild_id, campaign_id, group_id)
                break
            log.info("Party staying question sent (%d characters)", len(claimed))
            asked += len(claimed)
        except Exception as exc:
            log.warning("Party staying question not sent (%s)", type(exc).__name__)
            if group_id is not None:
                with contextlib.suppress(Exception):
                    await memory.unclaim_join_questions(guild_id, campaign_id, group_id)
            break
    return asked


def _others_on(message: Any, entity_id: str) -> list[str]:
    """The other characters with buttons on this message, read from its own buttons' ids: the
    pressed character's row may be gone (a forget), so its group can't be looked up through it."""
    found: list[str] = []
    for row in getattr(message, "components", None) or []:
        for item in getattr(row, "children", None) or []:
            m = re.fullmatch(_LEFT, str(getattr(item, "custom_id", "") or ""))
            if m is not None and m["entity"] != entity_id and m["entity"] not in found:
                found.append(m["entity"])
    return found


async def _gone(interaction: discord.Interaction, bot: Any, campaign: Any, entity_id: str) -> None:
    """A press on a question that no longer waits (#4908). Answered since this message was sent,
    on a question sent by then (by this DM or another): "Already answered". Otherwise it was
    removed by a forget, so "This question isn't waiting any more.", and the message is redrawn
    with the other characters on it that still wait (found from its own buttons), or with none.
    A failed check says the words that are true either way."""
    message = getattr(interaction, "message", None)
    sent = getattr(message, "created_at", None)
    # (no message time: any answer counts, the older behaviour, never a false "removed")
    since = int(sent.timestamp()) if sent is not None else 0
    try:
        answered = await bot.memory.staying_answered_since(
            campaign.guild_id, campaign.id, entity_id, since
        )
        if answered:
            await _edit(interaction, ANSWERED)
            return
        text, rest = NOT_WAITING, None
        for other in _others_on(message, entity_id):
            text, rest = await _after_answer(bot, campaign, other, NOT_WAITING)
            if rest is not None:
                break
    except Exception as exc:  # (the type only)
        log.warning("Staying question check failed (%s)", type(exc).__name__)
        text, rest = NOT_WAITING, None
    await _edit(interaction, text, rest)


async def _name(bot: Any, campaign: Any, entity_id: str) -> str:
    [name] = await _names(bot.memory, campaign.guild_id, campaign.id, [entity_id])
    return name


class LeftButton(discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]], template=_LEFT):
    """ "Left the party": the card's own call, one change with its ↩️ Undo."""

    def __init__(
        self, campaign_id: str, entity_id: str, label: str = LEFT_BUTTON, row: int | None = None
    ) -> None:
        super().__init__(
            discord.ui.Button(
                label=label, style=discord.ButtonStyle.secondary, row=row,
                custom_id=f"dmbot:pleavel:{campaign_id}:{entity_id}",
            )
        )  # fmt: skip
        self.campaign_id, self.entity_id = campaign_id, entity_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> LeftButton:
        return cls(match["campaign"], match["entity"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        await _answer_first(interaction, in_place=True)
        bot = _bot(interaction)
        campaign = await _dm_campaign(interaction, self.campaign_id)  # (on every press)
        if campaign is None or bot.memory is None:
            return
        g, c = campaign.guild_id, campaign.id
        try:
            name = await _name(bot, campaign, self.entity_id)
            done = await bot.memory.staying_answer_left(g, c, self.entity_id)
        except AlreadyAnswered:
            await _gone(interaction, bot, campaign, self.entity_id)
            return
        except Exception as exc:
            log.warning("Staying answer failed (%s)", type(exc).__name__)
            await _edit(interaction, FAILED)
            return
        log.info("A DM answered a staying question: left the party")
        text, rest = await _after_answer(bot, campaign, self.entity_id, LEFT_DONE.format(name=name))
        view = rest or discord.ui.View(timeout=None)
        if done.batch is not None:  # (its ↩️ Undo, on a row of its own below the others)
            view.add_item(StayingUndoButton(self.campaign_id, self.entity_id, done.batch, row=4))
        await _edit(interaction, text, view)


class StaysButton(discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]], template=_STAYS):
    """ "Still in the party": a plain answer; nothing is written."""

    def __init__(
        self, campaign_id: str, entity_id: str, label: str = STAYS_BUTTON, row: int | None = None
    ) -> None:
        super().__init__(
            discord.ui.Button(
                label=label, style=discord.ButtonStyle.secondary, row=row,
                custom_id=f"dmbot:pleaves:{campaign_id}:{entity_id}",
            )
        )  # fmt: skip
        self.campaign_id, self.entity_id = campaign_id, entity_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> StaysButton:
        return cls(match["campaign"], match["entity"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        await _answer_first(interaction, in_place=True)
        bot = _bot(interaction)
        campaign = await _dm_campaign(interaction, self.campaign_id)
        if campaign is None or bot.memory is None:
            return
        try:
            name = await _name(bot, campaign, self.entity_id)
            await bot.memory.staying_answer_stays(campaign.guild_id, campaign.id, self.entity_id)
        except AlreadyAnswered:
            await _gone(interaction, bot, campaign, self.entity_id)
            return
        except Exception as exc:
            log.warning("Staying answer failed (%s)", type(exc).__name__)
            await _edit(interaction, FAILED)
            return
        log.info("A DM answered a staying question: still in the party")
        text, rest = await _after_answer(
            bot, campaign, self.entity_id, STAYS_DONE.format(name=name)
        )
        await _edit(interaction, text, rest)


class LaterButton(discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]], template=_LATER):
    """ "Later": nothing changes; the question stays in ❓ Questions for you."""

    def __init__(self, campaign_id: str, row: int | None = None) -> None:
        super().__init__(
            discord.ui.Button(
                label=LATER_BUTTON, style=discord.ButtonStyle.secondary, row=row,
                custom_id=f"dmbot:pleavek:{campaign_id}",
            )
        )  # fmt: skip
        self.campaign_id = campaign_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> LaterButton:
        return cls(match["campaign"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        await _answer_first(interaction, in_place=True)
        if await _dm_campaign(interaction, self.campaign_id) is None:
            return
        await _edit(interaction, LATER_DONE)


class StayingUndoButton(discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]], template=_UNDO):
    """↩️ Undo of "Left the party": the stretch is open again and the question asked again."""

    def __init__(
        self, campaign_id: str, entity_id: str, batch: int, row: int | None = None
    ) -> None:
        super().__init__(
            discord.ui.Button(
                label=UNDO, style=discord.ButtonStyle.secondary, row=row,
                custom_id=f"dmbot:pleaveu:{campaign_id}:{entity_id}:{batch}",
            )
        )  # fmt: skip
        self.campaign_id, self.entity_id, self.batch = campaign_id, entity_id, batch

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> StayingUndoButton:
        return cls(match["campaign"], match["entity"], int(match["batch"]))

    async def callback(self, interaction: discord.Interaction) -> Any:
        await _answer_first(interaction, in_place=True)
        bot = _bot(interaction)
        campaign = await _dm_campaign(interaction, self.campaign_id)
        if campaign is None or bot.memory is None:
            return
        name = "the character"  # (until it is read, so the refusal below always has one)
        try:
            name = await _name(bot, campaign, self.entity_id)
            await bot.memory.undo(campaign.guild_id, campaign.id, self.batch)
        except (TooLateToUndo, MemoryRuleError):
            await _edit(interaction, CANT_UNDO.format(name=name))
            return
        except Exception as exc:
            log.warning("Undoing a staying answer failed (%s)", type(exc).__name__)
            await _edit(interaction, FAILED)
            return
        log.info("A DM undid a staying answer")
        # (the question waits again: drawn again with its own words and buttons, ruling S)
        text, rest = await _after_answer(bot, campaign, self.entity_id, UNDONE.format(name=name))
        await _edit(interaction, text, rest)


class LeavesOpenButton(discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]], template=_OPEN):
    """🧭 Still in the party?, from ❓ Questions for you: each waiting goodbye or death question
    again, privately, for a DM only. One not sent yet goes now, unless a session is running."""

    def __init__(self, campaign_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label=OPEN_LABEL, style=discord.ButtonStyle.primary,
                custom_id=f"dmbot:pleaveo:{campaign_id}",
            )
        )  # fmt: skip
        self.campaign_id = campaign_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> LeavesOpenButton:
        return cls(match["campaign"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        await interaction.response.defer(ephemeral=True, thinking=True)
        bot = _bot(interaction)
        campaign = await _dm_campaign(interaction, self.campaign_id)
        if campaign is None or bot.memory is None:
            return
        g, c = campaign.guild_id, campaign.id
        try:
            # (one not sent yet is claimed here and shown in this reply only, never sent twice)
            # (both kinds, a death and a goodbye, each its own message, as the sender does)
            sent = len(await bot.memory.staying_questions_to_send(g, c))
            sent += len(await bot.memory.staying_questions_to_send(g, c))
            groups = await bot.memory.staying_question_groups_waiting(g, c)
            waiting, _ = await bot.memory.staying_questions_waiting(g, c)
            drawn = [await waiting_in(bot, campaign, group) for group in groups[:5]]
            shown = [d for d in drawn if d is not None]
        except Exception as exc:
            log.warning("Staying questions not shown (%s)", type(exc).__name__)
            await _followup(interaction, FAILED)
            return
        if not shown:
            await _followup(interaction, WAIT_FOR_PLAY if waiting and not sent else NOTHING_LEFT)
            return
        for text, view in shown:
            await _followup(interaction, text, view)


DYNAMIC_ITEMS = (LeftButton, StaysButton, LaterButton, StayingUndoButton, LeavesOpenButton)

__all__ = [
    "DYNAMIC_ITEMS",
    "LeavesOpenButton",
    "line_for",
    "question_view",
    "send_staying_questions",
    "waiting_in",
]
