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
    optional: bool = False  # DMbot still works without it (it just can't do `why`)


# The bot-wide list. Keep README "Create the bot" in step: a test checks the number.
PERMISSIONS: tuple[Needed, ...] = (
    Needed("view_channel", "View Channels", "see the voice channel and its DM screens"),
    Needed("send_messages", "Send Messages", "post in the DM screen and the voice chat"),
    Needed("read_message_history", "Read Message History", "find and update its own notes"),
    Needed("connect", "Connect", "join the table's voice channel"),
    # Possibly not needed just to listen; to be confirmed in a live test (#148).
    Needed("speak", "Speak", "join voice (DMbot stays muted)"),
    Needed("manage_channels", "Manage Channels", "make its DM screen channels"),
    Needed("manage_roles", "Manage Roles", "set who can see each DM screen"),
    Needed("pin_messages", "Pin Messages", "pin its help card", optional=True),
)
_BY_FLAG = {p.flag: p for p in PERMISSIONS}

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
    """Discord's name for a permission on the list (KeyError if it isn't on it)."""
    return _BY_FLAG[flag].label


def is_optional(label_: str) -> bool:
    return any(p.label == label_ and p.optional for p in PERMISSIONS)


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
            f"A server admin can fix this in one click: [open the install link]({link}), "
            "pick this server, then press **Continue** and **Authorize**."
        )
    return "Ask a server admin to turn these on: **Server Settings → Roles → DMbot → Permissions**."


WELCOME_INTRO = (
    "👋 **Thanks for adding DMbot!** It listens to your table and writes private notes "
    "for the DM. It never invents story or makes rulings: the DM decides everything. "
    "Players are only heard after they say yes."
)
HOW_TO_START = "**To start:** the DM joins a voice channel and runs `/dmbot start`."


def welcome() -> str:
    return f"{WELCOME_INTRO}\n{HOW_TO_START}"


def missing_note(missing_labels: list[str], link: str | None) -> str:
    """Posted when DMbot joins a server without everything it needs.

    Needed permissions block the first game; optional ones (Pin Messages) don't, so
    they're asked for more gently.
    """
    needed = [m for m in missing_labels if not is_optional(m)]
    extra = [m for m in missing_labels if is_optional(m)]
    asks = []
    if needed:
        asks.append(f"Before your first game, it still needs {human_list(needed)}.")
    if extra:
        asks.append(
            f"{'It' if needed else 'Everything works, but it'} would also like "
            f"{human_list(extra)} (to pin its help card)."
        )
    return f"{WELCOME_INTRO}\n{' '.join(asks)} {fix_hint(link)}\n{HOW_TO_START}"


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
