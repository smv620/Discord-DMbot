"""What a plan lets a campaign do beyond running a session: AI reads, copies and downloads
(#437 part 3, #919; docs/PLAN.md, "Plans and pricing"), and the plain words when it doesn't.

The rules are pure over an owner's `Access` (`entitlements.effective`), so they test with
no Discord or database. The words live here, next to `hours.py`'s, so every refusal in the
bot says the same thing and the website can quote it (#437). Nothing here reads the
database: `usage.py` is the only module that opens the owner-scoped meter door.

Only the owner is told why. Anyone else (a co-DM, a player who pressed a download) is
told to ask the owner, so nobody learns another person's plan.
"""

from __future__ import annotations

from typing import Literal

from dmbot.entitlements import Access
from dmbot.hours import account_link

Rule = Literal["ai", "backup"]


def can_use_ai(access: Access | None) -> bool:
    """AI that spends tokens (Find names, later story memory and rules lookups) runs for a
    working plan: paid, Try It within its period, a grant or the free list. `None` is a
    campaign with no owner, whose plan nobody can be charged to."""
    return access is not None and access.works


def can_backup(access: Access | None) -> bool:
    """Copies of a campaign, restoring one, and transcript downloads need a plan that
    includes backups: any paid plan except Try It, a grant or the free list (CLAUDE.md:
    Try It campaigns have no backups or downloads). `None` is a campaign with no owner."""
    return access is not None and access.works and access.backups


def allowed(rule: Rule, access: Access | None) -> bool:
    return can_use_ai(access) if rule == "ai" else can_backup(access)


ASK_OWNER_AI = (
    "DMbot can't read documents for this campaign right now. Ask the campaign's owner to "
    "take a look."
)
ASK_OWNER_BACKUP = (
    "DMbot can't make copies or transcripts for this campaign right now. Ask the campaign's "
    "owner to take a look."
)
# Nobody to ask: a campaign with no owner has no plan to use. A DM of the campaign can take
# it on (the ⚙️ Settings card); anyone else needs one of them to.
NO_OWNER = (
    "This campaign has no owner yet, so it has no plan to do that with. One of its DMs "
    "needs to take it on first."
)


def refusal(
    rule: Rule,
    access: Access | None,
    *,
    is_owner: bool,
    owner_known: bool = True,
    site_url: str = "",
) -> str | None:
    """The plain words for a refused action, or None if it may go ahead.

    The owner (or someone restoring a copy, who is about to become one) hears the reason and
    the next step: their plan ended, or Try It has no copies. The link goes last so no full
    stop is glued onto it. Everyone else is told to ask the owner."""
    if allowed(rule, access):
        return None
    if not owner_known:
        return NO_OWNER
    if not is_owner:
        return ASK_OWNER_AI if rule == "ai" else ASK_OWNER_BACKUP
    where = f"here: {account_link(site_url)}" if site_url else "on DMbot's website"
    if access is None or not access.works:
        return f"Your plan has ended. Pick one {where}"
    # A plan that works but has no copies: Try It, the only one (plans.json).
    return f"Copies and transcripts come with a paid plan. Pick one {where}"
