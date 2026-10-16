"""The recap on the DM screen (#1455): when a recap is ready the DM can read it, post it to the
table or skip it without leaving Discord. Three buttons that carry the campaign and the session,
so they work after a restart. Private to the campaign's DMs: the campaign is checked on every press.

Nothing here decides anything itself. **Post** asks once ("Post it to the table now?") because it is
public, then makes the same move as the website's Post (`RecapStore.decide`) and lets the bot's own
`post_recaps` do the sending, which checks the plan, who is still recorded and the secret names once
more. **Not this one** is the website's "Don't post". Editing the words stays on the website.
"""

from __future__ import annotations

import re
from typing import Any

import discord

from dmbot.recaps import post as recap_post
from dmbot.recaps.notify import page_link
from dmbot.ui.dmbot_commands import NO_PINGS, _bot, _tell

NOT_YOURS = "Only this campaign's DM can do this. Ask your DM."
GONE = "I can't find this recap any more. Nothing was changed. Check your recap page."
DECIDED = "This recap was already posted or skipped, so nothing was changed."
NOT_READY = "This recap isn't ready yet. Try again in a minute."
ASK = "Post it to the table now? The players will all see it."
SKIPPED = "👍 Skipped. Nothing was posted. You can still find it on your recap page."
POSTED = "✅ Posted to the table. The players can see it now."
NO_STORE = "Recaps aren't available right now. Try again in a minute."
NOT_NOW = "Okay, nothing was posted. Press Post to the table whenever you're ready."

_TEMPLATE = (
    r"dmbot:recap:(?P<act>read|post|yes|no|drop):(?P<campaign>[0-9a-f]{32}):(?P<at>[0-9]{1,12})"
)
_LABELS = {
    "read": ("Read it", discord.ButtonStyle.primary),
    "post": ("Post to the table", discord.ButtonStyle.success),
    "drop": ("Not this one", discord.ButtonStyle.secondary),
    "yes": ("Yes", discord.ButtonStyle.success),
    "no": ("Not now", discord.ButtonStyle.secondary),
}


def ready_text(title: str, link: str) -> str:
    """The DM-screen line that goes with the buttons: what it is, that nothing has gone to the
    players yet, and where to change the words."""
    where = f"\nWant to change it first? Edit it here: {link}" if link else ""
    return (
        f"📜 Recap ready: **{discord.utils.escape_markdown(title)}**\n"
        f"Nothing goes to the players until you press Post.{where}"
    )


def reading_text(job: Any, link: str) -> str:
    """What "Read it" posts: the story as the table would read it, without the posted-by line."""
    text = recap_post.to_the_table(job, footer=False)
    if link:
        text += f"\n\nWant to change it first? Edit it here: {link}"
    return text


class RecapButton(discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]], template=_TEMPLATE):
    def __init__(self, act: str, campaign_id: str, session_at: int) -> None:
        label, style = _LABELS[act]
        super().__init__(
            discord.ui.Button(
                label=label,
                style=style,
                custom_id=f"dmbot:recap:{act}:{campaign_id}:{session_at}",
            )
        )
        self.act, self.campaign_id, self.session_at = act, campaign_id, session_at

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> RecapButton:
        return cls(match["act"], match["campaign"], int(match["at"]))

    async def callback(self, interaction: discord.Interaction) -> Any:
        bot = _bot(interaction)
        guild = interaction.guild
        campaign = (
            await bot.campaigns.get(guild.id, self.campaign_id) if guild is not None else None
        )
        # Only a DM of this campaign, on every press: a server manager who is not one, or a player
        # who finds the message, cannot read, post or skip it.
        if guild is None or campaign is None:
            await _tell(interaction, GONE)
            return
        if interaction.user.id not in campaign.dm_user_ids:
            await _tell(interaction, NOT_YOURS)
            return
        store = bot.recaps
        if store is None:
            await _tell(interaction, NO_STORE)
            return
        job = await store.get(guild.id, campaign.id, self.session_at)
        if job is None:
            await _tell(interaction, GONE)
            return
        if self.act == "no":
            await _tell(interaction, NOT_NOW)
            return
        if job.state != "ready":
            await _tell(interaction, NOT_READY)
            return
        if self.act == "read":
            await self._read(interaction, bot, campaign, job)
        elif self.act == "post":
            if job.status != "draft":
                await _tell(interaction, DECIDED)
                return
            await _tell(interaction, ASK, view=confirm_view(self.campaign_id, self.session_at))
        elif self.act == "yes":
            await self._post(interaction, bot, guild.id, campaign.id)
        else:  # drop
            if not await store.decide(guild.id, campaign.id, self.session_at, "drop"):
                await _tell(interaction, DECIDED)
                return
            await interaction.response.edit_message(view=None)
            await interaction.followup.send(SKIPPED, ephemeral=True, allowed_mentions=NO_PINGS)

    async def _read(
        self, interaction: discord.Interaction, bot: Any, campaign: Any, job: Any
    ) -> None:
        """The text on the DM screen in messages Discord accepts, the housekeeping after the story,
        and the narration and music attached to the last one when they are ready and current."""
        assert interaction.guild is not None
        await interaction.response.defer(thinking=False)
        link = (
            page_link(bot.settings.site_url, campaign.guild_id, campaign.id, job.session_at)
            if bot.settings.site_url
            else ""
        )
        parts = recap_post.parts_of(reading_text(job, link))
        audio, note = await bot._recap_audio_files(
            interaction.guild.id, campaign, job, interaction.channel
        )
        try:
            for i, part in enumerate(parts):
                last = i == len(parts) - 1
                if last and audio:
                    await interaction.followup.send(part, allowed_mentions=NO_PINGS, files=audio)
                else:
                    await interaction.followup.send(part, allowed_mentions=NO_PINGS)
            if note:
                await interaction.followup.send(note, ephemeral=True, allowed_mentions=NO_PINGS)
        finally:
            for f in audio:
                f.close()

    async def _post(self, interaction: discord.Interaction, bot: Any, gid: int, cid: str) -> None:
        """Yes was pressed: the website's own move, then the bot's own sending and checks."""
        if not await bot.recaps.decide(gid, cid, self.session_at, "post"):
            await _tell(interaction, DECIDED)
            return
        await interaction.response.defer(ephemeral=True, thinking=False)
        await bot.post_recaps(gid)  # the same checks as the website's Post, once more
        after = await bot.recaps.get(gid, cid, self.session_at)
        if after is not None and after.status == "posted":
            await interaction.followup.send(POSTED, ephemeral=True, allowed_mentions=NO_PINGS)
            return
        # last_error is plain words the bot's own checks wrote (post_recaps' notes); the recap is
        # back to a draft, so pressing Post again is the next step.
        why = (after.last_error if after is not None else "") or "Something stopped it."
        await interaction.followup.send(
            f"Not posted: {why} Nothing went to the table. Check your recap page, then try again.",
            ephemeral=True,
            allowed_mentions=NO_PINGS,
        )


def ready_view(campaign_id: str, session_at: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for act in ("read", "post", "drop"):
        view.add_item(RecapButton(act, campaign_id, session_at))
    return view


def confirm_view(campaign_id: str, session_at: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for act in ("yes", "no"):
        view.add_item(RecapButton(act, campaign_id, session_at))
    return view
