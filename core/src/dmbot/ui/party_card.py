"""The party record on a player character's card (#4537, party knowledge slice 2a;
`docs/design/party_knowledge.md` §§3 and 6; the ux-critic's words, Supervisor's ruling on #4537).

- **The line:** "🧭 In the party since session 7 (<date>)." or "🧭 Left the party on <date>
  (retired).", the DM's alone (`party_line`, pure).
- **🚪 Has left the party:** a DM-only button on a character in the party now. It asks why
  (Moved on, Retired, Died, Away for a while, or ↩️ Back), closes the character's stretch as one
  change (`MemoryStore.leave_party`) and offers ↩️ Undo. Nothing the character knew is taken
  away: every row they hold stays (§6).
- **🤝 Back in the party** (slice 4a, §6): a DM-only button on a character who left. It opens a
  new stretch as one change (`MemoryStore.back_in_party`) with ↩️ Undo; the character keeps
  everything from before, and DMbot asks the DM privately, never during play, what the character
  knows of the time away (`party_join`, a question of kind "back"). A "Has left" pressed by
  mistake is still put right with its own Undo (Supervisor's ruling D): that erases the leave,
  while coming back keeps it on record.

The buttons carry the campaign, the character and the word (or the change's number), never a
name; who may press is read again on every press. The character is named, never with a pronoun
(DMbot can't know one; Supervisor's ruling C). Counts-only logs.
"""

from __future__ import annotations

import contextlib
import logging
import re
from collections.abc import Mapping, Sequence
from typing import Any

import discord

from dmbot.memory.models import MemoryRuleError, TooLateToUndo
from dmbot.memory.party_record import ENDED, Span
from dmbot.ui.dmbot_commands import _answer_first, _bot

log = logging.getLogger(__name__)

LEAVE_BUTTON = "🚪 Has left the party"
WHY = "**Why did {name} leave the party?** Nothing {name} knew is forgotten."
REASON_LABELS = {  # (button words; the stored word is the key, §3)
    "left": "Moved on",
    "retired": "Retired",
    "died": "Died",
    "away": "Away for a while",
}
REASON_WORDS = {
    "left": "moved on",
    "retired": "retired",
    "died": "died",
    "away": "away for a while",
}
BACK = "↩️ Back"
LEFT = (
    "✅ {name} has left the party ({why}). Nothing {name} knew is forgotten. Answers to the "
    "table now count only who is still in the party."
)
UNDO = "↩️ Undo"
UNDONE = "↩️ Undone. {name} is in the party again."
CANT_UNDO = "Too late to undo this one. Nothing changed."
BACK_ALREADY = "{name} is already back in the party. Nothing changed."
NOT_IN_PARTY = "{name} isn't in the party now, so nothing changed."
FAILED = "Couldn't do that just now. Nothing changed. Try again in a moment."
RETURN_BUTTON = "🤝 Back in the party"  # (not ↩️: that is an Undo here, the ux-critic)
RETURNED = (
    "✅ {name} is back in the party and still knows everything from before. DMbot will ask what "
    "{name} knows of the time away, never during play."
)
RETURNED_ALONE = "✅ {name} is back in the party and still knows everything from before."
RETURN_UNDONE = "↩️ Undone. {name} is out of the party again."
RETURN_CANT_UNDO = (
    "Too late to undo this one. Nothing changed. Use 🚪 Has left the party on {name}'s card "
    "instead."
)

_WHY = r"dmbot:pleave:(?P<campaign>[0-9a-f]{32}):(?P<entity>[0-9a-f]{32}):(?P<why>[a-z]{4,7})"
_BACK = r"dmbot:pleaveb:(?P<campaign>[0-9a-f]{32}):(?P<entity>[0-9a-f]{32})"
_RET_UNDO = (
    r"dmbot:pretu:(?P<campaign>[0-9a-f]{32}):(?P<entity>[0-9a-f]{32}):(?P<batch>[0-9]{1,18})"
)
_UNDO = r"dmbot:pleaveu:(?P<campaign>[0-9a-f]{32}):(?P<entity>[0-9a-f]{32}):(?P<batch>[0-9]{1,18})"


def party_line(spans: Sequence[Span], numbers: Mapping[int, int]) -> str:
    """The card's line from the character's stretches (oldest first) and the campaign's session
    numbers by start time: where they are now. "" with none. Pure."""
    if not spans:
        return ""
    last = spans[-1]
    if last.open:
        number = numbers.get(last.from_at)
        when = f"<t:{last.from_at}:D>"
        since = f"session {number} ({when})" if number else when
        return f"\n🧭 In the party since {since}."
    why = REASON_WORDS.get(last.ended or "", "")
    return f"\n🧭 Left the party on <t:{last.to_at}:D>" + (f" ({why})." if why else ".")


async def _dm_campaign(interaction: discord.Interaction, campaign_id: str) -> Any:
    """The campaign, for one of its DMs only (`family_ties._dm_campaign`, imported here: the name
    card imports this module, and that one imports the card)."""
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


async def _name(bot: Any, campaign: Any, entity_id: str) -> str:
    entity = await bot.memory.entity(campaign.guild_id, campaign.id, entity_id)
    return _md(entity.name) if entity is not None else "That character"


async def ask_why(interaction: discord.Interaction, campaign_id: str, entity_id: str) -> None:
    """The card's 🚪 Has left the party: why, in its place (a DM only)."""
    await _answer_first(interaction, in_place=True)
    bot = _bot(interaction)
    campaign = await _dm_campaign(interaction, campaign_id)
    if campaign is None or bot.memory is None:
        return
    name = await _name(bot, campaign, entity_id)
    view = discord.ui.View(timeout=None)
    for why in ENDED:
        view.add_item(WhyButton(campaign_id, entity_id, why))
    view.add_item(BackButton(campaign_id, entity_id))
    await _edit(interaction, WHY.format(name=name), view)


class WhyButton(discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]], template=_WHY):
    """One reason: closes the character's stretch, one change with its ↩️ Undo."""

    def __init__(self, campaign_id: str, entity_id: str, why: str) -> None:
        super().__init__(
            discord.ui.Button(  # (all grey: no choice takes anything away)
                label=REASON_LABELS.get(why, why), style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:pleave:{campaign_id}:{entity_id}:{why}",
            )
        )  # fmt: skip
        self.campaign_id, self.entity_id, self.why = campaign_id, entity_id, why

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> WhyButton:
        return cls(match["campaign"], match["entity"], match["why"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        await _answer_first(interaction, in_place=True)
        bot = _bot(interaction)
        campaign = await _dm_campaign(interaction, self.campaign_id)  # (on every press)
        if campaign is None or bot.memory is None or self.why not in ENDED:
            return
        name = await _name(bot, campaign, self.entity_id)
        try:
            done = await bot.memory.leave_party(
                campaign.guild_id, campaign.id, self.entity_id, self.why
            )
        except MemoryRuleError:  # (not in the party now: left already, or no longer one)
            await _edit(interaction, NOT_IN_PARTY.format(name=name))
            return
        except Exception as exc:
            log.warning("Leaving the party failed (%s)", type(exc).__name__)
            await _edit(interaction, FAILED)
            return
        log.info("A character left the party (%s)", self.why)
        view = discord.ui.View(timeout=None)
        if done.batch is not None:
            view.add_item(LeaveUndoButton(self.campaign_id, self.entity_id, done.batch))
        text = LEFT.format(name=name, why=REASON_WORDS[self.why])
        await _edit(interaction, text, view)


class BackButton(discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]], template=_BACK):
    """↩️ Back: the card again, nothing changed."""

    def __init__(self, campaign_id: str, entity_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label=BACK, style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:pleaveb:{campaign_id}:{entity_id}",
            )
        )  # fmt: skip
        self.campaign_id, self.entity_id = campaign_id, entity_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> BackButton:
        return cls(match["campaign"], match["entity"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        from dmbot.ui.name_card import show_card

        await _answer_first(interaction, in_place=True)
        if await _dm_campaign(interaction, self.campaign_id) is None:  # (a DM only, as the card)
            return
        await show_card(interaction, self.campaign_id, self.entity_id, replace=True)


class LeaveUndoButton(discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]], template=_UNDO):
    """↩️ Undo of a "Has left the party": the stretch is open again (a DM only, read again)."""

    def __init__(self, campaign_id: str, entity_id: str, batch: int) -> None:
        super().__init__(
            discord.ui.Button(
                label=UNDO, style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:pleaveu:{campaign_id}:{entity_id}:{batch}",
            )
        )  # fmt: skip
        self.campaign_id, self.entity_id, self.batch = campaign_id, entity_id, batch

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> LeaveUndoButton:
        return cls(match["campaign"], match["entity"], int(match["batch"]))

    async def callback(self, interaction: discord.Interaction) -> Any:
        await _answer_first(interaction, in_place=True)
        bot = _bot(interaction)
        campaign = await _dm_campaign(interaction, self.campaign_id)
        if campaign is None or bot.memory is None:
            return
        name = await _name(bot, campaign, self.entity_id)
        try:
            # (back in the party since by another change: this leave can't be put back over it)
            held = await bot.memory.party_spans(campaign.guild_id, campaign.id, self.entity_id)
            if any(s.open for s in held):
                await _edit(interaction, BACK_ALREADY.format(name=name))
                return
            await bot.memory.undo(campaign.guild_id, campaign.id, self.batch)
        except (TooLateToUndo, MemoryRuleError):
            await _edit(interaction, CANT_UNDO)
            return
        except Exception as exc:
            log.warning("Undoing a leave failed (%s)", type(exc).__name__)
            await _edit(interaction, FAILED)
            return
        log.info("A DM undid a character leaving the party")
        await _edit(interaction, UNDONE.format(name=name))


async def come_back(interaction: discord.Interaction, campaign_id: str, entity_id: str) -> None:
    """The card's 🤝 Back in the party (a DM only): the character is in the party again."""
    await _answer_first(interaction, in_place=True)
    bot = _bot(interaction)
    campaign = await _dm_campaign(interaction, campaign_id)  # (on every press)
    if campaign is None or bot.memory is None:
        return
    g, c = campaign.guild_id, campaign.id
    name = await _name(bot, campaign, entity_id)
    try:
        done = await bot.memory.back_in_party(g, c, entity_id)
        others = set(await bot.memory.current_party(g, c)) - {entity_id}
    except MemoryRuleError:  # (in the party already: pressed twice, or by another DM)
        await _edit(interaction, BACK_ALREADY.format(name=name))
        return
    except Exception as exc:
        log.warning("Coming back to the party failed (%s)", type(exc).__name__)
        await _edit(interaction, FAILED)
        return
    log.info("A character came back to the party")
    view = discord.ui.View(timeout=None)
    if done.batch is not None:
        view.add_item(ReturnUndoButton(campaign_id, entity_id, done.batch))
    # (the question is asked only with someone else to catch up with, and only with the switch on)
    asked = others and getattr(bot.settings, "party_join_on", False)
    text = (RETURNED if asked else RETURNED_ALONE).format(name=name)
    await _edit(interaction, text, view)


class ReturnUndoButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]], template=_RET_UNDO
):
    """↩️ Undo of a "Back in the party": the stretch goes, and a question about the time away
    that still waits goes with it (`join_questions.undo`)."""

    def __init__(self, campaign_id: str, entity_id: str, batch: int) -> None:
        super().__init__(
            discord.ui.Button(
                label=UNDO, style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:pretu:{campaign_id}:{entity_id}:{batch}",
            )
        )  # fmt: skip
        self.campaign_id, self.entity_id, self.batch = campaign_id, entity_id, batch

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> ReturnUndoButton:
        return cls(match["campaign"], match["entity"], int(match["batch"]))

    async def callback(self, interaction: discord.Interaction) -> Any:
        await _answer_first(interaction, in_place=True)
        bot = _bot(interaction)
        campaign = await _dm_campaign(interaction, self.campaign_id)
        if campaign is None or bot.memory is None:
            return
        name = await _name(bot, campaign, self.entity_id)
        try:
            await bot.memory.undo(campaign.guild_id, campaign.id, self.batch)
        except (TooLateToUndo, MemoryRuleError):
            await _edit(interaction, RETURN_CANT_UNDO.format(name=name))
            return
        except Exception as exc:
            log.warning("Undoing a return failed (%s)", type(exc).__name__)
            await _edit(interaction, FAILED)
            return
        log.info("A DM undid a character coming back to the party")
        await _edit(interaction, RETURN_UNDONE.format(name=name))


DYNAMIC_ITEMS = (WhyButton, BackButton, LeaveUndoButton, ReturnUndoButton)

__all__ = [
    "DYNAMIC_ITEMS",
    "LEAVE_BUTTON",
    "RETURN_BUTTON",
    "ask_why",
    "come_back",
    "party_line",
]
