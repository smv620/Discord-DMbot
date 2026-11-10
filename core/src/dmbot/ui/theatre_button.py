"""The DM's "Make a script of this session" button (#3244, slice 5c): the question that comes with
a live session's other after-session questions, which says what it will cost right in the question,
so one press makes the script (no second "are you sure?" button), and the job
(`transcript.theatre_job`) with its progress card.

Everything private: the button works for a DM of the campaign only (checked on every press, the
DM screen can be seen by players), the confirm and the progress card are replies only the DM sees,
and the script is the DM's own copy (never the table's chat, never players). The AI is given only
the names and things the *table* knows and only lines from people who agreed (`theatre_run`).
Counts and stages only on the card.
"""

from __future__ import annotations

import contextlib
import logging
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

import discord

from dmbot import credits
from dmbot.dm_questions import SCRIPT, Ask
from dmbot.mentions import NO_PINGS
from dmbot.transcript import rel_run
from dmbot.transcript import theatre_job as job
from dmbot.transcript.theatre_runs import TheatreRuns
from dmbot.ui import dm_links
from dmbot.ui.dmbot_commands import _bot, _tell

log = logging.getLogger(__name__)

ONLY_DMS = "Only a DM of this campaign can make a script."
OFF = "Scripts are switched off."
NO_PLAN = "Scripts need the story memory that comes with a paid campaign."
GONE = "That session is gone."
OLD_QUESTION = "That question is from before the script button changed. Here it is again:"
ALREADY_MADE = "A script was already made for this session."


def ask_text(lines: int) -> str:
    """The question: what it makes, what it costs and who can read it, so the button is the one
    and only "yes" (a second confirm after the press was the same question twice)."""
    return (
        '📜 **Make a script of this session?** Each "he", "it" and "that one" gets the name it '
        f"means, in brackets.\n{job.estimate_line(lines)} {job.PRIVATE_NOTE}\n"
        "Nothing is used until you press the button."
    )


class ScriptButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=r"dmbot:script2:(?P<guild>[0-9]{1,20}):(?P<session>[0-9a-f]{32})",
):
    """In the DM's private questions: opens the confirm step. Works after a restart (the server
    and session are in the button's ID)."""

    def __init__(self, guild_id: int, session_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label="Make a script",
                emoji="📜",
                style=discord.ButtonStyle.primary,
                custom_id=f"dmbot:script2:{guild_id}:{session_id}",
            )
        )
        self.guild_id, self.session_id = guild_id, session_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> ScriptButton:
        return cls(int(match["guild"]), match["session"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        await press(interaction, self.guild_id, self.session_id)


class OldScriptButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=r"dmbot:script:(?P<guild>[0-9]{1,20}):(?P<session>[0-9a-f]{32})",
):
    """The button of a question sent before one press made the script. Those questions promised a
    cost screen first, so pressing one makes nothing and spends nothing: after the usual checks it
    answers with the new question and its one-press button for the same session."""

    def __init__(self, guild_id: int, session_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label="Make a script",
                emoji="📜",
                style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:script:{guild_id}:{session_id}",
            )
        )
        self.guild_id, self.session_id = guild_id, session_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> OldScriptButton:
        return cls(int(match["guild"]), match["session"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        await press_old(interaction, self.guild_id, self.session_id)


def ask_for(guild_id: int, session_id: str, lines: int) -> Ask:
    """The question for the DM's private message after a session (`lines`: how many it has, for
    the cost it names)."""
    text = ask_text(lines)
    return Ask(
        SCRIPT,
        text,
        "a script of the session can be made",
        lambda: ScriptButton(guild_id, session_id),
        lambda: ScriptButton(guild_id, session_id),
        text,
    )


async def _checked(interaction: discord.Interaction, guild_id: int, session_id: str) -> Any:
    """The checks both buttons make before anything is shown or started: the session is there, the
    presser is one of the campaign's DMs, the switch and the plan allow it. Tells the presser why
    not and returns None; otherwise the session. The interaction is already answered."""
    bot = _bot(interaction)
    session = await bot.transcripts.session(guild_id, session_id) if bot.transcripts else None
    if session is None:
        await _tell(interaction, GONE)
        return None
    campaign = await bot.campaigns.get(guild_id, session.campaign_id)
    if campaign is None or interaction.user.id not in campaign.dm_user_ids:
        await _tell(interaction, ONLY_DMS)
        return None
    if not bot.settings.theatre_on or bot.theatre_ai is None or bot.memory is None:
        await _tell(interaction, OFF)
        return None
    if not await bot.plan_allows("story", guild_id, campaign, fail_open=False):
        await _tell(interaction, NO_PLAN)
        return None
    return session


async def _already_made(
    interaction: discord.Interaction, guild_id: int, session_id: str, session: Any
) -> bool:
    """A script of this session is there: say so, with its page. The question's button stays on
    its message, so a later press (scrolling back, a second DM) must not spend a second run."""
    bot = _bot(interaction)
    if await TheatreRuns(bot.campaigns.db).state(guild_id, session_id) != "done":
        return False
    link = script_link(bot.settings.site_url, guild_id, session.campaign_id, session.number)
    await _tell(interaction, f"{ALREADY_MADE} {link}".strip())
    return True


async def press(interaction: discord.Interaction, guild_id: int, session_id: str) -> None:
    """The button was pressed: check who and what, then make the script. Answered first (the
    checks below can take a moment); the same private card is then kept current by `run`."""
    await interaction.response.defer(ephemeral=True)
    session = await _checked(interaction, guild_id, session_id)
    if session is None or await _already_made(interaction, guild_id, session_id, session):
        return
    await interaction.edit_original_response(content=job.TITLE)
    await run(interaction, guild_id, session_id)


async def press_old(interaction: discord.Interaction, guild_id: int, session_id: str) -> None:
    """The button of a question sent before one press made the script (it promised a cost screen
    first). It starts nothing: after the same checks it answers privately with the new question
    and its one-press button for this session, so the DM still gets to make the script."""
    await interaction.response.defer(ephemeral=True)
    session = await _checked(interaction, guild_id, session_id)
    if session is None or await _already_made(interaction, guild_id, session_id, session):
        return
    view = discord.ui.View(timeout=None)
    view.add_item(ScriptButton(guild_id, session_id))
    await _tell(interaction, f"{OLD_QUESTION}\n\n{ask_text(session.lines)}", view=view)


def _plain_name(bot: Any, guild_id: int, user_id: int) -> str | None:
    """The person's display name, or None when DMbot cannot see them (then the AI is given
    "Speaker N" and never a Discord id)."""
    name: str = bot.name_of(guild_id, user_id)
    return None if name.startswith("<@") else name


def script_link(site_url: str, guild_id: int, campaign_id: str, number: int) -> str:
    """The session's page on the website (its Transcripts page), where the script is read and
    saved. Empty when there is no site address or the session has no number yet."""
    if not site_url or number <= 0:
        return ""
    return f"{site_url.rstrip('/')}/account/transcripts/{guild_id}.{campaign_id}/{number}"


async def run(interaction: discord.Interaction, guild_id: int, session_id: str) -> job.Outcome:
    """Make the script and keep the DM's card current. The checks are made again here: a minute
    passed since the press (the switch, the plan, who is asking, who still agrees)."""
    bot = _bot(interaction)
    assert bot.transcripts is not None
    session = await bot.transcripts.session(guild_id, session_id)
    campaign = await bot.campaigns.get(guild_id, session.campaign_id) if session else None
    user = interaction.user.id
    problem = ONLY_DMS
    if session is not None and campaign is not None and user in campaign.dm_user_ids:
        if not bot.settings.theatre_on or bot.theatre_ai is None or bot.memory is None:
            problem = OFF
        elif not await bot.plan_allows("story", guild_id, campaign, fail_open=False):
            problem = NO_PLAN
        else:
            problem = ""
    if problem or session is None or campaign is None:
        await interaction.edit_original_response(content=problem or GONE)
        return job.Outcome(problem or GONE, started=False)
    edit_failed = False

    async def edit(text: str) -> None:
        nonlocal edit_failed
        try:
            await interaction.edit_original_response(content=text, allowed_mentions=NO_PINGS)
            edit_failed = False
        except discord.HTTPException:
            edit_failed = True  # (the card's 15 minutes ran out, or it was dismissed)

    outcome = await make_for(bot, guild_id, session_id, session, campaign, edit)
    if not outcome.started and not outcome.taken_over:
        await edit(outcome.text)  # a second card for a run already going: say so, never hang
    elif outcome.started and not outcome.taken_over and edit_failed:
        # The last word still reaches the DM, with the script's page on the website (#3663).
        site = bot.settings.site_url
        # (the address is built here, but only for a site Discord would take: an https one)
        page = (
            script_link(site, guild_id, session.campaign_id, session.number)
            if dm_links.site_link(site, "transcripts", guild_id, session.campaign_id)
            else ""
        )
        view = dm_links.for_campaign(
            None,
            site,
            guild_id,
            session.campaign_id,
            dm_links.DMLinks(page="transcripts", site_url_override=page),
        )
        await bot.sidebar_send_dm(user, outcome.text, view)
    return outcome


async def make_for(
    bot: Any,
    guild_id: int,
    session_id: str,
    session: Any,
    campaign: Any,
    edit: Callable[[str], Awaitable[None]],
) -> job.Outcome:
    """Make a session's script with the consent re-checked as the job runs (`edit` shows its
    progress: the DM's private card, or nothing when DMbot makes it by itself, #3901). The caller
    has checked who asked, the switch and the plan."""
    ai, memory = bot.theatre_ai, bot.memory
    assert ai is not None and memory is not None  # (the switch check above)

    async def ask(instructions: str, scene: str) -> Any:
        return await ai.complete(instructions, scene)

    async def agreed_now() -> Any:
        return await bot.consent.consenting(guild_id)

    agreed = await agreed_now()
    lines = await bot.transcripts.lines(guild_id, session_id)
    names = {}
    for uid in {x.user_id for x in lines if x.user_id in agreed}:
        name = _plain_name(bot, guild_id, uid)
        if name is not None:
            names[uid] = name
    # A script for an imported session is made because the DM imported it, so it is metered as AI
    # credits; a live session's script stays in the hours (#4005).
    metered = (
        credits.preparing(guild_id, session.campaign_id, "script")
        if await bot.transcripts.is_import(guild_id, session_id)
        else contextlib.nullcontext()
    )
    with metered:
        outcome = await job.make_script(
            bot.campaigns.db,
            ask,
            guild_id,
            session_id,
            agreed=agreed,
            names=names,
            known=await memory.table_names(guild_id, session.campaign_id),
            still_agreed=agreed_now,
            edit=edit,
            link=script_link(bot.settings.site_url, guild_id, session.campaign_id, session.number),
        )
        if outcome.ok and bot.settings.rel_read_on and bot.voyage is not None:
            await read_relationships(
                bot, guild_id, session_id, ai, agreed_now, names, memory, campaign, edit
            )
    return outcome


async def read_relationships(
    bot: Any,
    guild_id: int,
    session_id: str,
    ai: Any,
    agreed_now: Any,
    names: Mapping[int, str],
    memory: Any,
    campaign: Any,
    edit: Any = None,
) -> None:
    """Beside the script (#3244 slice 6), only with DMBOT_REL_READ_ON (off; the test server only):
    read the session's relationships once for dev1's comparison. Nothing the DM sees changes, the
    result is read by nothing else, and a failure is logged by class only and never shows."""
    try:
        agreed = await agreed_now()
        voyage = bot.voyage

        async def ask(instructions: str, window: str) -> Any:
            return await ai.complete(instructions, window)

        async def embed(texts: Sequence[str], as_query: bool) -> list[list[float]]:
            return list(await voyage.embed(texts, query=as_query))

        async def progress(p: rel_run.Progress) -> None:
            if edit is not None:  # the wait shows its stage, part n of m and time (counts only)
                done = p.stage in (rel_run.STAGE_DONE, rel_run.STAGE_STOPPED)
                await edit(
                    f"{'Finished reading' if done else 'Reading'} who is tied to whom: part "
                    f"{p.part} of {p.of}, {int(p.elapsed)}s so far."
                )

        report = await rel_run.run(
            bot.campaigns.db, ask, embed, guild_id, session_id,
            agreed=agreed, names=names, still_agreed=agreed_now, progress=progress,
            known=await memory.table_names(guild_id, campaign.id),
        )  # fmt: skip
        log.info(
            "Relationships read: %d windows, %d ties, %d kept, %d dropped, counts %s",
            report.windows, report.ties, report.kept, report.dropped, dict(report.counts),
        )  # fmt: skip
    except Exception as exc:
        log.warning("The relationship reader failed (%s)", type(exc).__name__)
