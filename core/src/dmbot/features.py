"""Which features are switched on in this DMbot, in one small table (#1831).

Several features are built but sit behind a server switch (an environment setting) or a key. From
the outside nobody could tell which were on, so a forgotten switch looked like a bug at the table.
This lists them in one place: one INFO line at start-up (`Features: ...`, names and on/off only,
never a value, key or address) and the plain-words list a DM can read from the settings card.
A new switch is added here, once: `tests/test_features.py` fails when a setting is in neither this
table nor the named list of settings that are not switches.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dmbot.config import Settings

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Feature:
    log_name: str  # in the start-up line: "time suggestions"
    dm_name: str  # what a DM calls it
    dm_words: str  # one short plain sentence about what it does
    is_on: Callable[[Settings], bool]
    covers: tuple[str, ...]  # the `Settings` fields that decide it (the completeness test)
    for_dms: bool = True  # shown on the settings card (the rest are for whoever runs DMbot)


FEATURES: tuple[Feature, ...] = (
    Feature(
        "handover by email",
        "Hand over by email",
        "A campaign is handed over by a link sent to an email address.",
        lambda s: s.handover_email_on,
        ("handover_email_on",),
        for_dms=False,  # for whoever runs DMbot: on only when the whole hand-over is live (#2532)
    ),
    Feature(
        "time suggestions",
        "Clock suggestions",
        "DMbot offers to move the game clock when time passes. You choose.",
        lambda s: s.time_suggestions,
        ("time_suggestions",),
    ),
    Feature(
        "recaps",
        "Written recaps",
        "DMbot writes a recap of each session for you to read first.",
        lambda s: s.recaps_on,
        ("recaps_on",),
    ),
    Feature(
        "AI feed",
        "A window on what DMbot asks the AI",
        "",
        lambda s: s.ai_feed_on,
        ("ai_feed_on",),
        for_dms=False,  # for whoever runs DMbot: off until the website's windows are live (#3710)
    ),
    Feature(
        "questions",
        "Questions for you",
        "DMbot gathers what it is waiting to ask you in one message, and reminds you less and "
        "less often.",
        lambda s: s.questions_on,
        ("questions_on",),
        for_dms=False,  # for whoever runs DMbot: the reminders are off until it turns them on
    ),
    Feature(
        "plot checks",
        "Story checks on recaps",
        "PlotBot compares each recap with the earlier ones and asks you about anything that "
        "does not fit. You decide.",
        lambda s: s.plot_check_on,
        ("recaps_on", "plot_check_on"),
        for_dms=False,
    ),
    Feature(
        "meaning search",
        "Finding notes by meaning",
        "PlotBot also finds notes that mean the same thing in different words.",
        lambda s: s.meaning_search_on,
        ("meaning_search", "voyage_key"),
        for_dms=False,  # for whoever runs DMbot: it needs the Voyage key (#2664)
    ),
    Feature(
        "recap audio",
        "Spoken recaps",
        "DMbot reads a recap aloud, with music.",
        lambda s: s.recap_audio_on,
        ("recaps_on", "elevenlabs_key", "elevenlabs_voice_id", "recap_voice_tags"),
    ),
    Feature(
        "structured split",
        "Recap opening split in two steps",
        "",
        lambda s: s.recap_structured_output_split,
        ("recap_structured_output_split",),
        for_dms=False,  # how the work is done, not something a DM sees (#2714)
    ),
    Feature(
        "final read",
        "Recap final read",
        "",
        lambda s: s.recap_final_read,
        ("recap_final_read",),
        for_dms=False,  # how the work is done, not something a DM chooses (#2952)
    ),
    Feature(
        "structured beats",
        "Recap beat sheet in two steps",
        "",
        lambda s: s.recap_structured_output_beats,
        ("recap_structured_output_beats",),
        for_dms=False,  # how the work is done, not something a DM sees (#2714)
    ),
    Feature(
        "sidebar",
        "Quick questions",
        "Ask DMbot a question in the game and get a short private answer.",
        lambda s: s.sidebar_on,
        ("sidebar_on",),
    ),
    Feature(
        "sidebar recall",
        "Story memory in answers",
        "DMbot's answers can use what it remembers of your story.",
        lambda s: s.sidebar_recall,
        ("sidebar_recall", "sidebar_recall_budget", "recall_budget"),
    ),
    Feature(
        "session-start note",
        "Start-of-session note",
        "A short note on the DM screen about where the story left off.",
        lambda s: s.presession_note,
        ("presession_note",),
    ),
    Feature(
        "extractor",
        "Remember the story",
        "After a session, DMbot suggests what to remember. You confirm each one.",
        lambda s: s.story_extractor_on,
        (
            "story_extractor_on",
            "story_readings_on",
            "story_scenes_on",
            "story_deeds_on",
            "story_who_on",
            "story_who_apply_on",
            "story_family_only",
            "theatre_on",
            "rel_read_on",
            "story_contradictions_on",
            "story_report_on",
            "story_extractor_tier",
            "story_extractor_max_calls",
            "story_extractor_max_calls_day",
        ),
    ),
    Feature(
        "live heads-up",
        "Heads-up during play",
        "DMbot tells you on the DM screen when someone you said is dead is voiced at the table.",
        lambda s: s.live_heads_up,
        ("live_heads_up", "live_heads_up_cooldown_s"),
    ),
    Feature(
        "secret reveal",
        "Asks when you say a secret",
        "DMbot asks you or your co-DMs privately, at once, if a secret name you said is revealed.",
        lambda s: s.secret_reveal_ask,
        ("secret_reveal_ask",),
    ),
    Feature(
        "plan limits",
        "Plan limits",
        "",
        lambda s: s.enforce_plans,
        ("enforce_plans",),
        for_dms=False,
    ),
    Feature(
        "importing sessions",
        "Importing sessions",
        "",
        lambda s: s.imports_on or bool(s.imports_servers),  # (on for all, or for chosen servers)
        ("imports_on", "imports_servers"),
        for_dms=False,
    ),
    Feature(
        "ai helpers",
        "AI helpers",
        "",
        lambda s: bool(s.ai_key),
        ("ai_key", "ai_models", "ai_model_notice"),
        for_dms=False,
    ),
    Feature(
        "billing link",
        "Website link",
        "",
        lambda s: bool(s.site_url),
        ("site_url",),
        for_dms=False,
    ),
    Feature(
        "email",
        "Email",
        "",
        lambda s: s.mail.configured,
        ("mail", "link_token_secret"),
        for_dms=False,
    ),
    Feature(
        "table pictures",
        "Pictures in the table chat",
        "",
        lambda s: s.table_images_on,
        ("table_images_on",),
        for_dms=False,  # not live yet: it is listed for whoever runs DMbot, not on the card
    ),
    Feature(
        "kept pictures",
        "Pictures kept with the campaign",
        "A picture posted in the table's chat is kept with the campaign and gets a link.",
        lambda s: s.pictures_ready,
        ("pictures_kept",),
        for_dms=False,  # not live until the consent wording is: for whoever runs DMbot
    ),
    Feature(
        "who is he",
        'Who "he" and "she" are in a document',
        "",
        lambda s: s.who_is_it,
        ("who_is_it",),
        for_dms=False,  # not live yet: on only after the calibration run on #2790
    ),
    Feature(
        "test recordings",
        "Test recordings",
        "",
        lambda s: bool(s.test_recording_guilds),
        ("test_recording_guilds", "test_recordings_dir"),
        for_dms=False,
    ),
)

# Settings that are not switches (a name, a number, a place), each with the reason. A setting
# that is neither here nor in FEATURES fails the test, so a new switch cannot be forgotten.
NOT_A_SWITCH: dict[str, str] = {
    "discord_token": "a secret, always needed",
    "ears_secret": "a secret, always needed",
    "ears_host": "where the voice service is",
    "ears_port": "where the voice service is",
    "dev_guild_id": "the test server's commands, not a feature",
    "clear_guild_commands": "a one-time clean-up list of servers",
    "database_url": "a secret address, always needed",
    "database_pool_max": "a number",
    "data_dir": "a folder",
    "transcription": "how speech is turned into words, always on",
    "shards": "how the bot is split over processes",
    "gemini_key": "a secret, used only when Gemini is chosen for the recap",
    "recap_writer_reads": "which text the recap writer reads, a choice (off by default)",
    "ai_provider_creative": "which company's AI writes the recap, a choice (off by default)",
    "log_format": "how logs look",
    "cleaner_rank_max": "a number: the most ranking calls a session makes (0 is off)",
    "log_level": "how much is logged",
    "memory_keep_days": "a number of days",
    "free_users": "the owner's own accounts: never listed",
    "free_guilds": "the owner's own test server: never listed",
    "elevenlabs_speech_model": "which voice model, a choice",
    "voyage_embed_model": "which meaning-search model, a choice",
    "voyage_rerank_model": "which meaning-search ranking model, a choice",
    "recap_audio_max_credits": "a cost limit",
    "recaps_per_month": "a limit",
    "recap_audio_per_month": "a limit",
    "continuity": "tuning numbers",
    "thread_stalled_sessions": "a number",
    "table_images_per_day": "a limit",
    "picture_storage_mb": "a limit",
    "r2_account_id": "which storage account, a setting; never listed",
    "r2_access_key": "a key; never listed",
    "r2_secret_key": "a key; never listed",
    "r2_bucket": "which storage bucket, a setting; never listed",
    "admin_primary_id": "who is told of problems; never listed",
    "admin_secondary_id": "who is told of problems; never listed",
}


def features_line(settings: Settings) -> str:
    """The start-up line: each feature and on or off. Names only, never a value."""
    return "Features: " + ", ".join(
        f"{f.log_name} {'on' if f.is_on(settings) else 'off'}" for f in FEATURES
    )


BUTTON_LABEL = "What's switched on"
NOTHING_ON = "Nothing extra is switched on yet. Whoever runs this DMbot can turn things on."
FOOTER = (
    "Whoever runs this DMbot can turn these on. Your campaign's own choices are on the "
    "settings card."
)


def whats_on_text(settings: Settings) -> str:
    """For a DM, in plain words: what DMbot can do here right now. What is on first, with a
    sentence each; what is off as names only (a repeated reason buries the answer on a phone).
    No values, no keys."""
    shown = [f for f in FEATURES if f.for_dms]
    on = [f for f in shown if f.is_on(settings)]
    off = [f for f in shown if not f.is_on(settings)]
    if not on:
        return NOTHING_ON
    lines = ["**What's switched on**"]
    lines += [f"✅ **{f.dm_name}**: {f.dm_words}" for f in on]
    if off:
        lines += ["", "**Not available right now**"]
        lines += [f"⬜ {f.dm_name}" for f in off]
        lines += ["", FOOTER]
    return "\n".join(lines)


SNAPSHOT_FILE = "features.json"  # read by the web API (#3693): the DM-visible list, nothing else


def whats_on_list(settings: Settings) -> dict[str, Any]:
    """The same split as `whats_on_text`, as data for the web: what is on, with its sentence, and
    what is off, by name. Names and sentences only, never a value, key or address."""
    shown = [f for f in FEATURES if f.for_dms]
    return {
        "on": [{"name": f.dm_name, "words": f.dm_words} for f in shown if f.is_on(settings)],
        "off": [f.dm_name for f in shown if not f.is_on(settings)],
    }


def write_snapshot(settings: Settings, directory: str | Path, now: float) -> bool:
    """Write the DM-visible list to `<directory>/features.json` for the web (#3693). Written to a
    temporary file, flushed to disk, then renamed, so a reader never sees half of it. Never raises:
    a failure logs one line with the exception's class name only (never a path or message) and
    returns False, so start-up goes on. On failure an earlier features.json is left in place
    (its writtenAt shows when it was written), so the web can tell it is old."""
    folder = Path(directory)
    tmp = folder / (SNAPSHOT_FILE + ".tmp")
    try:
        folder.mkdir(parents=True, exist_ok=True)
        body = {"version": 1, "writtenAt": int(now), **whats_on_list(settings)}
        with tmp.open("w", encoding="utf-8") as handle:
            handle.write(json.dumps(body))
            handle.flush()
            os.fsync(handle.fileno())  # a crash right after the rename must not leave it empty
        os.replace(tmp, folder / SNAPSHOT_FILE)
    except Exception as exc:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)
        log.warning("Feature list: not written (%s)", type(exc).__name__)
        return False
    return True
