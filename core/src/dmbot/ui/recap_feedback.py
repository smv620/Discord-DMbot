"""💬 Tell DMbot what you think (#2076): the DM's feedback on a recap, typed in a form.

Private to the campaign's DMs, checked again at every step. The words are sorted into points
(`recaps.feedback`) and each kind is treated differently:

- a **style** note is shown back with Save / Edit / Don't keep and kept only on Save;
- a detail that is wrong, or something to put in or leave out, is kept for this one recap and
  reaches the writer on **Try again**; it never becomes a style note;
- a request to make up or build the story gets a plain no, and nothing is kept.

Nothing here is posted anywhere players can see, nothing goes in a transcript, and the words are
never logged: counts only (CLAUDE.md: DM authority; the bot never invents story).
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import discord

from dmbot.campaigns import Campaign, CampaignError
from dmbot.mentions import NO_PINGS
from dmbot.recaps import feedback as fb
from dmbot.recaps.style import MAX_NOTES, NOTE_CHARS
from dmbot.ui.dmbot_commands import _bot, _tell

log = logging.getLogger(__name__)

NOT_YOURS = "Only this campaign's DM can do this. Ask your DM."
GONE = "I can't find this recap any more. Nothing was changed."
DECIDED = "This recap was already posted or skipped, so nothing was changed."
TIMED_OUT = "These buttons timed out. Tell DMbot again to get new ones."
NO_RECAPS = "Recaps are off for this campaign, or not in its plan, so there is nothing to change."
NO_FEEDBACK = "Feedback isn't available right now. Try again in a minute."
SAVED = "Saved. DMbot will remember this for your next recaps."
SAVED_DROPPED = " That was one note too many, so the oldest one was removed."
NOT_A_NOTE = (
    "That isn't about how recaps sound, so I didn't keep it. Change the words and try again."
)
NOT_KEPT = "Okay, I won't keep that."
ASK_AGAIN = "Noted for this recap. Want DMbot to write it again with that?"
AGAIN_STARTED = "👍 Writing it again. You'll get the new one when it's ready."
AGAIN_NOT_NOW = "I can't write it again now. It may already be posted, or have had its two tries."
HELP = "Say what you thought of the recap. DMbot keeps notes on how it sounds and fixes details."


async def dm_campaign(
    interaction: discord.Interaction, campaign_id: str, guild_id: int | None = None
) -> Campaign | None:
    """The campaign when the person is one of its DMs; otherwise they are told and it is None.
    `guild_id`: given by the views in a private message (where the interaction has no server);
    the bot put it there, it is never read from the person."""
    gid = (
        guild_id if guild_id is not None else (interaction.guild.id if interaction.guild else None)
    )
    campaign = await _bot(interaction).campaigns.get(gid, campaign_id) if gid is not None else None
    if campaign is None:
        await _tell(interaction, GONE)
        return None
    if interaction.user.id not in campaign.dm_user_ids:
        await _tell(interaction, NOT_YOURS)
        return None
    return campaign


async def open_form(interaction: discord.Interaction, campaign_id: str, session_at: int) -> None:
    """The button: a form with one text box. The DM is checked here and again on submit."""
    if await dm_campaign(interaction, campaign_id) is None:
        return
    await interaction.response.send_modal(FeedbackModal(campaign_id, session_at))


class FeedbackModal(discord.ui.Modal, title="Tell DMbot what you think"):
    text: discord.ui.TextInput[FeedbackModal] = discord.ui.TextInput(
        label="What would you change about this recap?",
        style=discord.TextStyle.paragraph,
        placeholder="For example: shorter, and more about the fight.",
        max_length=fb.MAX_FEEDBACK_CHARS,
    )

    def __init__(self, campaign_id: str, session_at: int) -> None:
        super().__init__()
        self.campaign_id, self.session_at = campaign_id, session_at

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await handle(interaction, self.campaign_id, self.session_at, str(self.text.value))


async def handle(
    interaction: discord.Interaction, campaign_id: str, session_at: int, feedback: str
) -> None:
    """Sort the feedback and answer privately, one message for what to keep and one for each
    style note to confirm. Also the entry for feedback spoken to the DM sidebar."""
    campaign = await dm_campaign(interaction, campaign_id)
    if campaign is None:
        return
    bot = _bot(interaction)
    ai = bot.recap_feedback_ai
    if ai is None or bot.recaps is None:
        await _tell(interaction, NO_FEEDBACK)
        return
    job = await bot.recaps.get(campaign.guild_id, campaign.id, session_at)
    if job is None:
        await _tell(interaction, GONE)
        return
    if job.status != "draft":
        await _tell(interaction, DECIDED)
        return
    stopped = await bot.recap_feedback_gate(campaign, job)
    if stopped is not None:  # the plan and the limit, before the AI call costs anything
        await _tell(interaction, stopped)
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    sorted_ = await fb.sort_feedback(ai, feedback)
    if sorted_ is None:
        await interaction.followup.send(fb.BROKEN, ephemeral=True, allowed_mentions=NO_PINGS)
        return
    await present(
        lambda text, view: interaction.followup.send(
            text, view=view or discord.utils.MISSING, ephemeral=True, allowed_mentions=NO_PINGS
        ),
        sorted_,
        bot.recaps,
        campaign,
        session_at,
    )


async def present(
    send: Callable[[str, discord.ui.View | None], Awaitable[Any]],
    sorted_: fb.Sorted,
    recaps: Any,
    campaign: Campaign,
    session_at: int,
) -> None:
    """Show what was sorted, privately: the plain replies, what was kept for this recap with a
    Try again, and each style note with Save / Edit / Don't keep. `send` is private to the DM (an
    ephemeral reply, or a private message for feedback spoken to the sidebar)."""
    # Counts only, never the words.
    log.info(
        "Recap feedback: %d style, %d corrections, %d replies",
        len(sorted_.style),
        len(sorted_.corrections),
        len(sorted_.replies),
    )
    if sorted_.empty:
        await send(fb.NOTHING_REPLY, None)
        return
    for reply in sorted_.replies:
        await send(reply, None)
    if sorted_.corrections:
        if not await recaps.add_corrections(
            campaign.guild_id, campaign.id, session_at, sorted_.corrections
        ):
            await send(DECIDED, None)
        else:
            lines = "\n".join(f"• {c}" for c in sorted_.corrections)
            again = AgainView(campaign.guild_id, campaign.id, session_at)
            again.message = await send(f"{ASK_AGAIN}\n{lines}", again)
    for note in sorted_.style:
        view = NoteView(campaign.guild_id, campaign.id, note)
        view.message = await send(f"I would keep this note for your next recaps:\n• {note}", view)


class _Timed(discord.ui.View):
    """A view that says so when its buttons stop working, rather than failing silently: the
    message it is on (set by `present` from what `send` returned) is edited to take them away."""

    message: Any = None

    async def on_timeout(self) -> None:
        if self.message is None:
            return
        # The message may be gone, or the private reply's 15 minutes over: nothing to take away.
        with contextlib.suppress(discord.HTTPException):
            await self.message.edit(content=TIMED_OUT, view=None)


class AgainView(_Timed):
    """Try again, with what the DM corrected. A private message that can't outlive itself, so it
    needs no stored button IDs; the DM is checked again on the press."""

    def __init__(self, guild_id: int, campaign_id: str, session_at: int) -> None:
        super().__init__(
            timeout=840
        )  # inside the private reply's 15 minutes, so on_timeout can still edit
        self.guild_id, self.campaign_id, self.session_at = guild_id, campaign_id, session_at

    @discord.ui.button(label="Try again", style=discord.ButtonStyle.primary)
    async def again(self, interaction: discord.Interaction, button: discord.ui.Button[Any]) -> None:
        campaign = await dm_campaign(interaction, self.campaign_id, self.guild_id)
        if campaign is None:
            return
        store = _bot(interaction).recaps
        ok = store is not None and await store.try_again(
            campaign.guild_id, campaign.id, self.session_at
        )
        self.stop()
        await interaction.response.edit_message(
            content=AGAIN_STARTED if ok else AGAIN_NOT_NOW, view=None, allowed_mentions=NO_PINGS
        )


class NoteView(_Timed):
    """Save / Edit / Don't keep for one style note. Saved only on Save."""

    def __init__(self, guild_id: int, campaign_id: str, note: str) -> None:
        super().__init__(
            timeout=840
        )  # inside the private reply's 15 minutes, so on_timeout can still edit
        self.guild_id, self.campaign_id, self.note = guild_id, campaign_id, note

    async def _save(self, interaction: discord.Interaction, note: str) -> None:
        campaign = await dm_campaign(interaction, self.campaign_id, self.guild_id)
        if campaign is None:
            return
        notes = _bot(interaction).recap_notes
        if notes is None:
            await _tell(interaction, NO_FEEDBACK)
            return
        try:
            _, dropped = await notes.add(campaign.guild_id, campaign.id, interaction.user.id, note)
        except CampaignError as problem:
            await _tell(interaction, str(problem))
            return
        self.stop()
        text = SAVED + (SAVED_DROPPED if dropped else "")
        if interaction.type == discord.InteractionType.modal_submit:
            # The edit form was answered (and the AI asked) first, so this is a follow-up; the
            # note's own message loses its buttons, which would only fail now.
            await interaction.followup.send(text, ephemeral=True, allowed_mentions=NO_PINGS)
            if self.message is not None:  # (the stored message: an ephemeral follow-up can't be
                with contextlib.suppress(discord.HTTPException):  # edited through the channel)
                    await self.message.edit(content=text, view=None)
        else:
            await interaction.response.edit_message(
                content=text, view=None, allowed_mentions=NO_PINGS
            )

    @discord.ui.button(label="Save", style=discord.ButtonStyle.success)
    async def save(self, interaction: discord.Interaction, button: discord.ui.Button[Any]) -> None:
        await self._save(interaction, self.note)

    @discord.ui.button(label="Edit", style=discord.ButtonStyle.secondary)
    async def edit(self, interaction: discord.Interaction, button: discord.ui.Button[Any]) -> None:
        if await dm_campaign(interaction, self.campaign_id, self.guild_id) is not None:
            await interaction.response.send_modal(EditNoteModal(self))

    @discord.ui.button(label="Don't keep", style=discord.ButtonStyle.secondary)
    async def drop(self, interaction: discord.Interaction, button: discord.ui.Button[Any]) -> None:
        if await dm_campaign(interaction, self.campaign_id, self.guild_id) is None:
            return
        self.stop()
        await interaction.response.edit_message(
            content=NOT_KEPT, view=None, allowed_mentions=NO_PINGS
        )


class EditNoteModal(discord.ui.Modal, title="Edit the note"):
    text: discord.ui.TextInput[EditNoteModal] = discord.ui.TextInput(
        label="How recaps should sound", max_length=NOTE_CHARS
    )

    def __init__(self, view: NoteView) -> None:
        super().__init__()
        self.view_ = view
        self.text.default = view.note

    async def on_submit(self, interaction: discord.Interaction) -> None:
        edited = " ".join(str(self.text.value).split())
        view = self.view_
        campaign = await dm_campaign(interaction, view.campaign_id, view.guild_id)
        if campaign is None:
            return
        # An edit is sorted again like the first words were: it is kept only as exactly one style
        # note, never as a correction or a request to invent (the same gate, the same AI call).
        bot = _bot(interaction)
        ai = bot.recap_feedback_ai
        if ai is None:
            await _tell(interaction, NO_FEEDBACK)
            return
        stopped = await bot.recap_feedback_gate(campaign)  # (no draft needed to edit a note)
        if stopped is not None:
            await _tell(interaction, stopped)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        sorted_ = await fb.sort_feedback(ai, edited)
        if sorted_ is None:
            await interaction.followup.send(fb.BROKEN, ephemeral=True, allowed_mentions=NO_PINGS)
            return
        # (what the AI wrote back is not kept: the DM's own words are, once it agrees they are one
        # style note)
        if (
            len(sorted_.style) != 1
            or sorted_.corrections
            or sorted_.replies
            or fb.asks_to_invent(edited)
        ):
            invent = fb.asks_to_invent(edited) or fb.STORY_HELP_REPLY in sorted_.replies
            await interaction.followup.send(
                fb.STORY_HELP_REPLY if invent else NOT_A_NOTE,
                ephemeral=True,
                allowed_mentions=NO_PINGS,
            )
            return
        await view._save(interaction, edited)


__all__ = ["MAX_NOTES", "handle", "open_form"]
