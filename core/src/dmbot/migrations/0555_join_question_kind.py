"""0555_join_question_kind

A character coming back to the party is asked about the same way as one joining it (#4537, party
knowledge slice 4a; `docs/design/party_knowledge.md` §6; Supervisor's ruling on #4537, 2026-11-19):
"Okthandom is back in the party. What does Okthandom know of what happened while away?"
"""

from __future__ import annotations

# `kind` says which question it is: `join` (a character new to the party, 0550) or `back` (one who
# left and came back, who keeps every row from before). Only the words differ: the answers, the
# claim, the Undo and the "never during play" rule are the join question's own, and a message holds
# one kind only (`join_questions.claim` takes each kind under its own group). Existing rows are all
# joins, so the default fills them; no row is rewritten, so no row-level-security policy is needed.
# The table is on the "not in a backup" list (`campaigns/tables.py`, 0550's reason), so no backup
# section changes, and its foreign keys already cascade a moved campaign (#2550).
#
# The website's read door (`dmbot_web_join_questions`, 0554) also returns each message's `kind`, so
# the website words a coming back as Discord does. The door is read back as it is and each anchor
# found exactly once (a drift stops the migration), as 0553 does; grants and search paths are
# unchanged.
_ANSWER = "'answer', t.answer,"
_FIRST = "min(q.created_at) AS first_at"


def _dollar(text: str) -> str:
    return "$q$" + text + "$q$"


SQL = (
    r"""
    ALTER TABLE memory_join_questions
        ADD COLUMN kind TEXT NOT NULL DEFAULT 'join' CHECK (kind IN ('join', 'back'));

    CREATE OR REPLACE FUNCTION pg_temp.swap_once(def text, old text, new text) RETURNS text
    LANGUAGE plpgsql AS $f$
    BEGIN
        IF (length(def) - length(replace(def, old, ''))) <> length(old) THEN
            RAISE EXCEPTION 'not found exactly once: %', left(old, 80);
        END IF;
        RETURN replace(def, old, new);
    END
    $f$;

    DO $do$
    DECLARE
        def text;
    BEGIN
        SELECT pg_get_functiondef(p.oid) INTO def FROM pg_proc p
            WHERE p.pronamespace = current_schema()::regnamespace
              AND p.proname = 'dmbot_web_join_questions';
        def := pg_temp.swap_once(def, """
    + _dollar(_ANSWER)
    + ", "
    + _dollar(_ANSWER + " 'kind', t.kind,")
    + r""");
        def := pg_temp.swap_once(def, """
    + _dollar(_FIRST)
    + ", "
    + _dollar(_FIRST + ", min(q.kind) AS kind")
    + r""");
        EXECUTE def;
    END
    $do$;
    """
)

ISOLATED_TABLES: tuple[str, ...] = ()
WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = ()
