"""The DM's side of the map question (MapBot slice 3b-2a, #4344; docs/design/mapbot_layers.md
section 3.3): a map a player kept waits for a DM's yes to using it. "🗺️ Maps waiting for your yes" is
a line in the Questions message with a button that opens the first waiting map in a private card:
"A map from <player>: use it?" with Yes, No and Remove. The first DM's answer stands; a second DM
is told who answered. A no keeps the picture and DMbot never reads it as a map; Remove deletes it.

Only a DM of the campaign may answer, read from the database on every press. The buttons carry the
campaign and the map, never who may press. The words are in `maps/words.py`; counts-only logs."""

from __future__ import annotations

import contextlib
import logging
import re
from typing import Any

import discord

from dmbot.maps import words
from dmbot.maps.store import GONE, AlreadyAnswered, MapError, Viewer
from dmbot.memory.models import MemoryRuleError, TooLateToUndo
from dmbot.ui.dmbot_commands import _answer_first, _bot, _tell
from dmbot.ui.family_ties import _dm_campaign

log = logging.getLogger(__name__)

_OPEN = r"dmbot:mapsq:open:(?P<campaign>[0-9a-f]{32})"
_ACT = r"dmbot:mapsq:(?P<act>[ynr]):(?P<campaign>[0-9a-f]{32}):(?P<id>[0-9a-f]{32})"
_UNDO = r"dmbot:mapsq:u:(?P<campaign>[0-9a-f]{32}):(?P<batch>[0-9]{1,18})"
LABELS = {"y": words.USE_YES_LABEL, "n": words.USE_NO_LABEL, "r": words.REMOVE_LABEL}
STYLES = {
    "y": discord.ButtonStyle.success,
    "n": discord.ButtonStyle.secondary,
    "r": discord.ButtonStyle.danger,  # last, red: its label says what it does
}


def _name(bot: Any, guild_id: int, user_id: int) -> str:
    """A person's server name for a line of text, or a plain word when it is not known."""
    guild = bot.get_guild(guild_id)
    member = guild.get_member(user_id) if guild is not None else None
    name = getattr(member, "display_name", None)
    return discord.utils.escape_markdown(
        discord.utils.escape_mentions(str(name or "a player")[:60])
    )


async def _card(bot: Any, campaign: Any, waiting: Any) -> tuple[str, discord.ui.View]:
    """The question for one waiting map, with its three buttons."""
    player = _name(bot, campaign.guild_id, waiting.right_by)
    text = f"**{words.dm_title(player)}**\n{words.dm_body(player)}"
    picture = await bot.picture_store.get(campaign.guild_id, campaign.id, waiting.picture_id)
    if picture is not None and not picture.gone and bot.settings.site_url:
        text += f"\n🖼️ {bot.settings.site_url}/pictures/{picture.code}"
    out = discord.ui.View(timeout=None)
    for act in ("y", "n", "r"):
        out.add_item(ActButton(act, campaign.id, waiting.id))
    return text, out


class ActButton(discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]], template=_ACT):
    def __init__(self, act: str, campaign_id: str, map_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label=LABELS[act],
                style=STYLES[act],
                custom_id=f"dmbot:mapsq:{act}:{campaign_id}:{map_id}",
            )
        )
        self.act, self.campaign_id, self.map_id = act, campaign_id, map_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> ActButton:
        return cls(match["act"], match["campaign"], match["id"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        await _answer(interaction, self.act, self.campaign_id, self.map_id)


class UndoButton(discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]], template=_UNDO):
    """↩️ Undo after a DM's yes, no or remove: one change in the change log (rule 8). Works after a
    restart (the campaign and the batch are in the button's ID); a DM only, read on every press."""

    def __init__(self, campaign_id: str, batch: int) -> None:
        super().__init__(
            discord.ui.Button(
                label=words.UNDO_LABEL,
                style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:mapsq:u:{campaign_id}:{batch}",
            )
        )
        self.campaign_id, self.batch = campaign_id, batch

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> UndoButton:
        return cls(match["campaign"], int(match["batch"]))

    async def callback(self, interaction: discord.Interaction) -> Any:
        await _answer_first(interaction, in_place=True)
        campaign = await _dm_campaign(interaction, self.campaign_id)
        memory = _bot(interaction).memory
        if campaign is None or memory is None:
            return
        try:
            await memory.undo(campaign.guild_id, campaign.id, self.batch)
        except TooLateToUndo as exc:  # (older than Undo works for: its own words)
            await _edit(interaction, str(exc))
            return
        except MemoryRuleError:
            await _edit(interaction, words.UNDO_FAILED)
            return
        except Exception as exc:
            log.warning("A map undo failed (%s)", type(exc).__name__)
            await _edit(interaction, words.FAILED)
            return
        log.info("A DM undid a map answer")
        await _show_next(interaction, campaign, edit=True, note=words.UNDONE)


class OpenButton(discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]], template=_OPEN):
    """🗺️ Maps, from the Questions message; works after a restart."""

    def __init__(self, campaign_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label="🗺️ Maps",
                style=discord.ButtonStyle.primary,
                custom_id=f"dmbot:mapsq:open:{campaign_id}",
            )
        )
        self.campaign_id = campaign_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> OpenButton:
        return cls(match["campaign"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        # A new private message (the Questions message is never replaced), answered first.
        await interaction.response.defer(ephemeral=True, thinking=True)
        campaign = await _dm_campaign(interaction, self.campaign_id)
        if campaign is None:
            return
        await _show_next(interaction, campaign, edit=False)


async def _show_next(
    interaction: discord.Interaction,
    campaign: Any,
    *,
    edit: bool,
    note: str = "",
    undo: int | None = None,
) -> None:
    """The first map still waiting, as a card; or "nothing waiting". `edit`: replace the card the
    DM pressed on (after an answer), else send a new private message."""
    bot = _bot(interaction)
    maps = bot.map_store
    waiting = await maps.waiting_for_a_dm(campaign.guild_id, campaign.id) if maps else []
    lead = f"{note}\n\n" if note else ""
    if not waiting:
        if edit:
            closing = discord.ui.View(timeout=None)
            if undo is not None:
                closing.add_item(UndoButton(campaign.id, undo))
            with contextlib.suppress(discord.HTTPException):
                await interaction.edit_original_response(
                    content=f"{lead}{words.NOTHING_WAITING}", view=closing if undo else None
                )
        else:
            await _tell(interaction, words.NOTHING_WAITING)
        return
    text, view = await _card(bot, campaign, waiting[0])
    text = lead + text
    if undo is not None:  # the answer just given can be taken back from this card
        view.add_item(UndoButton(campaign.id, undo))
    if edit:
        with contextlib.suppress(discord.HTTPException):
            await interaction.edit_original_response(content=text, view=view)
    else:
        await interaction.followup.send(text, view=view, ephemeral=True)


async def _answer(
    interaction: discord.Interaction, act: str, campaign_id: str, map_id: str
) -> None:
    bot = _bot(interaction)
    await _answer_first(interaction, in_place=True)
    maps = bot.map_store
    campaign = await _dm_campaign(interaction, campaign_id)  # (a DM, read now, not from the button)
    if campaign is None or maps is None:
        return
    try:
        found = await maps.get(campaign.guild_id, campaign.id, map_id, Viewer(dm=True))
        if found is None:
            await _edit(interaction, GONE)
            return
        if found.use_ok is not None and act != "r":  # the first answer stands (it can still go)
            dm = _name(bot, campaign.guild_id, found.use_by or 0)
            await _edit(interaction, words.answered_by(dm, found.use_ok))
            await _show_next(interaction, campaign, edit=False)
            return
        if act == "r":
            # Only the map goes (the picture stays a picture): one undoable change, so no
            # confirmation is needed before a player's picture is deleted (rule 8; #4373).
            written = await maps.remove_map(campaign.guild_id, campaign.id, map_id)
            log.info("A map was removed by a DM")
            note = words.done_removed()
        else:
            written = await maps.answer_use(
                campaign.guild_id, campaign.id, map_id, dm_id=interaction.user.id,
                yes=act == "y", first_only=True,
            )  # fmt: skip
            log.info("A DM answered a map question (%s)", "yes" if act == "y" else "no")
            note = words.done_yes() if act == "y" else words.done_no()
    except AlreadyAnswered as late:  # (the first answer stands, whoever pressed a moment later)
        dm = _name(bot, campaign.guild_id, late.map.use_by or 0)
        await _edit(interaction, words.answered_by(dm, bool(late.map.use_ok)))
        await _show_next(interaction, campaign, edit=False)
        return
    except MapError as exc:
        await _edit(interaction, str(exc))
        return
    except Exception as exc:
        log.warning("A map answer failed (%s)", type(exc).__name__)
        await _edit(interaction, words.FAILED)
        return
    await _show_next(interaction, campaign, edit=True, note=note, undo=written.batch)


async def _edit(interaction: discord.Interaction, text: str) -> None:
    with contextlib.suppress(discord.HTTPException):
        await interaction.edit_original_response(content=text, view=None)
