"""✍️ Recap style, under ⚙️ Settings → 📜 Recaps (#2075): the examples that show how recaps sound.

The DM pastes up to 3 recaps they like (each at most 600 words). DMbot copies how they sound, never
what happens in them: the code throws away a draft that takes a name or a run of words from one.
With none of their own a campaign gets DMbot's defaults. Only the campaign's DMs may open or change
it. The panel is private and needs no stored button IDs.
"""

from __future__ import annotations

import logging
from typing import Any

import discord

from dmbot.campaigns import Campaign, CampaignError
from dmbot.dm_screen import messages
from dmbot.dm_screen.recap_panel import FAILED, ONLY_DM, may_change
from dmbot.logs import set_log_context
from dmbot.mentions import NO_PINGS
from dmbot.recaps.style import (
    EXAMPLE_MAX_WORDS,
    MAX_EXAMPLES,
    MAX_NOTES,
    Example,
    Note,
    NoteStore,
    StyleStore,
)

log = logging.getLogger(__name__)

NO_STYLE = "✍️ Recap style isn't ready just now. Please try again."
PREVIEW_CHARS = 120


def panel_text(own: list[Example], note: str = "", notes: list[Note] | None = None) -> str:
    """The panel's words: what is in use, one line for each example and each saved note."""
    lines = [note, ""] if note else []
    lines.append("✍️ **Recap style**")
    lines += ["", "Paste a recap you like. DMbot copies how it sounds, never what happens in it."]
    if own:
        lines += ["", f"Your examples ({len(own)} of {MAX_EXAMPLES}):"]
        for n, e in enumerate(own, 1):
            text = " ".join(e.text.split())
            more = "…" if len(text) > PREVIEW_CHARS else ""
            lines.append(f"{n}. {text[:PREVIEW_CHARS]}{more}")
    else:
        lines += ["", "Now using DMbot's own examples."]
    if notes:
        lines += ["", f"Your notes ({len(notes)} of {MAX_NOTES}), the newest last:"]
        lines += [f"{n}. {x.text}" for n, x in enumerate(notes, 1)]
    return "\n".join(lines)


class AddModal(discord.ui.Modal, title="Add an example"):
    text: discord.ui.TextInput[AddModal] = discord.ui.TextInput(
        label=f"A recap you like (up to {EXAMPLE_MAX_WORDS} words)",
        style=discord.TextStyle.paragraph,
        max_length=4000,
    )

    def __init__(self, campaign_id: str) -> None:
        super().__init__()
        self.campaign_id = campaign_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await change(interaction, self.campaign_id, "add", str(self.text.value))


async def change(interaction: discord.Interaction, campaign_id: str, what: str, arg: Any) -> None:
    """Do one change and redraw the panel. The DM is checked again: a panel can be old."""
    set_log_context(guild_id=interaction.guild_id)
    guild = interaction.guild
    campaigns = getattr(interaction.client, "campaigns", None)
    styles: StyleStore | None = getattr(interaction.client, "recap_styles", None)
    if guild is None or campaigns is None or styles is None:
        await interaction.response.send_message(NO_STYLE, ephemeral=True)
        return
    campaign: Campaign | None = await campaigns.get(guild.id, campaign_id)
    if campaign is None:
        await interaction.response.send_message(messages.DM_CAMPAIGN_GONE, ephemeral=True)
        return
    if not may_change(interaction, campaign):
        await interaction.response.send_message(ONLY_DM, ephemeral=True)
        return
    note = ""
    try:
        if what == "add":
            await styles.add(guild.id, campaign_id, interaction.user.id, arg)
            note = "Saved. DMbot will use it."
        elif what == "remove":
            await styles.remove(guild.id, campaign_id, interaction.user.id, int(arg))
            note = "Removed."
        elif what == "forget":
            notes_store: NoteStore | None = getattr(interaction.client, "recap_notes", None)
            if notes_store is None:
                await interaction.response.send_message(NO_STYLE, ephemeral=True)
                return
            await notes_store.remove(guild.id, campaign_id, interaction.user.id, int(arg))
            note = "Removed."
        else:
            await styles.use_defaults(guild.id, campaign_id, interaction.user.id)
            note = "Now using DMbot's own examples."
    except CampaignError as problem:
        note = str(problem)
    except Exception:
        log.exception("Couldn't change the recap style")
        await interaction.response.send_message(FAILED, ephemeral=True)
        return
    await show(interaction, campaign, note, edit=what != "open")


async def show(
    interaction: discord.Interaction, campaign: Campaign, note: str = "", *, edit: bool = False
) -> None:
    styles: StyleStore | None = getattr(interaction.client, "recap_styles", None)
    guild = interaction.guild
    if styles is None or guild is None:
        await interaction.response.send_message(NO_STYLE, ephemeral=True)
        return
    notes_store: NoteStore | None = getattr(interaction.client, "recap_notes", None)
    try:
        own = await styles.own(guild.id, campaign.id)
        saved = await notes_store.notes(guild.id, campaign.id) if notes_store is not None else []
    except Exception:
        log.exception("Couldn't read the recap style")
        await interaction.response.send_message(FAILED, ephemeral=True)
        return
    view = StylePanel(campaign.id, own, saved)
    text = panel_text(own, note, saved)
    if edit and interaction.type == discord.InteractionType.component:
        await interaction.response.edit_message(content=text, view=view, allowed_mentions=NO_PINGS)
    else:
        await interaction.response.send_message(
            text, view=view, ephemeral=True, allowed_mentions=NO_PINGS
        )


class StylePanel(discord.ui.View):
    """A private panel; it can't outlive its message, so it needs no stored button IDs."""

    def __init__(
        self, campaign_id: str, own: list[Example], notes: list[Note] | None = None
    ) -> None:
        super().__init__(timeout=600)
        self.campaign_id = campaign_id
        add: discord.ui.Button[StylePanel] = discord.ui.Button(
            label="Add an example",
            style=discord.ButtonStyle.primary,
            disabled=len(own) >= MAX_EXAMPLES,
            row=0,
        )
        add.callback = self._add  # type: ignore[method-assign]
        self.add_item(add)
        for n, example in enumerate(own, 1):
            remove: discord.ui.Button[StylePanel] = discord.ui.Button(
                label=f"Remove {n}", style=discord.ButtonStyle.secondary, row=1
            )
            remove.callback = self._remove(example.id)  # type: ignore[method-assign]
            self.add_item(remove)
        for n, saved in enumerate(notes or [], 1):
            forget: discord.ui.Button[StylePanel] = discord.ui.Button(
                label=f"Remove note {n}", style=discord.ButtonStyle.secondary, row=3 + (n - 1) // 5
            )
            forget.callback = self._forget(saved.id)  # type: ignore[method-assign]
            self.add_item(forget)
        if own:
            defaults: discord.ui.Button[StylePanel] = discord.ui.Button(
                label="Use the defaults", style=discord.ButtonStyle.secondary, row=2
            )
            defaults.callback = self._defaults  # type: ignore[method-assign]
            self.add_item(defaults)

    async def _add(self, interaction: discord.Interaction) -> None:
        campaign = await dm_campaign(interaction, self.campaign_id)
        if campaign is not None:
            await interaction.response.send_modal(AddModal(self.campaign_id))

    def _remove(self, example_id: int) -> Any:
        async def callback(interaction: discord.Interaction) -> None:
            await change(interaction, self.campaign_id, "remove", example_id)

        return callback

    def _forget(self, note_id: int) -> Any:
        async def callback(interaction: discord.Interaction) -> None:
            await change(interaction, self.campaign_id, "forget", note_id)

        return callback

    async def _defaults(self, interaction: discord.Interaction) -> None:
        await change(interaction, self.campaign_id, "defaults", None)


async def dm_campaign(interaction: discord.Interaction, campaign_id: str) -> Campaign | None:
    """The campaign when the person is one of its DMs; otherwise they are told and it is None."""
    guild = interaction.guild
    campaigns = getattr(interaction.client, "campaigns", None)
    if guild is None or campaigns is None:
        await interaction.response.send_message(NO_STYLE, ephemeral=True)
        return None
    campaign: Campaign | None = await campaigns.get(guild.id, campaign_id)
    if campaign is None:
        await interaction.response.send_message(messages.DM_CAMPAIGN_GONE, ephemeral=True)
        return None
    if not may_change(interaction, campaign):
        await interaction.response.send_message(ONLY_DM, ephemeral=True)
        return None
    return campaign
