"""The recap's audio on the DM screen (#2621): said once when it is ready or has failed.

The DM approved a text that makes audio and went away; this is the line that brings them back. It
carries 🎧 Listen here (the files, sent privately to whoever pressed, who must be one of the
campaign's DMs), the link to the recap page, 📤 Post to the table (the same Post button as the
recap's line, which asks once and lets the bot's own sending check everything again), and Keep it,
don't post. A failure line has the page link and 🔁 Make the audio again.

Nothing here decides anything or posts to the players: the DM presses, DMbot only carries it out.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from typing import Any

import discord

from dmbot.mentions import NO_PINGS
from dmbot.ui.decision_lines import with_outcome
from dmbot.ui.dmbot_commands import _bot, _tell
from dmbot.ui.recap_dm import GONE, NOT_YOURS, RecapButton
from dmbot.waiting import WaitingMessage, message_is_gone

log = logging.getLogger(__name__)

MAKING = "🎧 Making the audio. It takes a few minutes. DMbot will say here when it's ready."
MAKING_STEP = "Making the audio"
DONE_LINE = "🎧 The audio is done. See below."
STOPPED_LINE = "🎧 DMbot is not making the audio any more."
STALL_AFTER_S = 600.0  # the audio is minutes of provider work: ten without a change is a stall
STOPS_AT_S = 1200.0  # the audio step's own lease (recaps.store.AUDIO_LEASE_S)
READY = "🎧 The audio for **{title}** is ready. Nothing has gone to the players."
FAILED = (
    "⚠️ The audio for **{title}** could not be made. The text is fine and nothing went to the "
    "players. You can try the audio again, or post the text as it is."
)
NO_FILES = "The audio files aren't here any more or are out of date. Open your recap page."
TOO_BIG = "The audio is too big to send here. Listen to it on your recap page."
KEPT_LINE = "Kept, not posted."
KEPT = "👍 Kept. Nothing was posted. You can post it any time from your recap page."
AGAIN_WORDS = {
    "queued": "🎧 Making the audio again. DMbot will say on the DM screen when it's ready.",
    "too_soon": "The audio was asked for a moment ago. Try again in a few minutes.",
    "not_allowed": "I can't make the audio again now. Check your recap page.",
}
FAILED_ASK = "Something went wrong, so nothing was started. Try again in a moment."

_LISTEN = r"dmbot:recapaudio:listen:(?P<campaign>[0-9a-f]{32}):(?P<at>[0-9]{1,12})"
_KEEP = r"dmbot:recapaudio:keep:(?P<campaign>[0-9a-f]{32}):(?P<at>[0-9]{1,12})"
_AGAIN = r"dmbot:recapaudio:again:(?P<campaign>[0-9a-f]{32}):(?P<at>[0-9]{1,12})"


async def _dm_campaign(interaction: discord.Interaction, campaign_id: str) -> Any | None:
    """The campaign, if the person pressing is one of its DMs (checked on every press)."""
    bot = _bot(interaction)
    guild = interaction.guild
    campaign = await bot.campaigns.get(guild.id, campaign_id) if guild is not None else None
    if campaign is None:
        await _tell(interaction, GONE)
        return None
    if interaction.user.id not in campaign.dm_user_ids:
        await _tell(interaction, NOT_YOURS)
        return None
    return campaign


class ListenButton(discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]], template=_LISTEN):
    """🎧 Listen here: the narration and music, privately to the DM who pressed."""

    def __init__(self, campaign_id: str, session_at: int, *, row: int | None = None) -> None:
        super().__init__(
            discord.ui.Button(
                label="Listen here",
                emoji="🎧",
                style=discord.ButtonStyle.primary,
                custom_id=f"dmbot:recapaudio:listen:{campaign_id}:{session_at}",
                row=row,
            )
        )
        self.campaign_id, self.session_at = campaign_id, session_at

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> ListenButton:
        return cls(match["campaign"], int(match["at"]))

    async def callback(self, interaction: discord.Interaction) -> Any:
        campaign = await _dm_campaign(interaction, self.campaign_id)
        if campaign is None:
            return
        bot = _bot(interaction)
        job = (
            await bot.recaps.get(campaign.guild_id, campaign.id, self.session_at)
            if bot.recaps is not None
            else None
        )
        if job is None:
            await _tell(interaction, GONE)
            return
        await interaction.response.defer(ephemeral=True, thinking=False)
        files, note = await bot._recap_audio_files(
            campaign.guild_id, campaign, job, interaction.channel
        )
        try:
            if not files:
                await _tell(interaction, note or NO_FILES)
                return
            await interaction.followup.send(
                "🎧 Here is the audio. Only you can see this.",
                files=files,
                ephemeral=True,
                allowed_mentions=NO_PINGS,
            )
            if note:
                await _tell(interaction, note)
        finally:
            for f in files:
                f.close()


class KeepAudioButton(discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]], template=_KEEP):
    """Keep it, don't post: closes the line. The recap stays approved on the recap page."""

    def __init__(self, campaign_id: str, session_at: int, *, row: int | None = None) -> None:
        super().__init__(
            discord.ui.Button(
                label="Keep it, don't post",
                emoji="📋",
                style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:recapaudio:keep:{campaign_id}:{session_at}",
                row=row,
            )
        )
        self.campaign_id, self.session_at = campaign_id, session_at

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> KeepAudioButton:
        return cls(match["campaign"], int(match["at"]))

    async def callback(self, interaction: discord.Interaction) -> Any:
        if await _dm_campaign(interaction, self.campaign_id) is None:
            return
        message = interaction.message
        if message is not None:
            await interaction.response.edit_message(
                content=with_outcome(message.content, KEPT_LINE),
                view=None,
                allowed_mentions=NO_PINGS,
            )
            await interaction.followup.send(KEPT, ephemeral=True, allowed_mentions=NO_PINGS)
        else:
            await _tell(interaction, KEPT)


class MakeAudioAgainButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]], template=_AGAIN
):
    """🔁 Make the audio again, after a failure (or a take the DM did not like)."""

    def __init__(self, campaign_id: str, session_at: int, *, row: int | None = None) -> None:
        super().__init__(
            discord.ui.Button(
                label="Make the audio again",
                emoji="🔁",
                style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:recapaudio:again:{campaign_id}:{session_at}",
                row=row,
            )
        )
        self.campaign_id, self.session_at = campaign_id, session_at

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> MakeAudioAgainButton:
        return cls(match["campaign"], int(match["at"]))

    async def callback(self, interaction: discord.Interaction) -> Any:
        campaign = await _dm_campaign(interaction, self.campaign_id)
        if campaign is None:
            return
        bot = _bot(interaction)
        if bot.recaps is None:
            await _tell(interaction, FAILED_ASK)
            return
        try:
            outcome = await bot.recaps.audio_again(campaign.guild_id, campaign.id, self.session_at)
        except Exception:
            log.exception("Make the audio again failed")
            await _tell(interaction, FAILED_ASK)
            return
        await _tell(interaction, AGAIN_WORDS.get(outcome, FAILED_ASK))
        if outcome == "queued":
            await bot.recap_audio_started(campaign, self.session_at)


def ready_text(title: str) -> str:
    return READY.format(title=discord.utils.escape_markdown(title))


def failed_text(title: str) -> str:
    return FAILED.format(title=discord.utils.escape_markdown(title))


def audio_view(
    campaign_id: str, session_at: int, *, failed: bool, review_url: str = ""
) -> discord.ui.View:
    """The buttons under the notice. Ready: Listen here, the page link, Post to the table (the
    recap line's own Post button) and Keep it. Failed: the page link, Make the audio again, Post
    the text as it is."""
    view = discord.ui.View(timeout=None)
    if not failed:
        view.add_item(ListenButton(campaign_id, session_at, row=0))
    if review_url:
        view.add_item(discord.ui.Button(label="Review", emoji="📝", url=review_url, row=0))
    post = RecapButton("post", campaign_id, session_at)
    post.item.row = 1
    post.item.label = "Post to the table"
    post.item.emoji = "📤"
    if failed:
        again = MakeAudioAgainButton(campaign_id, session_at, row=0)
        view.add_item(again)
    view.add_item(post)
    if not failed:
        view.add_item(KeepAudioButton(campaign_id, session_at, row=1))
    return view


Key = tuple[int, str, int]  # server, campaign, session


class AudioWaits:
    """The "Making the audio" lines on the DM screen, one per recap, each kept current while the
    audio is made (the wait rule): the step, the time so far and when it last changed, edited in
    place, then closed with a plain last line when the notice (or a stop) takes over. Kept in
    memory: after a restart the notice still comes, only the live line is gone."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._open: dict[Key, tuple[Any, WaitingMessage]] = {}

    def __contains__(self, key: Key) -> bool:
        return key in self._open

    def open_keys(self) -> list[Key]:
        return list(self._open)

    async def start(self, key: Key, message: Any) -> None:
        """Keep `message` (already posted with `MAKING`) current until `finish`."""
        if key in self._open:
            return

        async def edit(text: str) -> None:
            await message.edit(content=text, allowed_mentions=NO_PINGS)

        waiting = WaitingMessage(
            edit,
            MAKING,
            stall_after_s=STALL_AFTER_S,
            stops_at_s=STOPS_AT_S,
            gone=message_is_gone,
            clock=self._clock,
        )
        waiting.step(MAKING_STEP)
        await waiting.__aenter__()
        self._open[key] = (message, waiting)

    def moved(self, key: Key) -> None:
        """The audio did something (a try began, credits were spent): that is a change."""
        if key in self._open:
            self._open[key][1].moved()

    def step(self, key: Key, words: str) -> None:
        if key in self._open:
            self._open[key][1].step(words)

    async def finish(self, key: Key, last: str) -> None:
        """Stop the updates and leave `last` as the line's last word, so a stale "Making…" never
        stays on the DM screen."""
        entry = self._open.pop(key, None)
        if entry is None:
            return
        message, waiting = entry
        await waiting.__aexit__(None, None, None)
        try:
            await message.edit(content=last, allowed_mentions=NO_PINGS)
        except Exception as exc:  # the DM may have dismissed it: nothing to do
            if not message_is_gone(exc):
                log.warning("Could not close the audio waiting line", exc_info=True)
