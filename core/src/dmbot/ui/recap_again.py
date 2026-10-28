"""Make the recap of any past session, or make it again (#2608).

`/dmbot recap` lets a campaign's DM pick a session (newest first, with its number and date and what
it has now) and have DMbot write its recap: for a session that never had one (recaps were off, or
the answer was "No recap"), one that failed or is held, or one the DM wants done again. The same
move is the **🔁 Make the recap again** button on a failed or waiting recap's line on the DM screen.

Nothing here decides anything: it asks `DMBot.recap_again`, which checks the DM, the plan and the
switches, and the database's own door for starting over does the move. A recap that is already
approved or posted is kept until the new one is approved, and the DM is asked once before starting
over. Only the campaign's DMs, checked on every press.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
from typing import Any

import discord

from dmbot.ui.dmbot_commands import NOT_IN_SERVER, _bot, _tell, dmbot_group
from dmbot.ui.recap_share import session_numbers

log = logging.getLogger(__name__)

NOT_YOURS = "Only this campaign's DM can do this. Ask your DM."
GONE = "I can't find this campaign any more. Nothing was changed."
NO_SESSIONS = "This campaign has no sessions with a transcript yet, so there's no recap to make."
PICK_CAMPAIGN = "Which campaign?"
PICK = "Which session's recap? Newest first."
ASK = (
    "This session already has a recap. Make a new one?\n"
    "The old one stays until you approve the new one."
)
KEPT_OLD = "👍 Nothing was changed. The recap stays as it is."
QUEUED = (
    "📜 Writing the recap of this session. It's on your recap page when it's ready. "
    "Nothing goes to the table until you say so."
)
QUEUED_KEEPING = QUEUED + "\nThe recap you had stays until you approve the new one."
BUSY = "DMbot is already writing or sending this session's recap. Try again when it's done."
NO_LINES = (
    "This session's transcript is gone, so DMbot can't write its recap. "
    "You can upload one instead, on your recap page."
)
OFF = "Recaps are switched off for DMbot right now, so none can be made."
NO_PLAN = "This campaign's plan doesn't include recaps."
UNCHECKED = "I can't check the plan right now. Nothing was started. Try again in a minute."
FAILED = "Something went wrong, so nothing was started. Try again in a moment."
KEPT_PREVIOUS = "↩️ The earlier recap is back. Nothing was lost."
NO_PREVIOUS = "There's no earlier recap to bring back."
TIMEOUT_S = 840  # a private reply's buttons stop working after 15 minutes

_AGAIN = r"dmbot:recapagain:(?P<campaign>[0-9a-f]{32}):(?P<at>[0-9]{1,12})"
_KEEP = r"dmbot:recapkeep:(?P<campaign>[0-9a-f]{32}):(?P<at>[0-9]{1,12})"

WORDS = {
    "queued": None,  # said with the recap's name and whether the old one stays
    "busy": BUSY,
    "no_lines": NO_LINES,
    "not_allowed": NOT_YOURS,
    "off": OFF,
    "no_plan": NO_PLAN,
    "unchecked": UNCHECKED,
}


def has_recap(state: str, status: str) -> bool:
    """A finished recap is there to be replaced (so the DM is asked first)."""
    return state == "ready" and status in ("draft", "posted")


def session_line(state: str | None, status: str | None, approved: bool) -> str:
    """What a session has, in plain words, for the picker's second line."""
    if state is None or status is None:
        return "No recap yet"
    if status == "posted":
        return "Posted"
    if status == "dropped":
        return "Skipped"
    if state == "failed":
        return "Failed"
    if state != "ready":
        return "Being written"
    return "Approved" if approved else "Written"


def date_label(started_at: int) -> str:
    return dt.datetime.fromtimestamp(started_at, dt.timezone.utc).strftime("%d %b %Y")  # noqa: UP017


async def _campaign(interaction: discord.Interaction, campaign_id: str) -> Any | None:
    """The campaign, if the person pressing is one of its DMs; otherwise they are told why."""
    bot = _bot(interaction)
    guild = interaction.guild
    campaign = await bot.campaigns.get(guild.id, campaign_id) if guild is not None else None
    if campaign is None:
        await _tell(interaction, GONE)
        return None
    if interaction.user.id not in campaign.dm_user_ids:
        await _tell(interaction, NOT_YOURS)
        return None
    return campaign


async def start(
    interaction: discord.Interaction, campaign: Any, session_at: int, *, replace_ok: bool = False
) -> None:
    """Ask for the recap and say what happened, privately. An existing one is asked about first."""
    bot = _bot(interaction)
    try:
        outcome = await bot.recap_again(
            campaign, session_at, interaction.user.id, replace_ok=replace_ok
        )
    except Exception:
        log.exception("Make the recap again failed")
        await _tell(interaction, FAILED)
        return
    if outcome == "needs_confirm":
        await _tell(interaction, ASK, view=ConfirmView(campaign.id, session_at))
    elif outcome == "queued":
        await _tell(interaction, QUEUED_KEEPING if replace_ok else QUEUED)
    else:
        await _tell(interaction, WORDS.get(outcome) or FAILED)


class AgainButton(discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]], template=_AGAIN):
    """🔁 on a failed or waiting recap's line; it works after a restart like the other buttons."""

    def __init__(self, campaign_id: str, session_at: int, *, row: int | None = None) -> None:
        super().__init__(
            discord.ui.Button(
                label="Make the recap again",
                emoji="🔁",
                style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:recapagain:{campaign_id}:{session_at}",
                row=row,
            )
        )
        self.campaign_id, self.session_at = campaign_id, session_at

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> AgainButton:
        return cls(match["campaign"], int(match["at"]))

    async def callback(self, interaction: discord.Interaction) -> Any:
        campaign = await _campaign(interaction, self.campaign_id)
        if campaign is not None:
            await start(interaction, campaign, self.session_at)


class KeepButton(discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]], template=_KEEP):
    """↩️ Put the earlier recap back, when the new one failed or is not wanted."""

    def __init__(self, campaign_id: str, session_at: int, *, row: int | None = None) -> None:
        super().__init__(
            discord.ui.Button(
                label="Keep the earlier recap",
                emoji="↩️",
                style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:recapkeep:{campaign_id}:{session_at}",
                row=row,
            )
        )
        self.campaign_id, self.session_at = campaign_id, session_at

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> KeepButton:
        return cls(match["campaign"], int(match["at"]))

    async def callback(self, interaction: discord.Interaction) -> Any:
        campaign = await _campaign(interaction, self.campaign_id)
        if campaign is None:
            return
        recaps = _bot(interaction).recaps
        try:
            back = recaps is not None and await recaps.keep_previous(
                campaign.guild_id, campaign.id, self.session_at
            )
        except Exception:
            log.exception("Keep the earlier recap failed")
            await _tell(interaction, FAILED)
            return
        await _tell(interaction, KEPT_PREVIOUS if back else NO_PREVIOUS)


def again_view(campaign_id: str, session_at: int, *, keep: bool = False) -> discord.ui.View:
    """The buttons on a failed or waiting recap's line: make it again, and (when an earlier recap is
    kept for it) bring that one back."""
    view = discord.ui.View(timeout=None)
    view.add_item(AgainButton(campaign_id, session_at))
    if keep:
        view.add_item(KeepButton(campaign_id, session_at))
    return view


class ConfirmView(discord.ui.View):
    """Asked once before an existing recap is started over."""

    def __init__(self, campaign_id: str, session_at: int) -> None:
        super().__init__(timeout=TIMEOUT_S)
        self.campaign_id, self.session_at = campaign_id, session_at

    @discord.ui.button(label="Make a new one", style=discord.ButtonStyle.primary)
    async def yes(self, interaction: discord.Interaction, button: discord.ui.Button[Any]) -> None:
        campaign = await _campaign(interaction, self.campaign_id)
        if campaign is not None:
            await start(interaction, campaign, self.session_at, replace_ok=True)

    @discord.ui.button(label="Keep the old one", style=discord.ButtonStyle.secondary)
    async def no(self, interaction: discord.Interaction, button: discord.ui.Button[Any]) -> None:
        await _tell(interaction, KEPT_OLD)


class SessionPick(discord.ui.View):
    """The campaign's sessions, newest first, at most 25 (Discord's limit for a menu)."""

    def __init__(
        self,
        campaign: Any,
        sessions: list[Any],
        numbers: dict[int, int],
        overview: dict[int, tuple[str, str, bool]],
    ) -> None:
        super().__init__(timeout=TIMEOUT_S)
        self.campaign_id = campaign.id
        options = []
        for s in sessions[:25]:
            n = numbers.get(s.started_at, getattr(s, "number", 0))
            state, status, approved = overview.get(s.started_at, (None, None, False))
            options.append(
                discord.SelectOption(
                    label=(f"Session {n} · " if n else "") + date_label(s.started_at),
                    value=str(s.started_at),
                    description=session_line(state, status, approved),
                )
            )
        menu: discord.ui.Select[Any] = discord.ui.Select(
            placeholder="Pick a session", options=options
        )
        menu.callback = self._picked  # type: ignore[method-assign]
        self.menu = menu
        self.add_item(menu)

    async def _picked(self, interaction: discord.Interaction) -> None:
        campaign = await _campaign(interaction, self.campaign_id)
        if campaign is not None:
            await start(interaction, campaign, int(self.menu.values[0]))


class CampaignPick(discord.ui.View):
    def __init__(self, campaigns: list[Any]) -> None:
        super().__init__(timeout=TIMEOUT_S)
        menu: discord.ui.Select[Any] = discord.ui.Select(
            placeholder="Pick a campaign",
            options=[discord.SelectOption(label=c.name[:100], value=c.id) for c in campaigns[:25]],
        )
        menu.callback = self._picked  # type: ignore[method-assign]
        self.menu = menu
        self.add_item(menu)

    async def _picked(self, interaction: discord.Interaction) -> None:
        campaign = await _campaign(interaction, self.menu.values[0])
        if campaign is not None:
            await show_sessions(interaction, campaign)


async def show_sessions(interaction: discord.Interaction, campaign: Any) -> None:
    bot = _bot(interaction)
    if bot.transcripts is None or bot.recaps is None:
        await _tell(interaction, OFF)
        return
    try:
        sessions = await bot.transcripts.sessions(campaign.guild_id, campaign.id, limit=25)
        overview = await bot.recaps.overview(campaign.guild_id, campaign.id)
        numbers = await session_numbers(bot, campaign)
    except Exception:
        log.exception("Couldn't list the sessions for /dmbot recap")
        await _tell(interaction, FAILED)
        return
    if not sessions:
        await _tell(interaction, NO_SESSIONS)
        return
    await _tell(interaction, PICK, view=SessionPick(campaign, sessions, numbers, overview))


@dmbot_group.command(name="recap", description="Make a session's recap, or make it again")
async def dmbot_recap(interaction: discord.Interaction) -> None:
    guild = interaction.guild
    if guild is None:
        await _tell(interaction, NOT_IN_SERVER)
        return
    try:
        campaigns = await _bot(interaction).campaigns.list_campaigns(guild.id)
    except Exception:
        log.exception("Couldn't load the campaigns for /dmbot recap")
        await _tell(interaction, FAILED)
        return
    mine = [c for c in campaigns if interaction.user.id in c.dm_user_ids]
    if not mine:
        await _tell(interaction, NOT_YOURS)
    elif len(mine) == 1:
        await show_sessions(interaction, mine[0])
    else:
        await _tell(interaction, PICK_CAMPAIGN, view=CampaignPick(mine))
