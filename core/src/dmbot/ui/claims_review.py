"""The DM's after-session questions about what was said at the table (#1297): "**Bram** carries
**Heartseeker**? ✅ ❌". What the story extractor read out of a session is only a proposal; here
the DM turns each into a fact (✅) or turns it down for good (❌). Private to the campaign's DM,
never in a transcript and never shown to players. Grouped by who they are about, a few at a time.
"""

from __future__ import annotations

import re
from typing import Any

import discord

from dmbot.memory.claims import PAGE, Claim, ClaimStore
from dmbot.memory.models import HOW_DM, MemoryRuleError
from dmbot.ui.dmbot_commands import _Button, _Menu, _replace, _send, _tell
from dmbot.ui.name_card import FLAG_WHY
from dmbot.ui.names import _campaign_for, _md, _memory

NOTHING_WAITING = "✅ Nothing to check. DMbot asks after a session."
GONE = "That one was already answered. Press **Check what was said** again for the rest."
YES, NO = "✅", "❌"


def waiting_text(count: int) -> str:
    """For the DM screen after a session: how many, never what."""
    what = "thing" if count == 1 else "things"
    return (
        f"🗣️ **{count} {what} said at the table to check.** DMbot read the session and "
        "noticed some things. Nothing is remembered until you say so. Press the button; "
        "only you will see it."
    )


def question_text(claim: Claim) -> str:
    """One question in the words it was said in, and who said it."""
    by = "you said" if claim.how == HOW_DM else "a player said"
    extra = f" ({_md(claim.details)})" if claim.details else ""
    return (
        f"**{_md(claim.subject)}** {_md(claim.relationship)} "
        f"**{_md(claim.object)}**{extra}? _({by})_"
    ).replace(" ****", "")


def page_text(page: list[Claim], total: int) -> str:
    """A few questions, numbered, with a heading when a name has several."""
    lines = [f"**What was said at the table.** {YES} if it is true in your game, {NO} if not."]
    last = ""
    for number, claim in enumerate(page, 1):
        if claim.subject.casefold() != last:
            last = claim.subject.casefold()
            lines.append(f"\n__{_md(claim.subject)}__")
        lines.append(f"{number}. {question_text(claim)}")
    if total > len(page):
        lines.append(f"\n_{total - len(page)} more after these._")
    return "\n".join(lines)


class ClaimsReviewButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=r"dmbot:claims:review:(?P<campaign>[0-9a-f]{32})",
):
    """On the DM screen after a session; works after a restart (the campaign is in the
    button's ID)."""

    def __init__(self, campaign_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label="🗣️ Check what was said",
                style=discord.ButtonStyle.primary,
                custom_id=f"dmbot:claims:review:{campaign_id}",
            )
        )
        self.campaign_id = campaign_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> ClaimsReviewButton:
        return cls(match["campaign"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        await start_claims_review(interaction, self.campaign_id)


def claims_review_view(campaign_id: str) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(ClaimsReviewButton(campaign_id))
    return view


async def start_claims_review(interaction: discord.Interaction, campaign_id: str) -> None:
    memory = _memory(interaction)
    if memory is None:
        await _tell(interaction, "Remembering names isn't switched on for this DMbot yet.")
        return
    campaign = await _campaign_for(interaction, campaign_id)  # only whoever runs the campaign
    if campaign is None:
        return
    view = ClaimsReview(campaign.id)
    text = await view.current_text(interaction)
    if text is None:
        await _tell(interaction, NOTHING_WAITING)
        return
    await _send(interaction, text, view)


class ClaimsReview(_Menu):
    """A page of questions, each with ✅ and ❌ under its number."""

    def __init__(self, campaign_id: str) -> None:
        super().__init__()
        self.campaign_id = campaign_id
        self.page: list[Claim] = []

    async def current_text(self, interaction: discord.Interaction) -> str | None:
        memory = _memory(interaction)
        guild = interaction.guild
        if memory is None or guild is None:
            return None
        waiting = await ClaimStore(memory).waiting(guild.id, self.campaign_id)
        self.clear_items()
        self.page = waiting[:PAGE]
        if not self.page:
            return None
        for number, claim in enumerate(self.page, 1):
            self.add_item(
                _Button(
                    self._answer(claim.id, yes=True),
                    label=f"{YES} {number}",
                    style=discord.ButtonStyle.success,
                    row=0,
                )
            )
            self.add_item(
                _Button(
                    self._answer(claim.id, yes=False),
                    label=f"{NO} {number}",
                    style=discord.ButtonStyle.secondary,
                    row=1,
                )
            )
        return page_text(self.page, len(waiting))

    def _answer(self, claim_id: str, *, yes: bool) -> Any:
        async def handler(interaction: discord.Interaction) -> None:
            await self._decide(interaction, claim_id, yes)

        return handler

    async def _decide(self, interaction: discord.Interaction, claim_id: str, yes: bool) -> None:
        campaign = await _campaign_for(interaction, self.campaign_id)  # asked again each press
        memory = _memory(interaction)
        guild = interaction.guild
        if campaign is None or memory is None or guild is None:
            return
        claims = ClaimStore(memory)
        done = ""
        try:
            if yes:
                result = await claims.confirm(guild.id, self.campaign_id, claim_id)
                done = f"{YES} Remembered: {question_text(result.claim).rsplit('?', 1)[0]}."
                if result.flags:
                    why = FLAG_WHY.get(result.flags[0].kind) or "It's an odd pair"
                    done += f" {why}, so DMbot will ask you about it after the session."
            else:
                turned = await claims.reject(guild.id, self.campaign_id, claim_id)
                done = f"{NO} Not remembered: {question_text(turned).rsplit('?', 1)[0]}."
        except MemoryRuleError as exc:
            done = str(exc) if str(exc) else GONE
        text = await self.current_text(interaction)
        if text is None:
            self.stop()
            await _replace(interaction, f"{done}\n\n{NOTHING_WAITING}".strip(), None)
            return
        await _replace(interaction, f"{done}\n\n{text}".strip(), self)


__all__ = [
    "ClaimsReviewButton",
    "claims_review_view",
    "page_text",
    "question_text",
    "start_claims_review",
    "waiting_text",
]
