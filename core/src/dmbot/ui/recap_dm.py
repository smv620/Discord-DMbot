"""The recap on the DM screen (#1455): when a recap is ready the DM can read it, post it to the
table or skip it without leaving Discord. Three buttons that carry the campaign and the session,
so they work after a restart. Private to the campaign's DMs: the campaign is checked on every press.

Nothing here decides anything itself. **Post** asks once ("Post it to the table now?") because it is
public, then makes the same move as the website's Post (`RecapStore.decide`) and lets the bot's own
`post_recaps` do the sending, which checks the plan, who is still recorded and the secret names once
more. **Not this one** is the website's "Don't post". Editing the words stays on the website.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import discord

from dmbot.discord_limits import MESSAGE_MAX
from dmbot.memory.embedding import split_text
from dmbot.mentions import NO_PINGS
from dmbot.recaps import approval, cues
from dmbot.recaps import post as recap_post
from dmbot.recaps.notes import SessionNotesStore, numbered
from dmbot.recaps.notify import page_link
from dmbot.ui import recap_feedback
from dmbot.ui.decision_lines import with_outcome
from dmbot.ui.dmbot_commands import _bot, _tell
from dmbot.ui.recap_share import ShareButton

log = logging.getLogger(__name__)

NOT_YOURS = "Only this campaign's DM can do this. Ask your DM."
GONE = "I can't find this recap any more. Nothing was changed. Check your recap page."
DECIDED = "This recap was already posted or skipped, so nothing was changed."
NOT_READY = "This recap isn't ready yet. Try again in a minute."
ASK = "Post it to the table now? The players will all see it."
SKIPPED = "👍 Skipped. Nothing was posted. You can still find it on your recap page."
POSTED = "✅ Posted to the table. The players can see it now."
NO_STORE = "Recaps aren't available right now. Try again in a minute."
NOT_NOW = "Okay, nothing was posted. Press Post to the table whenever you're ready."
NOTES_LABEL = "📝 Session notes"  # the recap line's button (#3026)
NO_NOTES = "There are no notes for this session yet."
NOTES_FAILED = "I couldn't read the notes just now. Nothing was changed. Try again in a minute."
NOTES_HEADING = "**Session notes**"
NOTES_PART_CHARS = MESSAGE_MAX - 100  # the most a part holds, leaving room for the heading

_TEMPLATE = (
    r"dmbot:recap:(?P<act>read|post|yes|notes|no|drop|tell|approve|ap|aa|ak|cv|cm|cb|ct):"
    r"(?P<campaign>[0-9a-f]{32}):(?P<at>[0-9]{1,12})"
)
_LABELS = {
    "read": ("Read it", discord.ButtonStyle.primary),
    # The session's beats as a numbered list, privately (the session notes, #3026).
    "notes": (NOTES_LABEL, discord.ButtonStyle.secondary),
    "post": ("Post to the table", discord.ButtonStyle.success),
    "drop": ("Not this one", discord.ButtonStyle.secondary),
    "tell": ("💬 Tell DMbot what you think", discord.ButtonStyle.secondary),
    "yes": ("Yes", discord.ButtonStyle.success),
    "no": ("Not now", discord.ButtonStyle.secondary),
    "approve": ("✅ Approve the text", discord.ButtonStyle.primary),
    # The three plain ways to approve (#2420), the same words as the recap page (#2419): post the
    # text, make audio to review first, or keep it without posting.
    "ap": ("✅ Approve and post text only", discord.ButtonStyle.success),
    "aa": ("🎙️ Approve and make audio to review", discord.ButtonStyle.primary),
    "ak": ("📋 Approve but don't post", discord.ButtonStyle.secondary),
    # After approving: what to make (#2083). cv, cm, cb, ct: voice-over, music, both, text only.
    "cv": (approval.LABELS[approval.VOICE], discord.ButtonStyle.primary),
    "cm": (approval.LABELS[approval.MUSIC], discord.ButtonStyle.primary),
    "cb": (approval.LABELS[approval.BOTH], discord.ButtonStyle.primary),
    "ct": (approval.LABELS[approval.TEXT], discord.ButtonStyle.secondary),
}
_CHOICE = {
    "cv": approval.VOICE,
    "cm": approval.MUSIC,
    "cb": approval.BOTH,
    "ct": approval.TEXT,
}
CUES_FIRST = "Fix these first. Nothing was approved:"
APPROVED_ALREADY = "You already approved this text. Edit it to change what you approved."
ASK_AUDIO = "The cues look right. What should DMbot make?"
APPROVED = {
    approval.VOICE: "✅ Approved. DMbot is making the voice-over. Nothing is posted. "
    "Listen to it on your recap page when it is ready.",
    approval.MUSIC: "✅ Approved. DMbot is making the background music. Nothing is posted. Listen "
    "to it on your recap page when it is ready.",
    approval.BOTH: "✅ Approved. DMbot is making the voice-over and music. Nothing is posted. "
    "Listen to them on your recap page when they are ready.",
    approval.TEXT: "✅ Approved. No voice-over or music will be made.",
}
KEPT = "📋 Approved. Nothing was posted. Copy, share or post it later from your recap page."
# Why the audio button is off (#2420): the page's words. A co-DM is never told the owner's plan
# (#1470), and this line is seen by every DM, so only "not set up" is specific.
AUDIO_WHY = {
    "not_set_up": "Audio isn't available yet, so the audio button is off.",
    "unavailable": "Audio isn't available for this campaign right now, so the audio button is off.",
}
ASK_WHICH_AUDIO = (
    "Which audio should DMbot make? Pressing one approves the text. Nothing is posted."
)
PLOT_OPEN = (
    "PlotBot has points about this recap that need your answer first. Open your recap page "
    '({link}), answer them ("Leave it as is" is always an answer), then approve.'
)
CHANGED = (
    "The text changed after you approved it, so nothing was sent. Read it and approve it again."
)
TOO_LATE = "I couldn't approve that now. The text changed, or the audio is already being made."
NO_AUDIO = (
    "Voice-over and music can't be made for this campaign right now (the plan, the narration "
    "switch or the key). You can approve it as text only."
)
MAX_SHOWN = 8  # cue problems listed in one message


def ready_text(title: str, link: str, audio_why: str = "") -> str:
    """The DM-screen line that goes with the buttons: what it is, that nothing has gone to the
    players yet, and where to change the words. `audio_why` (#2420): why the audio button is off,
    in the page's words, when it is."""
    where = f"\nRead, edit and post it on your recap page: {link}" if link else ""
    off = f"\n{audio_why}" if audio_why else ""
    return (
        f"📜 Recap ready: **{discord.utils.escape_markdown(title)}**\n"
        f"Nothing goes to the players until you post it.{where}{off}"
    )


def reading_text(job: Any, link: str) -> str:
    """What "Read it" posts: the story as the table would read it, without the posted-by line."""
    text = recap_post.to_the_table(job, footer=False)
    if link:
        text += f"\n\nWant to change it first? Edit it here: {link}"
    return text


def _plot_open_line(bot: Any, guild_id: int, campaign_id: str, job: Any) -> str:
    """What the DM is told when PlotBot has points that need an answer first, with the page."""
    page = (
        page_link(bot.settings.site_url, guild_id, campaign_id, job.session_at)
        if bot.settings.site_url
        else "on the website"
    )
    return PLOT_OPEN.format(link=page)


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
        self.key = ("recap", campaign_id, session_at)  # the item, for the other copies of its line

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> RecapButton:
        return cls(match["act"], match["campaign"], int(match["at"]))

    async def _show_notes(self, interaction: discord.Interaction, bot: Any, guild_id: int) -> None:
        """The session's beats as a numbered list, privately, in as many messages as it takes. Only
        the beats' own text: PlotBot's notes and keeps are never shown. Nothing is written."""
        await interaction.response.defer(ephemeral=True, thinking=False)
        try:
            notes = await SessionNotesStore(bot.recaps.db).get(
                guild_id, self.campaign_id, self.session_at
            )
        except Exception:
            log.exception("Session notes: couldn't read them")  # ids only, never the notes' words
            await _tell(interaction, NOTES_FAILED)
            return
        text = numbered(notes) if notes is not None else ""
        if not text:
            await _tell(interaction, NO_NOTES)
            return
        for index, part in enumerate(split_text(text, NOTES_PART_CHARS)):
            body = f"{NOTES_HEADING}\n{part}" if index == 0 else part
            await interaction.followup.send(body, ephemeral=True, allowed_mentions=NO_PINGS)

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
        # Before the recap's own state: notes can exist for a recap started over.
        if self.act == "notes":
            await self._show_notes(interaction, bot, guild.id)
            return
        job = await store.get(guild.id, campaign.id, self.session_at)
        if job is None:
            await _tell(interaction, GONE)
            return
        if self.act in ("read", "post", "drop", "ap", "ak"):  # the bot's own line, with its buttons
            bot.decision_lines.remember_pressed(self.key, interaction)
        if self.act == "no":
            await _tell(interaction, NOT_NOW)
            return
        if job.state != "ready":
            await _tell(interaction, NOT_READY)
            return
        if self.act == "tell":  # the DM's feedback on it (#2076); the form checks the DM again
            if job.status != "draft":
                await _tell(interaction, DECIDED)
                return
            await recap_feedback.open_form(interaction, self.campaign_id, self.session_at)
            return
        if self.act in ("approve", "ap", "aa", "ak") or self.act in _CHOICE:
            if job.status != "draft":
                await _tell(interaction, DECIDED)
                return
            await self._approve(interaction, bot, campaign, job)
        elif self.act == "read":
            await self._read(interaction, bot, campaign, job)
        elif self.act == "post":
            if job.status != "draft":
                await _tell(interaction, DECIDED)
                return
            await _tell(interaction, ASK, view=confirm_view(self.campaign_id, self.session_at))
        elif self.act == "yes":
            await self._post(interaction, bot, guild.id, campaign.id, job)
        else:  # drop
            if not await store.decide(guild.id, campaign.id, self.session_at, "drop"):
                await _tell(interaction, DECIDED)
                return
            message = interaction.message
            if message is not None:
                await interaction.response.edit_message(
                    content=with_outcome(message.content, "Skipped."),
                    view=None,
                    allowed_mentions=NO_PINGS,
                )
            else:
                await interaction.response.edit_message(view=None)
            await bot.decision_lines.close(
                self.key, "Skipped.", pressed_id=message.id if message is not None else None
            )
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

    async def _refused_for_problems(
        self, interaction: discord.Interaction, bot: Any, campaign: Any, job: Any
    ) -> bool:
        """The checks every approval makes, whichever button it comes from (the Approve buttons
        and a Yes on a draft that is not approved yet): the cues and the secret names. True: it
        found problems, told the DM what and where to fix them, and nothing is to be approved."""
        problems = await bot.recap_approval_problems(campaign, job)
        if not problems:
            return False
        shown = problems[:MAX_SHOWN]
        more = len(problems) - len(shown)
        tail = f"\n…and {more} more." if more > 0 else ""
        words = cues.allowed_words()
        await _tell(
            interaction,
            f"{CUES_FIRST}\n"
            + "\n".join(f"• {p}" for p in shown)
            + tail
            + "\n\nMoods go in 《 》: "
            + ", ".join(words["moods"])
            + ".\nPacing goes in (( )): "
            + ", ".join(words["pacing"])
            + ".\nEdit the text on your recap page, then press Approve again.",
        )
        return True

    async def _approve(
        self, interaction: discord.Interaction, bot: Any, campaign: Any, job: Any
    ) -> None:
        """✅ Approve the text (#2083), then what to make. Every press checks the cues and the
        secret names again, so a choice taken from an old message approves nothing the text no
        longer earns. Counts only in the log, never the text."""
        expected = approval.text_hash(job.title, job.summary)  # the text the checks below read
        if await self._refused_for_problems(interaction, bot, campaign, job):
            return
        if bot.settings.plot_check_on and await bot.recaps.plot_open(
            campaign.guild_id, campaign.id, self.session_at
        ):  # (the store checks it again in the approval's own statement)
            await _tell(interaction, _plot_open_line(bot, campaign.guild_id, campaign.id, job))
            return
        if self.act in ("ap", "aa", "ak") and job.approved and job.audio_choice:
            if self.act == "ap" and job.audio_choice == approval.TEXT:
                # Approved as text only already (kept, or a post the bot's checks stopped and put
                # back to a draft): "post" finishes it, still only for the text as approved.
                if not await bot.recaps.decide(
                    campaign.guild_id, campaign.id, self.session_at, "post", require_approved=True
                ):
                    await _tell(interaction, TOO_LATE)
                    return
                await self._sent(interaction, bot, campaign.guild_id, campaign.id)
                return
            await _tell(interaction, APPROVED_ALREADY)
            return
        if self.act == "aa":  # which audio, if audio can be made at all (#2420)
            why = await bot.recap_audio_why(campaign.guild_id, campaign.id)
            if why:
                await _tell(interaction, AUDIO_WHY[why])
                return
            await _tell(
                interaction,
                ASK_WHICH_AUDIO,
                view=audio_view(self.campaign_id, self.session_at),
            )
            return
        if self.act in ("ap", "ak"):  # text only: posted at once, or kept without posting
            posting = self.act == "ap"
            if not await bot.recaps.approve(
                campaign.guild_id,
                campaign.id,
                self.session_at,
                interaction.user.id,
                approval.TEXT,
                expected=expected,
                post=posting,
                plot_gate=bot.settings.plot_check_on,
            ):
                await _tell(interaction, TOO_LATE)
                return
            if posting:
                await self._sent(interaction, bot, campaign.guild_id, campaign.id)
            else:
                await bot.decision_lines.close(self.key, "Approved, not posted.")
                await _tell(interaction, KEPT)
            return
        if self.act == "approve":
            if job.approved and job.audio_choice:
                await _tell(interaction, APPROVED_ALREADY)
                return
            offered = approval.choices_for(
                audio_in_plan=await bot.recap_audio_wanted(campaign.guild_id, campaign.id, job)
            )
            await _tell(
                interaction,
                ASK_AUDIO,
                view=choice_view(self.campaign_id, self.session_at, offered),
            )
            return
        choice = _CHOICE[self.act]
        if choice != approval.TEXT and not await bot.recap_audio_wanted(
            campaign.guild_id, campaign.id, job
        ):
            await _tell(interaction, NO_AUDIO)
            return
        if not await bot.recaps.approve(
            campaign.guild_id,
            campaign.id,
            self.session_at,
            interaction.user.id,
            choice,
            expected=expected,
            plot_gate=bot.settings.plot_check_on,
        ):
            await _tell(interaction, TOO_LATE)
            return
        if choice != approval.TEXT:
            await bot.recap_audio_started(campaign, self.session_at)
        link = (
            page_link(bot.settings.site_url, campaign.guild_id, campaign.id, self.session_at)
            if bot.settings.site_url
            else ""
        )
        view = None
        if link:
            view = discord.ui.View(timeout=None)
            view.add_item(discord.ui.Button(label="Review the recap", emoji="📝", url=link))
        await _tell(interaction, APPROVED[choice], view=view)

    async def _post(
        self, interaction: discord.Interaction, bot: Any, gid: int, cid: str, job: Any
    ) -> None:
        """Yes was pressed: the website's own move, then the bot's own sending and checks.
        Posting a draft that is not approved counts as approving it (Supervisor, 2026-10-23,
        #2122): it goes through the same approval as the Approve buttons, so the version is kept
        and PlotBot's open points hold it."""
        if not job.approved:
            campaign = await bot.campaigns.get(gid, cid)
            if campaign is None or await self._refused_for_problems(
                interaction, bot, campaign, job
            ):
                if campaign is None:
                    await _tell(interaction, GONE)
                return
            if not await bot.recaps.approve(
                gid,
                cid,
                self.session_at,
                interaction.user.id,
                approval.TEXT,
                expected=approval.text_hash(job.title, job.summary),
                post=True,
                plot_gate=bot.settings.plot_check_on,
            ):
                if bot.settings.plot_check_on and await bot.recaps.plot_open(
                    gid, cid, self.session_at
                ):
                    await _tell(interaction, _plot_open_line(bot, gid, cid, job))
                else:
                    await _tell(interaction, DECIDED)
                return
        elif not await bot.recaps.decide(gid, cid, self.session_at, "post", require_current=True):
            # Two different refusals (#2545): it was decided meanwhile, or it is still a draft but
            # its words are no longer the ones the DM approved.
            now = await bot.recaps.get(gid, cid, self.session_at)
            await _tell(
                interaction, CHANGED if now is not None and now.status == "draft" else DECIDED
            )
            return
        await self._sent(interaction, bot, gid, cid)

    async def _sent(self, interaction: discord.Interaction, bot: Any, gid: int, cid: str) -> None:
        """The recap is handed over to be posted (by Post, or by Approve and post text only): the
        bot's own sending and checks, then what came of it, said to the DM."""
        await interaction.response.defer(ephemeral=True, thinking=False)
        await bot.post_recaps(gid)  # the same checks as the website's Post, once more
        after = await bot.recaps.get(gid, cid, self.session_at)
        if after is not None and after.status == "posted":
            await bot.decision_lines.close(self.key, "Posted.")  # the line(s) with the buttons
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


def ready_view(
    campaign_id: str,
    session_at: int,
    site: str = "",
    review_url: str = "",
    audio_off: bool = False,
) -> discord.ui.View:
    """The buttons on a new recap line (#2420): the three plain ways to approve it, in the page's
    words, then **📝 Review the recap** (the link to its page, `review_url`) and Share a recap, then
    **📝 Session notes** (#3026) on the same row in both branches.
    `audio_off`: audio can't be made now, so that button is greyed (the line says why). "Don't
    post" is gone: a recap the DM doesn't approve simply stays a draft. The older buttons (read,
    approve, post, skip, tell) still work on lines already sent, so nothing breaks. With no website
    address there is no page to review or share, so Read it takes their place."""
    view = discord.ui.View(timeout=None)
    for act in ("ap", "aa", "ak"):
        button = RecapButton(act, campaign_id, session_at)
        button.item.row = 0
        if act == "aa" and audio_off:
            button.item.disabled = True
        view.add_item(button)
    if not site:
        read = RecapButton("read", campaign_id, session_at)
        read.item.row = 1
        view.add_item(read)
    else:
        link = review_url or f"{site.rstrip('/')}/account/recaps"
        view.add_item(discord.ui.Button(label="Review the recap", emoji="📝", url=link, row=1))
        share = ShareButton(campaign_id)
        share.item.row = 1
        view.add_item(share)
    notes = RecapButton("notes", campaign_id, session_at)
    notes.item.row = 1
    view.add_item(notes)
    return view


def choice_view(
    campaign_id: str, session_at: int, offered: tuple[str, ...] = approval.CHOICES
) -> discord.ui.View:
    """Voice-over, Background music, Both and Text only: only what the plan includes."""
    view = discord.ui.View(timeout=None)
    for act, choice in _CHOICE.items():
        if choice in offered:
            view.add_item(RecapButton(act, campaign_id, session_at))
    return view


def confirm_view(campaign_id: str, session_at: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for act in ("yes", "no"):
        view.add_item(RecapButton(act, campaign_id, session_at))
    return view


def audio_view(campaign_id: str, session_at: int) -> discord.ui.View:
    """Voice-over, Background music or Both (#2420): what "Approve and make audio to review" asks.
    Text only is not here: the other two approve buttons are that."""
    view = discord.ui.View(timeout=None)
    for act, choice in _CHOICE.items():
        if choice != approval.TEXT:
            view.add_item(RecapButton(act, campaign_id, session_at))
    return view
