"""0570_leave_questions

A goodbye or a death becomes an after-session question (#4537, party knowledge slice 6a;
`docs/design/party_knowledge.md` §4 "Readers only suggest", §6 and §11; Supervisor's ruling on
slice 6, 2026-11-21): "Okthandom is marked dead. Mark Okthandom as no longer in the party?"
"""

from __future__ import annotations

# The question rides the join question's rails (ruling R): one waiting list, one sender, so "never
# during play, never twice, one private message" stays in one place. `kind` gains `leave` (a
# goodbye heard at the table, slice 6b) and `died` (a player character marked dead), and `status`
# gains the two answers: `left` (the DM closed the stretch, with the card's own call, in the same
# change) and `stays` ("Still in the party": nothing written, and the same goodbye isn't asked
# again). No row is rewritten, so no row-level-security policy is needed.
#
# One question waits per character in each family: a join or coming back is about what the
# character knows, a goodbye or a death about whether they are still in the party, so a character
# who dies before their join question is answered is asked both. The old index (one waiting per
# character) is replaced by one keyed on the family.
#
# The website's read door (`dmbot_web_join_questions`, 0554) shows a waiting question with the
# join buttons, so until the website's half (the two kinds with their own buttons) it keeps to
# `join` and `back`: a goodbye is asked in Discord and counted on ❓ Questions for you meanwhile.
# The door is read back as it is and the anchor found exactly once (a drift stops the migration),
# as 0555 does; grants and search paths are unchanged.
_STATUSES = "q.status IN ('waiting', 'everyone')"


def _dollar(text: str) -> str:
    return "$q$" + text + "$q$"


SQL = (
    r"""
    ALTER TABLE memory_join_questions DROP CONSTRAINT memory_join_questions_kind_check;
    ALTER TABLE memory_join_questions ADD CONSTRAINT memory_join_questions_kind_check
        CHECK (kind IN ('join', 'back', 'leave', 'died'));
    ALTER TABLE memory_join_questions DROP CONSTRAINT memory_join_questions_status_check;
    ALTER TABLE memory_join_questions ADD CONSTRAINT memory_join_questions_status_check
        CHECK (status IN ('waiting', 'everyone', 'party', 'first', 'chosen', 'left', 'stays'));
    DROP INDEX memory_join_questions_one_waiting;
    CREATE UNIQUE INDEX memory_join_questions_one_waiting
        ON memory_join_questions (guild_id, campaign_id, entity_id, (kind IN ('leave', 'died')))
        WHERE status = 'waiting';

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
    + _dollar(_STATUSES)
    + ", "
    + _dollar(_STATUSES + " AND q.kind IN ('join', 'back')")
    + r""");
        EXECUTE def;
    END
    $do$;
    """
)

ISOLATED_TABLES: tuple[str, ...] = ()
WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = ()
