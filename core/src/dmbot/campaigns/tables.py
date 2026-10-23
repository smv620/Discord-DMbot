"""Every table that holds a campaign's rows, and where each one goes in a backup (#2552).

A campaign-keyed table that is in no backup section and on no written list is a campaign that
quietly loses data when it is restored or deleted. This module is the one list: `SECTION_TABLES`
says which tables each registered backup section carries, `NOT_IN_BACKUP` says why a table is left
out, and the schema test fails when a table with a `campaign_id` is on neither. The same list is
what a campaign move (#2550) is proved against: every table here moves with its campaign.
"""

from __future__ import annotations

from dmbot.campaigns.row_sections import STORY_NOTES

# Registered backup section name -> the tables it carries.
SECTION_TABLES: dict[str, tuple[str, ...]] = {
    "dms": ("campaign_dms",),
    "confirmations": ("shared_confirmations",),
    "optional_rules": ("campaign_optional_rules",),
    "memory": (
        "memory_types",
        "memory_predicates",
        "memory_entities",
        "memory_rule_links",
        "memory_states",
        "memory_claims",
        "memory_aliases",
        "memory_relations",
        "memory_perspectives",
        "memory_beliefs",
        "memory_impressions",
        "memory_attitudes",
        "memory_corrections",
        "memory_flags",
        "character_sheets",
    ),
    "house_rules": ("house_rules",),
    "house_rule_wins": ("house_rule_wins",),
    "homebrew": ("homebrew_entries",),
    "adventure": ("adventure_documents", "adventure_chunks"),
    "plan_spend": ("plan_spend",),
    "site_approvals": ("site_approvals",),
    "map_notes": ("map_notes",),
    "recap_style_notes": ("recap_style_notes",),
    "recap_style_examples": ("recap_style_examples",),
    "import_speakers": ("import_speakers",),
    "rule_set_follows": ("rule_set_follows",),
    "game_clock": ("game_clocks", "game_clock_groups"),
    "game_effects": ("game_effects",),
    "recaps": ("recap_jobs", "recap_dictionaries"),
    "memory_heard": ("memory_heard",),
    STORY_NOTES.name: tuple(t.name for t in STORY_NOTES.tables),
}

# Table -> why a backup does not carry it. A reason that is not already a PLAN decision is
# Supervisor's to approve on #2552 before it stays here.
NOT_IN_BACKUP: dict[str, str] = {
    "active_sessions": "a session that is running right now; a restore starts with none",
    "campaign_handover_offers": "an offer to hand it over ends with the campaign it was made for",
    "image_day_use": "today's image count, a daily meter that starts again each day",
    "merge_restores": "the undo record of one name merge; a restore starts a fresh history",
    "memory_changes": "the change log; a restore starts a fresh undo history (memory/backup.py)",
    "memory_mentions": "where each name was said; rebuilt as sessions are transcribed",
    "owner_campaigns": "the owner's own list of campaigns, rebuilt from who owns the restored one",
    "pack_suggestions_sent": "delivery bookkeeping for a suggestion already shown",
    "rule_set_suggestions_sent": "delivery bookkeeping for a suggestion already shown",
    "web_peeks": "who looked behind the DM screen on the website; looking is not campaign content",
}

# Tables a backup should carry and does not yet. This is #2552's remaining work: each one moves to
# a section (or to NOT_IN_BACKUP, with a reason Supervisor has approved) and the list ends empty.
OWED_TO_BACKUP: tuple[str, ...] = (
    "adventure_reads",
    "fightbot_players",
    "import_plays",
    "name_actions",
    "name_answers",
    "npcs_channel_messages",
    "pack_suggestions",
    "plan_divergences",
    "plan_links",
    "plan_nodes",
    "plan_position",
    "plan_same_as",
    "recap_holds",
    "recap_requests",
    "rule_set_codes",
    "rule_set_editors",
    "rule_set_suggestions",
    "rule_set_versions",
    "rule_sets",
    "session_imports",
    "session_usage",
    "setting_actions",
    "transcript_respells",
    "transcript_sessions",
    "transcript_suggestions",
    "waiting_name_questions",
)


def backed_up() -> frozenset[str]:
    return frozenset(t for tables in SECTION_TABLES.values() for t in tables)
