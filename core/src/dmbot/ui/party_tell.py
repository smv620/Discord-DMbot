"""Catching one party member up from a card (#4537, party knowledge slice 4b;
`docs/design/party_knowledge.md` §5, "Later, a little at a time"; Supervisor's ruling on #4537,
2026-11-19; the ux-critic's words).

- **On a name card** (a person, a place, a quest…; DM only): when a current member is missing
  something on the card that every other member knows, the card says so in one line and offers
  "📖 Catch up: Mira" (one such member) or "📖 Catch up…" (a choice of them, with how many things
  each). The design calls it "Tell Mira"; the button doesn't say "tell", because DMbot does send
  messages to players and this sends nothing (the ux-critic).
- **Pressing it** gives that member everything on the card the rest of the party knows
  (`MemoryStore.tell_member`): one change with one ↩️ Undo, never what only some know, a backstory
  or a hidden row. Nothing is sent to the player.

The buttons carry the campaign, the member, the card's entry and the change's number, never a name;
who may press is read again on every press, and only a campaign's DMs may. A character is named,
never with a pronoun (ruling C). Counts-only logs.
"""

from __future__ import annotations

import contextlib
import logging
import re
from collections.abc import Sequence
from functools import partial
from typing import Any

import discord

from dmbot.memory.models import MemoryRuleError, TooLateToUndo
from dmbot.ui.dmbot_commands import _answer_first, _bot, _Button
from dmbot.ui.party_join import names_text, things

log = logging.getLogger(__name__)

LABEL_MAX = 25  # (phones cut a button's label at about this: the name is cut, never the rest)
CATCH_UP_ONE = "📖 Catch up: {name}"
CATCH_UP_MANY = "📖 Catch up…"
MISSING_ONE = "{name} is missing {things} on this card that the rest of the party knows."
MISSING_MANY = "{names} are missing things on this card that the rest of the party knows."
WHO = "**Catch up who?** Each gets everything on this card the rest of the party knows."
BACK = "↩️ Back"
DONE = (
    "✅ {name} now knows the {things} on this card the rest of the party knew. Nothing was sent to "
    "{name}'s player."
)
UNDO = "↩️ Undo"
UNDONE = "↩️ Undone. {name} no longer knows those {things}."
CANT_UNDO = (
    "Too late to undo this one: some of it has changed since. To take something back from {name}, "
    "use 👁️ Who knows this? on it."
)
NOTHING_LEFT = (
    "{name} already knows everything the rest of the party knows here (another DM may have done "
    "it). Nothing changed."
)
NOT_IN_PARTY = "{name} isn't in the party now, so nothing changed."
FAILED = "Couldn't do that just now. Nothing changed. Try again in a moment."

# (the Undo's custom_id is at most 96 characters: two ids, a 12-digit change and a count, under
# Discord's 100)
_UNDO = (
    r"dmbot:ptellu:(?P<campaign>[0-9a-f]{32}):(?P<member>[0-9a-f]{32}):(?P<batch>[0-9]{1,12})"
    r":(?P<n>[0-9]{1,4})"
)


def _cut(name: str, room: int) -> str:
    """The name cut to `room` characters with "…", whole when it fits. Pure."""
    return name if len(name) <= room else name[: max(1, room - 1)].rstrip() + "…"


def one_label(name: str) -> str:
    """ "📖 Catch up: Mira", the name cut so the label fits a phone. Pure."""
    return CATCH_UP_ONE.format(name=_cut(name, LABEL_MAX - len(CATCH_UP_ONE.format(name=""))))


def pick_label(name: str, count: int) -> str:
    """ "Mira (6 things)": the name cut, never the count. Pure."""
    tail = f" ({things(count)})"
    return _cut(name, max(LABEL_MAX - len(tail), 8)) + tail


def card_line(missing: Sequence[tuple[str, int]]) -> str:
    """The card's line under the party's names, (name, how many) each: "" with nobody. Pure."""
    if not missing:
        return ""
    if len(missing) == 1:
        name, count = missing[0]
        return "\n" + MISSING_ONE.format(name=name, things=things(count))
    return "\n" + MISSING_MANY.format(names=names_text([n for n, _ in missing]))


async def _dm_campaign(interaction: discord.Interaction, campaign_id: str) -> Any:
    from dmbot.ui.family_ties import _dm_campaign as dm_campaign

    return await dm_campaign(interaction, campaign_id)


def _md(text: str) -> str:
    from dmbot.ui.names import _md as md

    return md(text)


async def _edit(interaction: discord.Interaction, text: str, view: Any = None) -> None:
    with contextlib.suppress(discord.HTTPException):
        await interaction.edit_original_response(
            content=text, view=view, allowed_mentions=discord.AllowedMentions.none()
        )


async def _aside(interaction: discord.Interaction, text: str) -> None:
    """A private note that leaves the pressed message as it is (its ↩️ Undo included)."""
    with contextlib.suppress(discord.HTTPException):
        await interaction.followup.send(
            text, ephemeral=True, allowed_mentions=discord.AllowedMentions.none()
        )


async def _name(memory: Any, guild_id: int, campaign_id: str, entity_id: str) -> str:
    entity = await memory.entity(guild_id, campaign_id, entity_id)
    return _md(entity.name) if entity is not None else "That character"


async def catch_up(
    interaction: discord.Interaction, campaign_id: str, member_id: str, entity_id: str
) -> None:
    """One member caught up on the card's entry (a DM only, read again on the press)."""
    await _answer_first(interaction, in_place=True)
    bot = _bot(interaction)
    campaign = await _dm_campaign(interaction, campaign_id)
    if campaign is None or bot.memory is None:
        return
    g, c = campaign.guild_id, campaign.id
    name = await _name(bot.memory, g, c, member_id)
    try:
        done = await bot.memory.tell_member(g, c, member_id, entity_id)
    except MemoryRuleError:
        await _aside(interaction, NOT_IN_PARTY.format(name=name))
        return
    except Exception as exc:
        log.warning("Catching up a party member failed (%s)", type(exc).__name__)
        await _edit(interaction, FAILED)
        return
    if not done.value or done.batch is None:  # (a second press: the first one's Undo stays shown)
        await _aside(interaction, NOTHING_LEFT.format(name=name))
        return
    log.info("A DM caught a party member up from a card (%d)", done.value)
    view = discord.ui.View(timeout=None)
    view.add_item(TellUndoButton(campaign_id, member_id, done.batch, done.value))
    await _edit(interaction, DONE.format(name=name, things=things(done.value)), view)


async def choose(
    interaction: discord.Interaction,
    campaign_id: str,
    entity_id: str,
    missing: Sequence[tuple[str, str, int]],
) -> None:
    """ "📖 Catch up…": one button per member missing something (id, name, how many), and ↩️ Back
    to the card. The press is the DM's own, read again by `catch_up`."""
    from dmbot.ui.name_card import show_card

    await _answer_first(interaction, in_place=True)
    if await _dm_campaign(interaction, campaign_id) is None:
        return
    view = discord.ui.View(timeout=600)
    for member_id, name, count in missing[:24]:  # (Discord's 25 buttons, ↩️ Back last)
        press = partial(_pick, campaign_id=campaign_id, member_id=member_id, entity_id=entity_id)
        label = pick_label(name, count)
        view.add_item(_Button(press, label=label, style=discord.ButtonStyle.secondary))

    async def to_card(i: discord.Interaction) -> None:
        await show_card(i, campaign_id, entity_id, replace=True)

    view.add_item(_Button(to_card, label=BACK, style=discord.ButtonStyle.secondary))
    await _edit(interaction, WHO, view)


async def _pick(
    interaction: discord.Interaction, *, campaign_id: str, member_id: str, entity_id: str
) -> None:
    await catch_up(interaction, campaign_id, member_id, entity_id)


class TellUndoButton(discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]], template=_UNDO):
    """↩️ Undo of a catch-up (a DM only, read again)."""

    def __init__(self, campaign_id: str, member_id: str, batch: int, count: int) -> None:
        super().__init__(
            discord.ui.Button(
                label=UNDO, style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:ptellu:{campaign_id}:{member_id}:{batch}:{min(count, 9999)}",
            )
        )  # fmt: skip
        self.campaign_id, self.member_id = campaign_id, member_id
        self.batch, self.count = batch, count

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> TellUndoButton:
        return cls(match["campaign"], match["member"], int(match["batch"]), int(match["n"]))

    async def callback(self, interaction: discord.Interaction) -> Any:
        await _answer_first(interaction, in_place=True)
        bot = _bot(interaction)
        campaign = await _dm_campaign(interaction, self.campaign_id)
        if campaign is None or bot.memory is None:
            return
        g, c = campaign.guild_id, campaign.id
        name = await _name(bot.memory, g, c, self.member_id)
        try:
            await bot.memory.undo(g, c, self.batch)
        except (TooLateToUndo, MemoryRuleError):
            await _edit(interaction, CANT_UNDO.format(name=name))
            return
        except Exception as exc:
            log.warning("Undoing a catch-up failed (%s)", type(exc).__name__)
            await _edit(interaction, FAILED)
            return
        log.info("A DM undid a catch-up")
        await _edit(interaction, UNDONE.format(name=name, things=things(self.count)))


DYNAMIC_ITEMS = (TellUndoButton,)

__all__ = [
    "DYNAMIC_ITEMS",
    "card_line",
    "catch_up",
    "choose",
    "one_label",
    "pick_label",
]
