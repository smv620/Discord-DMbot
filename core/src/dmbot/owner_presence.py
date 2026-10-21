"""Has a campaign's owner left Discord for good? (#1214; docs/PLAN.md)

DMbot knows people only by Discord user ID. An owner whose account is deleted, or who leaves the
server, can never come back to press anything, so the campaign would keep an owner nobody can
be. Without the privileged Server Members intent (least privilege, CLAUDE.md) DMbot can only ask
about one person when it already has a reason to look. The answer is one of four things, and
what to do about it is decided here, purely, so it tests with no Discord:

- the owner is in the server: forget any earlier "missing";
- not in the server: note the day it was first seen; after 7 days (they may have left by
  accident and come back) the campaign becomes ownerless, as if the account were deleted;
- the account is deleted: it can't come back, so ownerless at once;
- anything else (a timeout, a missing permission): don't know, change nothing.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import discord

DAY = 24 * 3600
GRACE_S = 7 * DAY
UNKNOWN_MEMBER = 10007  # Discord's code for "not a member of this server"
UNKNOWN_USER = 10013  # ... and for "no such account": it was deleted

HERE, GONE_FROM_SERVER, ACCOUNT_DELETED, DONT_KNOW = "here", "gone", "deleted", "unknown"

# What to do about the answer.
NOTHING, MARK, CLEAR, ORPHAN = "nothing", "mark", "clear", "orphan"

TELL_DMS = (
    "This campaign's owner isn't in the server any more. Press Take it on in ⚙️ Settings → "
    "🗂️ Campaign management to "
    "keep it running."
)


@dataclass(frozen=True, slots=True)
class Step:
    action: str  # NOTHING, MARK, CLEAR or ORPHAN


def decide(missing_since: int | None, seen: str, now: int) -> Step:
    """What to do, given when the owner was first missed (None: not missing) and what was
    just seen."""
    if seen == HERE:
        return Step(CLEAR if missing_since is not None else NOTHING)
    if seen == ACCOUNT_DELETED:
        return Step(ORPHAN)
    if seen == GONE_FROM_SERVER:
        if missing_since is None:
            return Step(MARK)
        return Step(ORPHAN if now - missing_since >= GRACE_S else NOTHING)
    return Step(NOTHING)  # DONT_KNOW


def _code(exc: discord.NotFound) -> int:
    return int(getattr(exc, "code", 0) or 0)


async def look(
    fetch_member: Callable[[int], Awaitable[Any]],
    fetch_user: Callable[[int], Awaitable[Any]],
    owner_id: int,
) -> str:
    """Ask Discord. Only Discord's own "unknown member" / "unknown user" answers count as
    "gone"; every other error, and any surprise, is "don't know" and changes nothing."""
    try:
        await fetch_member(owner_id)
        return HERE
    except discord.NotFound as exc:
        if _code(exc) != UNKNOWN_MEMBER:
            return DONT_KNOW
    except Exception:
        return DONT_KNOW
    try:
        await fetch_user(owner_id)
    except discord.NotFound as exc:
        return ACCOUNT_DELETED if _code(exc) == UNKNOWN_USER else DONT_KNOW
    except Exception:
        return GONE_FROM_SERVER  # not in the server; whether the account lives we don't know
    return GONE_FROM_SERVER
