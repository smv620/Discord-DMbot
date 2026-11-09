"""Who can see a campaign's DM Bot and transcript channels (#3891; docs/PLAN.md, "Who can see a
campaign's channels"), as plain data with no Discord dependency.

Two choices: everyone in the server (the channels as they were before the setting) or only the
campaign's people, the default. The overwrites are planned here and applied by the two channel
modules, the way `dm_screen/rules.py` does it for the DM screen: the bot owns the channel's
*member* overwrites, and role overwrites the server set are left alone and only reported when they
let someone outside the campaign see the channel.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from dmbot.campaigns.models import (
    CHANNELS_CHOICES,
    CHANNELS_EVERYONE,
    CHANNELS_PEOPLE,
    DEFAULT_CHANNELS,
    clean_channels,
)
from dmbot.dm_screen.rules import FULL, HIDDEN, READ_ONLY, Perms, Target, everyone

EVERYONE = CHANNELS_EVERYONE
PEOPLE = CHANNELS_PEOPLE
CHOICES = CHANNELS_CHOICES
DEFAULT = DEFAULT_CHANNELS
clean = clean_channels

HELP = (
    "Only the people in this campaign see its channels. Others in the server don't see them or "
    "get notified."
)


def people_of(
    *,
    dm_ids: Iterable[int],
    player_ids: Iterable[int] = (),
    recorded_ids: Iterable[int] = (),
    by_hand: Iterable[int] = (),
    bot_id: int,
) -> frozenset[int]:
    """The campaign's people: every Discord ID of its DMs, the members who play a character in it,
    the members recorded at one of its sessions, and anyone a DM added by hand. Never DMbot (it is
    planned on its own) and never a speaker without a Discord ID (their number is negative)."""
    everyone_here = {*dm_ids, *player_ids, *recorded_ids, *by_hand}
    return frozenset(i for i in everyone_here if i > 0 and i != bot_id)


def channel_plan(
    choice: str,
    *,
    guild_id: int,
    bot_id: int,
    people: Iterable[int] = (),
) -> dict[Target, Perms]:
    """The overwrites the bot sets on both channels for this choice. Under "everyone" it is what
    they were before the setting: everyone reads, only DMbot posts. Under "people" @everyone is
    denied View and each person may read; the channels stay read-only for them (in the DM Bot
    channel only DMbot posts, and the transcript channel is read-only)."""
    plan: dict[Target, Perms] = {everyone(guild_id): READ_ONLY}
    if clean(choice) == PEOPLE:
        plan[everyone(guild_id)] = HIDDEN
        for user_id in sorted(set(people)):
            if user_id != bot_id:
                plan[Target("member", user_id)] = READ_ONLY
    plan[Target("member", bot_id)] = FULL
    return plan


@dataclass(frozen=True, slots=True)
class Exposure:
    """Who can see a channel that should be the campaign's people's alone."""

    everyone: bool = False
    roles: tuple[int, ...] = ()
    members: tuple[int, ...] = ()

    def __bool__(self) -> bool:
        return self.everyone or bool(self.roles) or bool(self.members)


def find_exposure(
    choice: str,
    overwrites: Mapping[Target, Perms],
    *,
    guild_id: int,
    bot_id: int,
    people: Iterable[int],
) -> Exposure:
    """Overwrites that let someone outside the campaign see its channel. Role overwrites are the
    server's own and are left alone, only reported. Nothing is exposed under "everyone"."""
    if clean(choice) == EVERYONE:
        return Exposure()
    allowed = set(people)
    everyone_target = everyone(guild_id)
    roles: list[int] = []
    members: list[int] = []
    for target, perms in overwrites.items():
        if target == everyone_target or perms.get("view_channel") is not True:
            continue
        if target.kind == "role":
            roles.append(target.id)
        elif target.id not in allowed and target.id != bot_id:
            members.append(target.id)
    everyone_sees = overwrites.get(everyone_target, {}).get("view_channel") is not False
    return Exposure(everyone_sees, tuple(sorted(roles)), tuple(sorted(members)))
