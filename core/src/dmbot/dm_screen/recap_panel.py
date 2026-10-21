"""📜 Recaps on ⚙️ Settings (#1421): the DM's three choices for the recap of each session.

"Write a recap after each session", "Make the narration and music" and "Email me when it is
ready". They are on by default and can only turn things off: what the plan allows is decided
elsewhere (the plan gate fails closed), so a switch can never turn on something the plan does not
include. The narration switch is shown only where the plan includes spoken recaps, and says in one
line whether it is part of the plan or an add-on. Only the campaign's DMs may
open or change it. One tap saves, and the panel redraws; nothing else is said.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

import discord

from dmbot.campaigns import Campaign
from dmbot.dm_screen import messages
from dmbot.logs import set_log_context
from dmbot.mentions import NO_PINGS

log = logging.getLogger(__name__)

_ID = r"(?P<campaign>[0-9a-f]{32})"
ONLY_DM = "Only the DM can change recaps. Ask them to use ⚙️ Settings."
FAILED = "Sorry, that didn't save. Please try again."
NOT_IN_PLAN = "📜 Recaps aren't part of this campaign's plan. Plans and prices are on the website."
CANT_TELL = "📜 Couldn't check the plan just now. Please try again."
SAVED = {
    ("text", True): "Saved. A recap after each session.",
    ("text", False): "Saved. No recap after each session.",
    ("audio", True): "Saved. Narration and music on.",
    ("audio", False): "Saved. No narration and music.",
    ("email", True): "Saved. You'll get an email.",
    ("email", False): "Saved. No email.",
}
# Short verbs for the buttons (Discord cuts long labels on a phone); the full words are in the
# message above them.
BUTTONS = {
    "text": ("Stop writing recaps", "Write recaps"),
    "audio": ("Stop narration + music", "Make narration + music"),
    "email": ("Stop the email", "Email me recaps"),
}

LABELS = {
    "text": "Write a recap after each session",
    "audio": "Make the narration and music",
    "email": "Email me when it is ready",
}


@dataclass(frozen=True, slots=True)
class PlanFacts:
    """What the campaign's plan allows, for the panel: recaps at all, spoken ones, and how."""

    text: bool
    audio: bool
    audio_how: str = ""  # "part of your plan" or "an add-on", said in one line


def may_change(interaction: discord.Interaction, campaign: Campaign) -> bool:
    """Only the campaign's DMs: these choices spend the DM's plan (the narration's credits) and
    decide what they are emailed, so a server manager does not get them."""
    return interaction.user.id in campaign.dm_user_ids


def audio_how(plan_name: str | None, kind: str, included_plans: frozenset[str]) -> str:
    """How the DM has spoken recaps: with their plan (Guild and up, a grant, free access) or as
    the add-on they bought."""
    if kind == "paid" and (plan_name or "").casefold() not in included_plans:
        return "an add-on"
    return "part of your plan"


def panel_text(campaign: Campaign, facts: PlanFacts | None, note: str = "") -> str:
    """The panel's words. `facts` None: the plan could not be checked. `note`: one line saying
    what the last tap did."""
    if facts is None:
        return CANT_TELL
    if not facts.text:
        return NOT_IN_PLAN
    lines = [note, ""] if note else []
    lines += ["📜 **Recaps**", ""]
    for which in ("text", "audio", "email"):
        if which == "audio" and not facts.audio:
            continue
        on = getattr(campaign, f"recaps_{which}")
        extra = f" ({facts.audio_how})" if which == "audio" and facts.audio_how else ""
        lines.append(f"{'✅ On' if on else '⬜ Off'}: {LABELS[which]}{extra}")
    if not campaign.recaps_email:
        lines += ["", "The recap will be on your recap page."]
    lines += ["", "Nothing goes to the table until you say so."]
    return "\n".join(lines)


class RecapsPanel(discord.ui.View):
    """A private panel; it can't outlive its message, so it needs no stored button IDs."""

    def __init__(self, campaign: Campaign, facts: PlanFacts | None) -> None:
        super().__init__(timeout=600)
        self.campaign_id = campaign.id
        self.facts = facts
        if facts is None or not facts.text:
            return
        for row, which in enumerate(("text", "audio", "email")):
            if which == "audio" and not facts.audio:
                continue
            on = getattr(campaign, f"recaps_{which}")
            button: discord.ui.Button[RecapsPanel] = discord.ui.Button(
                label=BUTTONS[which][0 if on else 1],
                style=discord.ButtonStyle.secondary if on else discord.ButtonStyle.primary,
                row=row,
            )
            button.callback = self._toggle(which, not on)  # type: ignore[method-assign]
            self.add_item(button)
        style: discord.ui.Button[RecapsPanel] = discord.ui.Button(
            label="Recap style", emoji="✍️", style=discord.ButtonStyle.secondary, row=3
        )
        style.callback = self._style  # type: ignore[method-assign]
        self.add_item(style)
        from dmbot.ui.recap_share import ShareButton  # here: that module imports the bot's UI

        self.add_item(ShareButton(campaign.id, row=3))  # Share a recap (#2118)

    async def _style(self, interaction: discord.Interaction) -> None:
        # Imported here: the style panel borrows this module's DM check and words.
        from dmbot.dm_screen import recap_style

        campaign = await recap_style.dm_campaign(interaction, self.campaign_id)
        if campaign is not None:
            await recap_style.show(interaction, campaign)

    def _toggle(self, which: str, to: bool) -> Any:
        async def callback(interaction: discord.Interaction) -> None:
            await self.save(interaction, which, to)

        return callback

    async def save(self, interaction: discord.Interaction, which: str, to: bool) -> None:
        set_log_context(guild_id=interaction.guild_id)
        guild = interaction.guild
        store = getattr(interaction.client, "campaigns", None)
        if guild is None or store is None:
            await interaction.response.send_message(FAILED, ephemeral=True)
            return
        try:
            campaign: Campaign | None = await store.get(guild.id, self.campaign_id)
        except Exception:
            log.exception("Couldn't load a campaign for the recap choices")
            await interaction.response.send_message(FAILED, ephemeral=True)
            return
        if campaign is None:
            await interaction.response.send_message(messages.DM_CAMPAIGN_GONE, ephemeral=True)
            return
        if not may_change(interaction, campaign):  # checked again: the panel may be old
            await interaction.response.send_message(ONLY_DM, ephemeral=True)
            return
        try:
            saved: Campaign = await store.set_recap_switch(guild.id, campaign.id, which, to)
        except Exception:
            log.exception("Couldn't save a recap choice")
            await interaction.response.send_message(FAILED, ephemeral=True)
            return
        facts = await plan_facts(interaction, saved)
        await interaction.response.edit_message(
            content=panel_text(saved, facts, SAVED[(which, to)]),
            view=RecapsPanel(saved, facts),
            allowed_mentions=NO_PINGS,
        )


async def plan_facts(interaction: discord.Interaction, campaign: Campaign) -> PlanFacts | None:
    """What the plan allows, from the running bot; None when it can't be told (the panel then
    says so, and shows no switches: they can only turn things off, so showing none is safe)."""
    find = getattr(interaction.client, "recap_plan_facts", None)
    if find is None:
        return None
    try:
        facts: PlanFacts = await find(campaign)
    except Exception:
        log.exception("Couldn't tell what the plan allows for recaps")
        return None
    return facts


class RecapsButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:recaps:{_ID}",
):
    """📜 Recaps, on ⚙️ Settings: opens the private panel above."""

    def __init__(self, campaign_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label="Recaps",
                emoji="📜",
                style=discord.ButtonStyle.secondary,
                row=4,
                custom_id=f"dmbot:recaps:{campaign_id}",
            )
        )
        self.campaign_id = campaign_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> RecapsButton:
        return cls(match["campaign"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        set_log_context(guild_id=interaction.guild_id)
        guild = interaction.guild
        store = getattr(interaction.client, "campaigns", None)
        campaign: Campaign | None = None
        if guild is not None and store is not None:
            try:
                campaign = await store.get(guild.id, self.campaign_id)
            except Exception:
                log.exception("Couldn't load a campaign for the recap choices")
                await interaction.response.send_message(messages.LOAD_FAILED, ephemeral=True)
                return
        if campaign is None:
            await interaction.response.send_message(messages.DM_CAMPAIGN_GONE, ephemeral=True)
            return
        if not may_change(interaction, campaign):
            await interaction.response.send_message(ONLY_DM, ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)  # the plan check may take a moment
        facts = await plan_facts(interaction, campaign)
        await interaction.followup.send(
            panel_text(campaign, facts),
            view=RecapsPanel(campaign, facts),
            ephemeral=True,
            allowed_mentions=NO_PINGS,
        )
