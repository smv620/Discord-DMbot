"""The DM's "Make a script of this session" button (#3244, slice 5c): the question that comes with
a live session's other after-session questions, a confirm step that says what it will cost, and the
job (`transcript.theatre_job`) with its progress card.

Everything private: the button works for a DM of the campaign only (checked on every press, the
DM screen can be seen by players), the confirm and the progress card are replies only the DM sees,
and the script is the DM's own copy (never the table's chat, never players). The AI is given only
the names and things the *table* knows and only lines from people who agreed (`theatre_run`).
Counts and stages only on the card.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from typing import Any

import discord

from dmbot.dm_questions import SCRIPT, Ask
from dmbot.mentions import NO_PINGS
from dmbot.transcript import rel_run
from dmbot.transcript import theatre_job as job
from dmbot.ui.dmbot_commands import _bot, _tell

log = logging.getLogger(__name__)

ONLY_DMS = "Only a DM of this campaign can make a script."
OFF = "Scripts are switched off."
NO_PLAN = "Scripts need the story memory that comes with a paid campaign."
GONE = "That session is gone."
ASK_TEXT = (
    '📜 **A script of this session** can be made: every "he", "it" and "that one" named in '
    "brackets, for the DMs alone.\nPress the button; it tells you what it will cost before it "
    "spends anything, and only the DMs can read it."
)
CONFIRM_YES = "Make the script"
CONFIRM_NO = "Cancel"


class ScriptButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=r"dmbot:script:(?P<guild>[0-9]{1,20}):(?P<session>[0-9a-f]{32})",
):
    """In the DM's private questions: opens the confirm step. Works after a restart (the server
    and session are in the button's ID)."""

    def __init__(self, guild_id: int, session_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label="Make a script",
                emoji="📜",
                style=discord.ButtonStyle.primary,
                custom_id=f"dmbot:script:{guild_id}:{session_id}",
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


def ask_for(guild_id: int, session_id: str) -> Ask:
    """The question for the DM's private message after a session."""
    return Ask(
        SCRIPT,
        ASK_TEXT,
        "a script of the session can be made",
        lambda: ScriptButton(guild_id, session_id),
        lambda: ScriptButton(guild_id, session_id),
        ASK_TEXT,
    )


async def press(interaction: discord.Interaction, guild_id: int, session_id: str) -> None:
    """The button was pressed: check who and what, then show the confirm step."""
    bot = _bot(interaction)
    session = await bot.transcripts.session(guild_id, session_id) if bot.transcripts else None
    if session is None:
        await _tell(interaction, GONE)
        return
    campaign = await bot.campaigns.get(guild_id, session.campaign_id)
    if campaign is None or interaction.user.id not in campaign.dm_user_ids:
        await _tell(interaction, ONLY_DMS)
        return
    if not bot.settings.theatre_on or bot.theatre_ai is None or bot.memory is None:
        await _tell(interaction, OFF)
        return
    if not await bot.plan_allows("story", guild_id, campaign, fail_open=False):
        await _tell(interaction, NO_PLAN)
        return
    view = Confirm(guild_id, session_id, interaction.user.id)
    await _tell(interaction, f"{job.estimate_line(session.lines)}\n{job.PRIVATE_NOTE}", view=view)


class Confirm(discord.ui.View):
    """Make it, or not. Only the DM who pressed can answer it."""

    def __init__(self, guild_id: int, session_id: str, user_id: int) -> None:
        super().__init__(timeout=600)
        self.guild_id, self.session_id, self.user_id = guild_id, session_id, user_id
        yes: discord.ui.Button[Confirm] = discord.ui.Button(
            label=CONFIRM_YES, style=discord.ButtonStyle.primary
        )
        no: discord.ui.Button[Confirm] = discord.ui.Button(
            label=CONFIRM_NO, style=discord.ButtonStyle.secondary
        )
        yes.callback = self._make  # type: ignore[method-assign]
        no.callback = self._cancel  # type: ignore[method-assign]
        self.add_item(yes)
        self.add_item(no)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.user_id

    async def _cancel(self, interaction: discord.Interaction) -> None:
        self.stop()
        await interaction.response.edit_message(content="Cancelled. Nothing was spent.", view=None)

    async def _make(self, interaction: discord.Interaction) -> None:
        self.stop()
        await interaction.response.edit_message(content=job.TITLE, view=None)
        await run(interaction, self.guild_id, self.session_id)


def _plain_name(bot: Any, guild_id: int, user_id: int) -> str | None:
    """The person's display name, or None when DMbot cannot see them (then the AI is given
    "Speaker N" and never a Discord id)."""
    name: str = bot.name_of(guild_id, user_id)
    return None if name.startswith("<@") else name


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
    ai, memory = bot.theatre_ai, bot.memory
    assert ai is not None and memory is not None  # (the switch check above)

    async def ask(instructions: str, scene: str) -> Any:
        return await ai.complete(instructions, scene)

    async def agreed_now() -> Any:
        return await bot.consent.consenting(guild_id)

    edit_failed = False

    async def edit(text: str) -> None:
        nonlocal edit_failed
        try:
            await interaction.edit_original_response(content=text, allowed_mentions=NO_PINGS)
            edit_failed = False
        except discord.HTTPException:
            edit_failed = True  # (the card's 15 minutes ran out, or it was dismissed)

    agreed = await agreed_now()
    lines = await bot.transcripts.lines(guild_id, session_id)
    names = {}
    for uid in {x.user_id for x in lines if x.user_id in agreed}:
        name = _plain_name(bot, guild_id, uid)
        if name is not None:
            names[uid] = name
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
    )
    if outcome.ok and bot.settings.rel_read_on and bot.voyage is not None:
        await read_relationships(
            bot, guild_id, session_id, ai, agreed_now, names, memory, campaign, edit
        )
    if not outcome.started and not outcome.taken_over:
        await edit(outcome.text)  # a second card for a run already going: say so, never hang
    elif outcome.started and not outcome.taken_over and edit_failed:
        await bot.sidebar_send_dm(user, outcome.text)  # the last word still reaches the DM
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
