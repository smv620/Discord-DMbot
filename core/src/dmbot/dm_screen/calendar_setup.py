"""The DM's Game calendar in Discord (#5364 slice 3c), level with the website's panel: pick Days,
Harptos, Four seasons or "My own", say where Day 1 falls, read the preview, then Save or Cancel.

Everything that decides anything is `timebot.calendar_setting` (the same `from_body` the website
sends its body through, the same preview and the same warnings) and the save is the one store call
both surfaces make (`web_calendar_saves.save_body`). Here are only the forms, the buttons and the
words around them. Private to the DM who pressed (an ephemeral message); a form's words are never
logged. Short and plain: the form's hints say what to type.
"""

from __future__ import annotations

import logging
from typing import Any

import discord

from dmbot.memory import web_calendar_saves as saves
from dmbot.timebot import calendar as cal
from dmbot.timebot import calendar_setting as setting
from dmbot.timebot import layout as lay

log = logging.getLogger(__name__)

NOT_A_DM = "Only this campaign's DMs can change the calendar."
NO_CLOCK = "Start the game clock first (🕰️ Game clock), then set the calendar."
FAILED = "DMbot couldn't save the calendar. Try again."
SAVED = "📅 Saved. Every date DMbot says now reads this way."
CANCELLED = "Nothing was changed."
INTRO = (
    "📅 **Game calendar.** Pick how dates read at your table. Nothing changes until you press Save."
)
OWN_NEXT = "Now say where Day 1 of your campaign falls in it."
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
    lines = [
        setting.line_of(new) or "Dates will read as Day 1, Day 2, and so on.",
        setting.today_of(new, minute),
        *setting.warnings_for(current, new),
    ]
    return "\n".join(f"• {x}" if i else x for i, x in enumerate(x for x in lines if x)), True


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
    await interaction.response.send_message(
        f"{INTRO}\n{here}", view=Choices(campaign.id), ephemeral=True
    )


class Choices(discord.ui.View):
    def __init__(self, campaign_id: str) -> None:
        super().__init__(timeout=900)
        self.campaign_id = campaign_id

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
        await interaction.response.send_modal(OwnForm(self.campaign_id))

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, row=1)
    async def cancel(
        self, interaction: discord.Interaction, _b: discord.ui.Button[Choices]
    ) -> None:
        await interaction.response.edit_message(content=CANCELLED, view=None)


def _box(label: str, placeholder: str = "", *, long: bool = False, required: bool = True) -> Any:
    return discord.ui.TextInput(
        label=label,
        placeholder=placeholder,
        style=discord.TextStyle.paragraph if long else discord.TextStyle.short,
        required=required,
        max_length=1500 if long else 100,
    )


class HarptosForm(discord.ui.Modal, title="Harptos: where is Day 1?"):
    def __init__(self, campaign_id: str) -> None:
        super().__init__()
        self.campaign_id = campaign_id
        self.year = _box("Year", "1492")
        self.part = _box("Month or festival", "Flamerule")
        self.day = _box("Day (leave empty for a festival)", "14", required=False)
        for box in (self.year, self.part, self.day):
            self.add_item(box)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            one = setting.day_one_from_text(
                str(self.year.value), str(self.part.value), str(self.day.value)
            )
        except setting.CalendarProblem as bad:
            await interaction.response.send_message("\n".join(bad.problems), ephemeral=True)
            return
        await _show_preview(interaction, self.campaign_id, {"kind": "harptos", "dayOne": one})


class SeasonsForm(discord.ui.Modal, title="Four seasons"):
    def __init__(self, campaign_id: str) -> None:
        super().__init__()
        self.campaign_id = campaign_id
        self.length = _box("Days in each season", "90")
        self.year = _box("Year of Day 1", "1201")
        self.part = _box("Season of Day 1", "Spring, Summer, Autumn or Winter", required=True)
        self.day = _box("Day of that season", "1")
        for box in (self.length, self.year, self.part, self.day):
            self.add_item(box)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            days = str(self.length.value).strip()
            if not days.isdecimal():
                raise setting.CalendarProblem("number")
            layout = lay.four_seasons(int(days))
            one = setting.day_one_from_text(
                str(self.year.value), str(self.part.value), str(self.day.value)
            )
        except setting.CalendarProblem as bad:
            await interaction.response.send_message("\n".join(bad.problems), ephemeral=True)
            return
        except ValueError as bad:  # (a length the calendar cannot have: `Layout` says which)
            words = setting.PROBLEMS.get(str(bad), setting.PROBLEMS["layout"])
            await interaction.response.send_message(words, ephemeral=True)
            return
        body = {"kind": "custom", "def": layout.to_json(), "dayOne": one}
        await _show_preview(interaction, self.campaign_id, body)


class OwnForm(discord.ui.Modal, title="My own calendar"):
    """The calendar's own words: its name, its parts one to a line, weekdays, the year label and the
    extra day. Where Day 1 falls is the next step."""

    def __init__(self, campaign_id: str) -> None:
        super().__init__()
        self.campaign_id = campaign_id
        self.name = _box("Name of the calendar", "Calendar of the Vale")
        self.parts = _box(
            "Parts, one a line: name, days, month/season/festival",
            "Spring 90 season\nSummer 90 season\nMidwinter 1 festival",
            long=True,
        )
        self.weekdays = _box("Weekdays, with commas (if any)", "Sunday, Moonday", required=False)
        self.label = _box(
            "Year label (and how the voice says it)", "AR, after the Reckoning", required=False
        )
        self.leap = _box(
            "An extra day: name, years between, part it follows",
            "Shieldmeet, 4, Midsummer",
            required=False,
        )
        for box in (self.name, self.parts, self.weekdays, self.label, self.leap):
            self.add_item(box)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            made = setting.definition_from_text(
                str(self.name.value),
                str(self.parts.value),
                str(self.weekdays.value),
                str(self.label.value),
                str(self.leap.value),
            )
            lay.Layout.from_json(made)  # (refused now, in words, before Day 1 is asked)
        except setting.CalendarProblem as bad:
            await interaction.response.send_message("\n".join(bad.problems), ephemeral=True)
            return
        except ValueError as bad:
            code = str(bad)
            await interaction.response.send_message(
                setting.PROBLEMS.get(code, setting.PROBLEMS["layout"]), ephemeral=True
            )
            return
        await interaction.response.send_message(
            OWN_NEXT, view=NextStep(self.campaign_id, made), ephemeral=True
        )


class NextStep(discord.ui.View):
    def __init__(self, campaign_id: str, definition: dict[str, Any]) -> None:
        super().__init__(timeout=900)
        self.campaign_id, self.definition = campaign_id, definition

    @discord.ui.button(label="Where is Day 1?", style=discord.ButtonStyle.primary)
    async def go(self, interaction: discord.Interaction, _b: discord.ui.Button[NextStep]) -> None:
        await interaction.response.send_modal(DayOneForm(self.campaign_id, self.definition))

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(
        self, interaction: discord.Interaction, _b: discord.ui.Button[NextStep]
    ) -> None:
        await interaction.response.edit_message(content=CANCELLED, view=None)


class DayOneForm(discord.ui.Modal, title="Where is Day 1?"):
    def __init__(self, campaign_id: str, definition: dict[str, Any]) -> None:
        super().__init__()
        self.campaign_id, self.definition = campaign_id, definition
        self.year = _box("Year", "1201")
        self.part = _box("Part (month, season or festival)", "Summer")
        self.day = _box("Day (leave empty for a one-day festival)", "2", required=False)
        self.weekday = _box("Weekday of Day 1 (if there are weekdays)", "Moonday", required=False)
        for box in (self.year, self.part, self.day, self.weekday):
            self.add_item(box)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            one = setting.day_one_from_text(
                str(self.year.value),
                str(self.part.value),
                str(self.day.value),
                str(self.weekday.value),
            )
        except setting.CalendarProblem as bad:
            await interaction.response.send_message("\n".join(bad.problems), ephemeral=True)
            return
        body = {"kind": "custom", "def": self.definition, "dayOne": one}
        await _show_preview(interaction, self.campaign_id, body)


async def _show_preview(
    interaction: discord.Interaction, campaign_id: str, body: dict[str, Any]
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
        await interaction.response.send_message(text, ephemeral=True)
        return
    await interaction.response.send_message(text, view=Confirm(campaign_id, body), ephemeral=True)


class Confirm(discord.ui.View):
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
