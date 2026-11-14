"""The Questions message (#3435): one live message in the DM's private chat for each campaign,
listing every kind of question that waits, with a count and a button that opens the flow that
exists today (nothing is rebuilt). It is edited in place as the counts change. A kind with no flow
in Discord says where it is answered. Counts, campaign names and fixed words only."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

import discord

from dmbot.questions import kinds
from dmbot.questions.counts import Counted
from dmbot.questions.store import total

EMPTY = "Nothing waiting for you. 🎉"
SITE_WORD = "answer on the website"
SCREEN_WORD = "answer on the DM screen"
# Where a kind is answered when there is no button for it here.
ELSEWHERE: dict[str, str] = {
    kinds.READINGS: SITE_WORD,
    kinds.PLOT: SITE_WORD,
    kinds.RECAP_APPROVE: SCREEN_WORD,
    kinds.RULES: SCREEN_WORD,
}
NAME_MAX = 60


def text_name(campaign: str) -> str:
    """A campaign's name for a line of text: cut, with no markdown and no mention in it."""
    return (
        f"**{discord.utils.escape_markdown(discord.utils.escape_mentions(campaign[:NAME_MAX]))}**"
    )


def text(campaign: str, found: Mapping[str, Counted]) -> str:
    """The message: a heading with the campaign, then a line for each kind that waits."""
    head = f"❓ **Questions for you** about {text_name(campaign)}"
    lines = []
    for kind in kinds.SHOWN:
        count = found.get(kind)
        if count is None or count.n <= 0:
            continue
        where = f" ({ELSEWHERE[kind]})" if kind in ELSEWHERE else ""
        lines.append(f"• {kinds.LABELS[kind]}: **{count.n}**{where}")
    return f"{head}\n{EMPTY}" if not lines else "\n".join([head, *lines])


class LookAlikesButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=r"dmbot:questions:look:(?P<campaign>[0-9a-f]{32})",
):
    """🔗 Look-alikes, from the Questions message: the same screen as the names panel's. It works
    after a restart (the campaign is in the button's ID); the DM check is the screen's own."""

    def __init__(self, campaign_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label="🔗 Look-alikes",
                style=discord.ButtonStyle.primary,
                custom_id=f"dmbot:questions:look:{campaign_id}",
            )
        )
        self.campaign_id = campaign_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> LookAlikesButton:
        return cls(match["campaign"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        from dmbot.ui.look_alikes import show_look_alikes

        # Answer with a private "thinking" message first: the screen swaps the message it is
        # opened from for each group, and that must be this private one, never the Questions
        # message (which would lose its list and buttons).
        await interaction.response.defer(ephemeral=True, thinking=True)
        await show_look_alikes(interaction, self.campaign_id)


def view(guild_id: int, campaign_id: str, found: Mapping[str, Counted]) -> discord.ui.View:
    """One button for each kind that waits and has a flow in Discord. "Which one?" is answered in
    the connections flow, which asks it first, so the two share one button."""
    from dmbot.dm_screen.links_ready import ReadyButton
    from dmbot.ui.claims_review import ClaimsReviewButton
    from dmbot.ui.family_ties import FamilyTiesButton
    from dmbot.ui.heard_review import HeardReviewButton
    from dmbot.ui.map_answer import OpenButton as MapsOpenButton
    from dmbot.ui.names import RecapNamesButton
    from dmbot.ui.trait_questions import TraitQuestionsButton

    waiting = {k for k, c in found.items() if c.n > 0}
    out = discord.ui.View(timeout=None)
    if kinds.NAMES in waiting:
        out.add_item(RecapNamesButton(guild_id, campaign_id))
    if kinds.HEARD in waiting:
        out.add_item(HeardReviewButton(campaign_id))
    if kinds.LOOKALIKES in waiting:
        out.add_item(LookAlikesButton(campaign_id))
    if kinds.CONNECTIONS in waiting or kinds.WHICH in waiting:
        out.add_item(ReadyButton(campaign_id))
    if kinds.FAMILY in waiting:
        out.add_item(FamilyTiesButton(campaign_id))
    if kinds.TRAITS in waiting:
        out.add_item(TraitQuestionsButton(campaign_id))
    if kinds.MAPS in waiting:
        out.add_item(MapsOpenButton(campaign_id))
    if kinds.CLAIMS in waiting:
        out.add_item(ClaimsReviewButton(campaign_id))
    return out


class QuestionsButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=r"dmbot:questions:open:(?P<campaign>[0-9a-f]{32})",
):
    """📬 Questions for you (n), first on the ⚙️ Manage DMbot card (#3435): makes or refreshes the
    Questions message in the DM's private chat and links to it. It opens even at zero. Only a
    campaign's DM may press it (checked on every press); the count is only the label."""

    def __init__(self, campaign_id: str, total: int | None = None) -> None:
        label = "📬 Questions for you" + (f" ({total if total < 100 else '99+'})" if total else "")
        super().__init__(
            discord.ui.Button(
                label=label,
                style=discord.ButtonStyle.primary,
                custom_id=f"dmbot:questions:open:{campaign_id}",
            )
        )
        self.campaign_id = campaign_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> QuestionsButton:
        return cls(match["campaign"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        from dmbot.questions.discord import NOT_A_DM, NOT_AVAILABLE, send_lists
        from dmbot.ui.dmbot_commands import _tell

        # Answer Discord first: the campaign is read from the database next (3 seconds to answer).
        await interaction.response.defer(ephemeral=True)
        bot: Any = interaction.client
        guild = interaction.guild
        campaign = await bot.campaigns.get(guild.id, self.campaign_id) if guild else None
        round_ = getattr(bot, "question_round", None)
        if campaign is None or interaction.user.id not in campaign.dm_user_ids:
            await _tell(interaction, NOT_A_DM)
            return
        if round_ is None:
            await _tell(interaction, NOT_AVAILABLE)
            return
        await send_lists(interaction, round_, [campaign])


async def total_for(interaction: discord.Interaction, campaign_id: str) -> int | None:
    """How many questions wait for this campaign, from the stored counts, for the button's label;
    None when they can't be read (the button then shows without a number)."""
    bot: Any = interaction.client
    round_ = getattr(bot, "question_round", None)
    guild = interaction.guild
    if round_ is None or guild is None:
        return None
    try:
        stored = await round_.counts.read(guild.id, campaign_id)
    except Exception:
        return None
    return total(stored) if stored else None
