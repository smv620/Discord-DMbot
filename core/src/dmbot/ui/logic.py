"""Plain logic behind `/dmbot` commands, kept free of Discord so it can be unit-tested.

User-facing words here follow CLAUDE.md, "Simple enough for a child": plain words, no
technical terms, and always clear that DMbot never invents story or decides.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable
from datetime import UTC, datetime

from dmbot.campaigns import DM_SCREEN_VISIBILITY, FALLBACK_NONE, RULESETS, Campaign
from dmbot.campaigns.models import (
    DEFAULT_DM_SCREEN_LEVEL,
    DM_SCREEN_LEVELS,
    DM_SCREEN_LEVELS_OFFERED,
)
from dmbot.transcription.config import Engine

# Discord limits.
BUTTON_LABEL_MAX = 80
FILE_MAX = 10 * 1024 * 1024  # the biggest file a bot may send
OPTION_LABEL_MAX = 100
SELECT_OPTIONS_MAX = 25
# What a phone shows of a button or menu choice before cutting it off (#112). Labels
# DMbot writes itself stay within this. Names people chose (a campaign's) are cut to
# NAME_LABEL_MAX, only in menu lists, which open full-width; never in buttons.
PHONE_LABEL_MAX = 25
NAME_LABEL_MAX = 32

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


_NAME_TAIL = 12  # the end of a long name stays visible: "(restored 2)", "Season 2"


def name_label(name: str) -> str:
    """A campaign's name as a menu choice (never a button), short enough for a phone.
    A long name is cut in the middle, so its end still tells copies and seasons apart:
    "Curse of Strahd Fri…(restored 2)"."""
    name = " ".join(name.split())
    if len(name) <= NAME_LABEL_MAX:
        return name
    tail = name[-_NAME_TAIL:].lstrip()
    head = name[: NAME_LABEL_MAX - 1 - len(tail)].rstrip()
    return f"{head}…{tail}"


def option_description(campaign: Campaign, now: int) -> str:
    if campaign.last_played_at is None:
        return "Not played yet"
    return shorten(f"Last played {ago(campaign.last_played_at, now)}", OPTION_LABEL_MAX)


CONTINUE_LABEL = "▶ Continue last campaign"  # the message above names it


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


def fallback_words(fallback: str) -> str:
    """What happens when the main rules don't cover something, as its buttons say it."""
    if fallback == FALLBACK_NONE:
        return "skip it, use only the main rules"
    return f"use the {ruleset_label(fallback)}"


def settings_summary(
    target: str,
    fallback: str,
    optional_rules: bool,
    visibility: str,
    level: str,
) -> list[str]:
    """The settings in full, in the message above the buttons (message text wraps on a
    phone; buttons don't). Each line starts with the words its buttons start with."""
    return [
        f"• **Main rules:** {ruleset_label(target)}",
        f"• **If missing** (the main rules don't cover something): {fallback_words(fallback)}",
        "• **Optional rules** from Xanathar's and Tasha's: "
        f"all {'on' if optional_rules else 'off'} "
        "(pick which ones later with `/dmbot optionalrules`)",
        f"• **DM screen:** {DM_SCREEN_VISIBILITY.get(visibility, visibility)}",
        f"• **{level.capitalize()}**{RECOMMENDED if level == DEFAULT_DM_SCREEN_LEVEL else ''}"
        f" — how much DMbot says, in the DM screen only: {DM_SCREEN_LEVELS.get(level, level)}."
        " Warnings (like DMbot no longer hearing the table) always show. Change it any time:"
        " press ⚙️ Settings on the pinned card in your DM screen.",
    ]


# Button labels for the new-campaign settings: one row of buttons per setting. Each says
# what it's about, and all fit a phone (PHONE_LABEL_MAX, #112); the message above explains.


def main_rules_choices() -> dict[str, str]:
    return {k: f"Main rules: {k}" for k in RULESETS}


def fallback_choices(target: str) -> dict[str, str]:
    choices = {k: f"If missing: use {k}" for k in RULESETS if k != target}
    choices[FALLBACK_NONE] = "If missing: skip it"
    return choices


OPTIONAL_RULES_CHOICES = {
    "on": "Optional rules: on",
    "off": "Optional rules: off",
}

_SCREEN_SHORT = {"private": "only the DM", "peek": "players peek", "open": "everyone"}


def visibility_choices() -> dict[str, str]:
    return {k: f"DM screen: {_SCREEN_SHORT[k]}" for k in DM_SCREEN_VISIBILITY}


RECOMMENDED = " (recommended)"


def level_choices() -> dict[str, str]:
    """How much DMbot says in the DM screen (#504)."""
    return {k: k.capitalize() for k in DM_SCREEN_LEVELS_OFFERED}


def chosen_label(label: str, chosen: bool) -> str:
    """The chosen button is marked with a tick as well as its colour."""
    return f"✓ {label}" if chosen else label


def screen_note(visibility: str) -> str:
    """How the start message describes who can see the DM screen."""
    if visibility == "peek":
        return " (players can peek if they choose)"
    if visibility == "open":
        return " (everyone in the server can see it)"
    return ""


# Waiting speech clips at which the Status button says writing is falling behind.
WRITING_BEHIND_BACKLOG = 16


def writing_status(engine: Engine, backlog: int, last_latency_s: float | None) -> str:
    """The Status button's line about writing down what's said (#39).

    With transcription turned off (TRANSCRIBER=none) nothing is written down, so it
    must not say it's keeping up. Every state starts with the same words, so a DM
    skimming the list finds it.
    """
    if engine == "none":
        return (
            "Writing things down: off. DMbot hears who's talking but doesn't write down "
            "what's said. Whoever hosts DMbot can turn it on."
        )
    if backlog >= WRITING_BEHIND_BACKLOG:
        return (
            "⚠️ Writing things down: falling behind. DMbot still hears everyone; the "
            "words will show up late."
        )
    behind = (
        f" (about {last_latency_s:.0f} s behind)"
        if last_latency_s is not None and last_latency_s >= 1
        else ""
    )
    return f"Writing things down: keeping up{behind}"


def backup_filename(campaign_name: str, now: int) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", campaign_name.casefold()).strip("-")[:40] or "campaign"
    day = datetime.fromtimestamp(now, UTC).strftime("%Y-%m-%d")
    return f"{slug}-{day}.dmbot.json.gz"  # compressed since #164; .dmbot.json still restores


WELCOME_TEXT = (
    "**🎲 DMbot helps the DM run the game.**\n"
    "It listens to people who said yes, writes down what's said, and sends the DM rules "
    "tips in private. It remembers each campaign between sessions.\n"
    "**It never makes up the story and never decides. The DM always does.**\n"
    "\n"
    "Let's set up your first campaign. It takes about a minute."
)

HELP_TEXT = (
    "**🎲 DMbot helps the Dungeon Master.**\n"
    "It listens to your table, writes down what's said, and gives the DM rules tips "
    "in private. It remembers each campaign between sessions.\n"
    "**DMbot never makes up the story and never decides anything. The DM always does.**\n"
    "\n"
    "**Commands**\n"
    "• `/dmbot start`: pick your campaign and voice channel, then start listening\n"
    "• `/dmbot stop`: stop listening\n"
    "• `/dmbot names`: the names DMbot listens for (characters, places, NPCs)\n"
    "• `/dmbot optionalrules`: turn optional rules from Xanathar's and Tasha's on or off\n"
    "• `/transcript`: download what was said in a session (anyone in the server)\n"
    "• `/dmbot backup`: download a complete copy of a campaign (anyone can)\n"
    "• `/dmbot restore`: bring a campaign back from a copy\n"
    "\n"
    "**Recording:** DMbot only records people who say yes. When the DM starts a "
    "session, or when you join the voice channel, DMbot sends you a private message "
    "with an **I consent** button. Anyone in this server can read what DMbot writes down, "
    "and an AI company (Anthropic) reads it to give the DM notes.\n"
    "No message from DMbot? Check your Message Requests, or type `/consent give`. "
    "To stop, press **Stop recording me** or type `/consent revoke`."
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
