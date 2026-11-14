"""The right-to-use question for a map (MapBot slice 3b-1, #4344; docs/design/mapbot_layers.md
section 3.3): when a picture posted in the table's chat is found to be a map, its poster is asked,
once, in their private chat with DMbot, "Is this a map you're allowed to use and save in this
campaign?". DMbot can't check, so it goes by the answer.

- **Yes** keeps the picture as a map (`MapStore.add_map`): a DM's yes also lets DMbot use it; a
  player's yes keeps it and waits for a DM (3b-2). Who said yes and when is on the map.
- **No** takes the picture back (it was kept when it was posted): only its transcript line stays.
- **Not answered:** nothing changes; it stays a picture as any picture in the chat is.

The buttons carry the server and the picture's number. Only the picture's own poster may answer,
read from the database on every press, so a made-up number or a forwarded message finds nothing.
The words are in `maps/words.py` (shared with the website). Counts-only logs."""

from __future__ import annotations

import contextlib
import logging
import re
from typing import Any

import discord

from dmbot.maps import words
from dmbot.maps.store import MapError
from dmbot.ui.dmbot_commands import _answer_first, _bot

log = logging.getLogger(__name__)

_YES = r"dmbot:mapqy:(?P<guild>[0-9]{1,20}):(?P<id>[0-9]{1,18})"
_NO = r"dmbot:mapqn:(?P<guild>[0-9]{1,20}):(?P<id>[0-9]{1,18})"


async def _finish(interaction: discord.Interaction, text: str) -> None:
    """Replace the question with its outcome (no buttons left to press twice)."""
    with contextlib.suppress(discord.HTTPException):
        await interaction.edit_original_response(content=text, view=None)


async def _answer(
    interaction: discord.Interaction, guild_id: int, picture_id: int, yes: bool
) -> None:
    bot = _bot(interaction)
    await _answer_first(interaction, in_place=True)
    store, keeper, maps = bot.picture_store, bot.picture_keeper, bot.map_store
    if store is None or keeper is None or maps is None:
        await _finish(interaction, words.FAILED)
        return
    try:
        picture = await store.by_id(guild_id, picture_id)
        if picture is None or picture.gone:
            await _finish(interaction, words.GONE)
            return
        if interaction.user.id != picture.poster_id:
            # (the card was forwarded, or the number made up): the question stays for its owner
            await interaction.followup.send(words.NOT_YOURS, ephemeral=True)
            return
        campaign = await bot.campaigns.get(guild_id, picture.campaign_id)
        if campaign is None:
            await _finish(interaction, words.GONE)
            return
        # A map posted in the table's chat has a message; the question is only asked for those.
        from_chat = picture.message_id != 0
        if yes:
            if not bot.consent.has_image_consent(guild_id, interaction.user.id):
                # (they stopped, or withdrew their yes to pictures, since the question was sent)
                await _finish(interaction, words.NO_CONSENT)
                return
            is_dm = interaction.user.id in campaign.dm_user_ids
            await maps.add_map(
                guild_id, campaign.id, picture_id=picture.id, uploader_id=interaction.user.id,
                uploader_is_dm=is_dm, right_to_use=True, kind="other", came_from="discord",
                audience="table" if from_chat else "dm", storage_mb=bot.settings.picture_storage_mb,
            )  # fmt: skip
            log.info("A map was kept (%s)", "dm" if is_dm else "player")
            await _finish(interaction, words.said_yes(is_dm, from_chat))
            return
        # (a yes pressed a moment before keeps the map: the store leaves it, under its own lock)
        if not await keeper.discard(guild_id, campaign.id, picture, unless_map=True):
            await _finish(interaction, words.ANSWERED)
            return
        log.info("A map was not kept")
        await _finish(interaction, words.said_no(from_chat))
    except MapError as exc:  # (the store's own words: no room, already a map)
        await _finish(interaction, str(exc))
    except Exception as exc:
        log.warning("A map question's answer failed (%s)", type(exc).__name__)
        await _finish(interaction, words.FAILED)


class YesButton(discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]], template=_YES):
    def __init__(self, guild_id: int, picture_id: int) -> None:
        super().__init__(
            discord.ui.Button(
                label=words.YES_LABEL,
                style=discord.ButtonStyle.success,
                custom_id=f"dmbot:mapqy:{guild_id}:{picture_id}",
            )
        )
        self.guild_id, self.picture_id = guild_id, picture_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> YesButton:
        return cls(int(match["guild"]), int(match["id"]))

    async def callback(self, interaction: discord.Interaction) -> Any:
        await _answer(interaction, self.guild_id, self.picture_id, True)


class NoButton(discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]], template=_NO):
    def __init__(self, guild_id: int, picture_id: int) -> None:
        super().__init__(
            discord.ui.Button(
                label=words.NO_LABEL,
                style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:mapqn:{guild_id}:{picture_id}",
            )
        )
        self.guild_id, self.picture_id = guild_id, picture_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> NoButton:
        return cls(int(match["guild"]), int(match["id"]))

    async def callback(self, interaction: discord.Interaction) -> Any:
        await _answer(interaction, self.guild_id, self.picture_id, False)


def view(guild_id: int, picture_id: int) -> discord.ui.View:
    out = discord.ui.View(timeout=None)
    out.add_item(YesButton(guild_id, picture_id))
    out.add_item(NoButton(guild_id, picture_id))
    return out


async def ask(bot: Any, guild_id: int, campaign: Any, picture: Any) -> None:
    """Send the question to the picture's poster, once, in private. When their private messages
    are closed, the DMs are told so, and the map is simply not used. Never raises."""
    user: Any = None
    try:
        user = await bot.fetch_user(picture.poster_id)
        text = f"❓ **DMbot has a question** · {discord.utils.escape_markdown(campaign.name)}\n"
        text += words.question(from_chat=picture.message_id != 0)
        await user.send(
            text, view=view(guild_id, picture.id), allowed_mentions=discord.AllowedMentions.none()
        )
    except (discord.Forbidden, discord.NotFound):
        from dmbot.dm_screen import private_note

        name = discord.utils.escape_markdown(getattr(user, "display_name", None) or "the player")
        with contextlib.suppress(Exception):
            await private_note.send(
                bot, campaign, words.dms_could_not_ask(name), lambda: discord.ui.View()
            )
    except Exception as exc:
        log.warning("A map question could not be sent (%s)", type(exc).__name__)
