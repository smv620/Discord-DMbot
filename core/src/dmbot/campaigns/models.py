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
    QUIET: (
        "only what you ask for, warnings, and lines it left out as off-topic; fewer "
        "misheard names get fixed"
    ),
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
# (NAMES_LIST is no longer asked for since #2447, but old rows keep it, so it stays in the list.)
NAMES_LIST, SHARED_STORY, RULEBOOK = "names_list", "shared_story", "rulebook"
OWN_WRITING = "own_writing"  # a DM's own background or notes, read for names (#2167)
CONFIRMATION_PURPOSES = (NAMES_LIST, SHARED_STORY, RULEBOOK, OWN_WRITING)


# The Shared story switch (#1692; docs/STORY_MEMORY.md, section 6). Nothing reads it yet: the
# story-plan reader, the divergence warnings ("Watch the story") and "Guide me" are the later
# slices of Phase 5d. The database checks the same list (schema.STORY_MODE).
STORY_OFF, STORY_WATCH, STORY_GUIDE = "off", "watch", "guide"
STORY_MODES = (STORY_OFF, STORY_WATCH, STORY_GUIDE)
DEFAULT_STORY_MODE = STORY_OFF
# What the DM is shown before the first time it is turned on (docs/PLAN.md, "Shared story
# switch"; CLAUDE.md, IP rule). The confirmation records its fingerprint, not the words.
STORY_RIGHT_WARNING = (
    "Only give DMbot material you have the right to use. DMbot doesn't check this."
)
STORY_NEEDS_CONFIRM = "Confirm that you have the right to use the material first."


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
    # waits for the bot to send it (#690). claimed_at: when a bot process started sending
    # it (a claim lapses after CLAIM_SECONDS, so a send cut short is tried again).
    delivered_at: int | None = None
    claimed_at: int | None = None
    # The private message to the person offered, and when the owner was told the offer
    # ran out of days (#690).
    message_id: int | None = None
    end_told_at: int | None = None
    # Made by email (#2532): to whoever opens its link on the website; `to_name` is empty.
    via_email: bool = False
    # The person accepted and chose another server (#2532, slice C): the bot moves the campaign
    # there. Until it has, the offer is neither open to answer nor allowed to run out or be taken
    # back. `moved_campaign_id`: the copy in that server, once made (a retry reuses it).
    move_to_guild_id: int | None = None
    moved_campaign_id: str | None = None  # (unused since the move changes the campaign's server)
    move_claimed_at: int | None = None  # the bot's lease on the move that is under way
    move_asked_at: int | None = None
    move_failed_for: int | None = None  # who had accepted when a move did not happen,
    move_failed_reason: str | None = None  # and the plain reason, for that person only

    @property
    def expires_at(self) -> int:
        return self.created_at + HANDOVER_SECONDS

    def is_open(self, now: int) -> bool:
        return self.status == "open" and now < self.expires_at and self.move_to_guild_id is None


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
    gametime_channel_id: int | None = None  # the players' game-time channel (#1116)
    dmbchat_channel_id: int | None = None  # where the table asks DMbot questions (#1187)
    dm_bot_channel_id: int | None = None  # the DM Bot channel: settings by role (#1666)
    rules_channel_id: int | None = None  # the rules archive: house rules, read only (#1752)
    npcs_channel_id: int | None = None  # the NPC roster: people and groups, read only (#1761)
    dm_bot_message_id: int | None = None  # ...and its one panel message
    # The DM's D&D Beyond campaign invite link, shown to players on a button (#1119).
    dndbeyond_invite: str | None = None
    # Typed chat at the table (#1118): whether DMbot reads it, and the one extra text channel
    # the DM picked (the voice channel's own chat is always the first place).
    table_chat: bool = True
    table_chat_channel_id: int | None = None
    # How much DMbot says in the DM screen (#504): quiet, normal or chatty.
    dm_screen_level: str = DEFAULT_DM_SCREEN_LEVEL
    # Whose plan the campaign uses (#437): its creator or restorer; None for campaigns
    # from before owners were recorded.
    owner_user_id: int | None = None
    # Rules cards on the DM screen when a spell or creature is named (#931); off by default.
    rules_cards: bool = False
    # Kept, unused, for a later turn-order feature (#1769): nothing reads it today and the
    # Settings screen no longer offers it. It stays in backups so none is lost.
    # Folded into `initiative_style` ("side") by migration 0130 (#1791) and not read any more. It
    # is kept, and still written to backups, only so an older DMbot can read a new backup.
    side_initiative: bool = False  # combat tracks rounds only either way (#1127)
    # How long the campaign keeps its transcripts (#1533): None is forever; else 30, 90 or 365
    # days after a session ended. `transcript_deleted_noted`: the DM screen has said so once.
    transcript_keep_days: int | None = None
    transcript_deleted_noted: bool = False
    # Minutes with nobody speaking before DMbot stops a session by itself (#2034): 5, 10, 15 or
    # 30, never off (#2288). The empty-channel stop is always on and has no setting.
    quiet_stop_minutes: int = 10
    # The DM's recap choices (#1421): write a recap after each session, make the narration and
    # music, email when it is ready. They only ever turn things off: the plan decides the rest.
    recaps_text: bool = True
    recaps_audio: bool = True
    recaps_email: bool = True
    # The Shared story switch (#1692): "off", "watch" or "guide". Nothing reads it yet.
    story_mode: str = DEFAULT_STORY_MODE
    # The initiative style the DM last set, kept for the next session (#1784): see fight_style.
    initiative_style: str | None = None
    # Paused by its owner, or by DMbot when the owner's plan shrank (#957): it keeps all its
    # data and downloads, can't start, and doesn't count toward the owner's campaign cap.
    paused: bool = False
    # Which retention warning it has had, and for which delete date (#964).
    retention_warned_stage: int = 0
    retention_warned_for: int | None = None
    # When the owner deleted their account (#1059): the campaign is kept, ownerless, and the
    # retention job deletes it 120 days later unless a DM takes it on. None otherwise.
    owner_left_at: int | None = None
    # When DMbot first saw the owner was not in the server (#1214); None while they are here.
    owner_missing_since: int | None = None

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
