"""Which build of DMbot made a row (#3739): the commit stamped on what DMbot generates.

The owner's rule: a fault found later must be pinned to a version, so an old row from a build that
is already fixed is not mistaken for a new bug. The value is the commit the running image was built
from (`GIT_COMMIT`, the same one a backup writes as `dmbot_commit`), read once at start-up.

**What the stamp means, and what it does not.** It says which *build wrote the row*, never "DMbot
invented this": a row a DM adds by hand in Discord is still written by some build of the code, and
the row's own `source` says who asked for it. Rows from before versions were recorded have NULL,
and readers say "made before versions were recorded".

**How it is attached** (so no writer and no call site has to remember): `Database` opens every
transaction with `dmbot.commit`, and the trigger `dmbot_stamp_commit()` (migration 0378) copies it
into `made_by_commit`. On INSERT the row gets the running commit. On UPDATE the stored stamp is
kept, so a DM's own edit never changes it, unless the writer is DMbot itself rewriting its own work
(`dmbot.rewrites`, opened by `Database.guild(..., by_dmbot=True)`; story memory opens it for every
write whose source is not the DM). A restore opens `dmbot.restoring` around its section loads, and the trigger then keeps exactly the
stamp the row carries, NULL included: an old row is never stamped with the build that restored it.
An Undo is the DM's act and updates without restamping. A writer that forgets to opt in keeps the older stamp, which never
claims a version made something it did not.

**Per transaction, not per row:** a write that opted in restamps every row it updates, a dependent
hide a DM's row included; the stamp is the build that last wrote the row, not who asked.

**Part 1's backup** carries `made_by` for the memory section's own tables only (`memory/backup.py`,
`_TAGS`). The other stamped tables are restored by their own sections, which do not carry it yet, so
they restore with none ("made before versions were recorded"): honest, but a loss, filed as a
follow-up under #3739.
"""

from __future__ import annotations

import os
import re

_COMMIT = re.compile(r"[0-9a-f]{40}")
OLD_ROW = "made before versions were recorded"


# The tables that carry the stamp now (the catalog test checks this against the database). Each part
# of #3739 adds its area's tables; `NOT_YET` shrinks by the same amount.
STAMPED_MEMORY = (
    "memory_entities", "memory_aliases", "memory_relations", "memory_claims", "memory_states",
    "memory_perspectives", "memory_beliefs", "memory_evidence", "memory_impressions",
    "memory_attitudes", "memory_deed_proposals", "memory_scenes", "memory_changes",
    "memory_contradictions", "memory_differences", "memory_heard", "memory_mentions",
    "memory_vectors", "memory_who_answers", "memory_kind_offers", "memory_knowers",
    "memory_alias_knowers", "memory_told", "memory_link_sources", "memory_importance",
    "memory_flags", "memory_rule_links", "memory_report_runs", "backstory_links",
    "backstory_which", "waiting_name_questions", "name_pass_fixes", "name_pass_reports",
    "rel_ties", "lore_uploads",
)  # fmt: skip
STAMPED = frozenset(STAMPED_MEMORY)

# Campaign tables a later part of #3739 stamps: recaps and audio (part 2), transcripts and imports
# (part 3), the rest (part 4: maps, pictures, rules, questions, the AI feed, sheets). Each part
# moves its tables to `STAMPED`; the list ends empty.
NOT_YET: tuple[str, ...] = (
    # part 2: recaps and audio
    "recap_accepted", "recap_dictionaries", "recap_holds", "recap_jobs", "recap_name_checks",
    "recap_requests", "theatre_lines", "theatre_runs",
    # part 3: transcripts and imports
    "transcript_lines", "transcript_respells", "transcript_sessions", "transcript_suggestions",
    "session_imports", "import_plays", "import_speakers", "said_as_suggestions", "session_notes",
    # part 4: the rest
    "ai_feed", "map_notes", "campaign_pictures", "picture_actions", "plot_chat", "plot_checks",
    "plot_fine", "plot_issues", "pack_suggestions", "rule_set_suggestions", "import_clock_offers",
    "character_sheets", "memory_corrections",
)  # fmt: skip

_DM = "what the DM wrote or chose"
_QUEUE = "a queue of the DM's website choices, waiting for the bot"
_PLUMBING = "bookkeeping, not something DMbot generated"
# Campaign tables that are never stamped, each with why. A new campaign table must be stamped or
# be added here with its reason (the catalog test, `tests/test_lineage_catalog.py`).
NOT_STAMPED: dict[str, str] = {
    **dict.fromkeys(
        (
            "campaign_dms",
            "campaign_optional_rules",
            "homebrew_entries",
            "house_rule_wins",
            "house_rules",
            "house_rules_file",
            "memory_predicates",
            "memory_types",
            "name_later",
            "name_not_same",
            "name_answers",
            "npc_notes",
            "npc_roster",
            "recap_must_haves",
            "recap_style_examples",
            "recap_style_notes",
            "recap_upload_rights",
            "rule_set_codes",
            "rule_set_editors",
            "rule_set_follows",
            "rule_set_versions",
            "rule_sets",
            "shared_confirmations",
            "site_approvals",
            "speaker_erasures",
            "speaker_removals",
            "import_consents",
            "campaign_speakers",
            "adventure_chunks",
            "adventure_documents",
            "fightbot_players",
            "game_clocks",
            "game_clock_groups",
            "game_effects",
            "plan_divergences",
            "plan_links",
            "plan_nodes",
            "plan_position",
            "plan_same_as",
            "report_answers",
            "secret_exposures",
        ),
        _DM,
    ),
    **dict.fromkeys(
        (
            "name_actions",
            "setting_actions",
            "web_backup_parts",
            "web_backups",
            "web_house_file_actions",
            "web_line_fixes",
            "web_peeks",
            "web_recap_style_actions",
            "web_report_requests",
            "web_rule_actions",
            "web_sheet_actions",
            "campaign_handover_offers",
            "merge_restores",
        ),
        _QUEUE,
    ),
    **dict.fromkeys(
        (
            "active_sessions",
            "adventure_reads",
            "dm_waiting_sent",
            "game_clock_group_log",
            "game_clock_log",
            "image_day_use",
            "npcs_channel_messages",
            "owner_campaigns",
            "pack_suggestions_sent",
            "plan_spend",
            "question_counts",
            "question_notices",
            "rule_set_suggestions_sent",
            "session_usage",
        ),
        _PLUMBING,
    ),
}


def running_commit() -> str | None:
    """The commit this build was made from (the full 40 characters), or None when the image has
    none (`GIT_COMMIT` unset or malformed): the stamp is then NULL, never the word "unknown"."""
    value = (os.environ.get("GIT_COMMIT") or "").strip().casefold()
    return value if _COMMIT.fullmatch(value) else None


def valid(value: object) -> bool:
    """A stamp as it may arrive in an untrusted backup: None or 40 lower-case hex characters."""
    return value is None or (isinstance(value, str) and _COMMIT.fullmatch(value) is not None)


def short(value: str | None) -> str:
    """What the website's details show: "made by DMbot version abc1234", or the old-row words."""
    return f"made by DMbot version {value[:7]}" if value else OLD_ROW
