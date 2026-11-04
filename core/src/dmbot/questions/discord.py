"""The Discord side of "Questions for you" (#3435): sending the notices and reminders as private
messages, the 🔕 Not now button, and the loop that runs the round once a minute. Everything that
decides anything is in `timer` and `loop`; this only talks to Discord. Counts and campaign names
only, never a name from a story or a question's words."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import TYPE_CHECKING, Any

import discord

from dmbot.questions import messages
from dmbot.questions.counts import Sources
from dmbot.questions.loop import ROUND_S, Round
from dmbot.questions.notices import NoticeStore
from dmbot.questions.store import QuestionCounts

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
        bot: Any = interaction.client
        notices: NoticeStore | None = getattr(bot, "question_notices", None)
        if notices is None:
            return
        try:
            await notices.mute_all(interaction.user.id, [g.id for g in bot.guilds])
        except Exception as exc:
            log.warning("Questions: Not now didn't save (%s)", type(exc).__name__)
        await interaction.response.send_message(messages.NOT_NOW_DONE, ephemeral=True)


class DiscordSender:
    def __init__(self, bot: DMBot) -> None:
        self.bot = bot

    async def recording(self, guild_id: int, campaign_id: str) -> bool:
        return bool(await self.bot.is_campaign_playing(guild_id, campaign_id))

    async def send(self, user_id: int, text: str) -> int | None:
        view = discord.ui.View(timeout=None)
        view.add_item(NotNowButton())
        sent = await self.bot._dm_message(user_id, text, view)
        return None if sent is None else sent.id

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
