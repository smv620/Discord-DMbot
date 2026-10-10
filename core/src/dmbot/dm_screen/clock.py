"""🕰️ The game clock on the DM screen (#965; docs/PLAN.md, "TimeBot"): one pinned message,
"🕰️ Day 4, afternoon (14:30)", edited in place, with the DM's buttons: +10 min, +1 hour,
Short rest, Long rest, It's dawn and Set time…. Only the campaign's DMs can press them
(checked on every press, in the database transaction that changes the clock), and the
buttons keep working after a restart (their IDs carry the campaign).

The clock only moves when a DM presses a button or clearly says a rest happened (see
`DMBot.note_clock_phrase`); nothing is guessed from the story. It speaks up only for dawn,
noon and dusk as they are passed, and for 24 hours without a long rest.

Register with `bot.add_dynamic_items(ClockButton, ClockUndoButton)`.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from dataclasses import dataclass, field
from typing import Any, cast

import discord

from dmbot.campaigns import Campaign
from dmbot.campaigns.models import CampaignError
from dmbot.rules import optional
from dmbot.timebot import clock as game
from dmbot.timebot import effects
from dmbot.timebot.clock import Clock
from dmbot.timebot.store import ClockStore, Stored

log = logging.getLogger(__name__)

NO_PINGS = discord.AllowedMentions.none()
_ID = r"(?P<campaign>[0-9a-f]{32})"
NO_REST_RULE = "xge-no-long-rest"  # the optional rule the 24-hour line follows

SET_FIRST = (
    "The clock isn't set yet. Press **Game clock** in ⚙️ Settings and give the day and the hour."
)
GONE = "That campaign isn't here any more."
FAILED = "Sorry, that didn't save. Please try again."
BAD_TIME = "Couldn't read that time. Press **Set time…** again and try day 4, time 14 (or 14:30)."
ALREADY_SHOWN = "The clock is already pinned in the DM screen: {where}"
NEEDS_SCREEN = "This campaign has no DM screen yet. Run `/dmbot start` once first."
OFF_SCREEN = (
    "I can't post in the DM screen. Give DMbot **Send Messages** there, then press "
    "**Game clock** again."
)
SET_DONE = "🕰️ Clock set to {label}. It's pinned here: {where}"

ACTIONS: dict[str, tuple[str, str, discord.ButtonStyle, int]] = {
    # id: (label, emoji, style, row)
    "m10": ("+10 min", "⏱️", discord.ButtonStyle.primary, 0),
    "h1": ("+1 hour", "⏱️", discord.ButtonStyle.secondary, 0),
    "set": ("Set time…", "🕰️", discord.ButtonStyle.secondary, 0),
    "short": ("Short rest", "🛌", discord.ButtonStyle.secondary, 1),
    "long": ("Long rest", "🌙", discord.ButtonStyle.secondary, 1),
    "dawn": ("Skip to dawn", "🌅", discord.ButtonStyle.secondary, 1),
    "timer": ("Start a timer", "⏳", discord.ButtonStyle.secondary, 2),
}
MARK_LINES = {"dawn": "🌅 Dawn", "noon": "☀️ Noon", "dusk": "🌇 Dusk"}


def clock_text(clock: Clock, effect_lines: list[str] | None = None) -> str:
    """The pinned message: the time, then the running timers one to a line (#998)."""
    head = f"🕰️ **{game.label(clock.minute)}**"
    return "\n".join([head, *(effect_lines or [])])


async def render(client: Any, campaign: Campaign, clock: Clock) -> str:
    """The clock message as it should read now, with the campaign's running timers. A
    timers store that can't be read just leaves them off: the time still shows."""
    store = getattr(client, "effects", None)
    if store is None:
        return clock_text(clock)
    try:
        running = await store.running(campaign.guild_id, campaign.id)
    except Exception:
        log.exception("Couldn't read the timers for the clock message")
        return clock_text(clock)
    return clock_text(clock, effects.lines(running, clock.minute))


@dataclass(slots=True)
class Result:
    """What a press did: the clock before and after, and the short lines to say."""

    before: Clock | None
    after: Clock | None
    lines: list[str] = field(default_factory=list)
    changed: bool = False


def _apply(action: str, clock: Clock | None, set_to: int | None) -> Clock | None:
    """The new clock for a press, or None to change nothing (no clock yet, or nonsense)."""
    if action == "set":
        if set_to is None:
            return None
        if clock is None:  # the first time it is set the party counts as rested
            return Clock(set_to, set_to, None)
        return game.set_to(clock, set_to)
    if clock is None:
        return None
    if action == "m10":
        return game.advance(clock, 10)
    if action in ("h1", "short"):
        return game.advance(clock, game.SHORT_REST_MIN)
    if action == "long":
        return game.long_rest(clock)
    if action == "dawn":
        return game.set_to(clock, game.next_dawn(clock.minute))
    return None


def _speaks(action: str) -> bool:
    """Dawn, noon and dusk are said when time passes by a button or a rest; not when the
    DM says what time it is (the DM already knows)."""
    return action in ("m10", "h1", "short", "long")


async def press(
    client: Any,
    guild_id: int,
    campaign_id: str,
    user_id: int,
    action: str,
    *,
    set_to: int | None = None,
) -> Result:
    """Do one thing to the clock. Raises CampaignError (plain words) if the person isn't a
    DM of the campaign. The 24-hour line is worked out here, once, in the same change."""
    store: ClockStore = client.clocks
    tired_on = action in ("m10", "h1", "short", "long") and await _tired_rule_on(
        client, guild_id, campaign_id
    )
    result = Result(None, None)

    def change(current: Clock | None) -> Clock | None:
        new = _apply(action, current, set_to)
        if new is None:
            return None
        if tired_on and game.tired_due(new):
            new = game.told_tired(new)
            result.lines.append(tired_line(new))
        return new

    before, after = await store.change(guild_id, campaign_id, user_id, change)
    result.before, result.after = before, after
    result.changed = after is not None and after != before
    if before is not None and after is not None and _speaks(action):
        marks = game.crossings(before.minute, after.minute)
        result.lines = [MARK_LINES[name] for _, name in marks] + result.lines
    return result


def tired_line(clock: Clock) -> str:
    """The 24-hours-without-a-rest line, with its source: an optional rule, so a DM decides
    whether it applies."""
    rule = optional.rule(NO_REST_RULE)
    source = rule.source if rule else "an optional rule"
    return (
        f"😴 Check: 24 hours since the last long rest ({game.short_label(clock.last_long_rest)}). "
        f"Optional rule, {source}. Your call."
    )


async def _tired_rule_on(client: Any, guild_id: int, campaign_id: str) -> bool:
    """On unless the DM switched it off in the campaign's optional rules."""
    try:
        overrides = await client.campaigns.optional_rule_overrides(guild_id, campaign_id)
    except Exception:
        log.exception("Couldn't read the optional rules for the game clock")
        return True
    return optional.is_on(NO_REST_RULE, overrides, True)


def clock_view(campaign_id: str) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for action in ACTIONS:
        view.add_item(ClockButton(campaign_id, action))
    return view


async def show(client: Any, campaign: Campaign, stored: Stored) -> discord.Message | None:
    """Edit the pinned clock message in place with the clock now; post (and pin) a new one if
    there isn't one or it is gone. Returns the message, or None if nothing could be shown."""
    text, view = await render(client, campaign, stored.clock), clock_view(campaign.id)
    channel = client.get_channel(stored.channel_id) if stored.channel_id else None
    if isinstance(channel, discord.abc.Messageable) and stored.message_id:
        try:
            message = await channel.fetch_message(stored.message_id)
            await message.edit(content=text, view=view, allowed_mentions=NO_PINGS)
            return message
        except discord.NotFound:
            pass  # the pinned message was deleted: post a new one below
        except discord.HTTPException:
            # A hiccup (rate limit, Discord error): don't pile a second clock beside it.
            log.warning("Couldn't edit the pinned clock message")
            return None
    if campaign.dm_screen_channel_id is None:
        return None
    message = await client.post_message(campaign.dm_screen_channel_id, text, view)
    if message is None:
        return None
    with contextlib.suppress(discord.HTTPException):  # pinning needs a permission DMbot may lack
        await message.pin()
    await client.clocks.set_message(campaign.guild_id, campaign.id, message.channel.id, message.id)
    return cast(discord.Message, message)


async def say(client: Any, campaign: Campaign, lines: list[str], undo: str | None = None) -> None:
    """The short lines (dawn, noon, dusk, tired) on the DM screen, nowhere else."""
    if campaign.dm_screen_channel_id is None or not lines:
        return
    view = None
    if undo:
        view = discord.ui.View(timeout=None)
        view.add_item(ClockUndoButton(campaign.id, undo))
    await client.post_message(campaign.dm_screen_channel_id, "\n".join(lines), view)


def parse_time(day: str, hour: str) -> int | None:
    """Game minutes for the form's two boxes, or None if they aren't a day and an hour.
    The hour is 14 or 14:30."""
    day, hour = day.strip(), hour.strip()
    if not (day.isascii() and day.isdigit()):
        return None
    match = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?", hour)
    if match is None:
        return None
    try:
        return game.from_day_and_time(int(day), int(match[1]), int(match[2] or 0))
    except ValueError:
        return None


class SetTimeForm(discord.ui.Modal):
    day: discord.ui.TextInput[SetTimeForm] = discord.ui.TextInput(
        label="Day (1 or higher)", placeholder="4", max_length=5
    )
    hour: discord.ui.TextInput[SetTimeForm] = discord.ui.TextInput(
        label="Time (14 or 14:30)", placeholder="14:30", max_length=5
    )

    def __init__(self, campaign_id: str, clock: Clock | None = None) -> None:
        super().__init__(title="Set the game time")
        self.campaign_id = campaign_id
        if clock is not None:
            self.day.default = str(game.day_of(clock.minute))
            self.hour.default = game.clock_time(clock.minute)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        minute = parse_time(self.day.value, self.hour.value)
        if minute is None:
            await interaction.response.send_message(BAD_TIME, ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await run_press(interaction, self.campaign_id, "set", set_to=minute)


_locks: dict[str, asyncio.Lock] = {}  # one press at a time per campaign, so the pinned
# message always ends up showing the latest time (the database already keeps the count right)


async def run_press(
    interaction: discord.Interaction, campaign_id: str, action: str, *, set_to: int | None = None
) -> None:
    """A press after the interaction was answered: change the clock, show it, say the lines."""
    async with _locks.setdefault(campaign_id, asyncio.Lock()):
        await _run_press(interaction, campaign_id, action, set_to)


async def _run_press(
    interaction: discord.Interaction, campaign_id: str, action: str, set_to: int | None
) -> None:
    client: Any = interaction.client
    guild = interaction.guild
    campaign = await client.campaigns.get(guild.id, campaign_id) if guild else None
    if campaign is None:
        await _reply(interaction, GONE)
        return
    try:
        result = await press(
            client, campaign.guild_id, campaign.id, interaction.user.id, action, set_to=set_to
        )
    except CampaignError as exc:
        await _reply(interaction, str(exc))
        return
    except Exception:
        log.exception("Couldn't change the game clock")
        await _reply(interaction, FAILED)
        return
    if result.after is None:
        await _reply(interaction, SET_FIRST)
        return
    stored = await client.clocks.get(campaign.guild_id, campaign.id)
    if stored is None:
        await _reply(interaction, FAILED)
        return
    message = await _show_here(interaction, client, campaign, stored)
    if message is None:
        await _reply(interaction, OFF_SCREEN)
    elif action == "set" and interaction.message is None:
        # The form saved: the DM's own screen shows nothing else, so say where the clock is.
        await _reply(
            interaction,
            SET_DONE.format(label=game.short_label(result.after.minute), where=message.jump_url),
        )
    await say(client, campaign, result.lines)  # dawn, noon, dusk, tired: on the DM screen
    from dmbot.dm_screen import effects as timers

    await timers.announce_due(client, campaign, result.after.minute)
    if result.before is not None and action in ("short", "long", "dawn"):
        # The DM who pressed sees the clock change; the note with Undo is for them alone.
        note = rest_note(action, result.after)
        view = discord.ui.View(timeout=None)
        view.add_item(ClockUndoButton(campaign.id, undo_id(result.before, result.after)))
        with contextlib.suppress(discord.HTTPException):
            await interaction.followup.send(note, view=view, ephemeral=True)


def rest_note(action: str, clock: Clock) -> str:
    if action == "dawn":
        return f"🌅 Skipped to dawn. Now {game.short_label(clock.minute)}."
    kind, emoji = ("Short", "🛌") if action == "short" else ("Long", "🌙")
    return f"{emoji} {kind} rest. Now {game.short_label(clock.minute)}."


async def _show_here(
    interaction: discord.Interaction, client: Any, campaign: Campaign, stored: Stored
) -> discord.Message | None:
    """Redraw the clock in the message that was pressed; if the press came from somewhere
    else (the form opened from ⚙️ Settings), show it the usual way."""
    message = interaction.message
    if message is not None and message.id == stored.message_id:
        text, view = await render(client, campaign, stored.clock), clock_view(campaign.id)
        with contextlib.suppress(discord.HTTPException):
            await message.edit(content=text, view=view, allowed_mentions=NO_PINGS)
            return message
    if message is not None and _is_clock_message(message):
        with contextlib.suppress(discord.HTTPException):
            text = await render(client, campaign, stored.clock)
            await message.edit(content=text, view=clock_view(campaign.id))
            await client.clocks.set_message(
                campaign.guild_id, campaign.id, message.channel.id, message.id
            )
            return message
    return await show(client, campaign, stored)


def _is_clock_message(message: discord.Message) -> bool:
    return message.content.startswith("🕰️")


async def _reply(interaction: discord.Interaction, text: str) -> None:
    with contextlib.suppress(discord.HTTPException):
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True, allowed_mentions=NO_PINGS)
        else:
            await interaction.response.send_message(text, ephemeral=True)


class ClockButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:clock:{_ID}:(?P<action>m10|h1|short|long|dawn|set|timer|open)",
):
    """One of the clock's buttons (`open` is the ⚙️ Settings button that starts or shows it)."""

    def __init__(self, campaign_id: str, action: str) -> None:
        if action == "open":
            label, emoji, style, row = "Game clock", "🕰️", discord.ButtonStyle.secondary, 3
        else:
            label, emoji, style, row = ACTIONS[action]
        super().__init__(
            discord.ui.Button(
                label=label,
                emoji=emoji,
                style=style,
                row=row,
                custom_id=f"dmbot:clock:{campaign_id}:{action}",
            )
        )
        self.campaign_id, self.action = campaign_id, action

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> ClockButton:
        return cls(match["campaign"], match["action"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        client: Any = interaction.client
        guild = interaction.guild
        if guild is None:
            return
        try:
            if self.action == "timer":
                from dmbot.dm_screen import effects as timers

                await interaction.response.send_modal(timers.TimerForm(self.campaign_id))
                return
            if self.action in ("set", "open"):
                await self._form_or_show(interaction, client, guild.id)
                return
            await interaction.response.defer()  # the press edits the clock message
            await run_press(interaction, self.campaign_id, self.action)
        except Exception:
            log.exception("The game clock button failed")
            await _reply(interaction, FAILED)

    async def _form_or_show(
        self, interaction: discord.Interaction, client: Any, guild_id: int
    ) -> None:
        # A form can't be put off with "thinking…": it must open within Discord's 3 seconds.
        async with asyncio.timeout(2):
            campaign = await client.campaigns.get(guild_id, self.campaign_id)
            if campaign is None:
                await _reply(interaction, GONE)
                return
            if interaction.user.id not in campaign.dm_user_ids:  # checked again when it saves
                await _reply(interaction, "Only this campaign's DMs can use the clock.")
                return
            stored = await client.clocks.get(guild_id, campaign.id)
        if self.action == "set" or stored is None:
            await interaction.response.send_modal(
                SetTimeForm(campaign.id, None if stored is None else stored.clock)
            )
            return
        await interaction.response.defer(ephemeral=True)
        message = await show(client, campaign, stored)
        if message is None:
            await _reply(
                interaction, NEEDS_SCREEN if campaign.dm_screen_channel_id is None else OFF_SCREEN
            )
            return
        await _reply(interaction, ALREADY_SHOWN.format(where=message.jump_url))


class SimpleGuild:
    """Just the server's id (the undo works from it alone)."""

    def __init__(self, guild_id: int) -> None:
        self.id = guild_id


def undo_id(before: Clock, after: Clock) -> str:
    """What Undo needs, in the button's own ID so it works after a restart: the clock to go
    back to, and the minute it must still be at (if the DM moved it since, Undo does nothing)."""
    return f"{before.minute}:{before.last_long_rest}:{after.minute}"


class ClockUndoButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:clockundo:{_ID}:(?P<before>\d+):(?P<rest>\d+):(?P<after>\d+)",
):
    """↩️ Undo on a rest said at the table: back to the time before, if nothing has moved
    the clock since."""

    def __init__(self, campaign_id: str, spec: str) -> None:
        super().__init__(
            discord.ui.Button(
                label="Undo",
                emoji="↩️",
                style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:clockundo:{campaign_id}:{spec}",
            )
        )
        self.campaign_id = campaign_id
        before, rest, after = (int(x) for x in spec.split(":"))
        self.before, self.rest, self.after = before, rest, after

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> ClockUndoButton:
        return cls(match["campaign"], f"{match['before']}:{match['rest']}:{match['after']}")

    async def callback(self, interaction: discord.Interaction) -> Any:
        guild = interaction.guild
        if guild is None:
            return
        await interaction.response.defer()
        try:
            await self._undo(interaction, guild.id)
        except Exception:
            log.exception("Undoing a game clock change failed")
            await _reply(interaction, FAILED)

    async def _undo(self, interaction: discord.Interaction, guild_id: int) -> None:
        client: Any = interaction.client
        guild = SimpleGuild(guild_id)
        campaign = await client.campaigns.get(guild.id, self.campaign_id)
        if campaign is None:
            await _reply(interaction, GONE)
            return
        moved = False

        def change(current: Clock | None) -> Clock | None:
            nonlocal moved
            if current is None or current.minute != self.after:
                moved = True  # the DM moved the clock since: leave it
                return None
            return Clock(self.before, min(self.rest, self.before), None)

        try:
            _, after = await client.clocks.change(
                guild.id, campaign.id, interaction.user.id, change
            )
        except CampaignError as exc:
            await _reply(interaction, str(exc))
            return
        if moved or after is None:
            await _reply(interaction, "The clock has moved since, so there is nothing to undo.")
            return
        stored = await client.clocks.get(guild.id, campaign.id)
        if stored is not None:
            await show(client, campaign, stored)
        with contextlib.suppress(discord.HTTPException):
            await interaction.edit_original_response(
                content=f"↩️ Undone. Back to {game.short_label(self.before)}.", view=None
            )
