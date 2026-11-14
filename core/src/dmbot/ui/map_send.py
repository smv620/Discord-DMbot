"""Sending a map to DMbot yourself (#4372; docs/design/mapbot_layers.md section 3.1): `/dmbot map`,
or a picture sent to DMbot in a private chat.

Neither way has a transcript line to keep, so **nothing is stored before the sender's yes**: the
picture waits in memory (`maps/pending.py`, 15 minutes, one per person, a total cap) behind the
right-to-use question (`maps/words.py`). Order, as Supervisor ruled: the campaign choice first, only
when there is more than one, then the question; its Yes keeps the picture through the one picture
keeper and `add_map`, its No (or a time-out, or a restart) keeps nothing. Sending a map is the
person's own act, so there is no chat-capture consent check here (the yes is the agreement), the
same as a website upload. The audience starts `dm`. The command answers in place with replies only
the sender sees, so closed private messages don't matter. Counts-only logs.
"""

from __future__ import annotations

import contextlib
import logging
import re
import time
from typing import TYPE_CHECKING, Any

import discord
from discord import app_commands

from dmbot.consent import IMAGE_TERMS_VERSION
from dmbot.maps import words
from dmbot.maps.pending import Busy
from dmbot.maps.store import MapError
from dmbot.mentions import NO_PINGS
from dmbot.pictures import prepare
from dmbot.pictures.keeper import Context as PictureContext
from dmbot.ui.dmbot_commands import _answer_first, _bot, _tell, dmbot_group
from dmbot.ui.map_question import TakeBackButton

if TYPE_CHECKING:
    from dmbot.bot import DMBot

log = logging.getLogger(__name__)

TYPES = ("image/png", "image/jpeg", "image/webp", "image/gif")
MAX_BUTTONS = 5
MAX_OPTIONS = 25
SHORT = 25  # a name on a phone's menu is cut here

_PICK = r"dmbot:mapsc:(?P<guild>[0-9]{1,20}):(?P<campaign>[0-9a-f]{32}):(?P<code>[0-9a-f]{12})"
_MENU = r"dmbot:mapss:(?P<code>[0-9a-f]{12})"
_YES = r"dmbot:mapsy:(?P<guild>[0-9]{1,20}):(?P<campaign>[0-9a-f]{32}):(?P<code>[0-9a-f]{12})"
_NO = r"dmbot:mapsn:(?P<code>[0-9a-f]{12})"


def _short(name: str) -> str:
    return name if len(name) <= SHORT else name[: SHORT - 1] + "…"


def check_picture(content_type: str | None, size: int) -> str | None:
    """Why this file can't be kept, in plain words, or None. The limits are the picture keeper's
    own (`pictures/prepare.py`), checked before anything is asked or held."""
    if (content_type or "").split(";")[0].strip().lower() not in TYPES:
        return words.WRONG_TYPE
    if size > prepare.MAX_BYTES:
        return words.too_big(prepare.MAX_BYTES // (1024 * 1024))
    return None


async def campaigns_for(bot: DMBot, user_id: int, guild_id: int | None) -> list[tuple[int, Any]]:
    """The campaigns this person is in: one the person runs, or where DMbot has their yes to be
    recorded (`guild_id` limits it to one server; None looks in every server DMbot is in)."""
    guilds = [guild_id] if guild_id is not None else await bot.campaigns.guilds_with_campaigns()
    found: list[tuple[int, Any]] = []
    for gid in guilds:
        for campaign in await bot.campaigns.list_campaigns(gid):
            if user_id in campaign.dm_user_ids or bot.consent.has_consent(gid, user_id):
                found.append((gid, campaign))
    return found


async def offer(
    bot: DMBot,
    user_id: int,
    data: bytes,
    title: str,
    guild_id: int | None,
) -> tuple[str, discord.ui.View | None]:
    """Hold the picture and return what to show: the campaign choice, or the question itself when
    there is only one campaign. Nothing is stored. A picture DMbot can't keep is never asked
    about, and no campaign or a full memory is said in plain words."""
    try:
        await prepare.prepare_async(data)  # (decodes it once, to refuse a broken file up front)
    except prepare.Refused:
        return words.WRONG_TYPE, None
    campaigns = await campaigns_for(bot, user_id, guild_id)
    if not campaigns:
        return (words.NO_CAMPAIGN_HERE if guild_id is not None else words.NO_CAMPAIGN_PRIVATE), None
    try:
        held = bot.pending_maps.put(user_id, data, title)
    except Busy:
        log.info("A map was not held (busy)")
        return words.BUSY, None
    log.info("A map is waiting for its yes (%s bytes)", len(data))
    if len(campaigns) == 1:
        gid, campaign = campaigns[0]
        return _question(), _question_view(gid, campaign.id, held.code)
    return _choice(bot, campaigns, held.code, guild_id is None)


def _question() -> str:
    return words.question(from_chat=False)


def _question_view(guild_id: int, campaign_id: str, code: str) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(YesButton(guild_id, campaign_id, code))
    view.add_item(NoButton(code))
    return view


def _choice(
    bot: DMBot, campaigns: list[tuple[int, Any]], code: str, show_server: bool
) -> tuple[str, discord.ui.View]:
    """Buttons for a few campaigns, a menu for more (names cut at 25 characters, and the full
    names listed when any was cut; the first 25 only, and said so)."""

    def label(gid: int, name: str) -> str:
        guild = bot.get_guild(gid) if show_server else None
        return f"{name} · {guild.name}" if guild is not None else name

    shown = campaigns[:MAX_OPTIONS]
    text = words.WHICH_CAMPAIGN
    view = discord.ui.View(timeout=None)
    if len(shown) <= MAX_BUTTONS:
        for gid, campaign in shown:
            view.add_item(PickButton(gid, campaign.id, code, label(gid, campaign.name)))
    else:
        view.add_item(MenuSelect(code, [(g, c.id, label(g, c.name)) for g, c in shown]))
    cut = [label(g, c.name) for g, c in shown if len(label(g, c.name)) > SHORT]
    if cut and len(shown) > MAX_BUTTONS:
        text += "\n" + "\n".join(f"• {name}" for name in cut)
    if len(campaigns) > MAX_OPTIONS:
        text += "\n" + words.FIRST_25
    return text, view


async def _gone(interaction: discord.Interaction) -> None:
    with contextlib.suppress(discord.HTTPException):
        await interaction.edit_original_response(content=words.EXPIRED, view=None)


class PickButton(discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]], template=_PICK):
    def __init__(self, guild_id: int, campaign_id: str, code: str, label: str = "Pick") -> None:
        super().__init__(
            discord.ui.Button(
                label=_short(label),
                style=discord.ButtonStyle.primary,
                custom_id=f"dmbot:mapsc:{guild_id}:{campaign_id}:{code}",
            )
        )
        self.guild_id, self.campaign_id, self.code = guild_id, campaign_id, code

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> PickButton:
        return cls(int(match["guild"]), match["campaign"], match["code"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        await _chosen(interaction, self.guild_id, self.campaign_id, self.code)


class MenuSelect(discord.ui.DynamicItem[discord.ui.Select[discord.ui.View]], template=_MENU):
    def __init__(self, code: str, options: list[tuple[int, str, str]] | None = None) -> None:
        select: discord.ui.Select[discord.ui.View] = discord.ui.Select(
            placeholder="Pick a campaign",
            custom_id=f"dmbot:mapss:{code}",
            options=[
                discord.SelectOption(label=_short(name), value=f"{gid}:{cid}")
                for gid, cid, name in (options or [])
            ]
            or [discord.SelectOption(label="Pick a campaign", value="0:0")],
        )
        super().__init__(select)
        self.code = code

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> MenuSelect:
        return cls(match["code"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        data: Any = interaction.data
        values = (data or {}).get("values") or []
        match = re.fullmatch(r"([0-9]{1,20}):([0-9a-f]{32})", str(values[0])) if values else None
        if match is None:
            await _answer_first(interaction, in_place=True)
            await _gone(interaction)
            return
        await _chosen(interaction, int(match[1]), match[2], self.code)


async def _chosen(
    interaction: discord.Interaction, guild_id: int, campaign_id: str, code: str
) -> None:
    """A campaign was picked: show the right-to-use question for it. The sender, the held picture
    and that this person is still in that campaign are all read now, not trusted from the card."""
    bot = _bot(interaction)
    await _answer_first(interaction, in_place=True)
    if bot.pending_maps.get(interaction.user.id, code) is None:
        await _gone(interaction)
        return
    mine = await campaigns_for(bot, interaction.user.id, guild_id)
    if not any(c.id == campaign_id for _, c in mine):
        await _gone(interaction)
        return
    with contextlib.suppress(discord.HTTPException):
        await interaction.edit_original_response(
            content=_question(), view=_question_view(guild_id, campaign_id, code)
        )


class YesButton(discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]], template=_YES):
    def __init__(self, guild_id: int, campaign_id: str, code: str) -> None:
        super().__init__(
            discord.ui.Button(
                label=words.YES_LABEL,
                style=discord.ButtonStyle.success,
                custom_id=f"dmbot:mapsy:{guild_id}:{campaign_id}:{code}",
            )
        )
        self.guild_id, self.campaign_id, self.code = guild_id, campaign_id, code

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> YesButton:
        return cls(int(match["guild"]), match["campaign"], match["code"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        bot = _bot(interaction)
        await _answer_first(interaction, in_place=True)
        user_id = interaction.user.id
        held = bot.pending_maps.get(user_id, self.code)
        keeper, maps = bot.picture_keeper, bot.map_store
        if held is None:
            await _gone(interaction)
            return
        if keeper is None or maps is None:
            await _show(interaction, words.FAILED)
            return
        mine = await campaigns_for(bot, user_id, self.guild_id)
        campaign = next((c for _, c in mine if c.id == self.campaign_id), None)
        if campaign is None:
            await _gone(interaction)
            return
        await _show(interaction, words.SAVING)
        picture = None
        try:
            agreed = await bot.consent.agreed_under(self.guild_id, user_id)
            # (a sender with no recorded yes to pictures: this yes is their own agreement)
            granted, version = (
                agreed if agreed is not None else (int(time.time()), IMAGE_TERMS_VERSION)
            )
            got = await keeper.keep(
                PictureContext(
                    guild_id=self.guild_id, campaign_id=campaign.id, poster_id=user_id,
                    session_id="", line_id=None, message_id=0, description=held.title,
                    consent_version=max(version, IMAGE_TERMS_VERSION), right_granted_at=granted,
                ),
                held.data,
            )  # fmt: skip
            picture = got.picture
            if picture is None:
                await _show(interaction, words.BUSY if got.why_not == "full" else words.FAILED)
                return
            is_dm = user_id in campaign.dm_user_ids
            await maps.add_map(
                self.guild_id, campaign.id, picture_id=picture.id, uploader_id=user_id,
                uploader_is_dm=is_dm, right_to_use=True, kind="other", came_from="discord",
                title=held.title, audience="dm", storage_mb=bot.settings.picture_storage_mb,
            )  # fmt: skip
        except MapError as exc:  # (no room, already a map): the store's own words
            if picture is not None and got.new:
                await keeper.discard(self.guild_id, campaign.id, picture, unless_map=True)
            await _show(interaction, str(exc))
            return
        except Exception as exc:
            log.warning("A sent map failed to save (%s)", type(exc).__name__)
            await _show(interaction, words.FAILED)
            return
        bot.pending_maps.drop(user_id, self.code)
        log.info("A sent map was kept (%s)", "dm" if is_dm else "player")
        back = discord.ui.View(timeout=None)
        back.add_item(TakeBackButton(self.guild_id, picture.id))
        await _show(interaction, words.said_yes(is_dm, False), back)


class NoButton(discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]], template=_NO):
    def __init__(self, code: str) -> None:
        super().__init__(
            discord.ui.Button(
                label=words.NO_LABEL,
                style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:mapsn:{code}",
            )
        )
        self.code = code

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> NoButton:
        return cls(match["code"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        bot = _bot(interaction)
        await _answer_first(interaction, in_place=True)
        if not bot.pending_maps.drop(interaction.user.id, self.code):
            await _gone(interaction)
            return
        log.info("A sent map was not kept")
        await _show(interaction, words.said_no(False))


async def _show(
    interaction: discord.Interaction, text: str, view: discord.ui.View | None = None
) -> None:
    with contextlib.suppress(discord.HTTPException):
        await interaction.edit_original_response(content=text, view=view)


@dmbot_group.command(name="map", description=words.COMMAND_DESCRIPTION)
@app_commands.describe(picture=words.PICTURE_OPTION, name=words.NAME_OPTION)
async def dmbot_map(
    interaction: discord.Interaction, picture: discord.Attachment, name: str | None = None
) -> None:
    """Keep a map picture. The answer is private and in place (no private message needed)."""
    bot = _bot(interaction)
    await _answer_first(interaction)
    why = check_picture(picture.content_type, picture.size)
    if why is not None:
        await _tell(interaction, why)
        return
    if interaction.guild_id is None:
        return
    try:
        data = await picture.read()
    except discord.HTTPException:
        await _tell(interaction, words.FAILED)
        return
    text, view = await offer(
        bot, interaction.user.id, data, (name or "").strip()[:120], interaction.guild_id
    )
    await _tell(interaction, text, view=view)


async def from_private_message(bot: DMBot, message: discord.Message) -> bool:
    """A picture sent to DMbot in a private chat: True when this took it (the sidebar then says
    nothing more). Only one picture at a time, and only a real picture; anything else is left to
    the sidebar's own note."""
    if message.guild is not None or message.author.bot or len(message.attachments) != 1:
        return False
    attached = message.attachments[0]
    if attached.content_type is None or not attached.content_type.startswith("image/"):
        return False
    why = check_picture(attached.content_type, attached.size)
    if why is not None:
        with contextlib.suppress(discord.HTTPException):
            await message.channel.send(why, allowed_mentions=NO_PINGS)
        return True
    try:
        data = await attached.read()
        text, view = await offer(bot, message.author.id, data, "", None)
        if view is None:
            await message.channel.send(text, allowed_mentions=NO_PINGS)
        else:
            await message.channel.send(text, view=view, allowed_mentions=NO_PINGS)
    except discord.HTTPException:
        log.info("A private map could not be answered")
    return True
