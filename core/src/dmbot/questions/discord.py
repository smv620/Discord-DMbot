"""The Discord side of "Questions for you" (#3435): sending the notices and reminders as private
messages, the 🔕 Not now button, and the loop that runs the round once a minute. Everything that
decides anything is in `timer` and `loop`; this only talks to Discord. Counts and campaign names
only, never a name from a story or a question's words."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

import discord

from dmbot.campaigns.models import Campaign
from dmbot.mentions import NO_PINGS
from dmbot.questions import board, messages
from dmbot.questions import counts as counting
from dmbot.questions.counts import Counted, Sources
from dmbot.questions.loop import ROUND_S, Round
from dmbot.questions.notices import NoticeStore
from dmbot.questions.store import QuestionCounts
from dmbot.ui import dm_links
from dmbot.ui.dmbot_commands import _tell, dmbot_group

if TYPE_CHECKING:
    from dmbot.bot import DMBot

log = logging.getLogger(__name__)

FIRST_ROUND_DELAY_S = 90  # a restart loop must not send anything in a hurry
_TEMPLATE = r"dmbot:questions:notnow"


class NotNowButton(discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]], template=_TEMPLATE):
    """🔕 Not now, under a notice: no reminders for this DM, in any of their campaigns, until a new
    question arrives. It works after a restart (nothing is kept in the message)."""

    def __init__(self) -> None:
        super().__init__(
            discord.ui.Button(
                label=messages.NOT_NOW,
                style=discord.ButtonStyle.secondary,
                custom_id=_TEMPLATE,
            )
        )

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> NotNowButton:
        return cls()

    async def callback(self, interaction: discord.Interaction) -> Any:
        # Answer Discord first: the mute is written to the database next, and a button must be
        # answered within 3 seconds. The confirmation is a follow-up.
        await interaction.response.defer(ephemeral=True)
        bot: Any = interaction.client
        notices: NoticeStore | None = getattr(bot, "question_notices", None)
        saved = False
        if notices is not None:
            try:
                await notices.mute_all(interaction.user.id, [g.id for g in bot.guilds])
                saved = True
            except Exception as exc:
                log.warning("Questions: Not now didn't save (%s)", type(exc).__name__)
        await interaction.followup.send(
            messages.NOT_NOW_DONE if saved else messages.NOT_NOW_FAILED, ephemeral=True
        )


class DiscordSender:
    def __init__(self, bot: DMBot) -> None:
        self.bot = bot

    async def recording(self, guild_id: int, campaign_id: str) -> bool:
        return bool(await self.bot.is_campaign_playing(guild_id, campaign_id))

    async def send(self, user_id: int, text: str) -> int | None:
        view = discord.ui.View(timeout=None)
        view.add_item(NotNowButton())
        # The two links (#3663): the Questions page, and the message's own address, edited in
        # once it is sent (never fatal).
        site = dm_links.questions_link(self.bot.settings.site_url)
        shown = dm_links.add_links(view, site_url=site)
        sent = await self.bot._dm_message(user_id, text, shown)
        await dm_links.point_at_itself(sent, shown)
        return None if sent is None else sent.id

    async def board(
        self,
        user_id: int,
        campaign: Campaign,
        found: Mapping[str, Counted],
        message_id: int | None,
        *,
        create: bool,
    ) -> tuple[int | None, str]:
        """The Questions message (see `board`): edited in place, or made when there is none or it
        was deleted. A DM whose private chat is closed gets nothing (None, no link)."""
        content = board.text(campaign.name, found)
        view = board.view(campaign.guild_id, campaign.id, found)
        site = dm_links.questions_link(self.bot.settings.site_url)
        try:
            user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
            channel = user.dm_channel or await user.create_dm()
            if message_id is not None:
                try:
                    kept = channel.get_partial_message(message_id)
                    # (its address is known before the edit, so it goes in the same edit)
                    linked = dm_links.add_links(
                        view, discord_url=dm_links.jump_link(kept), site_url=site
                    )
                    await kept.edit(content=content, view=linked)
                    return message_id, kept.jump_url
                except discord.NotFound:
                    message_id = None  # they deleted it
            if not create:
                return None, ""
            linked = dm_links.add_links(view, site_url=site) or view
            sent = await channel.send(content, view=linked, allowed_mentions=NO_PINGS)
            await dm_links.point_at_itself(sent, linked)
            return sent.id, sent.jump_url
        except discord.HTTPException as exc:
            log.info("Questions: couldn't show the list to a DM (%s)", type(exc).__name__)
            return message_id, ""

    async def delete(self, user_id: int, message_id: int) -> None:
        try:
            user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
            channel = user.dm_channel or await user.create_dm()
            await channel.get_partial_message(message_id).delete()
        except Exception:
            pass  # already gone (or the DM closed): nothing to take away


def build_round(bot: DMBot) -> Round | None:
    """The round, or None for a bot with no story memory or database to count from."""
    if bot.memory is None:
        return None
    db = bot.memory._db
    from dmbot.rules.pack_updates import PackUpdateStore

    sets = bot.rule_sets
    sources = Sources(
        db=db,
        memory=bot.memory,
        claims=_claims(bot),
        backstory=bot.backstory_links,
        heard=bot.heard_questions,
        rule_sets=sets,
        packs=sets if isinstance(sets, PackUpdateStore) else None,
        agreed=bot.consent.consenting,
    )
    return Round(
        sources=sources,
        counts=QuestionCounts(db),
        notices=NoticeStore(db),
        sender=DiscordSender(bot),
        campaigns=bot.campaigns.list_campaigns,
        notify=bot.settings.questions_on,
    )


def _claims(bot: DMBot) -> Any | None:
    if bot.memory is None or bot.extractor_ai is None:
        return None  # the claim reader is off: there are no facts to check
    from dmbot.memory.claims import ClaimStore

    return ClaimStore(bot.memory)


async def run_loop(bot: DMBot, round_: Round) -> None:
    """Once a minute, over every server the bot is in. Each server and campaign has its own
    `try`, so one failure never stops the rest (see `Round.run`)."""
    await asyncio.sleep(FIRST_ROUND_DELAY_S)
    while True:
        try:
            await round_.run([g.id for g in bot.guilds])
        except Exception as exc:
            log.warning("Questions: a round failed (%s)", type(exc).__name__)
        await asyncio.sleep(ROUND_S)


NOT_A_DM = "Only a campaign's DM has questions to answer. Ask your DM."
NOT_AVAILABLE = "Questions for you isn't available right now. Try again in a moment."
SENT = "❓ The list is in your private messages:"


@dmbot_group.command(
    name="questions", description="See every question DMbot is waiting on you to answer"
)
async def dmbot_questions(interaction: discord.Interaction) -> None:
    """Make (or refresh) the Questions message in the DM's private chat for each campaign they
    run here, and say where it is. The DM's alone: a player has no questions."""
    bot: Any = interaction.client
    guild = interaction.guild
    round_: Round | None = getattr(bot, "question_round", None)
    if guild is None:
        await _tell(interaction, NOT_AVAILABLE)
        return
    mine = [
        c
        for c in await bot.campaigns.list_campaigns(guild.id)
        if interaction.user.id in c.dm_user_ids
    ]
    if not mine:
        await _tell(interaction, NOT_A_DM)
        return
    if round_ is None:
        await _tell(interaction, NOT_AVAILABLE)
        return
    await send_lists(interaction, round_, mine)


async def send_lists(
    interaction: discord.Interaction, round_: Round, campaigns: list[Campaign]
) -> None:
    """Make or refresh the Questions message for each of these campaigns in the person's private
    chat and say where it is. The caller has checked the person is a DM of every one."""
    if not interaction.response.is_done():  # the 📬 button has already answered
        await interaction.response.defer(ephemeral=True)
    links: list[str] = []
    for campaign in campaigns:
        try:
            found = await counting.count_kinds(round_.sources, campaign.guild_id, campaign.id)
            round_.latest[(campaign.guild_id, campaign.id)] = found
            await round_.counts.write(campaign.guild_id, campaign.id, found)
            stored = await round_.notices.get(campaign.guild_id, campaign.id, interaction.user.id)
            message_id, link = await round_.sender.board(
                interaction.user.id, campaign, found, stored.message_id, create=True
            )
            if message_id != stored.message_id:
                await round_.notices.set_message(
                    campaign.guild_id, campaign.id, interaction.user.id, message_id
                )
            if link:
                links.append(f"• {board.text_name(campaign.name)}: [Open the list]({link})")
        except Exception as exc:
            log.warning("Questions: the list could not be made (%s)", type(exc).__name__)
    if not links:
        await interaction.followup.send(
            "I couldn't send you a private message. Turn on private messages from this server, "
            "then try again.",
            ephemeral=True,
        )
        return
    await interaction.followup.send("\n".join([SENT, *links]), ephemeral=True)
