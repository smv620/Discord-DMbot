"""DM-screen rules with no Discord dependency: permission plans and exposure.

Channel names are in `dmbot.dm_screen.names`.

The DM screen's permission overwrites are planned here as plain data and applied by
`dmbot.dm_screen.channel`. The bot owns the screen's *member* overwrites (DMs, itself,
peekers); *role* overwrites other than @everyone are left as the server set them, and
are only reported if they let someone see a screen that should be hidden.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Literal, NamedTuple

from dmbot import install

# Overwrite permission names are discord.py's, so the plan maps straight onto
# discord.PermissionOverwrite(**perms). Discord only lets the bot allow or deny a
# permission it holds itself, so plans are filtered with `restrict()` before use.
Perms = Mapping[str, bool]
FULL: Perms = {"view_channel": True, "send_messages": True, "read_message_history": True}
READ_ONLY: Perms = {
    "view_channel": True,
    "read_message_history": True,
    "send_messages": False,
    # Read-only means no side doors either: threads, reactions, slash commands.
    "send_messages_in_threads": False,
    "create_public_threads": False,
    "create_private_threads": False,
    "add_reactions": False,
    "use_application_commands": False,
}
HIDDEN: Perms = {"view_channel": False}

# The part of the bot-wide list (dmbot.install.PERMISSIONS) a DM screen can't work
# without. Pin Messages is wanted too, but the screen still works if it's missing.
REQUIRED_PERMISSIONS = (
    "view_channel",
    "send_messages",
    "read_message_history",
    "manage_channels",
    "manage_roles",
)


class Target(NamedTuple):
    kind: Literal["role", "member"]
    id: int


def everyone(guild_id: int) -> Target:
    """@everyone is the role whose ID equals the server's ID."""
    return Target("role", guild_id)


def restrict(perms: Perms, held: Iterable[str]) -> dict[str, bool]:
    """Only the permissions the bot holds; Discord rejects overwrites for the others."""
    have = set(held)
    return {name: value for name, value in perms.items() if name in have}


def missing_required(held: Iterable[str]) -> list[str]:
    """Discord's names for the required permissions the bot lacks."""
    have = set(held)
    return [install.label(p) for p in REQUIRED_PERMISSIONS if p not in have]


def is_peeker(perms: Perms) -> bool:
    """A peek overwrite is read-only: it allows View but explicitly denies Send."""
    return perms.get("view_channel") is True and perms.get("send_messages") is False


def merge_overwrites(
    current: Mapping[Target, Perms], plan: Mapping[Target, Perms], *, guild_id: int
) -> dict[Target, Perms]:
    """The full overwrite set to send: the plan, plus the server's own role overwrites.

    Role overwrites other than @everyone are kept as the server set them. Member
    overwrites belong to the bot, so any not in the plan (removed DMs, old peekers, or
    members added by hand) are dropped.
    """
    kept = {
        target: perms
        for target, perms in current.items()
        if target.kind == "role" and target != everyone(guild_id)
    }
    return {**kept, **plan}


def overwrite_plan(
    visibility: str,
    *,
    guild_id: int,
    bot_id: int,
    dm_ids: Iterable[int],
    peeker_ids: Iterable[int] = (),
) -> dict[Target, Perms]:
    """The overwrites the bot sets on a DM screen for this visibility.

    Peekers are kept only under "peek"; switching to "private" or "open" drops them.
    """
    dms = set(dm_ids)
    plan: dict[Target, Perms] = {
        everyone(guild_id): READ_ONLY if visibility == "open" else HIDDEN,
    }
    if visibility == "peek":
        for user_id in peeker_ids:
            if user_id not in dms and user_id != bot_id:
                plan[Target("member", user_id)] = READ_ONLY
    for user_id in dms:
        plan[Target("member", user_id)] = FULL
    plan[Target("member", bot_id)] = FULL
    return plan


@dataclass(frozen=True, slots=True)
class Exposure:
    """Who can see a DM screen that should be hidden from them."""

    everyone: bool = False
    roles: tuple[int, ...] = ()
    members: tuple[int, ...] = ()

    def __bool__(self) -> bool:
        return self.everyone or bool(self.roles) or bool(self.members)


def find_exposure(
    visibility: str,
    overwrites: Mapping[Target, Perms],
    *,
    guild_id: int,
    bot_id: int,
    dm_ids: Iterable[int],
) -> Exposure:
    """Overwrites that let someone other than the DMs (and, under "peek", peekers) see it.

    Uses channel overwrites only: once @everyone is denied View, only an explicit allow
    (or Administrator, which no channel can block) lets anyone in. That works without
    the privileged members intent.
    """
    if visibility == "open":
        return Exposure()
    dms = set(dm_ids)
    everyone_target = everyone(guild_id)
    roles: list[int] = []
    members: list[int] = []
    for target, perms in overwrites.items():
        if target == everyone_target or perms.get("view_channel") is not True:
            continue
        if target.kind == "role":
            roles.append(target.id)
        elif target.id not in dms and target.id != bot_id:
            if visibility == "peek" and is_peeker(perms):
                continue  # chose to peek: expected
            members.append(target.id)
    everyone_sees = overwrites.get(everyone_target, {}).get("view_channel") is not False
    return Exposure(everyone_sees, tuple(sorted(roles)), tuple(sorted(members)))
