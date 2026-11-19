"""0557_join_question_chosen

"Let me choose" answers the join question too (#4537, party knowledge slice 4c;
`docs/design/party_knowledge.md` §5; Supervisor's ruling on #4537, 2026-11-19).
"""

from __future__ import annotations

# A join (or coming back) question gains one more answer: `chosen`, set by the DM's first pick on
# the "Let me choose" page (a session or a story thread), in that pick's change (`answered_batch`),
# so the pick's Undo makes it wait again. Later picks only add, each its own change. Only the CHECK
# changes: no row is rewritten, so no row-level-security policy is needed, and the table stays on
# the "not in a backup" list (0550's reason).
SQL = r"""
    ALTER TABLE memory_join_questions DROP CONSTRAINT memory_join_questions_status_check;
    ALTER TABLE memory_join_questions ADD CONSTRAINT memory_join_questions_status_check
        CHECK (status IN ('waiting', 'everyone', 'party', 'first', 'chosen'));
    """

ISOLATED_TABLES: tuple[str, ...] = ()
WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = ()
