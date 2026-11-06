"""0054_sheet_questions."""

from __future__ import annotations

SHEET_QUESTIONS = """
    -- Sheet re-reads (#1128; docs/PLAN.md, "Re-reading D&D Beyond"): whether DMbot may ask this
    -- sheet's player, privately, when a re-read doesn't show what was said at the table. On
    -- unless the player turns it off from their character sheet panel. Defaulted column: no
    -- row changes, so no backfill policy.
    ALTER TABLE character_sheets ADD COLUMN ask_mismatch BOOLEAN NOT NULL DEFAULT TRUE;
"""

SQL = SHEET_QUESTIONS
