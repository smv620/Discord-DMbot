"""Every table that holds a campaign's rows, and where each one goes in a backup (#2552).

A campaign-keyed table that is in no backup section and on no written list is a campaign that
quietly loses data when it is restored or deleted. This module is the one list: `SECTION_TABLES`
says which tables each registered backup section carries, `NOT_IN_BACKUP` says why a table is left
out (with the decision it comes from), and `OWED_TO_BACKUP` is what #2552 still has to add. The
schema test fails when a table that belongs to a campaign, by a `campaign_id` or by a chain of
foreign keys to one that has it, is on none of them, and when a section does not mention a table it
claims. A backup is downloadable by anyone in the server, so nothing private goes into one.
"""

from __future__ import annotations

# Registered backup section name -> the tables it carries. The memory section's tables are read
# from the section itself by the test, so this list cannot drift from `memory/backup.py`.
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
    "adventure": ("adventure_documents",),
    "plan_spend": ("plan_spend",),
    "site_approvals": ("site_approvals",),
    "map_notes": ("map_notes",),
    "recap_style_notes": ("recap_style_notes",),
    "recap_style_examples": ("recap_style_examples",),
    "import_speakers": ("import_speakers",),
    "rule_set_follows": ("rule_set_follows",),
    "game_clock": ("game_clocks", "game_clock_groups"),
    "game_effects": ("game_effects",),
    # (only ready jobs are carried today, which is part of what #2552 lists)
    "recaps": ("recap_jobs",),
    "memory_heard": ("memory_heard",),
}

# Table -> why a backup does not carry it, and the decision that says so. A backup is downloadable
# by anyone in the server, hence the private ones; the rest are runtime state, logs, waits and
# pointers, or are made again by reading.
_RUNTIME = "runtime state or a log, not content (Supervisor, #2553)"
_WAIT = "a wait or a pointer, kept out by its schema comment"
_PLAN_READ = "made again by reading the adventure again (PLAN:170-172, #1714)"
_PLAN_LIKE = "like the plan: it points into what is read again (its schema comment)"
NOT_IN_BACKUP: dict[str, str] = {
    "active_sessions": _RUNTIME,
    "campaign_handover_offers": _RUNTIME,
    "image_day_use": _RUNTIME,
    "web_peeks": _RUNTIME,
    "npc_notes": "each person's own private 'My notes' never go into a backup (PLAN:1427)",
    "npc_roster": "derived, made again from the names (its schema comment)",
    "house_rules_file": "the private share link of the DM's rules file (PLAN:677)",
    "npcs_channel_messages": "message ids of one server's channel (PLAN:1401)",
    "name_not_same": "the DM's alone (PLAN:1987)",
    "plan_nodes": _PLAN_READ,
    "plan_links": _PLAN_READ,
    "adventure_reads": _PLAN_READ,
    "adventure_chunks": "the adventure's text is never in a backup, only its list "
    "(PLAN:167-170, #1698)",
    "plan_position": _PLAN_LIKE,
    "plan_divergences": _PLAN_LIKE,
    "plan_same_as": _PLAN_LIKE,
    "import_plays": _WAIT,
    "said_as_suggestions": _WAIT,
    "name_later": _WAIT,
    "name_answers": _WAIT,
    "recap_holds": _WAIT,
    "recap_requests": _WAIT,
    "recap_name_checks": _WAIT,
    "backstory_links": _WAIT,
    "secret_exposures": _WAIT,
    "name_actions": _WAIT,
    "setting_actions": _WAIT,
    "rule_set_codes": "a share code is a bearer secret; a restored publisher gets a new one "
    "(Supervisor, #2553; PLAN:947)",
    "memory_mentions": "where each name was said, made again as lines are read (PLAN:164)",
    "memory_changes": "the change log: a restore starts a fresh undo history (PLAN:164)",
    "owner_campaigns": "the owner's own list, rebuilt from who owns the restored campaign "
    "(PLAN:3722)",
    "merge_restores": "the undo record of one name merge (PLAN:1971)",
}

# Tables a backup should carry and does not yet. This is #2552's remaining work: each one moves to
# a section and the list ends empty. `transcript_lines` has no `campaign_id`; it hangs off
# `transcript_sessions` (the test follows foreign keys to find such tables).
OWED_TO_BACKUP: tuple[str, ...] = (
    "transcript_sessions",
    "transcript_lines",
    "transcript_respells",
    "transcript_suggestions",
    "session_imports",
    "session_usage",
    "fightbot_players",
    "waiting_name_questions",
    "pack_suggestions",
    "pack_suggestions_sent",  # with its suggestions: both carried or both not
    "recap_dictionaries",
    "rule_sets",
    "rule_set_versions",
    "rule_set_editors",
    "rule_set_suggestions",
    "rule_set_suggestions_sent",  # with its suggestions
)


def backed_up() -> frozenset[str]:
    """The tables some registered backup section carries."""
    return frozenset(t for tables in SECTION_TABLES.values() for t in tables)


# Columns of a campaign's tables that hold an id from one Discord server (a channel or a message),
# with what a move does to them. Looked up through the new server's own guild they find nothing, so
# the ones left alone are harmless until the DM screen is made again with /dmbot start; the others
# are cleared so nothing tries to edit a message that is not there.
SERVER_BOUND_COLUMNS: dict[str, str] = {
    "campaigns.*_channel_id": "cleared by the move (and the pinned message id with them)",
    "game_clocks.channel_id": "cleared by the move",
    "game_clocks.message_id": "cleared by the move",
    "game_clocks.players_channel_id": "cleared by the move",
    "game_clocks.players_message_id": "cleared by the move",
    "map_notes.message_id": "set to 0 by the move, which is what a restored note has",
    "npcs_channel_messages.message_id": "rows removed by the move; the list is posted again",
    "recap_holds.channel_id": "left: the new server finds no such channel until /dmbot start",
    "active_sessions.screen_channel_id": "none: a campaign with a running session does not move",
    "active_sessions.voice_channel_id": "none: a campaign with a running session does not move",
    "campaign_handover_offers.message_id": "none: the offer is removed by the move",
}
