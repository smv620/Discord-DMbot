"""Campaign data model and validation.

Error messages in `CampaignError` are shown to DMs, so they use plain words (CLAUDE.md,
"Simple enough for a child").
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal

# Rulesets a DM can pick, with the plain label shown in pickers.
RULESETS: dict[str, str] = {
    "2024": "2024 rules (newest)",
    "2014": "2014 rules (older)",
}
# A fallback of "none" means: only the target ruleset, nothing older.
FALLBACK_NONE = "none"

# Who besides the DM can see the campaign's DM screen (docs/PLAN.md, "DM-screen visibility").
DM_SCREEN_VISIBILITY: dict[str, str] = {
    "private": "Only the DM",
    "peek": "Players can peek (recommended)",
    "open": "Everyone in the server (read-only)",
}
DEFAULT_DM_SCREEN_VISIBILITY = "peek"

# How much DMbot says in the DM screen (docs/PLAN.md, "How much DMbot says"; #504).
QUIET, NORMAL, CHATTY = "quiet", "normal", "chatty"
DM_SCREEN_LEVELS: dict[str, str] = {
    QUIET: "only what you ask for, so fewer misheard names get fixed",
    NORMAL: "asks about names it misheard and shows its fixes, one at a time",
    CHATTY: "the same as Normal for now; later it also tells you what it noticed",
}
# The ones offered as buttons: Chatty waits until something uses it (a choice that does
# nothing would be a trap).
DM_SCREEN_LEVELS_OFFERED = (QUIET, NORMAL)
DEFAULT_DM_SCREEN_LEVEL = NORMAL

# What a DM confirmed the right to use shared material for (CLAUDE.md, IP rule; #252).
# The database checks the same list (schema.SHARED_CONFIRMATIONS): a new purpose needs a
# migration too (a test compares them).
NAMES_LIST, SHARED_STORY, RULEBOOK = "names_list", "shared_story", "rulebook"
CONFIRMATION_PURPOSES = (NAMES_LIST, SHARED_STORY, RULEBOOK)


def fingerprint(text: str) -> str:
    """Tells shared documents apart without keeping them: SHA-256 of the text."""
    return hashlib.sha256(text.encode()).hexdigest()


DEFAULT_TARGET = "2024"
DEFAULT_FALLBACK = "2014"

NAME_MAX = 80


# A hand-over offer is open this long (#437).
HANDOVER_DAYS = 7
HANDOVER_SECONDS = HANDOVER_DAYS * 24 * 3600


HandoverStatus = Literal["open", "accepted", "declined", "withdrawn", "expired"]
NAME_ON_OFFER_MAX = 100  # a display name kept on an offer (Discord's are 32 at most)


@dataclass(frozen=True, slots=True)
class HandoverOffer:
    """An offer to hand a campaign over to another subscriber (#437 part 1b)."""

    id: int
    guild_id: int
    campaign_id: str
    from_user_id: int
    to_user_id: int
    from_name: str  # display names when the offer was made, for the account page
    to_name: str
    created_at: int
    status: HandoverStatus
    decided_at: int | None
    # When DMbot sent the private message; None while an offer made on the website
    # waits for the bot to send it (#690).
    delivered_at: int | None = None

    @property
    def expires_at(self) -> int:
        return self.created_at + HANDOVER_SECONDS

    def is_open(self, now: int) -> bool:
        return self.status == "open" and now < self.expires_at


class CampaignError(ValueError):
    """A problem the DM can fix. The message is safe to show them as-is."""


@dataclass(frozen=True, slots=True)
class Campaign:
    id: str
    guild_id: int
    name: str
    created_at: int
    last_played_at: int | None
    target_ruleset: str
    fallback_ruleset: str
    optional_rules_default: bool
    dm_user_ids: frozenset[int]
    dm_screen_channel_id: int | None
    last_voice_channel_id: int | None
    dm_screen_visibility: str
    # Number in front of the campaign's channel names if they'd clash with another
    # campaign's (1 = none); None until its first channel is made. See dmbot.dm_screen.names.
    channel_number: int | None = None
    # The live transcript channel (#124); None until the first session.
    transcript_channel_id: int | None = None
    # How much DMbot says in the DM screen (#504): quiet, normal or chatty.
    dm_screen_level: str = DEFAULT_DM_SCREEN_LEVEL
    # Whose plan the campaign uses (#437): its creator or restorer; None for campaigns
    # from before owners were recorded.
    owner_user_id: int | None = None

    @property
    def last_active_at(self) -> int:
        """Last played, or created if it has never been played. Used for 'last used'."""
        return self.last_played_at if self.last_played_at is not None else self.created_at


def clean_name(raw: str) -> str:
    """Normalise a campaign name typed by a DM. Raises CampaignError if unusable."""
    name = " ".join(raw.split())
    if not name:
        raise CampaignError("Please give the campaign a name.")
    if len(name) > NAME_MAX:
        raise CampaignError(f"That name is too long. Keep it under {NAME_MAX} characters.")
    return name


def name_key(name: str) -> str:
    """Case- and spacing-insensitive key, so 'Frost Maiden' and 'frost  maiden' collide."""
    return " ".join(name.split()).casefold()


def check_rulesets(target: str, fallback: str) -> None:
    if target not in RULESETS:
        raise CampaignError("Please pick a main ruleset from the list.")
    if fallback != FALLBACK_NONE and fallback not in RULESETS:
        raise CampaignError("Please pick what to use when the main rules don't cover something.")
    if fallback == target:
        raise CampaignError(
            "'If missing' can't be the same rules as 'Main rules'. Pick the other one, or skip it."
        )


def check_dm_screen_visibility(value: str) -> None:
    if value not in DM_SCREEN_VISIBILITY:
        raise CampaignError("Please pick who can see the DM screen from the list.")


def check_dm_screen_level(value: str) -> None:
    if value not in DM_SCREEN_LEVELS:
        raise CampaignError("Please pick how much DMbot says: quiet or normal.")
