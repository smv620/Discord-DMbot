"""The DM's Game calendar in Discord (#5364 slice 3c), level with the website's panel: pick Days,
Harptos, Four seasons or "My own", say where Day 1 falls, read the preview, then Save or Cancel.

Everything that decides anything is `timebot.calendar_setting` (the same `from_body` the website
sends its body through, the same preview and the same warnings) and the save is the one store call
both surfaces make (`web_calendar_saves.save_body`). Here are only the forms, the buttons and the
words around them. Private to the DM who pressed (an ephemeral message); a form's words are never
logged. Short and plain: the form's hints say what to type.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Callable
from typing import Any

import discord

from dmbot.dm_screen import dm_bot_panel as panel
from dmbot.memory import web_calendar_saves as saves
from dmbot.timebot import calendar as cal
from dmbot.timebot import calendar_setting as setting
from dmbot.timebot import layout as lay

log = logging.getLogger(__name__)

NOT_A_DM = "Only this campaign's DMs can change the calendar."
NO_CLOCK = "Start the game clock first (🕰️ Game clock), then set the calendar."
FAILED = "DMbot couldn't save the calendar. Nothing was changed. Try again."
SAVED = "📅 Saved. Every date DMbot says now reads this way."
CANCELLED = "Nothing was changed."
INTRO = (
    "📅 **Game calendar.** How should dates read at your table? Nothing changes until you press "
    "Save.\n**Days:** Day 1, Day 2… **Harptos:** the Forgotten Realms calendar. **Four seasons:** "
    "four equal seasons. **My own:** type your months or seasons."
)
LONG_ONE = " (A long one is easier on the website.)"
WEBSITE_BUTTON = "Build on the website"
OWN_NEXT = "Next: which date is your campaign's first day?"
TIMED_OUT = "This timed out. Press Game calendar again."
NO_CHANGE = "No change from now."
ENDINGS = {
    saves.NOT_ALLOWED: NOT_A_DM,
    saves.NO_CLOCK_ERROR: NO_CLOCK,
    saves.BAD_CALENDAR_ERROR: setting.PROBLEMS["layout"],
}


def preview_text(current: cal.Calendar, body: object, minute: int | None) -> tuple[str, bool]:
    """(what to show before Save, whether it can be saved): the Day 1 line, today in the new
    calendar when there is a game clock, then the warnings. Core's own words for a refusal."""
    try:
        new = setting.from_body(body)
    except setting.CalendarProblem as bad:
        return "\n".join(bad.problems), False
    warned = setting.warnings_for(current, new)
    if not warned:
        return NO_CHANGE, True
    # The lines that take something away come first, so the danger is read before Save.
    danger = [w for w in warned if w in (setting.REMOVED, setting.REPLACES)]
    lines = [
        *(f"⚠️ {w}" for w in danger),
        setting.line_of(new) or "Dates will read as Day 1, Day 2, and so on.",
        setting.leap_line(new),
        setting.today_of(new, minute),
        *(w for w in warned if w not in danger),
    ]
    return "\n".join(x for x in lines if x), True


async def _context(
    interaction: discord.Interaction, campaign_id: str
) -> tuple[Any, Any, Any] | None:
    """(client, campaign, stored clock) for a DM who may change the calendar, else the reply has
    been sent and None."""
    client: Any = interaction.client
    guild = interaction.guild
    if guild is None:
        return None
    campaign = await client.campaigns.get(guild.id, campaign_id)
    if campaign is None or interaction.user.id not in campaign.dm_user_ids:
        await interaction.response.send_message(NOT_A_DM, ephemeral=True)
        return None
    return client, campaign, await client.clocks.get(guild.id, campaign_id)


async def open_panel(interaction: discord.Interaction, campaign_id: str) -> None:
    """The Game calendar button: where the calendar stands, and the four choices."""
    got = await _context(interaction, campaign_id)
    if got is None:
        return
    _client, campaign, stored = got
    if stored is None:
        await interaction.response.send_message(NO_CLOCK, ephemeral=True)
        return
    now = stored.calendar
    line = setting.line_of(now)
    here = (
        f"Now: **{now.layout.name}**. {line}."
        if now.layout is not None and line
        else (f"Now: {line}." if line else "Now: Day 1, Day 2, and so on.")
    )
    link = _website_link(interaction, campaign)
    await _send(
        interaction,
        f"{INTRO}{LONG_ONE if link else ''}\n{here}",
        Choices(campaign.id, now, link=link),
    )


def _website_link(interaction: discord.Interaction, campaign: Any) -> str:
    """The campaign page's Settings, where the website's calendar panel is, or "" when the link
    is off (DMBOT_WEB_CALENDAR, flipped with the website's CALENDAR_LIVE) or the bot has no
    website address: then the button is not shown at all."""
    settings = getattr(interaction.client, "settings", None)
    site = str(getattr(settings, "site_url", "") or "")
    if not getattr(settings, "web_calendar_on", False) or not site:
        return ""
    return panel.campaign_url(site, campaign.guild_id, campaign.id) + "#settings"


def _box(
    label: str,
    placeholder: str = "",
    *,
    long: bool = False,
    required: bool = True,
    default: str = "",
) -> Any:
    return discord.ui.TextInput(
        label=label,
        placeholder=placeholder,
        default=default or None,
        style=discord.TextStyle.paragraph if long else discord.TextStyle.short,
        required=required,
        max_length=4000 if long else 100,
    )


async def _send(interaction: discord.Interaction, text: str, view: discord.ui.View) -> None:
    """A private message with a step under it, remembered so it can come down with a word when it
    times out."""
    await interaction.response.send_message(text, view=view, ephemeral=True)
    if isinstance(view, _Timed):
        with contextlib.suppress(discord.HTTPException):
            view.message = await interaction.original_response()


class _Timed(discord.ui.View):
    """A private step that comes down with a word when it is left for fifteen minutes (not "This
    interaction failed")."""

    message: discord.Message | None = None

    async def on_timeout(self) -> None:
        if self.message is not None:
            with contextlib.suppress(discord.HTTPException):  # (dismissed: nothing to say)
                await self.message.edit(content=TIMED_OUT, view=None)


class Again(_Timed):
    """Under a refusal: reopens the same form with what the DM typed, so a bad line on a phone is
    not forty lines typed again."""

    def __init__(self, reopen: Callable[[], discord.ui.Modal]) -> None:
        super().__init__(timeout=900)
        self.reopen = reopen

    @discord.ui.button(label="Try again", style=discord.ButtonStyle.primary)
    async def again(self, interaction: discord.Interaction, _b: discord.ui.Button[Again]) -> None:
        await interaction.response.send_modal(self.reopen())


async def _refuse(
    interaction: discord.Interaction,
    words: str,
    reopen: Callable[[], discord.ui.Modal] | None = None,
) -> None:
    """Say what is wrong, in core's words. From a form, with a button to try again."""
    if reopen is None:
        await interaction.response.send_message(words, ephemeral=True)
        return
    await _send(interaction, words, Again(reopen))


class Choices(_Timed):
    def __init__(
        self, campaign_id: str, current: cal.Calendar | None = None, link: str = ""
    ) -> None:
        super().__init__(timeout=900)
        self.campaign_id = campaign_id
        self.current = current or cal.Calendar()
        if link:
            self.add_item(discord.ui.Button(label=WEBSITE_BUTTON, url=link, row=0))

    @discord.ui.button(label="Days", style=discord.ButtonStyle.secondary, row=0)
    async def days(self, interaction: discord.Interaction, _b: discord.ui.Button[Choices]) -> None:
        await _show_preview(interaction, self.campaign_id, {"kind": "days"})

    @discord.ui.button(label="Harptos", style=discord.ButtonStyle.secondary, row=0)
    async def harptos(
        self, interaction: discord.Interaction, _b: discord.ui.Button[Choices]
    ) -> None:
        await interaction.response.send_modal(HarptosForm(self.campaign_id))

    @discord.ui.button(label="Four seasons", style=discord.ButtonStyle.secondary, row=0)
    async def seasons(
        self, interaction: discord.Interaction, _b: discord.ui.Button[Choices]
    ) -> None:
        await interaction.response.send_modal(SeasonsForm(self.campaign_id))

    @discord.ui.button(label="My own", style=discord.ButtonStyle.primary, row=0)
    async def own(self, interaction: discord.Interaction, _b: discord.ui.Button[Choices]) -> None:
        # (a campaign that has its own calendar finds it filled in: changing one month is not
        # retyping forty lines)
        await interaction.response.send_modal(OwnForm(self.campaign_id, self.current))

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, row=1)
    async def cancel(
        self, interaction: discord.Interaction, _b: discord.ui.Button[Choices]
    ) -> None:
        await interaction.response.edit_message(content=CANCELLED, view=None)


class HarptosForm(discord.ui.Modal, title="Harptos: first day of the game"):
    def __init__(self, campaign_id: str, typed: dict[str, str] | None = None) -> None:
        super().__init__()
        self.campaign_id = campaign_id
        had = typed or {}
        self.year = _box("Year", "1492", default=had.get("year", ""))
        self.part = _box("Month or festival", "Flamerule", default=had.get("part", ""))
        self.day = _box(
            "Day (empty for a festival)", "14", required=False, default=had.get("day", "")
        )
        for box in (self.year, self.part, self.day):
            self.add_item(box)

    def typed(self) -> dict[str, str]:
        return {
            "year": str(self.year.value),
            "part": str(self.part.value),
            "day": str(self.day.value),
        }

    async def on_submit(self, interaction: discord.Interaction) -> None:
        again = lambda: HarptosForm(self.campaign_id, self.typed())  # noqa: E731
        try:
            one = setting.day_one_from_text(*self.typed().values())
        except setting.CalendarProblem as bad:
            await _refuse(interaction, "\n".join(bad.problems), again)
            return
        await _show_preview(
            interaction, self.campaign_id, {"kind": "harptos", "dayOne": one}, again
        )


class SeasonsForm(discord.ui.Modal, title="Four seasons"):
    def __init__(self, campaign_id: str, typed: dict[str, str] | None = None) -> None:
        super().__init__()
        self.campaign_id = campaign_id
        had = typed or {}
        self.length = _box("Days in each season", "90", default=had.get("length", ""))
        self.year = _box("Year of the first day", "1201", default=had.get("year", ""))
        self.part = _box(
            "First season", "Spring, Summer, Autumn or Winter", default=had.get("part", "")
        )
        self.day = _box("First day of that season", "1", default=had.get("day", ""))
        for box in (self.length, self.year, self.part, self.day):
            self.add_item(box)

    def typed(self) -> dict[str, str]:
        return {
            "length": str(self.length.value),
            "year": str(self.year.value),
            "part": str(self.part.value),
            "day": str(self.day.value),
        }

    async def on_submit(self, interaction: discord.Interaction) -> None:
        again = lambda: SeasonsForm(self.campaign_id, self.typed())  # noqa: E731
        try:
            days = str(self.length.value).strip()
            if not days.isdecimal():
                raise setting.CalendarProblem("length_number")
            layout = lay.four_seasons(int(days))
            one = setting.day_one_from_text(
                str(self.year.value), str(self.part.value), str(self.day.value)
            )
        except setting.CalendarProblem as bad:
            await _refuse(interaction, "\n".join(bad.problems), again)
            return
        except ValueError as bad:  # (a length the calendar cannot have: `Layout` says which)
            words = setting.PROBLEMS.get(str(bad), setting.PROBLEMS["layout"])
            await _refuse(interaction, words, again)
            return
        body = {"kind": "custom", "def": layout.to_json(), "dayOne": one}
        await _show_preview(interaction, self.campaign_id, body, again)


class OwnForm(discord.ui.Modal, title="My own calendar"):
    """The calendar's own words: its name, its parts one to a line, weekdays, the year label and the
    extra day. Where the first day falls is the next step."""

    def __init__(
        self,
        campaign_id: str,
        current: cal.Calendar | None = None,
        typed: dict[str, str] | None = None,
    ) -> None:
        super().__init__()
        self.campaign_id = campaign_id
        self.current = current
        # What was typed comes back after a refusal; else the calendar the campaign has, if it has
        # one of its own (changing one month is not retyping forty lines).
        had = typed or (
            setting.text_of(current.layout) if current and current.layout is not None else {}
        )
        self.name = _box(
            "Name of the calendar", "Calendar of the Vale", default=had.get("name", "")
        )
        self.parts = _box(
            "Parts, one per line: name days kind",
            "Hammer 30\nSummer 90 season\nMidwinter 1 festival",
            long=True,
            default=had.get("parts", ""),
        )
        self.weekdays = _box(
            "Weekdays, commas between (optional)",
            "Sunday, Moonday",
            required=False,
            default=had.get("weekdays", ""),
        )
        self.label = _box(
            "Year label, then spoken form (optional)",
            "AR, after the Reckoning",
            required=False,
            default=had.get("label", ""),
        )
        self.leap = _box(
            "Extra day: name, years apart, after",
            "Shieldmeet, 4, Midsummer",
            required=False,
            default=had.get("leap", ""),
        )
        for box in (self.name, self.parts, self.weekdays, self.label, self.leap):
            self.add_item(box)

    def typed(self) -> dict[str, str]:
        return {
            "name": str(self.name.value),
            "parts": str(self.parts.value),
            "weekdays": str(self.weekdays.value),
            "label": str(self.label.value),
            "leap": str(self.leap.value),
        }

    async def on_submit(self, interaction: discord.Interaction) -> None:
        again = lambda: OwnForm(self.campaign_id, self.current, self.typed())  # noqa: E731
        try:
            made = setting.definition_from_text(*self.typed().values())
            lay.Layout.from_json(made)  # (refused now, in words, before the first day is asked)
        except setting.CalendarProblem as bad:
            await _refuse(interaction, "\n".join(bad.problems), again)
            return
        except ValueError as bad:
            words = setting.PROBLEMS.get(str(bad), setting.PROBLEMS["layout"])
            await _refuse(interaction, words, again)
            return
        step = NextStep(self.campaign_id, made, self.current)
        await _send(interaction, OWN_NEXT, step)


class NextStep(_Timed):
    def __init__(
        self, campaign_id: str, definition: dict[str, Any], current: cal.Calendar | None = None
    ) -> None:
        super().__init__(timeout=900)
        self.campaign_id, self.definition, self.current = campaign_id, definition, current

    @discord.ui.button(label="Set first day", style=discord.ButtonStyle.primary)
    async def go(self, interaction: discord.Interaction, _b: discord.ui.Button[NextStep]) -> None:
        had = setting.day_one_of(self.current) if self.current is not None else None
        await interaction.response.send_modal(DayOneForm(self.campaign_id, self.definition, had))

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(
        self, interaction: discord.Interaction, _b: discord.ui.Button[NextStep]
    ) -> None:
        await interaction.response.edit_message(content=CANCELLED, view=None)


class DayOneForm(discord.ui.Modal, title="Your campaign's first day"):
    def __init__(
        self, campaign_id: str, definition: dict[str, Any], had: dict[str, Any] | None = None
    ) -> None:
        super().__init__()
        self.campaign_id, self.definition = campaign_id, definition
        had = had or {}
        self.year = _box("Year", "1201", default=str(had.get("year") or ""))
        self.part = _box("Month, season or festival", "Summer", default=str(had.get("part") or ""))
        self.day = _box(
            "Day (empty for a one-day festival)",
            "2",
            required=False,
            default=str(had.get("day") or ""),
        )
        self.weekday = _box(
            "Weekday of that day (optional)",
            "Moonday",
            required=False,
            default=str(had.get("weekday") or ""),
        )
        for box in (self.year, self.part, self.day, self.weekday):
            self.add_item(box)

    def typed(self) -> dict[str, Any]:
        return {
            "year": str(self.year.value),
            "part": str(self.part.value),
            "day": str(self.day.value),
            "weekday": str(self.weekday.value),
        }

    async def on_submit(self, interaction: discord.Interaction) -> None:
        again = lambda: DayOneForm(self.campaign_id, self.definition, self.typed())  # noqa: E731
        try:
            one = setting.day_one_from_text(*self.typed().values())
        except setting.CalendarProblem as bad:
            await _refuse(interaction, "\n".join(bad.problems), again)
            return
        body = {"kind": "custom", "def": self.definition, "dayOne": one}
        await _show_preview(interaction, self.campaign_id, body, again)


async def _show_preview(
    interaction: discord.Interaction,
    campaign_id: str,
    body: dict[str, Any],
    reopen: Callable[[], discord.ui.Modal] | None = None,
) -> None:
    got = await _context(interaction, campaign_id)
    if got is None:
        return
    _client, _campaign, stored = got
    if stored is None:
        await interaction.response.send_message(NO_CLOCK, ephemeral=True)
        return
    text, ok = preview_text(stored.calendar, body, stored.clock.minute)
    if not ok:
        await _refuse(interaction, text, reopen)  # (what was typed is kept: Try again)
        return
    await _send(interaction, text, Confirm(campaign_id, body))


class Confirm(_Timed):
    """Save or Cancel, under the preview and its warnings."""

    def __init__(self, campaign_id: str, body: dict[str, Any]) -> None:
        super().__init__(timeout=900)
        self.campaign_id, self.body = campaign_id, body

    @discord.ui.button(label="Save", style=discord.ButtonStyle.success)
    async def save(self, interaction: discord.Interaction, _b: discord.ui.Button[Confirm]) -> None:
        client: Any = interaction.client
        guild = interaction.guild
        if guild is None:
            return
        try:
            error = await saves.save_body(
                client.clocks, guild.id, self.campaign_id, interaction.user.id, self.body
            )
        except Exception as exc:  # the type only: the words are the DM's
            log.warning("A calendar save from Discord failed: %s", type(exc).__name__)
            error = saves.FAILED
        log.info("A calendar save from Discord ended: %s", error or "saved")
        await interaction.response.edit_message(
            content=SAVED if not error else ENDINGS.get(error, FAILED), view=None
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(
        self, interaction: discord.Interaction, _b: discord.ui.Button[Confirm]
    ) -> None:
        await interaction.response.edit_message(content=CANCELLED, view=None)
