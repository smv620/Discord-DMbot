"""🕰️ The game clock on the DM screen (#965; docs/PLAN.md, "TimeBot"): one pinned message,
"🕰️ Day 4, afternoon (14:30)", edited in place, with the DM's buttons: +10 min, +1 hour,
Rests & time… and Start a timer. Short rest, Long rest and Skip to dawn are in the private
panel that **Rests & time…** opens (rests are rare buttons, because DMbot hears them, and
players who peek behind the DM screen could take them for D&D Beyond sheet links). The players
see only the day and a rough time of day, a small "-# 🕰️ Day 4, afternoon" line in the campaign's
DMbot chat channel (#2877; before it, a pinned message in a game-time channel, #1116).
Only the campaign's DMs can press the buttons (checked on every press, in the database
transaction that changes the clock), and the buttons keep working after a restart (their
IDs carry the campaign).

The clock only moves when a DM presses a button or clearly says a rest happened (see
`DMBot.note_clock_phrase`); nothing is guessed from the story. It speaks up only for dawn,
noon and dusk as they are passed (except on a long rest or Skip to dawn, whose note already
gives the time), and for 24 hours without a long rest.

**Split party** (#1607): **Rests & time…** also holds **✂️ Split the party**. The DM names two
or three groups; the pinned message then shows one time per group and a picker, and the
buttons, rests and Undo act on the picked group. Spoken phrases move nothing while split (said
once). **🤝 Rejoin the party** asks which time to keep. See `timebot.split`.

Register with `bot.add_dynamic_items(ClockButton, ClockUndoButton, CombatUndoButton,
ClockPicker, SplitUndoButton)`.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from dataclasses import dataclass, field, replace
from typing import Any, cast

import discord

from dmbot.campaigns import Campaign
from dmbot.campaigns.models import CampaignError
from dmbot.mentions import NO_PINGS
from dmbot.rules import optional
from dmbot.timebot import clock as game
from dmbot.timebot import durations, effects, split, where_are_we
from dmbot.timebot.clock import Clock
from dmbot.timebot.store import GONE as GROUP_GONE
from dmbot.timebot.store import MOVED_ON, ClockStore, Rejoined, Stored

log = logging.getLogger(__name__)

_ID = r"(?P<campaign>[0-9a-f]{32})"
NO_REST_RULE = "xge-no-long-rest"  # the optional rule the 24-hour line follows

SET_FIRST = (
    "The clock isn't set yet. Press **Game clock** in ⚙️ Manage DMbot and give the day and the hour."
)
GONE = "That campaign isn't here any more."
FAILED = "That didn't save. Try again in a moment."
BAD_TIME = "Couldn't read that time. Try day 4 and time 14 (or 14:30)."
ALREADY_SHOWN = "The clock is already pinned on the DM screen: {where}"
PANEL_TEXT = "Take a rest, skip to dawn, or type the time. Only you see this."
PLAYERS_SEE = "\nPlayers see {what}a rough time in <#{channel}>."
NEEDS_SCREEN = "This campaign has no DM screen yet. Run `/dmbot start` once first."
OFF_SCREEN = (
    "DMbot can't post on the DM screen. Give DMbot **Send Messages** there, then press "
    "**Game clock** again."
)
SET_DONE = "🕰️ Clock set to {label}. It's pinned here: {where}"
REJOIN_ASK = "Which time does the whole party keep? Only you see this."
REJOIN_NOTE = "The other groups' clocks are dropped. Timers keep their time left."
SPLIT_SAID = "✂️ Split: {names}. Buttons act on {first}. Change it under the clock."
SPLIT_PHRASE = "The party is split, so I didn't move the clock. Use the buttons for each group."
REJOINED = "🤝 Together again at {when} ({name}'s time). Timers kept their time left."
SPLIT_GONE = (
    "Can't undo: the clock has changed since. "
    "Press **Split the party** in **Rests & time…** to split again."
)

ACTIONS: dict[str, tuple[str, str, discord.ButtonStyle, int]] = {
    # id: (label, emoji, style, row)
    "m10": ("+10 min", "⏱️", discord.ButtonStyle.primary, 0),
    "h1": ("+1 hour", "⏱️", discord.ButtonStyle.secondary, 0),
    "set": ("Rests & time…", "🕰️", discord.ButtonStyle.secondary, 0),
    "short": ("Short rest", "🛌", discord.ButtonStyle.secondary, 0),
    "long": ("Long rest", "🌙", discord.ButtonStyle.secondary, 0),
    "dawn": ("Skip to dawn", "🌅", discord.ButtonStyle.secondary, 0),
    "typed": ("Type the time…", "🕰️", discord.ButtonStyle.secondary, 1),
    "timer": ("Start a timer", "⏳", discord.ButtonStyle.secondary, 1),
    # The split party (#1607), in the private Rests & time… panel.
    "split": ("Split the party", "✂️", discord.ButtonStyle.secondary, 1),
    "rejoin": ("Rejoin the party", "🤝", discord.ButtonStyle.primary, 1),
    "who": ("Who is where", "🧭", discord.ButtonStyle.secondary, 1),
    # Combat (#1127): the DM's correction for what DMbot heard (or missed).
    "cstart": ("Start combat", "⚔️", discord.ButtonStyle.success, 1),
    "cnext": ("Next round", "⏭️", discord.ButtonStyle.primary, 1),
    "cend": ("End combat", "🏁", discord.ButtonStyle.secondary, 1),
}
# On the pinned DM-screen message: no rests, which are in the private Rests & time… panel.
PINNED = ("m10", "h1", "set", "timer")
PANEL = ("short", "long", "dawn", "typed")
COMBAT_ACTIONS = ("cstart", "cnext", "cend", "cround")  # "cround": a named round, said
# The actions that move game time: they say dawn, noon and dusk as they pass, and the 24-hour
# line (#1173: one list instead of two that could drift apart).
# "Three days later", said by the DM (#1594): rested, or without rest when the DM said so. Days
# passing end a fight like a long rest does: nobody is still in round 4 three days later.
DAY_SKIPS = ("days", "days_norest")
ENDS_COMBAT = ("long", "dawn", *DAY_SKIPS)
# A long rest and skipping to dawn pass the night on purpose, and the note the DM gets already
# says the new time ("Skipped to dawn. Now ..."): saying dawn, noon and dusk too would be the
# same news up to three more times (#1197 item 6). The tired line and "Combat is over" stay.
# The same two actions as ENDS_COMBAT on purpose: both are "the night passes" moves.
QUIET_RESTS = ("long", "dawn")
TIME_MOVING = ("m10", "h1", "short", "long", "advance", *DAY_SKIPS, "cnext", "cend", "cround")
MARK_LINES = {"dawn": "🌅 Dawn", "noon": "☀️ Noon", "dusk": "🌇 Dusk"}


def clock_text(
    clock: Clock,
    effect_lines: list[str] | None = None,
    stored: Stored | None = None,
    style_line: str | None = None,
) -> str:
    """The pinned message: the time, then the running timers one to a line (#998). While the
    party is split (#1607) the head has one time per group and says which group the buttons
    act on."""
    if stored is not None and stored.split and stored.picked_group is not None:
        head = (
            f"🕰️ **{split.dm_line(stored.groups)}**\nButtons act on **{stored.picked_group.name}**."
        )
    else:
        head = f"🕰️ **{game.label(clock.minute)}**"
    fight = game.combat_label(clock)
    # (#1771) how FightBot heard the table run initiative: the DM's line only, in a fight only
    heard = [style_line] if fight and style_line else []
    return "\n".join([head, *([fight] if fight else []), *heard, *(effect_lines or [])])


def _note_round(client: Any, campaign: Any, clock: Clock) -> None:
    """The fight's round just changed: tell FightBot at once (#1771), so a line the DM says right
    after "next round" is counted in that round and not the one before."""
    table = getattr(client, "tables", {}).get(campaign.guild_id)
    fight = getattr(table, "fight", None)
    if table is not None and fight is not None and table.campaign_id == campaign.id:
        fight.moved_to(clock.combat_round)


def _style_line(client: Any, campaign: Campaign) -> str | None:
    """How the running session's table does initiative, as FightBot heard it (#1771)."""
    table = getattr(client, "tables", {}).get(campaign.guild_id)
    if table is None or table.campaign_id != campaign.id:
        return None
    fight = getattr(table, "fight", None)
    return None if fight is None else str(fight.style.dm_phrase())


async def render(
    client: Any, campaign: Campaign, clock: Clock, stored: Stored | None = None
) -> str:
    """The clock message as it should read now, with the campaign's running timers. A
    timers store that can't be read just leaves them off: the time still shows."""
    style_line = _style_line(client, campaign)
    store = getattr(client, "effects", None)
    if store is None:
        return clock_text(clock, None, stored, style_line)
    try:
        running = await store.running(campaign.guild_id, campaign.id)
    except Exception:
        log.exception("Couldn't read the timers for the clock message")
        return clock_text(clock, None, stored, style_line)
    groups = (
        {g.position: (g.name, g.clock.minute) for g in stored.groups}
        if stored is not None and stored.split
        else None
    )
    return clock_text(clock, effects.lines(running, clock.minute, groups), stored, style_line)


@dataclass(slots=True)
class Result:
    """What a press did: the clock before and after, and the short lines to say."""

    before: Clock | None
    after: Clock | None
    lines: list[str] = field(default_factory=list)
    changed: bool = False
    # The group the change was made on while the party is split (#1607), else None.
    group: split.Group | None = None


def _apply(
    action: str, clock: Clock | None, set_to: int | None, minutes: int | None = None
) -> Clock | None:
    """The new clock for a press, or None to change nothing (no clock yet, or nonsense)."""
    if action == "set":
        if set_to is None:
            return None
        if clock is None:  # the first time it is set the party counts as rested
            return Clock(set_to, set_to, None)
        return game.set_to(clock, set_to)
    if clock is None:
        return None
    if action in ENDS_COMBAT and clock.combat_round is not None:
        # A long rest or skipping to dawn means the fight is over: as if End combat was
        # pressed first (#1173). A short rest and the other moves leave a fight running.
        clock = game.combat_end(clock) or clock
    if action == "m10":
        return game.advance(clock, 10)
    if action == "advance":  # "an hour later", said by the DM (#1117)
        return None if minutes is None else game.advance(clock, minutes)
    if action in DAY_SKIPS:
        if minutes is None or minutes % game.MINUTES_PER_DAY:
            return None
        return game.skip_days(clock, minutes // game.MINUTES_PER_DAY, rested=action == "days")
    if action in ("h1", "short"):
        return game.advance(clock, game.SHORT_REST_MIN)
    if action == "long":
        return game.long_rest(clock)
    if action == "dawn":
        return game.set_to(clock, game.next_dawn(clock.minute))
    if action == "cstart":
        return game.combat_start(clock)
    if action == "cnext":
        return game.combat_next(clock)
    if action == "cend":
        return game.combat_end(clock)
    if action == "cround":  # "round three", said by the DM
        return None if set_to is None else game.combat_round_to(clock, set_to)
    return None


apply_action = _apply  # the press's own rule, for a caller that walks a whole session (#2979)


def _speaks(action: str) -> bool:
    """Dawn, noon and dusk are said when time passes by a button or a rest; not when the
    DM says what time it is (the DM already knows)."""
    return action in TIME_MOVING


async def press(
    client: Any,
    guild_id: int,
    campaign_id: str,
    user_id: int,
    action: str,
    *,
    set_to: int | None = None,
    minutes: int | None = None,
    refuse_if_split: bool = False,
) -> Result:
    """Do one thing to the clock. Raises CampaignError (plain words) if the person isn't a
    DM of the campaign. The 24-hour line is worked out here, once, in the same change."""
    store: ClockStore = client.clocks
    tired_on = action in TIME_MOVING and await _tired_rule_on(client, guild_id, campaign_id)
    result = Result(None, None)

    def change(current: Clock | None) -> Clock | None:
        new = _apply(action, current, set_to, minutes)
        if new is None:
            return None
        if tired_on and game.tired_due(new):
            new = game.told_tired(new)
            result.lines.append(tired_line(new))
        return new

    before, after, group = await store.change_named(
        guild_id, campaign_id, user_id, change, refuse_if_split=refuse_if_split
    )
    result.before, result.after, result.group = before, after, group
    result.changed = after is not None and after != before
    if (
        action in ENDS_COMBAT
        and before is not None
        and after is not None
        and before.combat_round is not None
        and after.combat_round is None
    ):
        # (Undo of this rest brings the fight back: the round rides in the Undo button.)
        result.lines.insert(
            0,
            f"🏁 Combat is over after {before.combat_round} "
            f"round{'s' if before.combat_round != 1 else ''}.",
        )
    if (
        before is not None
        and after is not None
        and _speaks(action)
        and action not in QUIET_RESTS + DAY_SKIPS
    ):
        marks = game.crossings(before.minute, after.minute)
        result.lines = [MARK_LINES[name] for _, name in marks] + result.lines
    if group is not None:
        # Dawn, noon, dusk and the 24-hour line are said per group (#1607).
        result.lines = [f"{group.name}: {line}" for line in result.lines]
        if result.changed and after is not None and before is not None and after != before:
            drift = await store.claim_drift(guild_id, campaign_id)
            if drift is not None:
                result.lines.append(drift)
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


def clock_view(
    campaign_id: str, clock: Clock | None = None, stored: Stored | None = None
) -> discord.ui.View:
    """The pinned message's buttons. Combat's (#1127) show what makes sense now: Start combat
    out of a fight, Next round and End combat in one. While the party is split (#1607) a picker
    of the groups sits under them."""
    view = discord.ui.View(timeout=None)
    for action in PINNED:
        view.add_item(ClockButton(campaign_id, action))
    for action in ("cnext", "cend") if game.in_combat(clock) else ("cstart",):
        view.add_item(ClockButton(campaign_id, action))
    if stored is not None and stored.split:
        view.add_item(ClockPicker(campaign_id, stored.groups, stored.picked))
    return view


def panel_view(campaign_id: str, is_split: bool = False) -> discord.ui.View:
    """What **Rests & time…** answers with, privately: the rests, skipping to dawn and typing a
    time, then Split the party (or, once split, Rejoin the party). An ephemeral answer, so only
    the DM who pressed it sees it."""
    view = discord.ui.View(timeout=600)
    for action in PANEL:
        view.add_item(ClockButton(campaign_id, action))
    view.add_item(ClockButton(campaign_id, "rejoin" if is_split else "split"))
    if is_split:
        view.add_item(ClockButton(campaign_id, "who"))  # who is in each group (#3131)
    return view


async def show(client: Any, campaign: Campaign, stored: Stored) -> discord.Message | None:
    """Edit the pinned clock message in place with the clock now; post (and pin) a new one if
    there isn't one or it is gone. Returns the message, or None if nothing could be shown."""
    where_are_we.shown(client, campaign)  # (the quiet "where are we" note waits again)
    _note_round(client, campaign, stored.clock)
    text = await render(client, campaign, stored.clock, stored)
    view = clock_view(campaign.id, stored.clock, stored)
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


def players_see(campaign: Campaign, what: str = "the day and ") -> str:
    """Where the players see the clock: said only when the chat channel exists. While splitting,
    `what` also tells the DM the players read the group names."""
    if campaign.dmbchat_channel_id is None:
        return ""
    return PLAYERS_SEE.format(channel=campaign.dmbchat_channel_id, what=what)


# ---- the players' clock: a small line in the DMbot chat channel (#2877) -------------------------

MARK = "🕰️"
SMALL = "-# "  # Discord's small text
SCAN = 50  # recent messages looked at for DMbot's last clock line (a busier chat may post it again)
SPLIT_HEAD = "The party is split"
_VIEW = re.compile(r"Day \d+, [a-z -]+")


def players_view(stored: Stored) -> list[str]:
    """What the players are told of the clock, as lines: the day and a rough time of day, nothing
    else (no exact time, no timer, no button, no fight: those are the DM's), or while the party is
    split one line per group in the same words."""
    if not stored.split:
        return [game.short_label(stored.clock.minute)]
    return [SPLIT_HEAD, *split.players_lines(stored.groups)]


def small(lines: list[str]) -> str:
    """The lines as Discord's small text, the clock mark on the first."""
    return "\n".join(
        f"{SMALL}{MARK} {line}" if i == 0 else f"{SMALL}{line}" for i, line in enumerate(lines)
    )


def _body(content: str) -> str:
    return "\n".join(
        line.removeprefix(SMALL).removeprefix(f"{MARK} ") for line in content.splitlines()
    )


def view_of(content: str) -> str:
    """The clock view a line of DMbot's holds: "Day 5, morning" (also inside "Long rest done.
    Day 6, dawn."), or the whole split text."""
    body = _body(content)
    if body.startswith(SPLIT_HEAD):
        return body
    found = _VIEW.search(body)
    return found.group(0).strip() if found else body


def is_plain(content: str) -> bool:
    """A plain clock line (the day and the word, or the split text), as opposed to a rest, a new
    day or a correction, which are kept when the next change comes."""
    body = _body(content)
    return body.startswith(SPLIT_HEAD) or _VIEW.fullmatch(body) is not None


async def put_line(
    client: Any, campaign: Campaign, lines: list[str], view: str, *, plain: bool
) -> None:
    """Say it in the campaign's chat channel as a small line. A plain line says nothing if
    DMbot's last clock line there already holds this view; if that last line is still the newest
    message in the channel and a plain one, it is edited, so quick changes stay one line;
    otherwise a new line is posted. No chat channel, or DMbot cannot read or post there: nothing
    is said (a counts-only note) and nothing is held up."""
    channel_id = campaign.dmbchat_channel_id
    if channel_id is None:
        log.info("A clock line was not posted: the campaign has no chat channel")
        return
    channel = client.get_channel(channel_id)
    me = getattr(client.user, "id", None)
    text = small(lines)
    try:
        if channel is None and hasattr(client, "fetch_channel"):
            channel = await client.fetch_channel(
                channel_id
            )  # (a new channel may not be cached yet)
        if not isinstance(channel, discord.abc.Messageable) or me is None:
            log.info("A clock line was not posted: the chat channel is not available")
            return
        recent = [m async for m in channel.history(limit=SCAN)]  # newest first
        last = next(
            (m for m in recent if m.author.id == me and m.content.startswith(SMALL + MARK)), None
        )
        if plain and last is not None and view_of(last.content) == view:
            return  # nothing the players see has changed
        if last is not None and recent[0].id == last.id and is_plain(last.content):
            await last.edit(content=text, allowed_mentions=NO_PINGS)
        else:
            await client.post_message(channel_id, text)
    except discord.HTTPException:
        log.info("A clock line was not posted: Discord refused")


_players_locks: dict[str, asyncio.Lock] = {}  # one players' clock update at a time per campaign


async def show_players(client: Any, campaign: Campaign, stored: Stored | None = None) -> None:
    """Tell the players the clock when what they see of it has changed. One at a time per
    campaign, and the clock is read again inside the lock, so two updates (a press, a spoken rest,
    the start) can never leave older words over newer ones. Never raises and never holds anything
    up: the DM's clock is the one that matters at the table."""
    try:
        async with _players_locks.setdefault(campaign.id, asyncio.Lock()):
            current = await client.clocks.get(campaign.guild_id, campaign.id)
            if current is not None:
                lines = players_view(current)
                await put_line(
                    client,
                    campaign,
                    lines,
                    "\n".join(lines) if current.split else lines[0],
                    plain=True,
                )
    except Exception:
        log.exception("Couldn't update the players' clock")


def players_line(action: str, before: Clock | None, after: Clock) -> str | None:
    """The one short line the players get: a rest finishing, or a new day beginning. Only
    for time moving forward by the DM's buttons or words; setting the time is a correction
    and says nothing."""
    if before is None or after.minute <= before.minute or action == "set":
        return None
    when = game.short_label(after.minute)
    if action in ("short", "long"):
        return f"{'Short' if action == 'short' else 'Long'} rest done. {when}."
    if game.day_of(after.minute) > game.day_of(before.minute):
        return f"A new day begins. {when}."
    return None


async def update_players(
    client: Any, campaign: Campaign, action: str, result: Result, *, announce: bool = True
) -> None:
    """After a change: a finished rest or a new day is said once (it holds the day and the word
    too, so it stands in for the plain line); otherwise the plain line, if what the players see
    changed. Best effort."""
    try:
        stored = await client.clocks.get(campaign.guild_id, campaign.id)
        if stored is None:
            return
        fresh = await client.campaigns.get(campaign.guild_id, campaign.id) or campaign
        after = result.after or stored.clock
        line = players_line(action, result.before, after) if announce else None
        if line is None:
            await show_players(client, fresh, stored)
            return
        if result.group is not None:
            line = f"{result.group.name}: {line}"  # (#1607) which part of the party
        async with _players_locks.setdefault(campaign.id, asyncio.Lock()):
            await put_line(client, fresh, [line], game.short_label(after.minute), plain=False)
    except Exception:
        log.exception("Couldn't tell the players about the clock")


async def correct_players(client: Any, campaign: Campaign, line: str) -> None:
    """An Undo: the players may have been told a rest finished, so say it was taken back."""
    try:
        async with _players_locks.setdefault(campaign.id, asyncio.Lock()):
            await put_line(client, campaign, [f"Correction: {line}"], view_of(line), plain=False)
    except Exception:
        log.exception("Couldn't tell the players about a correction")


async def say(
    client: Any,
    campaign: Campaign,
    lines: list[str],
    undo: str | None = None,
    combat_undo: str | None = None,
    split_undo: tuple[str, str] | None = None,
) -> None:
    """The short lines (dawn, noon, dusk, tired, combat) on the DM screen, nowhere else.
    `split_undo` is (kind, token): the Undo under a split or a rejoin (#1607)."""
    if campaign.dm_screen_channel_id is None or not lines:
        return
    view = None
    if split_undo:
        view = discord.ui.View(timeout=None)
        view.add_item(SplitUndoButton(campaign.id, *split_undo))
    elif undo:
        view = discord.ui.View(timeout=None)
        view.add_item(ClockUndoButton(campaign.id, undo))
    elif combat_undo:
        view = discord.ui.View(timeout=None)
        view.add_item(CombatUndoButton(campaign.id, combat_undo))
    posted = await client.post_message(campaign.dm_screen_channel_id, "\n".join(lines), view)
    if posted is not None:  # (a line that never arrived shows nothing, so the wait goes on)
        where_are_we.shown(client, campaign)


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
) -> bool:
    """A press after the interaction was answered: change the clock, show it, say the lines.
    True if the clock was changed; a failure is told to the presser and returns False, so a
    caller that says "done" can tell."""
    async with _locks.setdefault(campaign_id, asyncio.Lock()):
        return await _run_press(interaction, campaign_id, action, set_to)


async def _run_press(
    interaction: discord.Interaction, campaign_id: str, action: str, set_to: int | None
) -> bool:
    client: Any = interaction.client
    guild = interaction.guild
    campaign = await client.campaigns.get(guild.id, campaign_id) if guild else None
    if campaign is None:
        await _reply(interaction, GONE)
        return False
    try:
        result = await press(
            client, campaign.guild_id, campaign.id, interaction.user.id, action, set_to=set_to
        )
    except CampaignError as exc:
        await _reply(interaction, str(exc))
        return False
    except Exception:
        log.exception("Couldn't change the game clock")
        await _reply(interaction, FAILED)
        return False
    if result.after is None:
        await _reply(interaction, SET_FIRST)
        return False
    if action in COMBAT_ACTIONS and not result.changed:
        await _reply(interaction, NO_COMBAT_CHANGE[action])
        return False
    stored = await client.clocks.get(campaign.guild_id, campaign.id)
    if stored is None:
        await _reply(interaction, FAILED)
        return False
    message = await _show_here(interaction, client, campaign, stored)
    if message is None:
        await _reply(interaction, OFF_SCREEN)
    elif action == "set":
        # The form saved: the DM's own screen shows nothing else, so say where the clock is.
        await _reply(
            interaction,
            SET_DONE.format(label=game.short_label(result.after.minute), where=message.jump_url)
            + players_see(campaign),
        )
    # The players hear a finished rest or a new day in the chat channel (#2877); a fight's rounds
    # are the DM's, so a combat action says nothing to them.
    await update_players(client, campaign, action, result, announce=action not in COMBAT_ACTIONS)
    if action in COMBAT_ACTIONS and result.before is not None:
        await say(  # one line with Undo, like what is heard at the table (#1127)
            client,
            campaign,
            [combat_note(action, result.before, result.after), *result.lines],
            combat_undo=combat_undo_id(result.before, result.after, result.group),
        )
    else:
        await say(client, campaign, result.lines)  # dawn, noon, dusk, tired: on the DM screen
    from dmbot.dm_screen import effects as timers

    await timers.announce_due(client, campaign, result.after.minute)
    if result.before is not None and action in ("short", "long", "dawn"):
        # The DM who pressed sees the clock change; the note with Undo is for them alone.
        note = rest_note(action, result.after)
        view = discord.ui.View(timeout=None)
        view.add_item(
            ClockUndoButton(campaign.id, undo_id(result.before, result.after, result.group))
        )
        with contextlib.suppress(discord.HTTPException):
            await interaction.followup.send(note, view=view, ephemeral=True)
    return True


NO_COMBAT_CHANGE = {
    "cstart": "Combat is already running.",
    "cnext": "There is no combat running, or it has reached round 99, the most DMbot counts.",
    "cend": "There is no combat running.",
    "cround": "There is no combat running, or it is already on that round.",
}


def combat_note(action: str, before: Clock, after: Clock) -> str:
    """The one DM-screen line for a combat change (#1127)."""
    moved = f" Now {game.short_label(after.minute)}." if after.minute != before.minute else ""
    if action == "cstart":
        return "⚔️ Combat starts. Round 1."
    if action == "cend":
        rounds = before.combat_round or 1
        return f"🏁 Combat is over after {rounds} round{'s' if rounds != 1 else ''}.{moved}"
    return f"⚔️ Round {after.combat_round}.{moved}"


def rest_note(action: str, clock: Clock, minutes: int | None = None) -> str:
    if action == "dawn":
        return f"🌅 Skipped to dawn. Now {game.short_label(clock.minute)}."
    if action in DAY_SKIPS and minutes is not None:
        counted = "counted as rested" if action == "days" else "no rest counted"
        later = durations.words(minutes)
        return f"⏩ {later} later ({counted}). Now {game.short_label(clock.minute)}."
    if action == "advance" and minutes is not None:
        later = durations.words(minutes).capitalize()
        return f"⏩ {later} later. Now {game.short_label(clock.minute)}."
    kind, emoji = ("Short", "🛌") if action == "short" else ("Long", "🌙")
    return f"{emoji} {kind} rest. Now {game.short_label(clock.minute)}."


async def _show_here(
    interaction: discord.Interaction, client: Any, campaign: Campaign, stored: Stored
) -> discord.Message | None:
    """Redraw the clock in the message that was pressed; if the press came from somewhere
    else (the form opened from ⚙️ Manage DMbot), show it the usual way."""
    where_are_we.shown(client, campaign)  # the clock is redrawn right here: shown, and moved
    message = interaction.message
    if message is not None and message.id == stored.message_id:
        text, view = (
            await render(client, campaign, stored.clock, stored),
            clock_view(campaign.id, stored.clock, stored),
        )
        with contextlib.suppress(discord.HTTPException):
            await message.edit(content=text, view=view, allowed_mentions=NO_PINGS)
            return message
    if message is not None and _is_clock_message(message):
        with contextlib.suppress(discord.HTTPException):
            text = await render(client, campaign, stored.clock, stored)
            await message.edit(content=text, view=clock_view(campaign.id, stored.clock, stored))
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
    template=(
        rf"dmbot:clock:{_ID}:"
        r"(?P<action>m10|h1|short|long|dawn|set|typed|timer|open|cstart|cnext|cend|split|rejoin|who)"
    ),
):
    """One of the clock's buttons (`open` is the ⚙️ Manage DMbot button that starts or shows it)."""

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

                # Players don't get the form: checked first, inside the 3 seconds a form allows.
                async with asyncio.timeout(2):
                    campaign = await client.campaigns.get(guild.id, self.campaign_id)
                    if campaign is None or interaction.user.id not in campaign.dm_user_ids:
                        await _reply(interaction, "Only this campaign's DMs can use the clock.")
                        return
                await interaction.response.send_modal(timers.TimerForm(self.campaign_id))
                return
            if self.action in ("split", "rejoin"):
                await self._split_or_rejoin(interaction, client, guild.id)
                return
            if self.action == "who":
                await self._who(interaction, client, guild.id)
                return
            if self.action in ("set", "typed", "open"):
                await self._form_or_show(interaction, client, guild.id)
                return
            await interaction.response.defer()  # the press edits the clock message
            await run_press(interaction, self.campaign_id, self.action)
        except Exception:
            log.exception("The game clock button failed")
            await _reply(interaction, FAILED)

    async def _split_or_rejoin(
        self, interaction: discord.Interaction, client: Any, guild_id: int
    ) -> None:
        """Split the party: the form that names the groups. Rejoin: the question of which time
        to keep. Both are checked for a DM before they are shown (and again where they save)."""
        async with asyncio.timeout(2):
            campaign = await client.campaigns.get(guild_id, self.campaign_id)
            if campaign is None:
                await _reply(interaction, GONE)
                return
            if interaction.user.id not in campaign.dm_user_ids:
                await _reply(interaction, NOT_A_DM)
                return
            stored = await client.clocks.get(guild_id, campaign.id)
        if stored is None:
            await _reply(interaction, SET_FIRST)
        elif self.action == "split":
            if stored.split:
                await _reply(interaction, split.ALREADY_SPLIT)
            elif game.in_combat(stored.clock):
                await _reply(interaction, split.IN_COMBAT)
            else:
                await interaction.response.send_modal(SplitForm(campaign.id))
        elif not stored.split:
            await _reply(interaction, split.NOT_SPLIT)
        else:
            times = [
                f"{g.name}: {split.exact_label(g.clock.minute)}" + (" (latest)" if i == 0 else "")
                for i, g in enumerate(split.latest_first(stored.groups))
            ]
            await interaction.response.send_message(
                "\n".join([REJOIN_ASK, *times, REJOIN_NOTE]),
                view=RejoinView(campaign.id, stored.groups),
                ephemeral=True,
            )

    async def _who(self, interaction: discord.Interaction, client: Any, guild_id: int) -> None:
        """Who is in each group: the private question, for a DM, while the party is split."""
        async with asyncio.timeout(2):
            campaign = await client.campaigns.get(guild_id, self.campaign_id)
            if campaign is None:
                await _reply(interaction, GONE)
                return
            if interaction.user.id not in campaign.dm_user_ids:
                await _reply(interaction, NOT_A_DM)
                return
            stored = await client.clocks.get(guild_id, campaign.id)
        if stored is None or not stored.split:
            await _reply(interaction, split.NOT_SPLIT)
            return
        content, view = await placement_message(client, campaign.id, guild_id, stored.groups)
        if view is None:  # discord.py cannot take view=None here
            await _reply(interaction, content)
            return
        await interaction.response.send_message(content, view=view, ephemeral=True)

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
        if self.action == "set" and stored is not None:
            # The private panel: rests, skipping to dawn, typing a time. Only this DM sees it.
            acting = stored.picked_group
            text = PANEL_TEXT if acting is None else f"{PANEL_TEXT} Acting on **{acting.name}**."
            await interaction.response.send_message(
                text, view=panel_view(campaign.id, stored.split), ephemeral=True
            )
            return
        if self.action == "typed" or stored is None:
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
        await _reply(
            interaction, ALREADY_SHOWN.format(where=message.jump_url) + players_see(campaign)
        )


class SplitForm(discord.ui.Modal):
    """Name the groups: two are needed, a third is optional (#1607)."""

    first: discord.ui.TextInput[SplitForm] = discord.ui.TextInput(
        label="First group (players see it)", placeholder="Tower", max_length=split.NAME_MAX
    )
    second: discord.ui.TextInput[SplitForm] = discord.ui.TextInput(
        label="Second group", placeholder="Camp", max_length=split.NAME_MAX
    )
    third: discord.ui.TextInput[SplitForm] = discord.ui.TextInput(
        label="Third group (optional)", required=False, max_length=split.NAME_MAX
    )

    def __init__(self, campaign_id: str) -> None:
        super().__init__(title="Split the party")
        self.campaign_id = campaign_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        checked = split.check_names([self.first.value, self.second.value, self.third.value])
        if isinstance(checked, str):
            await interaction.response.send_message(checked, ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await run_split(interaction, self.campaign_id, checked)


def _names_in_words(names: list[str] | tuple[str, ...]) -> str:
    bold = [f"**{discord.utils.escape_markdown(n)}**" for n in names]
    return bold[0] if len(bold) == 1 else ", ".join(bold[:-1]) + " and " + bold[-1]


async def run_split(interaction: discord.Interaction, campaign_id: str, names: list[str]) -> bool:
    """The form was saved: split the party, redraw the clock, say it on the DM screen with an
    Undo. A failure is told to the presser and returns False."""
    client: Any = interaction.client
    guild = interaction.guild
    campaign = await client.campaigns.get(guild.id, campaign_id) if guild else None
    if campaign is None:
        await _reply(interaction, GONE)
        return False
    async with _locks.setdefault(campaign_id, asyncio.Lock()):
        try:
            stored, token = await client.clocks.split(
                campaign.guild_id, campaign.id, interaction.user.id, names
            )
        except CampaignError as exc:
            await _reply(interaction, str(exc))
            return False
        except Exception:
            log.exception("Couldn't split the party")
            await _reply(interaction, FAILED)
            return False
        await show(client, campaign, stored)
        await show_players(client, campaign, stored)
        await say(
            client,
            campaign,
            [SPLIT_SAID.format(names=_names_in_words(names), first=_names_in_words(names[:1]))],
            split_undo=("split", token),
        )
    await _reply(
        interaction,
        f"✂️ Split into {_names_in_words(names)}." + players_see(campaign, "these names and "),
    )
    await _ask_who(interaction, client, campaign.guild_id, campaign.id)
    return True


# Who is in each group (#3131): so a line spoken in a split gets its speaker's group's game time.
WHO_ASK = (
    "Pick who is in each group. Anyone you skip gets no game time on their lines until you do."
)
WHO_NONE = "There are no player characters in this campaign yet, so there is nobody to place."
WHO_SAVED = "Saved."
WHO_SKIPPED = (
    "Skipped. Press **Who is where** in **Rests & time…** any time while the party is split."
)
SELECT_MAX = 25  # a select shows at most 25 choices
WHO_CUT = "Showing the first 25 player characters."


async def _ask_who(
    interaction: discord.Interaction, client: Any, guild_id: int, campaign_id: str
) -> None:
    """After a split: the private question of who is in each group. Told nothing when the
    campaign has no player characters, and never an error (the split itself is done)."""
    try:
        stored = await client.clocks.get(guild_id, campaign_id)
        if stored is None or not stored.split:
            return
        content, view = await placement_message(client, campaign_id, guild_id, stored.groups)
        if view is not None:
            await interaction.followup.send(content, view=view, ephemeral=True)
    except Exception:
        log.exception("Couldn't ask who is in each group")


async def placement_message(
    client: Any, campaign_id: str, guild_id: int, groups: tuple[split.Group, ...]
) -> tuple[str, discord.ui.View | None]:
    """The words and the view for placing the player characters, as they stand now."""
    characters = await client.clocks.player_characters(guild_id, campaign_id)
    if not characters:
        return WHO_NONE, None
    members = await client.clocks.members(guild_id, campaign_id)
    return _who_text(groups, characters, members), WhoView(campaign_id, groups, characters, members)


def _who_text(
    groups: tuple[split.Group, ...], characters: list[tuple[str, str]], members: dict[str, int]
) -> str:
    names = dict(characters)
    lines = [WHO_ASK]
    if len(characters) > SELECT_MAX:
        lines.append(WHO_CUT)
    for g in groups:
        here = [names[c] for c, p in members.items() if p == g.position and c in names]
        lines.append(
            f"**{discord.utils.escape_markdown(g.name)}**: "
            + (", ".join(sorted(here)) or "nobody yet")
        )
    return "\n".join(lines)


class WhoView(discord.ui.View):
    """One picker per group, with the campaign's player characters, and a Skip button. Private
    and short-lived: it answers one press. Each pick says who is in that group now."""

    def __init__(
        self,
        campaign_id: str,
        groups: tuple[split.Group, ...],
        characters: list[tuple[str, str]],
        members: dict[str, int],
    ) -> None:
        super().__init__(timeout=600)
        self.campaign_id, self.groups, self.characters = campaign_id, groups, characters
        for g in groups:
            self.add_item(_WhoSelect(g, characters, members))
        self.add_item(_WhoSkip())


class _WhoSelect(discord.ui.Select["WhoView"]):
    def __init__(
        self, group: split.Group, characters: list[tuple[str, str]], members: dict[str, int]
    ) -> None:
        shown = characters[:SELECT_MAX]
        super().__init__(
            placeholder=f"Who is in {group.name}?"[:150],
            min_values=0,
            max_values=len(shown),
            options=[
                discord.SelectOption(
                    label=name[:100], value=cid, default=members.get(cid) == group.position
                )
                for cid, name in shown
            ],
        )
        self.group = group

    async def callback(self, interaction: discord.Interaction) -> Any:
        view = self.view
        client: Any = interaction.client
        guild = interaction.guild
        if view is None or guild is None:
            return
        chosen = set(self.values)
        try:
            # An old picker must not write into groups made since (rejoin, then split again):
            # the same number can now be another group.
            now = await client.clocks.get(guild.id, view.campaign_id)
            if (
                now is None
                or not now.split
                or [(g.position, g.name) for g in now.groups]
                != [(g.position, g.name) for g in view.groups]
            ):
                await interaction.response.send_message(GROUP_GONE, ephemeral=True)
                return
            before = await client.clocks.members(guild.id, view.campaign_id)
            changes: dict[str, int | None] = {c: self.group.position for c in chosen}
            shown = {cid for cid, _ in view.characters[:SELECT_MAX]}
            for cid, position in before.items():  # in this group before, not picked now: released
                # (only among those the picker showed: the rest could never be picked)
                if position == self.group.position and cid not in chosen and cid in shown:
                    changes[cid] = None
            members = await client.clocks.place_members(
                guild.id, view.campaign_id, interaction.user.id, changes
            )
        except CampaignError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        except Exception:
            log.exception("Couldn't place the player characters")
            await interaction.response.send_message(FAILED, ephemeral=True)
            return
        fresh = WhoView(view.campaign_id, view.groups, view.characters, members)
        await interaction.response.edit_message(
            content=_who_text(view.groups, view.characters, members), view=fresh
        )


class _WhoSkip(discord.ui.Button["WhoView"]):
    def __init__(self) -> None:
        super().__init__(label="Skip for now", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction) -> Any:
        await interaction.response.edit_message(content=WHO_SKIPPED, view=None)


class RejoinView(discord.ui.View):
    """The question after **Rejoin the party**: one button per group with its time, the latest
    first. Short-lived and private: it is an answer to one press."""

    def __init__(self, campaign_id: str, groups: tuple[split.Group, ...]) -> None:
        super().__init__(timeout=600)
        for i, g in enumerate(split.latest_first(groups)):
            self.add_item(_RejoinChoice(campaign_id, g, primary=i == 0))


class _RejoinChoice(discord.ui.Button["RejoinView"]):
    def __init__(self, campaign_id: str, group: split.Group, *, primary: bool) -> None:
        super().__init__(
            label=f"Keep {group.name}"[:25],
            style=discord.ButtonStyle.primary if primary else discord.ButtonStyle.secondary,
        )
        self.campaign_id, self.position = campaign_id, group.position

    async def callback(self, interaction: discord.Interaction) -> Any:
        await interaction.response.edit_message(content="🤝 Rejoining the party…", view=None)
        try:
            done = await run_rejoin(interaction, self.campaign_id, self.position)
        except Exception:
            log.exception("Couldn't rejoin the party")
            done = None
            await _reply(interaction, FAILED)
        if done is not None:
            with contextlib.suppress(discord.HTTPException):
                await interaction.edit_original_response(
                    content=f"🤝 Together again at {split.exact_label(done.clock.minute)}."
                )


async def run_rejoin(
    interaction: discord.Interaction, campaign_id: str, keep: int
) -> Rejoined | None:
    """Back to one clock at the time the DM chose, shown and said with an Undo. None (and the
    reason told to the presser) when it didn't happen."""
    client: Any = interaction.client
    guild = interaction.guild
    campaign = await client.campaigns.get(guild.id, campaign_id) if guild else None
    if campaign is None:
        await _reply(interaction, GONE)
        return None
    async with _locks.setdefault(campaign_id, asyncio.Lock()):
        try:
            done: Rejoined = await client.clocks.rejoin(
                campaign.guild_id, campaign.id, interaction.user.id, keep
            )
        except CampaignError as exc:
            await _reply(interaction, str(exc))
            return None
        stored = await client.clocks.get(campaign.guild_id, campaign.id)
        if stored is None:
            await _reply(interaction, FAILED)
            return None
        await show(client, campaign, stored)
        await show_players(client, campaign, stored)
        from dmbot.dm_screen import effects as timers

        await timers.announce_due(client, campaign, stored.clock.minute)
        await say(
            client,
            campaign,
            [
                REJOINED.format(when=split.exact_label(done.clock.minute), name=done.kept.name)
                + (
                    f" {_names_in_words(done.dropped_fights)}: a fight there was dropped."
                    if done.dropped_fights
                    else ""
                )
            ],
            split_undo=("rejoin", done.token),
        )
    return done


class ClockPicker(
    discord.ui.DynamicItem[discord.ui.Select[discord.ui.View]],
    template=rf"dmbot:clockpick:{_ID}",
):
    """Under the pinned clock while the party is split: which group the buttons act on
    (#1607). The choice is remembered across restarts; only a DM of the campaign can change it
    (checked where it is saved)."""

    def __init__(
        self,
        campaign_id: str,
        groups: tuple[split.Group, ...] = (),
        picked: int | None = None,
    ) -> None:
        options = [
            discord.SelectOption(
                label=g.name[:100],
                value=str(g.position),
                default=g.position == picked,
            )
            for g in groups
        ] or [discord.SelectOption(label="Group", value="1")]  # (a restart: only the id matters)
        super().__init__(
            discord.ui.Select(
                placeholder="Act on which group?",
                options=options,
                custom_id=f"dmbot:clockpick:{campaign_id}",
                row=2,
            )
        )
        self.campaign_id = campaign_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> ClockPicker:
        return cls(match["campaign"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        client: Any = interaction.client
        guild = interaction.guild
        data: Any = interaction.data
        values = data.get("values") if isinstance(data, dict) else None
        if guild is None or not values:
            return
        await interaction.response.defer()
        try:
            campaign = await client.campaigns.get(guild.id, self.campaign_id)
            if campaign is None:
                await _reply(interaction, GONE)
                return
            async with _locks.setdefault(self.campaign_id, asyncio.Lock()):
                stored = await client.clocks.pick(
                    guild.id, campaign.id, interaction.user.id, int(values[0])
                )
                await show(client, campaign, stored)
        except CampaignError as exc:
            # (The pinned message still shows the old pick: put the picker back as it was.)
            await _reply(interaction, str(exc))
            stored = await client.clocks.get(guild.id, self.campaign_id)
            campaign = await client.campaigns.get(guild.id, self.campaign_id)
            if stored is not None and campaign is not None:
                await show(client, campaign, stored)
        except Exception:
            log.exception("The group picker failed")
            await _reply(interaction, FAILED)


class SplitUndoButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:splitundo:{_ID}:(?P<kind>split|rejoin):(?P<token>[0-9a-f]{{8}})",
):
    """↩️ Undo under a split or a rejoin (#1607): the party goes back to how it was, if it is
    still the last of the two and no clock has moved since (the answer says so if not)."""

    def __init__(self, campaign_id: str, kind: str, token: str) -> None:
        super().__init__(
            discord.ui.Button(
                label="Undo",
                emoji="↩️",
                style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:splitundo:{campaign_id}:{kind}:{token}",
            )
        )
        self.campaign_id, self.kind, self.token = campaign_id, kind, token

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> SplitUndoButton:
        return cls(match["campaign"], match["kind"], match["token"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        client: Any = interaction.client
        guild = interaction.guild
        if guild is None:
            return
        await interaction.response.defer()
        try:
            campaign = await client.campaigns.get(guild.id, self.campaign_id)
            if campaign is None:
                await _reply(interaction, GONE)
                return
            async with _locks.setdefault(self.campaign_id, asyncio.Lock()):
                undo = (
                    client.clocks.undo_split if self.kind == "split" else client.clocks.undo_rejoin
                )
                try:
                    done = await undo(guild.id, campaign.id, interaction.user.id, self.token)
                except CampaignError as exc:
                    await _reply(interaction, str(exc))
                    return
                if not done:
                    await _reply(interaction, MOVED_ON if self.kind == "split" else SPLIT_GONE)
                    return
                stored = await client.clocks.get(guild.id, campaign.id)
                if stored is not None:
                    await show(client, campaign, stored)
                    await show_players(client, campaign, stored)
        except Exception:
            log.exception("Undoing a split or rejoin failed")
            await _reply(interaction, FAILED)
            return
        said = (
            "The party is together again." if self.kind == "split" else "The party is split again."
        )
        with contextlib.suppress(discord.HTTPException):
            await interaction.edit_original_response(content=f"↩️ Undone. {said}", view=None)


_answered: set[int] = set()  # suggestion messages already answered (bounded below)
SUGGEST_GAP_S = 300  # at most one rest suggestion per campaign in this long (#1117)
NOT_A_DM = "Only this campaign's DMs can use the clock."


def suggestion_view(campaign_id: str, kind: str) -> discord.ui.View:
    """The two buttons under a player's suggestion: take the rest, or not now."""
    view = discord.ui.View(timeout=None)
    view.add_item(SuggestionButton(campaign_id, kind, "yes"))
    view.add_item(SuggestionButton(campaign_id, kind, "no"))
    return view


class SuggestionButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:clocksuggest:{_ID}:(?P<kind>short|long):(?P<answer>yes|no)",
):
    """A player said "let's take a short rest": the DM decides. **Short rest** moves the clock
    like the button in the Rests & time… panel; **Not now** just closes the question. Nothing
    moves until a DM of the campaign presses (checked again when the clock changes)."""

    def __init__(self, campaign_id: str, kind: str, answer: str) -> None:
        yes = answer == "yes"
        super().__init__(
            discord.ui.Button(
                label=f"{kind.capitalize()} rest" if yes else "Not now",
                emoji="🛌" if yes else None,
                style=discord.ButtonStyle.primary if yes else discord.ButtonStyle.secondary,
                custom_id=f"dmbot:clocksuggest:{campaign_id}:{kind}:{answer}",
            )
        )
        self.campaign_id, self.kind, self.answer = campaign_id, kind, answer

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> SuggestionButton:
        return cls(match["campaign"], match["kind"], match["answer"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        client: Any = interaction.client
        guild = interaction.guild
        if guild is None:
            return
        await interaction.response.defer()
        try:
            campaign = await client.campaigns.get(guild.id, self.campaign_id)
            if campaign is None:
                await _reply(interaction, GONE)
                return
            if interaction.user.id not in campaign.dm_user_ids:
                await _reply(interaction, NOT_A_DM)
                return
            message_id = interaction.message.id if interaction.message is not None else 0
            if message_id in _answered:
                return  # a second press (or a second DM) on a question already answered
            if message_id:
                _answered.add(message_id)  # before any await below: one answer per question
                if len(_answered) > 500:
                    _answered.clear()
            if self.answer == "yes" and await client.clocks.get(guild.id, campaign.id) is None:
                _answered.discard(message_id)  # nothing happened: the question stays open
                await _reply(interaction, SET_FIRST)
                return
            asked = interaction.message.content if interaction.message is not None else ""
            who = discord.utils.escape_markdown(getattr(interaction.user, "display_name", "a DM"))
            ended = (
                f"Dismissed by {who}."
                if self.answer == "no"
                else f"✅ {self.kind.capitalize()} rest, chosen by {who}."
            )
            if self.answer == "yes" and not await run_press(interaction, campaign.id, self.kind):
                # The press failed (and told the DM): never say the rest was taken, and keep
                # the buttons up so the DM can try again.
                _answered.discard(message_id)
                return
            with contextlib.suppress(discord.HTTPException):
                await interaction.edit_original_response(
                    content=f"{asked}\n{ended}", view=None, allowed_mentions=NO_PINGS
                )
        except Exception:
            log.exception("A rest suggestion's button failed")
            await _reply(interaction, FAILED)


def combat_undo_id(before: Clock, after: Clock, group: split.Group | None = None) -> str:
    """What Undo needs for a combat change: where the clock and the round were, and where they
    must still be (round 0 means out of combat). If anything moved since, Undo does nothing."""

    def part(c: Clock) -> str:
        return f"{c.minute}:{c.combat_round or 0}:{c.combat_secs}"

    # While the party is split (#1607) the group rides along, so Undo only ever changes that one.
    return f"{part(before)}:{part(after)}" + (f":g{group.position}" if group else "")


class CombatUndoButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    # minute, round (under 1000), seconds (under 60), twice: bounded so the id always fits the
    # 100 characters Discord allows (#1818)
    template=rf"dmbot:combatundo:{_ID}:(?P<spec>\d{{1,9}}:\d{{1,3}}:\d{{1,2}}:\d{{1,9}}:\d{{1,3}}:\d{{1,2}}"
    r"(?::g[1-3])?)",
):
    """↩️ Undo on a combat change (#1127): back to the round and time before, if nothing has
    moved them since. Only a DM of the campaign (checked where the clock is changed)."""

    def __init__(self, campaign_id: str, spec: str) -> None:
        super().__init__(
            discord.ui.Button(
                label="Undo",
                emoji="↩️",
                style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:combatundo:{campaign_id}:{spec}",
            )
        )
        self.campaign_id, self.spec = campaign_id, spec
        parts = spec.split(":")
        self.group = int(parts.pop()[1:]) if parts[-1].startswith("g") else None
        numbers = [int(x) for x in parts]
        self.before, self.after = tuple(numbers[:3]), tuple(numbers[3:])

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> CombatUndoButton:
        return cls(match["campaign"], match["spec"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        guild = interaction.guild
        if guild is None:
            return
        await interaction.response.defer()
        try:
            await self._undo(interaction, guild.id)
        except Exception:
            log.exception("Undoing a combat change failed")
            await _reply(interaction, FAILED)

    async def _undo(self, interaction: discord.Interaction, guild_id: int) -> None:
        client: Any = interaction.client
        campaign = await client.campaigns.get(guild_id, self.campaign_id)
        if campaign is None:
            await _reply(interaction, GONE)
            return
        moved = False
        minute, round_, secs = self.before

        def change(current: Clock | None) -> Clock | None:
            nonlocal moved
            if (
                current is None
                or (
                    current.minute,
                    current.combat_round or 0,
                    current.combat_secs,
                )
                != self.after
            ):
                moved = True  # the DM changed it since: leave it
                return None
            return replace(
                current,
                minute=minute,
                combat_round=round_ or None,
                combat_secs=secs,
                last_long_rest=min(current.last_long_rest, minute),
                tired_told_for=None,  # as for a rest's Undo: the 24-hour line may be said again
            )

        try:
            _, after, _ = await client.clocks.change_named(
                guild_id,
                campaign.id,
                interaction.user.id,
                change,
                group=self.group,
                ungrouped=self.group is None,
            )
        except CampaignError as exc:
            await _reply(interaction, str(exc))
            return
        if moved or after is None:
            await _reply(
                interaction,
                "The clock or round has moved on since, so Undo can't help. Use **Next round** or "
                "**End combat** instead.",
            )
            return
        stored = await client.clocks.get(guild_id, campaign.id)
        if stored is not None:
            await show(client, campaign, stored)
            await show_players(client, campaign, stored)
        with contextlib.suppress(discord.HTTPException):
            where = "Not in combat." if not round_ else f"Back to round {round_}."
            await interaction.edit_original_response(content=f"↩️ Undone. {where}", view=None)


class SimpleGuild:
    """Just the server's id (the undo works from it alone)."""

    def __init__(self, guild_id: int) -> None:
        self.id = guild_id


def undo_id(before: Clock, after: Clock, group: split.Group | None = None) -> str:
    """What Undo needs, in the button's own ID so it works after a restart: the clock to go
    back to, and the minute it must still be at (if the DM moved it since, Undo does nothing).
    The fight's round and seconds ride along, so a rest that ended a fight can bring it back."""
    return (
        f"{before.minute}:{before.last_long_rest}:{after.minute}"
        f":{before.combat_round or 0}:{before.combat_secs}"
        + (f":g{group.position}" if group else "")
    )


class ClockUndoButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    # (the round and seconds are optional: buttons made before #1173 carry only the first three)
    template=rf"dmbot:clockundo:{_ID}:(?P<before>\d+):(?P<rest>\d+):(?P<after>\d+)"
    rf"(?::(?P<round>\d{{1,3}}):(?P<secs>\d{{1,2}}))?(?::g(?P<group>[1-3]))?",
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
        parts = spec.split(":")
        self.group = int(parts.pop()[1:]) if parts[-1].startswith("g") else None
        before, rest, after, *fight = (int(x) for x in parts)
        self.before, self.rest, self.after = before, rest, after
        self.round, self.secs = (fight[0], fight[1]) if len(fight) == 2 else (0, 0)

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> ClockUndoButton:
        spec = f"{match['before']}:{match['rest']}:{match['after']}"
        if match["round"] is not None:
            spec += f":{match['round']}:{match['secs']}"
        if match["group"] is not None:
            spec += f":g{match['group']}"
        return cls(match["campaign"], spec)

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
            clock = replace(
                current,
                minute=self.before,
                last_long_rest=min(self.rest, self.before),
                tired_told_for=None,
            )
            if current.combat_round is None and self.round:
                # The rest ended a fight: Undo brings it back, round included (#1173).
                clock = replace(clock, combat_round=self.round, combat_secs=self.secs)
            return clock

        try:
            _, after, acted = await client.clocks.change_named(
                guild.id,
                campaign.id,
                interaction.user.id,
                change,
                group=self.group,
                ungrouped=self.group is None,
            )
        except CampaignError as exc:
            await _reply(interaction, str(exc))
            return
        if moved or after is None:
            await _reply(
                interaction,
                "The clock has moved on since, so there is nothing to undo. "
                "Use **Rests & time…** to fix it.",
            )
            return
        stored = await client.clocks.get(guild.id, campaign.id)
        if stored is not None:
            await show(client, campaign, stored)
            fresh = await client.campaigns.get(guild.id, campaign.id) or campaign
            who = f"{acted.name} is" if acted is not None else "it is"
            await correct_players(client, fresh, f"{who} back to {game.short_label(after.minute)}.")
        with contextlib.suppress(discord.HTTPException):
            await interaction.edit_original_response(
                content=f"↩️ Undone. Back to {game.short_label(self.before)}.", view=None
            )
