"""0057_table_questions."""

from __future__ import annotations

TABLE_QUESTIONS = """
    -- A player's question to DMbot, and the answer posted in the table's DMbot chat (#1187):
    -- a new kind of sidebar line, `table_question`, shown in the as-heard (raw) transcript as
    -- `[Table Q via=...]`, never in the cleaned one. A question typed in the DMbot chat has
    -- the new "via" `chat`. Only two CHECK constraints are replaced to allow the new values:
    -- no row changes, so no row-level-security policy is needed.
    ALTER TABLE transcript_lines DROP CONSTRAINT transcript_lines_sidebar_check;
    ALTER TABLE transcript_lines ADD CONSTRAINT transcript_lines_sidebar_check
        CHECK (sidebar IN ('question', 'answer', 'table_question'));
    ALTER TABLE transcript_lines DROP CONSTRAINT transcript_lines_sidebar_via_check;
    ALTER TABLE transcript_lines ADD CONSTRAINT transcript_lines_sidebar_via_check
        CHECK (sidebar_via IN ('voice-memo', 'typed', 'table-trigger', 'table-chat', 'chat'));
"""

SQL = TABLE_QUESTIONS
