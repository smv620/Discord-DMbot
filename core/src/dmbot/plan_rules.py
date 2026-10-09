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
    rules = {"ai": can_use_ai, "backup": can_backup}  # explicit: a third rule can't inherit one
    return rules[rule](access)


Action = Literal["ai", "copy", "transcript", "restore"]

# What a person pressed, in the words the refusal uses, so it names what they tried and not
# a neighbour (UX review: "transcripts" is noise to someone loading a copy). Non-owners are
# told the same thing whatever the cause, and where to go, so they learn nothing of the plan.
_UNAVAILABLE = {
    "ai": "Finding names with DMbot's AI isn't available for this campaign",
    "copy": "Copies of this campaign aren't available",
    "transcript": "Transcripts aren't available for this campaign",
    "restore": "Loading a copy isn't available",
}
ASK_OWNER_AI = f"{_UNAVAILABLE['ai']}. Ask the campaign's owner to check DMbot's website."
ASK_OWNER_BACKUP = f"{_UNAVAILABLE['copy']}. Ask the campaign's owner to check DMbot's website."
ASK_OWNER_TRANSCRIPT = (
    f"{_UNAVAILABLE['transcript']}. Ask the campaign's owner to check DMbot's website."
)
_ASK_OWNER = {
    "ai": ASK_OWNER_AI,
    "copy": ASK_OWNER_BACKUP,
    "transcript": ASK_OWNER_TRANSCRIPT,
    "restore": f"{_UNAVAILABLE['restore']}. Ask the campaign's owner to check DMbot's website.",
}
# Nobody to ask: a campaign with no owner has no plan to use. A DM of the campaign can take
# it on (the **Take it on** button); anyone else needs one of them to.
NO_OWNER = "This campaign has no owner yet. One of its DMs needs to press **Take it on** first."


def refusal(
    rule: Rule,
    access: Access | None,
    *,
    is_owner: bool,
    owner_known: bool = True,
    site_url: str = "",
    action: Action | None = None,
) -> str | None:
    """The plain words for a refused action, or None if it may go ahead.

    `action` is what the person pressed (default: the AI for "ai", a copy for "backup").
    The owner (or someone restoring a copy, who is about to become one) hears the reason and
    the next step; the link goes last so no full stop is glued onto it. Everyone else is
    told it isn't available and to ask the owner. `Access` can't tell a plan that ended from
    one never had, so the words fit both."""
    if allowed(rule, access):
        return None
    what: Action = action or ("ai" if rule == "ai" else "copy")
    if not owner_known:
        return NO_OWNER
    if not is_owner:
        return _ASK_OWNER[what]
    where = f"here: {account_link(site_url)}" if site_url else "on DMbot's website"
    if access is None or not access.works:
        return f"You need a plan for that. Pick one {where}"
    # A plan that works but has no copies: Try It, the only one (plans.json).
    if what == "restore":
        return f"Nothing was loaded. Loading a copy needs a paid plan. Pick one {where}"
    return f"Try It campaigns can't make copies or transcripts. A paid plan can. See plans {where}"
