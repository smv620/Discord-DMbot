"""Plain logic behind `/dmbot` commands, kept free of Discord so it can be unit-tested.

User-facing words here follow CLAUDE.md, "Simple enough for a child": plain words, no
technical terms, and always clear that DMbot never invents story or decides.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable
from datetime import UTC, datetime

from dmbot.campaigns import DM_SCREEN_VISIBILITY, FALLBACK_NONE, RULESETS, Campaign

# Discord limits.
BUTTON_LABEL_MAX = 80
OPTION_LABEL_MAX = 100
SELECT_OPTIONS_MAX = 25

NO_CAMPAIGN_ACCESS = "Only this campaign's DM (or a server manager) can do that."


def shorten(text: str, limit: int) -> str:
    """Trim text to fit a Discord label, ending with an ellipsis if it was cut."""
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def can_run(campaign: Campaign, user_id: int, is_server_manager: bool) -> bool:
    """May this person start, stop, back up, or replace this campaign?"""
    return user_id in campaign.dm_user_ids or is_server_manager


def runnable(
    campaigns: Iterable[Campaign], user_id: int, is_server_manager: bool
) -> list[Campaign]:
    return [c for c in campaigns if can_run(c, user_id, is_server_manager)]


def ago(then: int, now: int) -> str:
    """'5 minutes ago', 'yesterday', '3 weeks ago' — for select menus, which can't show
    Discord's local-time timestamps."""
    seconds = max(0, now - then)
    minutes, hours, days = seconds // 60, seconds // 3600, seconds // 86400
    if seconds < 60:
        return "just now"
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    if hours < 24:
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    if days == 1:
        return "yesterday"
    if days < 14:
        return f"{days} days ago"
    if days < 60:
        return f"{days // 7} weeks ago"
    return datetime.fromtimestamp(then, UTC).strftime("%b %Y")


def played_line(campaign: Campaign) -> str:
    """For message text, where Discord shows each reader their own local time."""
    if campaign.last_played_at is None:
        return "not played yet"
    return f"last played <t:{campaign.last_played_at}:f>"


def option_description(campaign: Campaign, now: int) -> str:
    if campaign.last_played_at is None:
        return "Not played yet"
    return shorten(f"Last played {ago(campaign.last_played_at, now)}", OPTION_LABEL_MAX)


def continue_label(campaign: Campaign) -> str:
    return shorten(f"▶ Continue: {campaign.name}", BUTTON_LABEL_MAX)


def default_voice_channel(
    campaign: Campaign | None,
    member_voice_id: int | None,
    usable_voice_ids: Collection[int],
) -> int | None:
    """Last time's channel if it still exists and DMbot can use it, else the one the
    person is sitting in, else nothing (they pick)."""
    if campaign and campaign.last_voice_channel_id in usable_voice_ids:
        return campaign.last_voice_channel_id
    if member_voice_id in usable_voice_ids:
        return member_voice_id
    return None


def ruleset_label(ruleset: str) -> str:
    if ruleset == FALLBACK_NONE:
        return "None (main rules only)"
    return RULESETS.get(ruleset, ruleset)


def settings_summary(
    target: str, fallback: str, optional_rules: bool, visibility: str
) -> list[str]:
    return [
        f"• **Main rules:** {ruleset_label(target)}",
        f"• **Backup rules** (used only when the main rules don't cover something): "
        f"{ruleset_label(fallback)}",
        "• **Optional rules** from Xanathar's and Tasha's (where the main rules don't "
        f"cover them): {'on' if optional_rules else 'off'}",
        f"• **Who sees the DM screen:** {DM_SCREEN_VISIBILITY.get(visibility, visibility)}",
    ]


def backup_filename(campaign_name: str, now: int) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", campaign_name.casefold()).strip("-")[:40] or "campaign"
    day = datetime.fromtimestamp(now, UTC).strftime("%Y-%m-%d")
    return f"{slug}-{day}.dmbot.json"


HELP_TEXT = (
    "**🎲 DMbot helps the Dungeon Master.**\n"
    "It listens to your table, writes down what's said, and gives the DM rules tips "
    "in private. It remembers each campaign between sessions.\n"
    "**DMbot never makes up the story and never decides anything. The DM always does.**\n"
    "\n"
    "**Commands**\n"
    "• `/dmbot start`: pick your campaign and voice channel, then start listening\n"
    "• `/dmbot stop`: stop listening\n"
    "• `/dmbot backup`: download a copy of a campaign\n"
    "• `/dmbot restore`: bring a campaign back from a copy\n"
    "\n"
    "**Recording:** only people who agree are recorded. Players use `/consent give` "
    "to agree and `/consent revoke` to stop."
)


def backup_campaign_name(data: object) -> str | None:
    """The campaign name inside a parsed backup, for messages; None if it's not there."""
    if isinstance(data, dict):
        campaign = data.get("campaign")
        if isinstance(campaign, dict):
            name = campaign.get("name")
            if isinstance(name, str) and name.strip():
                return shorten(name, OPTION_LABEL_MAX)
    return None


def dm_list(campaign: Campaign) -> str:
    """'<@1>, <@2>' for messages (sent with pings turned off)."""
    return ", ".join(f"<@{uid}>" for uid in sorted(campaign.dm_user_ids)) or "nobody"
