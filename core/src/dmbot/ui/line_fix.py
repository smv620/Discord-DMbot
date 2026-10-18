"""A player suggests a fix to one of their own lines; the DM decides (#1491).

The player runs `/dmbot fix`, or presses ✏️ Fix one of my lines in the private message that
comes when a session ends, picks the line, and types what it should say. The DM gets one short
line on the DM screen with **Accept** and **No** (buttons that work after a restart). Accept
changes only the cleaned transcript's words for that line; what was heard never changes. Nothing
is posted in a public channel, and the player hears the answer once in a private message.

Private to the speaker (their own lines only, checked in the database query) and to the
campaign's DMs for the answer, on every press. The suggested words are checked against the
campaign's secret names before they can be accepted: the cleaned transcript is open to the whole
server, so a secret identity must never be written into it.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

import discord

from dmbot.discord_limits import MESSAGE_MAX, fit_message
from dmbot.mentions import NO_PINGS
from dmbot.transcript import export, suggestions
from dmbot.transcript.cleaner import text_names_a_secret
from dmbot.transcript.stream import escape
from dmbot.ui.dmbot_commands import _bot, _Menu, _Select, _send, _tell, dmbot_group

if TYPE_CHECKING:
    from dmbot.bot import DMBot

log = logging.getLogger(__name__)

NOT_AVAILABLE = "Fixing lines isn't available right now. Try again in a minute."
NOT_IN_SERVER = "Use this in the server where you played."
NO_LINES = "No lines of yours to fix yet. They show up here after DMbot has written down a session."
NOT_RECORDED = "DMbot isn't recording you, so it has no lines of yours to fix."
PICK = "Pick the line that's wrong. Your DM decides: nothing changes unless they say yes."
MODAL_TITLE = "Fix one of your lines"
MODAL_LABEL = "What should it say?"
SAME = "That is what it already says."
BAD = f"Please type between 1 and {suggestions.MAX_CHARS} characters."
OPEN = "You already have a fix waiting for that line. Your DM hasn't answered yet."
TOO_MANY = f"You have {suggestions.MAX_OPEN} fixes waiting for your DM. Wait for their answer."
SENT = (
    "👍 Sent to your DM. Nothing changes unless they say yes. "
    "You'll get one message with the answer."
)
GONE_LINE = "I can't find that line any more."
NOT_YOURS = "Only this campaign's DM can answer this."
GONE = "I can't find this fix any more. Nothing was changed."
DECIDED = "This was already answered, so nothing was changed."
NO_DM_SCREEN = "Your DM can't see this right now. Ask them to set up the DM screen."
PLAYER_STOPPED = "That player isn't being recorded any more, so nothing was changed."
SECRET = "That would put a secret name in the transcript, so nothing was changed."
LINE_GONE = "That line isn't there any more, so nothing was changed."
LINE_CHANGED = "That line has changed since they asked, so nothing was changed."
NOT_WRITTEN = "I couldn't change the line. Try again in a moment."
ANSWER_YES = "✅ Your DM said yes: that line now says what you wrote."
# The same words whether the DM said no or the words could not be used, so a player can never
# learn from the answer whether their words named a secret.
ANSWER_NO = "👍 That line stays as it is."
LEFT = "👍 Left as it is."
CLOSED = "Closed."  # a suggestion that could not be used (the player stopped, or the line is gone)
KEPT = "Kept as is."  # the word on a copy of the line that was answered somewhere else
PLAYER_STOPPED_DM = "That person isn't being recorded any more, so nothing was changed."
NOT_WRITTEN_DM = (
    "I couldn't change that line. If it is listed in ✏️ Name fixes to check, press Undo there "
    "first, then try again."
)
UNDO_HERE = "Made a mistake? Press Undo in ✏️ Name fixes to check."
PICK_DM = (
    "Pick the line to fix. It changes right away. DMbot keeps the words it first heard. "
    "The player is not told."
)

_TEMPLATE = r"dmbot:linefix:(?P<act>yes|no):(?P<id>[0-9]{1,18})"
_FIX_BUTTON = r"dmbot:fixline:(?P<guild>[0-9]{1,20}):(?P<session>[0-9a-f]{32})"


def _short(text: str, limit: int = 120) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def dm_text(who: str, heard: str, now: str, suggested: str) -> str:
    """The DM-screen line: who, the line as it reads now, what they'd like. What was heard is
    added only when the cleaned words differ from it. Everything from the players is escaped so it
    can't format, link or ping."""
    shown = f"\nNow: “{escape(_short(now))}”"
    if _short(now) != _short(heard):
        shown += f"\nHeard: “{escape(_short(heard))}”"
    return (
        f"✏️ **{escape(who)}** suggests a fix to their line:{shown}\n"
        f"They'd like: “{escape(_short(suggested, suggestions.MAX_CHARS))}”\n"
        "Accept changes the cleaned transcript only."
    )


def decided_text(text: str, yes: bool) -> str:
    return fit_message(f"✅ Changed to: {text}" if yes else LEFT, limit=MESSAGE_MAX - 100)


class LineFixButton(discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]], template=_TEMPLATE):
    """Accept / No on the DM screen. The suggestion is in the button's ID, so it works after a
    restart; the server comes from where it is pressed, so a made-up ID finds nothing."""

    def __init__(self, act: str, suggestion_id: int) -> None:
        yes = act == "yes"
        super().__init__(
            discord.ui.Button(
                label="Accept" if yes else "Keep as is",
                style=discord.ButtonStyle.success if yes else discord.ButtonStyle.secondary,
                custom_id=f"dmbot:linefix:{act}:{suggestion_id}",
            )
        )
        self.act, self.suggestion_id = act, suggestion_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> LineFixButton:
        return cls(match["act"], int(match["id"]))

    async def callback(self, interaction: discord.Interaction) -> Any:
        bot, guild = _bot(interaction), interaction.guild
        store = bot.suggestions
        if guild is None or store is None:
            await _tell(interaction, GONE)
            return
        # Answer Discord first (#1798): the checks below are several database trips, and a DM at a
        # live table must not see "interaction failed". Every reply after this is a follow-up
        # (a button's defer is a quiet update; each follow-up sets ephemeral itself).
        await interaction.response.defer(ephemeral=True, thinking=False)
        found = await store.get(guild.id, self.suggestion_id)
        campaign = (
            await bot.campaigns.get(guild.id, found.campaign_id) if found is not None else None
        )
        if found is None or campaign is None:
            await _tell(interaction, GONE)
            return
        if interaction.user.id not in campaign.dm_user_ids:  # on every press
            await _tell(interaction, NOT_YOURS)
            return
        if found.state != suggestions.OPEN:
            await _tell(interaction, DECIDED)
            return
        bot.decision_lines.remember_pressed(("fix", found.id), interaction)
        if self.act == "no":
            if await store.decide(guild.id, found.id, suggestions.DECLINED, interaction.user.id):
                await _finish(interaction, bot, found, LEFT, ANSWER_NO, KEPT)
            else:
                await _tell(interaction, DECIDED)
            return
        await self._accept(interaction, bot, guild.id, found)

    async def _accept(
        self, interaction: discord.Interaction, bot: DMBot, guild_id: int, found: Any
    ) -> None:
        store = bot.suggestions
        assert store is not None
        by = interaction.user.id

        async def closed(state: str, shown: str, to_player: str | None) -> None:
            """Answer it without writing: only the press that closes it says so."""
            if await store.decide(guild_id, found.id, state, by):
                word = CLOSED if state == suggestions.DROPPED else KEPT
                await _finish(interaction, bot, found, shown, to_player, word)
            else:
                await _tell(interaction, DECIDED)

        # Checked now, not when it was asked: the speaker must still be recorded, the line must
        # still be there and unchanged (a name fix or another answer may have changed it), and the
        # words must not be or sound like a secret name, with the names as they are now.
        if not bot.consent.has_consent(guild_id, found.user_id):
            await closed(suggestions.DROPPED, PLAYER_STOPPED, None)
            return
        line = await store.line(guild_id, found.session_id, found.user_id, found.started_ms)
        if line is None:
            await closed(suggestions.DROPPED, LINE_GONE, None)
            return
        if " ".join(line.text.split()) != " ".join(found.base_text.split()):
            await closed(suggestions.DECLINED, LINE_CHANGED, ANSWER_NO)
            return
        lookup = await bot.lookup.get(guild_id, found.campaign_id) if bot.lookup else None
        text = found.suggested
        if lookup is None or text_names_a_secret(lookup, text):
            await closed(suggestions.DECLINED, SECRET, ANSWER_NO)
            return
        if not await store.decide(guild_id, found.id, suggestions.ACCEPTED, by):
            await _tell(interaction, DECIDED)
            return
        try:
            written = await bot.apply_line_fix(
                guild_id, found.campaign_id, found.session_id, found.user_id, found.started_ms, text
            )
        except Exception:
            log.exception("Couldn't write an accepted fix into a line")
            written = False
        if not written:
            await store.reopen(guild_id, found.id)  # the DM can try again
            await _tell(interaction, NOT_WRITTEN)
            return
        await _finish(
            interaction,
            bot,
            found,
            decided_text(f"“{escape(_short(text))}”", True),
            ANSWER_YES,
            "Accepted.",
        )


async def _finish(
    interaction: discord.Interaction,
    bot: DMBot,
    found: Any,
    shown: str,
    to_player: str | None,
    outcome: str,
) -> None:
    """Take the buttons down, say it on the DM screen, and tell the player once. Any other copy
    of the same line (the re-post at a session start, #1504) loses its buttons too, with the
    outcome word (#1509)."""
    if interaction.message is not None:
        try:
            await interaction.message.edit(content=shown, view=None, allowed_mentions=NO_PINGS)
        except discord.HTTPException:
            log.warning("Couldn't update a line-fix message")
    pressed = interaction.message.id if interaction.message is not None else None
    await bot.decision_lines.close(("fix", found.id), outcome, pressed_id=pressed)
    with_note = shown if interaction.message is None else None
    if with_note:
        await interaction.followup.send(with_note, ephemeral=True, allowed_mentions=NO_PINGS)
    if to_player is not None:
        await bot.tell_user(found.user_id, to_player)


def _option_label(line: suggestions.Own, started_at: int) -> str:
    """The time first (it survives a phone's cut), then the start of the words; the whole line is
    in the menu's description."""
    when = export.clock(line.started_ms / 1000 - started_at)
    return f"{when}  {_short(line.text, 20)}"[:100]


class _PickLine(_Menu):
    """Newest lines of one session, to pick from: the speaker's own, or (`dm`) anyone's."""

    def __init__(
        self,
        guild_id: int,
        campaign_id: str,
        session_id: str,
        lines: list[Any],
        started_at: int,
        *,
        dm: bool = False,
        names: dict[int, str] | None = None,
    ) -> None:
        super().__init__()
        self.guild_id, self.campaign_id, self.session_id = guild_id, campaign_id, session_id
        self.dm = dm
        self.lines = {str(line.started_ms): line for line in lines}
        names = names or {}
        options = [
            discord.SelectOption(
                label=_option_label(line, started_at),
                description=(
                    f"{names.get(line.user_id, 'Someone')}: {_short(line.text, 80)}"[:100]
                    if dm
                    else _short(line.text, 100)
                ),
                value=str(line.started_ms),
            )
            for line in lines
        ]
        self.add_item(_Select(self._picked, placeholder="Pick the line", options=options))

    async def _picked(self, interaction: discord.Interaction) -> None:
        select = next(c for c in self.children if isinstance(c, discord.ui.Select))
        line = self.lines.get(select.values[0])
        if line is None:
            await _tell(interaction, GONE_LINE)
            return
        await interaction.response.send_modal(
            _Words(self.guild_id, self.campaign_id, self.session_id, line, dm=self.dm)
        )


class _Words(discord.ui.Modal, title=MODAL_TITLE):
    """The corrected words, typed by the speaker."""

    def __init__(
        self,
        guild_id: int,
        campaign_id: str,
        session_id: str,
        line: suggestions.Own,
        *,
        dm: bool = False,
    ) -> None:
        super().__init__()
        self.dm = dm
        self.guild_id, self.campaign_id, self.session_id, self.line = (
            guild_id,
            campaign_id,
            session_id,
            line,
        )
        self.words: discord.ui.TextInput[Any] = discord.ui.TextInput(
            label=MODAL_LABEL,
            style=discord.TextStyle.paragraph,
            default=line.text[: suggestions.MAX_CHARS],
            max_length=suggestions.MAX_CHARS,
            required=True,
        )
        self.add_item(self.words)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        send = submit_dm if self.dm else submit
        await send(
            interaction,
            self.guild_id,
            self.campaign_id,
            self.session_id,
            self.line,
            str(self.words.value),
        )


async def submit(
    interaction: discord.Interaction,
    guild_id: int,
    campaign_id: str,
    session_id: str,
    line: suggestions.Own,
    words: str,
) -> None:
    """The speaker sent their words: check them, keep them, and put the question on the DM
    screen. The player is told "Sent" only once the DM can see it."""
    bot = _bot(interaction)
    store = bot.suggestions
    if store is None:
        await _tell(interaction, NOT_AVAILABLE)
        return
    await interaction.response.defer(ephemeral=True, thinking=False)
    user_id = interaction.user.id
    if not bot.consent.has_consent(guild_id, user_id):
        await _tell(interaction, NOT_RECORDED)
        return
    text = suggestions.tidy(words)
    if text is None:
        await _tell(interaction, BAD)
        return
    if text == " ".join(line.text.split()):
        await _tell(interaction, SAME)
        return
    campaign = await bot.campaigns.get(guild_id, campaign_id)
    channel = campaign.dm_screen_channel_id if campaign is not None else None
    if campaign is None or channel is None:
        await _tell(interaction, NO_DM_SCREEN)
        return
    # (The words are checked against the secret names only when the DM accepts: refusing them
    # here would tell a player whether a guess was a secret name.)
    if not bot.consent.has_consent(guild_id, user_id):  # again, after the waits above
        await _tell(interaction, NOT_RECORDED)
        return
    made = await store.suggest(guild_id, campaign_id, session_id, user_id, line.started_ms, text)
    if made == "open":
        await _tell(interaction, OPEN)
        return
    if made == "many":
        await _tell(interaction, TOO_MANY)
        return
    if isinstance(made, str):
        await _tell(interaction, GONE_LINE)
        return
    posted = await bot.post_message(
        channel,
        dm_text(interaction.user.display_name, line.heard, line.text, text),
        suggestion_view(made.id),
        remember=("fix", made.id),
    )
    if posted is None:  # the DM can't see it: don't leave it waiting unseen
        await store.decide(guild_id, made.id, suggestions.DROPPED, user_id)
        await _tell(interaction, NO_DM_SCREEN)
        return
    await _tell(interaction, SENT)


async def submit_dm(
    interaction: discord.Interaction,
    guild_id: int,
    campaign_id: str,
    session_id: str,
    line: suggestions.Own,
    words: str,
) -> None:
    """A DM rewrote a line (#1499): checked, then written at once through the same path as an
    accepted suggestion (`Bot.apply_line_fix`, so the saved line, the notes, the channel message
    and the helpers' copies follow), with Undo in the fix notes while the session runs. Only the
    cleaned words change, never what was heard. Nothing is told to the speaker or posted."""
    bot = _bot(interaction)
    await interaction.response.defer(ephemeral=True, thinking=False)
    campaign = await bot.campaigns.get(guild_id, campaign_id)
    if campaign is None or interaction.user.id not in campaign.dm_user_ids:  # on every press
        await _tell(interaction, NOT_YOURS)
        return
    store = bot.suggestions
    if store is None:
        await _tell(interaction, NOT_AVAILABLE)
        return
    text = suggestions.tidy(words)
    if text is None:
        await _tell(interaction, BAD)
        return
    if text == " ".join(line.text.split()):
        await _tell(interaction, SAME)
        return
    if not bot.consent.has_consent(guild_id, line.user_id):
        await _tell(interaction, PLAYER_STOPPED_DM)
        return
    now = await store.line(guild_id, session_id, line.user_id, line.started_ms)
    if now is None:
        await _tell(interaction, LINE_GONE)
        return
    if " ".join(now.text.split()) != " ".join(line.text.split()):
        await _tell(interaction, LINE_CHANGED)  # as shown to them a moment ago
        return
    lookup = await bot.lookup.get(guild_id, campaign_id) if bot.lookup else None
    if lookup is None or text_names_a_secret(lookup, text):
        await _tell(interaction, SECRET)
        return
    try:
        written = await bot.apply_line_fix(
            guild_id, campaign_id, session_id, line.user_id, line.started_ms, text, noted=True
        )
    except Exception:
        log.exception("Couldn't write a DM's edit into a line")
        written = False
    if not written:
        await _tell(interaction, NOT_WRITTEN_DM)
        return
    running = any(
        t is not None and t.transcript_session_id == session_id for t in (bot.tables.get(guild_id),)
    )
    await _tell(
        interaction,
        decided_text(f"“{escape(_short(text))}”", True) + (f"\n{UNDO_HERE}" if running else ""),
    )


def suggestion_view(suggestion_id: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for act in ("yes", "no"):
        view.add_item(LineFixButton(act, suggestion_id))
    return view


WAITING_SHOWN = 3  # fixes still waiting that the next session start shows
# True to what happens: a suggestion not answered by the end of this session is dropped (#1491).
MORE_WAITING = "…and {n} more that go when this session ends."


async def start(
    interaction: discord.Interaction, *, session_id: str | None = None, guild_id: int | None = None
) -> None:
    """Show newest lines to pick from: those of one session, or, with no session given, of the
    newest session in this server that has some. A speaker sees their own lines; a DM of the
    session's campaign sees any speaker's (#1499), and need not be recorded."""
    bot = _bot(interaction)
    gid = guild_id or (interaction.guild.id if interaction.guild else None)
    if gid is None:
        await _tell(interaction, NOT_IN_SERVER)
        return
    store = bot.suggestions
    if store is None or bot.transcripts is None:
        await _tell(interaction, NOT_AVAILABLE)
        return
    await interaction.response.defer(ephemeral=True, thinking=False)
    user_id = interaction.user.id
    if session_id is not None:
        session = await bot.transcripts.session(gid, session_id)
        candidates = [session] if session is not None else []
    else:
        candidates = []
        for campaign in await bot.campaigns.list_campaigns(gid):
            candidates += await bot.transcripts.sessions(gid, campaign.id, limit=1)
        candidates.sort(key=lambda s: s.started_at, reverse=True)
    recorded = bot.consent.has_consent(gid, user_id)
    for session in candidates:
        found = await bot.campaigns.get(gid, session.campaign_id)
        is_dm = found is not None and user_id in found.dm_user_ids
        if is_dm:
            lines = await store.recent_lines(gid, session.id)
        elif recorded:
            lines = await store.own_lines(gid, session.id, user_id)
        else:
            continue
        if lines:
            names = await _names(interaction, {line.user_id for line in lines}) if is_dm else {}
            await _send(
                interaction,
                PICK_DM if is_dm else PICK,
                _PickLine(
                    gid, session.campaign_id, session.id, lines, session.started_at,
                    dm=is_dm, names=names,
                ),
            )  # fmt: skip
            return
    await _tell(interaction, NO_LINES if recorded or candidates else NOT_RECORDED)


async def _names(interaction: discord.Interaction, ids: set[int]) -> dict[int, str]:
    """Display names for a DM's picker (not stored anywhere); unknown people are "Someone"."""
    guild = interaction.guild
    found: dict[int, str] = {}
    for uid in ids:
        member = guild.get_member(uid) if guild is not None else None
        if member is not None:
            found[uid] = member.display_name
    return found


class FixLineButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]], template=_FIX_BUTTON
):
    """On the private message when a session ends: ✏️ Fix one of my lines. Works after a
    restart (the server and session are in the ID)."""

    def __init__(self, guild_id: int, session_id: str, dm: bool = False) -> None:
        super().__init__(
            discord.ui.Button(
                label="✏️ Fix a line" if dm else "✏️ Fix one of my lines",
                style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:fixline:{guild_id}:{session_id}",
            )
        )
        self.guild_id, self.session_id = guild_id, session_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> FixLineButton:
        return cls(int(match["guild"]), match["session"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        await start(interaction, session_id=self.session_id, guild_id=self.guild_id)


@dmbot_group.command(name="fix", description="Fix a line DMbot heard wrong")
async def dmbot_fix(interaction: discord.Interaction) -> None:
    await start(interaction)


def fix_line_view(guild_id: int, session_id: str, *, dm: bool = False) -> discord.ui.View:
    """The ✏️ button alone (for the DM screen's end-of-session summary, #1499)."""
    view = discord.ui.View(timeout=None)
    view.add_item(FixLineButton(guild_id, session_id, dm))
    return view
