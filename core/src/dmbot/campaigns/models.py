"""Campaign data model and validation.

Error messages in `CampaignError` are shown to DMs, so they use plain words (CLAUDE.md,
"Simple enough for a child").
"""

from __future__ import annotations

from dataclasses import dataclass

# Rulesets a DM can pick, with the plain label shown in pickers.
RULESETS: dict[str, str] = {
    "2024": "2024 rules (newest)",
    "2014": "2014 rules (legacy)",
}
# A fallback of "none" means: only the target ruleset, nothing older.
FALLBACK_NONE = "none"

# Who besides the DM can see the campaign's DM screen (docs/PLAN.md, "DM-screen visibility").
DM_SCREEN_VISIBILITY: dict[str, str] = {
    "private": "Only the DM",
    "peek": "Players can peek if they choose (with a spoiler warning)",
    "open": "Everyone at the table",
}
DEFAULT_DM_SCREEN_VISIBILITY = "peek"

DEFAULT_TARGET = "2024"
DEFAULT_FALLBACK = "2014"

NAME_MAX = 80


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
        raise CampaignError("Please pick a backup ruleset from the list, or none.")
    if fallback == target:
        raise CampaignError("The backup ruleset must be different from the main one.")


def check_dm_screen_visibility(value: str) -> None:
    if value not in DM_SCREEN_VISIBILITY:
        raise CampaignError("Please pick who can see the DM screen from the list.")
