"""What DMbot needs when it's added to a server: the one list of permissions, the install
link, and the notes it posts when it joins (#104).

A bot can't give itself permissions. The install link is the automatic way: whoever adds
DMbot sees every permission below, presses Authorize once, and Discord puts them on
DMbot's role. Opening the same link again on a server that already has DMbot updates
its role, which also fixes older installs.

Everything here is plain logic, testable without Discord. `configure()` is called once
at startup with the bot's application ID, so messages can include the link.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

import discord
from discord.utils import oauth_url

SCOPES = ("bot", "applications.commands")


@dataclass(frozen=True, slots=True)
class Needed:
    flag: str  # discord.py's name, e.g. "view_channel"
    label: str  # Discord's name, as shown in Server Settings → Roles
    why: str  # one plain line for README and notes


# The bot-wide list. Keep README "Create the bot" in step: a test checks the number.
PERMISSIONS: tuple[Needed, ...] = (
    Needed("view_channel", "View Channels", "see the voice channel and its DM screens"),
    Needed("send_messages", "Send Messages", "post in the DM screen and the voice chat"),
    Needed("read_message_history", "Read Message History", "find and update its own notes"),
    Needed("connect", "Connect", "join the table's voice channel"),
    Needed("speak", "Speak", "stay connected to voice (DMbot stays muted)"),
    Needed("manage_channels", "Manage Channels", "make its own dmb- channels"),
    Needed("manage_roles", "Manage Roles", "choose who can see each DM screen"),
    Needed("pin_messages", "Pin Messages", "pin the help card in each DM screen"),
)

_application_id: int | None = None


def configure(application_id: int | None) -> None:
    """Remember the bot's application ID (from Discord at login) for install links."""
    global _application_id
    _application_id = application_id


def permissions() -> discord.Permissions:
    return discord.Permissions(**{p.flag: True for p in PERMISSIONS})


def install_link(application_id: int | None = None) -> str | None:
    """The one-click install link, or None before the bot knows its application ID."""
    app_id = application_id if application_id is not None else _application_id
    if app_id is None:
        return None
    return oauth_url(app_id, permissions=permissions(), scopes=SCOPES)


def missing(perms: discord.Permissions, flags: Iterable[str] | None = None) -> list[str]:
    """Discord's names for the needed permissions `perms` lacks (all of them by default).

    Administrator covers everything. Checked by attribute, so discord.py's older names
    (View Channel is stored as `read_messages`) can't hide a permission the bot has.
    """
    if perms.administrator:
        return []
    wanted = set(flags) if flags is not None else None
    return [
        p.label
        for p in PERMISSIONS
        if (wanted is None or p.flag in wanted) and not getattr(perms, p.flag)
    ]


def label(flag: str) -> str:
    return next(p.label for p in PERMISSIONS if p.flag == flag)


def human_list(items: list[str]) -> str:
    """ "A", "A and B", "A, B and C", each in bold."""
    bold = [f"**{item}**" for item in items]
    if len(bold) <= 2:
        return " and ".join(bold)
    return ", ".join(bold[:-1]) + " and " + bold[-1]


def fix_hint(link: str | None) -> str:
    """How to fix missing permissions: one click if we have the link."""
    if link:
        return (
            f"A server admin can fix this in one click: open {link} , pick this server and "
            "press **Authorize**."
        )
    return "Ask a server admin to turn them on for DMbot (Server Settings → Roles → DMbot)."


def welcome() -> str:
    return (
        "👋 **Thanks for adding DMbot!** It listens to your table and writes private notes "
        "for the DM. It never makes rulings or story; the DM decides everything.\n"
        "**To start:** the DM joins a voice channel and runs `/dmbot start`."
    )


def missing_note(missing_labels: list[str], link: str | None) -> str:
    """Posted when DMbot joins a server without everything it needs."""
    return (
        "👋 **Thanks for adding DMbot!** Before your first game, it still needs "
        f"{human_list(missing_labels)}. {fix_hint(link)}\n"
        "Then the DM joins a voice channel and runs `/dmbot start`."
    )


def first_postable(
    channels: Iterable[discord.TextChannel], me: discord.Member
) -> discord.TextChannel | None:
    """The first channel (in sidebar order) where DMbot can post a note."""
    for channel in sorted(channels, key=lambda c: c.position):
        perms = channel.permissions_for(me)
        if perms.view_channel and perms.send_messages:
            return channel
    return None


async def post_welcome(guild: discord.Guild) -> str:
    """Post the welcome, or the note naming missing permissions, where an admin will see
    it: the server's system channel if DMbot can post there, else the first channel it can.

    Returns what happened, for the log (IDs and permission names only, never text).
    """
    me = guild.me
    if me is None:
        return "not ready to post a welcome"
    gaps = missing(me.guild_permissions)
    text = missing_note(gaps, install_link()) if gaps else welcome()
    system = guild.system_channel
    channel = first_postable([system] if system is not None else [], me)
    if channel is None:
        channel = first_postable(guild.text_channels, me)
    outcome = f"missing: {', '.join(gaps)}" if gaps else "has every permission"
    if channel is None:
        return f"{outcome}; nowhere to post the welcome"
    try:
        await channel.send(text, allowed_mentions=discord.AllowedMentions.none())
    except discord.HTTPException as exc:
        return f"{outcome}; couldn't post the welcome ({exc.status})"
    return f"{outcome}; welcome posted in channel {channel.id}"
