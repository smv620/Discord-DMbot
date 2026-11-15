"""`/dmbot maps`: a DM shows a kept map to the table, or hides it again (#4414; the website's Maps
page has the same buttons, `maps-show`). Level with the website: the same words (`maps/words.py`),
the same warning before showing ("Players will see everything on this map."), and one Undo.

The reply is private. A DM picks the campaign (only when they run more than one here), then a map
(the ones DMbot may use), then the one button that fits: Show to the table (with its warning and a
yes) or Hide from the table. The change is one entry in the change log (`MapStore.set_audience`),
so ↩ Undo takes it back. Every press reads the campaign and the DM again from the database: the
buttons carry the campaign and the map, never who may press. Counts-only logs."""

from __future__ import annotations

import contextlib
import logging
import re
from typing import Any, ClassVar

import discord
from discord import app_commands

from dmbot.maps import words
from dmbot.maps.store import DM_ONLY, GONE, TABLE, MapError, Viewer
from dmbot.memory.models import MemoryRuleError, TooLateToUndo
from dmbot.ui.dmbot_commands import _answer_first, _bot, _tell, dmbot_group
from dmbot.ui.family_ties import _dm_campaign

log = logging.getLogger(__name__)

MAX_OPTIONS = 25
SHORT = 100  # a select option's label is cut here (Discord's limit)

_CAMPAIGNS = r"dmbot:mapsw:c"
_MAPS = r"dmbot:mapsw:m:(?P<campaign>[0-9a-f]{32})"
_ACT = r"dmbot:mapsw:(?P<act>[shy]):(?P<campaign>[0-9a-f]{32}):(?P<id>[0-9a-f]{32})"
_BACK = r"dmbot:mapsw:b:(?P<campaign>[0-9a-f]{32})"
_UNDO = r"dmbot:mapsw:u:(?P<campaign>[0-9a-f]{32}):(?P<batch>[0-9]{1,18})"


def _title(title: str) -> str:
    """A map's name for a line of text or an option: markdown and pings made harmless."""
    clean = discord.utils.escape_markdown(discord.utils.escape_mentions(title.strip()))
    return clean[:SHORT] or words.UNTITLED


def _seen(audience: str) -> str:
    return words.SEEN_NOW_TABLE if audience == TABLE else words.SEEN_BY_DMS


async def _edit(
    interaction: discord.Interaction, text: str, view: discord.ui.View | None = None
) -> None:
    with contextlib.suppress(discord.HTTPException):
        await interaction.edit_original_response(
            content=text, view=view, allowed_mentions=discord.AllowedMentions.none()
        )


class CampaignSelect(
    discord.ui.DynamicItem[discord.ui.Select[discord.ui.View]], template=_CAMPAIGNS
):
    def __init__(self, options: list[tuple[str, str]] | None = None) -> None:
        select: discord.ui.Select[discord.ui.View] = discord.ui.Select(
            placeholder=words.PICK_CAMPAIGN,
            custom_id="dmbot:mapsw:c",
            options=[
                discord.SelectOption(label=_title(name)[:SHORT], value=cid)
                for cid, name in (options or [])
            ]
            # (a menu needs one option even when rebuilt from its id after a restart)
            or [discord.SelectOption(label=words.PICK_CAMPAIGN, value="0")],
        )
        super().__init__(select)

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> CampaignSelect:
        return cls()

    async def callback(self, interaction: discord.Interaction) -> Any:
        await _answer_first(interaction, in_place=True)
        data: Any = interaction.data
        values = (data or {}).get("values") or []
        if not values or not re.fullmatch(r"[0-9a-f]{32}", str(values[0])):
            await _edit(interaction, words.GONE)
            return
        await _list(interaction, str(values[0]))


class MapSelect(discord.ui.DynamicItem[discord.ui.Select[discord.ui.View]], template=_MAPS):
    def __init__(self, campaign_id: str, options: list[tuple[str, str]] | None = None) -> None:
        select: discord.ui.Select[discord.ui.View] = discord.ui.Select(
            placeholder=words.PICK_MAP,
            custom_id=f"dmbot:mapsw:m:{campaign_id}",
            options=[
                discord.SelectOption(label=label[:SHORT], value=map_id)
                for map_id, label in (options or [])
            ]
            or [discord.SelectOption(label=words.PICK_MAP, value="0")],
        )
        super().__init__(select)
        self.campaign_id = campaign_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> MapSelect:
        return cls(match["campaign"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        await _answer_first(interaction, in_place=True)
        data: Any = interaction.data
        values = (data or {}).get("values") or []
        if not values or not re.fullmatch(r"[0-9a-f]{32}", str(values[0])):
            await _edit(interaction, words.GONE)
            return
        await _card(interaction, self.campaign_id, str(values[0]))


class ActButton(discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]], template=_ACT):
    """`s` Show to the table (asks first), `y` Yes, show it, `h` Hide from the table."""

    LABELS: ClassVar[dict[str, str]] = {
        "s": words.SHOW_LABEL,
        "y": words.SHOW_YES_LABEL,
        "h": words.HIDE_LABEL,
    }

    def __init__(self, act: str, campaign_id: str, map_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label=self.LABELS[act],
                style=discord.ButtonStyle.success if act == "y" else discord.ButtonStyle.primary,
                custom_id=f"dmbot:mapsw:{act}:{campaign_id}:{map_id}",
            )
        )
        self.act, self.campaign_id, self.map_id = act, campaign_id, map_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> ActButton:
        return cls(match["act"], match["campaign"], match["id"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        await _answer_first(interaction, in_place=True)
        if self.act == "s":
            await _ask(interaction, self.campaign_id, self.map_id)
        else:
            await _change(interaction, self.campaign_id, self.map_id, show=self.act == "y")


class BackButton(discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]], template=_BACK):
    def __init__(self, campaign_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label=words.BACK_LABEL,
                style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:mapsw:b:{campaign_id}",
            )
        )
        self.campaign_id = campaign_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> BackButton:
        return cls(match["campaign"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        await _answer_first(interaction, in_place=True)
        await _list(interaction, self.campaign_id)


class UndoButton(discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]], template=_UNDO):
    """↩ Undo of a show or hide: one change in the change log. Works after a restart (the
    campaign and the batch are in the button's ID); a DM only, read on every press."""

    def __init__(self, campaign_id: str, batch: int) -> None:
        super().__init__(
            discord.ui.Button(
                label=words.UNDO_LABEL,
                style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:mapsw:u:{campaign_id}:{batch}",
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
            log.warning("A map show or hide could not be undone (%s)", type(exc).__name__)
            await _edit(interaction, words.FAILED)
            return
        log.info("A DM undid a show or hide of a map")
        await _list(interaction, campaign.id, note=words.UNDONE)


async def _list(interaction: discord.Interaction, campaign_id: str, *, note: str = "") -> None:
    """The campaign's usable maps as a menu, with whether each is shown; or why there are none."""
    bot = _bot(interaction)
    campaign = await _dm_campaign(interaction, campaign_id)
    if campaign is None or bot.map_store is None:
        return
    found = await bot.map_store.maps(
        campaign.guild_id, campaign.id, Viewer(dm=True), usable_only=True
    )
    lead = f"{note}\n\n" if note else ""
    if not found:
        await _edit(interaction, f"{lead}{words.NO_MAPS}")
        return
    shown = found[:MAX_OPTIONS]
    options = [
        (m.id, f"{_title(m.title)} ({'shown' if m.audience == TABLE else 'DMs only'})")
        for m in shown
    ]
    out = discord.ui.View(timeout=None)
    out.add_item(MapSelect(campaign.id, options))
    more = f"\n{words.FIRST_25}" if len(found) > MAX_OPTIONS else ""
    await _edit(interaction, f"{lead}🗺️ **{_title(campaign.name)}**{more}", out)


async def _card(interaction: discord.Interaction, campaign_id: str, map_id: str, *, note: str = "",
                undo: int | None = None) -> None:  # fmt: skip
    """One map: who can see it now, and the one button that fits."""
    bot = _bot(interaction)
    campaign = await _dm_campaign(interaction, campaign_id)
    if campaign is None or bot.map_store is None:
        return
    found = await bot.map_store.get(campaign.guild_id, campaign.id, map_id, Viewer(dm=True))
    if found is None:
        await _edit(interaction, GONE)  # (the store's own words for a map that is gone)
        return
    out = discord.ui.View(timeout=None)
    if found.audience == TABLE:
        out.add_item(ActButton("h", campaign.id, found.id))
    else:
        out.add_item(ActButton("s", campaign.id, found.id))
    out.add_item(BackButton(campaign.id))
    if undo is not None:
        out.add_item(UndoButton(campaign.id, undo))
    lead = f"{note}\n" if note else ""
    await _edit(interaction, f"{lead}**{_title(found.title)}**\n{_seen(found.audience)}", out)


async def _ask(interaction: discord.Interaction, campaign_id: str, map_id: str) -> None:
    """The warning before showing: Yes, show it, or Back."""
    campaign = await _dm_campaign(interaction, campaign_id)
    if campaign is None:
        return
    out = discord.ui.View(timeout=None)
    out.add_item(ActButton("y", campaign.id, map_id))
    out.add_item(BackButton(campaign.id))
    await _edit(interaction, words.SHOW_SURE, out)


async def _change(
    interaction: discord.Interaction, campaign_id: str, map_id: str, *, show: bool
) -> None:
    bot = _bot(interaction)
    campaign = await _dm_campaign(interaction, campaign_id)
    if campaign is None or bot.map_store is None:
        return
    try:
        written = await bot.map_store.set_audience(
            campaign.guild_id, campaign.id, map_id, TABLE if show else DM_ONLY
        )
    except MapError as exc:  # (gone meanwhile: the store's own words)
        await _edit(interaction, str(exc))
        return
    except Exception as exc:
        log.warning("A map could not be shown or hidden (%s)", type(exc).__name__)
        await _edit(interaction, words.FAILED)
        return
    log.info("A DM %s a map", "showed" if show else "hid")
    await _card(
        interaction, campaign.id, map_id,
        note=words.NOW_SHOWN if show else words.NOW_HIDDEN, undo=written.batch,
    )  # fmt: skip


# (plain strings, not constants: the help index reads the command from the code)
@dmbot_group.command(name="maps", description="Show a map to the table, or hide it again")
@app_commands.guild_only()
async def dmbot_maps(interaction: discord.Interaction) -> None:
    """The campaign's maps, for a DM. The answer is private and in place."""
    bot = _bot(interaction)
    await _answer_first(interaction)
    guild = interaction.guild
    if guild is None or bot.map_store is None:
        await _tell(interaction, words.NOT_A_DM)
        return
    mine = [
        c
        for c in await bot.campaigns.list_campaigns(guild.id)
        if interaction.user.id in c.dm_user_ids
    ]
    if not mine:
        await _tell(interaction, words.NOT_A_DM)
        return
    if len(mine) == 1:
        found = await bot.map_store.maps(guild.id, mine[0].id, Viewer(dm=True), usable_only=True)
        if not found:
            await _tell(interaction, words.NO_MAPS)
            return
        options = [
            (m.id, f"{_title(m.title)} ({'shown' if m.audience == TABLE else 'DMs only'})")
            for m in found[:MAX_OPTIONS]
        ]
        menu = discord.ui.View(timeout=None)
        menu.add_item(MapSelect(mine[0].id, options))
        more = f"\n{words.FIRST_25}" if len(found) > MAX_OPTIONS else ""
        await _tell(interaction, f"🗺️ **{_title(mine[0].name)}**{more}", view=menu)
        return
    menu = discord.ui.View(timeout=None)
    menu.add_item(CampaignSelect([(c.id, c.name) for c in mine[:MAX_OPTIONS]]))
    await _tell(interaction, words.PICK_CAMPAIGN, view=menu)
