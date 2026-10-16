"""The recap on the DM screen (#1382): shown only to the campaign's DM, with [Post to the table],
[Try again], [Edit the text] and [Don't post]. Nothing reaches the players unless the DM presses
Post; and then it goes out as text with the cues taken out, in the campaign's chat channel. The
buttons carry the campaign and the session in their IDs, so they work after a restart, and every
press is checked against the campaign's DMs (a server manager who is not one cannot post).

The recap is a story told in a rule-bound world: it is not a ruling, and it is the DM's to change.
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

import discord

from dmbot.recaps import draft as rd
from dmbot.recaps.store import MAX_TRIES, Recap
from dmbot.ui.dmbot_commands import _bot, _tell

NO_PINGS = discord.AllowedMentions.none()
NOT_THE_DM = "Only the campaign's DM can do that."
NO_RECAP = "Couldn't write a recap this time. Nothing was posted."
GONE = "That recap was already posted or left out."
NO_MORE_TRIES = "No more tries for this one. Post it, edit it or leave it out."
LAST_TRY = "That was the last new try. You can post it, edit it or leave it out."
POSTED = "✅ Posted in the chat channel."
DROPPED = "👍 Left out. Nothing was posted."
NO_CHANNEL = (
    "Can't post yet: this campaign has no chat channel (dmb-chat) yet. DMbot makes it when a "
    "session starts. Nothing was posted and your recap is kept. Press Post again then."
)
CANT_SEND = "Couldn't post in the chat channel. Check DMbot can write there, then press Post again."
EDIT_TOO_LONG = "Too long for one message (about 1,500 characters). Nothing was changed."
MESSAGE_MAX = 1900  # Discord's limit is 2,000


def as_posted(recap: Recap) -> str:
    """What the table reads: the heading, the story without its cues, and the housekeeping."""
    parts = [rd.heading(recap.title, recap.game_day), "", rd.strip_cues(recap.summary)]
    if recap.housekeeping:
        parts += [
            "",
            "**Mechanical Housekeeping** (hit points, items, conditions)",
            *(f"- {b}" for b in recap.housekeeping),
        ]
    return "\n".join(parts)


def to_the_table(recap: Recap) -> str:
    """As_posted, with a line saying the DM approved it: it is a story, not a ruling."""
    return as_posted(recap) + "\n\n📜 _Recap, approved by your DM._"


def for_the_dm(recap: Recap) -> str:
    """The DM's copy: the same, and a line saying nobody else has seen it."""
    return (
        "📜 **Recap ready. Only you can see it.** Nothing goes to the table until you press "
        "Post.\n\n" + as_posted(recap) + (f"\n\n_{LAST_TRY}_" if recap.tries >= MAX_TRIES else "")
    )


def parts_of(text: str) -> list[str]:
    """The text in messages Discord accepts, cut at line ends."""
    out: list[str] = []
    current = ""
    for line in text.split("\n"):
        while len(line) > MESSAGE_MAX:
            if current:
                out.append(current)
                current = ""
            out.append(line[:MESSAGE_MAX])
            line = line[MESSAGE_MAX:]
        if len(current) + len(line) + 1 > MESSAGE_MAX and current:
            out.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        out.append(current)
    return out or [""]


_LABELS = {
    "post": ("📣 Post to the table", discord.ButtonStyle.success),
    "again": ("🔁 Try again", discord.ButtonStyle.secondary),
    "edit": ("✏️ Edit the text", discord.ButtonStyle.secondary),
    "drop": ("🚫 Don't post", discord.ButtonStyle.secondary),
}


class RecapButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=r"dmbot:recap:(?P<act>post|again|edit|drop):(?P<campaign>[0-9a-f]{32}):(?P<at>[0-9]+)",
):
    def __init__(self, act: str, campaign_id: str, session_at: int, *, disabled: bool = False):
        label, style = _LABELS[act]
        super().__init__(
            discord.ui.Button(
                label=label,
                style=style,
                custom_id=f"dmbot:recap:{act}:{campaign_id}:{session_at}",
                disabled=disabled,
            )
        )
        self.act, self.campaign_id, self.session_at = act, campaign_id, session_at

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> RecapButton:
        return cls(match["act"], match["campaign"], int(match["at"]))

    async def callback(self, interaction: discord.Interaction) -> Any:
        await press(interaction, self.act, self.campaign_id, self.session_at)


def recap_view(campaign_id: str, session_at: int, *, tries: int = 0) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for act in ("post", "again", "edit", "drop"):
        if act == "again" and tries >= MAX_TRIES:
            continue  # asked twice already: the DM posts, edits or drops it
        view.add_item(RecapButton(act, campaign_id, session_at))
    return view


class EditRecap(discord.ui.Modal, title="Edit the recap"):
    def __init__(self, campaign_id: str, session_at: int, text: str) -> None:
        super().__init__()
        self.campaign_id, self.session_at = campaign_id, session_at
        self.text: discord.ui.TextInput[Any] = discord.ui.TextInput(
            label="The story (leave the 《 》 words in)",
            style=discord.TextStyle.paragraph,
            default=text,
            placeholder="Words in 《 》 or (( )) set the music's mood. Leave them as they are.",
            max_length=4000,
        )
        self.add_item(self.text)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        bot = _bot(interaction)
        campaign = await _dm_campaign(interaction, self.campaign_id)
        if campaign is None or bot.recaps is None:
            return
        value = self.text.value.strip()
        recap = await bot.recaps.get(campaign.guild_id, campaign.id, self.session_at)
        if recap is None or recap.status != "draft":
            await _tell(interaction, GONE)
            return
        long = rd.shown_length(recap.title, recap.game_day, value, recap.housekeeping)
        if not value or len(value) > rd.SUMMARY_MAX or long > rd.SHOWN_MAX + 100:
            await _tell(interaction, EDIT_TOO_LONG)
            return
        if not await bot.recaps.edit(
            campaign.guild_id, campaign.id, self.session_at, summary=value
        ):
            await _tell(interaction, GONE)
            return
        recap = replace(recap, summary=value)
        if interaction.message is None:
            await _tell(interaction, "Saved.")
            return
        await interaction.response.edit_message(
            content=for_the_dm(recap)[:MESSAGE_MAX],
            view=recap_view(campaign.id, self.session_at, tries=recap.tries),
            allowed_mentions=NO_PINGS,
        )


async def _dm_campaign(interaction: discord.Interaction, campaign_id: str) -> Any | None:
    """The campaign, if the person pressing is one of its DMs (not just a server manager)."""
    guild = interaction.guild
    bot = _bot(interaction)
    if guild is None:
        await _tell(interaction, NOT_THE_DM)
        return None
    campaign = await bot.campaigns.get(guild.id, campaign_id)
    if campaign is None or interaction.user.id not in campaign.dm_user_ids:
        await _tell(interaction, NOT_THE_DM)
        return None
    return campaign


async def press(interaction: discord.Interaction, act: str, campaign_id: str, at: int) -> None:
    bot = _bot(interaction)
    campaign = await _dm_campaign(interaction, campaign_id)
    if campaign is None:
        return
    if bot.recaps is None:
        await _tell(interaction, GONE)
        return
    recap = await bot.recaps.get(campaign.guild_id, campaign.id, at)
    if recap is None or recap.status != "draft":
        await _tell(interaction, GONE)
        return
    if act == "edit":
        await interaction.response.send_modal(EditRecap(campaign.id, at, recap.summary))
        return
    await interaction.response.defer()
    if act == "drop":
        if await bot.recaps.decide(campaign.guild_id, campaign.id, at, "dropped"):
            await interaction.edit_original_response(content=DROPPED, view=None)
        else:
            await _tell(interaction, GONE)
    elif act == "post":
        await post_to_table(interaction, campaign, recap)
    elif act == "again":
        await try_again(interaction, campaign, recap)


async def post_to_table(interaction: discord.Interaction, campaign: Any, recap: Recap) -> None:
    """Share the recap in the campaign's chat channel, cues removed: the one way it reaches the
    table. Decided first, so two presses cannot post it twice."""
    bot = _bot(interaction)
    channel = bot.get_channel(campaign.dmbchat_channel_id) if campaign.dmbchat_channel_id else None
    if channel is None or not hasattr(channel, "send"):
        await _tell(interaction, NO_CHANNEL)
        return
    if not await bot.recaps.decide(campaign.guild_id, campaign.id, recap.session_at, "posted"):
        await _tell(interaction, GONE)
        return
    try:
        for part in parts_of(to_the_table(recap)):
            await channel.send(part, allowed_mentions=NO_PINGS)
    except discord.HTTPException:
        await bot.recaps.reopen(campaign.guild_id, campaign.id, recap.session_at)
        await _tell(interaction, CANT_SEND)
        return
    await interaction.edit_original_response(content=POSTED, view=None)


async def try_again(interaction: discord.Interaction, campaign: Any, recap: Recap) -> None:
    bot = _bot(interaction)
    if recap.tries >= MAX_TRIES:
        await _tell(interaction, NO_MORE_TRIES)
        return
    result = await bot.write_recap_again(campaign, recap)
    if result is None:
        await _tell(
            interaction, "Couldn't write another recap this time. The first one is still here."
        )
        return
    await interaction.edit_original_response(
        content=for_the_dm(result)[:MESSAGE_MAX],
        view=recap_view(campaign.id, recap.session_at, tries=result.tries),
    )
