"""0242_name_change_words

Change the words on names to check (#2593).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    _CHECK_READ_DOOR,
    NAME_ANSWERS,
    _swap,
)

_CHECK_ANSWER_DOOR = _swap(
    NAME_ANSWERS[
        NAME_ANSWERS.index("    CREATE FUNCTION dmbot_name_answer_add") : NAME_ANSWERS.index(
            "    DO $do$"
        )
    ].replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION"),
    (
        (
            "'respell', 'not_a_name', 'explain')",
            "'respell', 'not_a_name', 'explain', 'change_words')",
        ),
        (
            "OR (act = 'respell' AND char_length(clean) NOT BETWEEN 1 AND 80)",
            "OR (act = 'respell' AND char_length(clean) NOT BETWEEN 1 AND 80)\n"
            "           OR (act = 'change_words' AND char_length(clean) NOT BETWEEN 1 AND 100)",
        ),
        (
            "OR (act <> 'same' AND COALESCE(tgt, '') <> '') THEN",
            "OR (act NOT IN ('same', 'change_words') AND COALESCE(tgt, '') <> '')\n"
            "           OR (act = 'change_words' AND (char_length(COALESCE(tgt, '')) > 100\n"
            "                OR COALESCE(tgt, '') ~ '[[:cntrl:]]')) THEN",
        ),
    ),
)

NAME_CHANGE_WORDS = (
    """
    ALTER TABLE name_answers DROP CONSTRAINT name_answers_action_check;
    ALTER TABLE name_answers ADD CONSTRAINT name_answers_action_check CHECK (action IN
        ('confirm', 'same', 'respell', 'not_a_name', 'explain', 'change_words'));
    ALTER TABLE name_answers DROP CONSTRAINT name_answers_target_id_check;
    ALTER TABLE name_answers ADD CONSTRAINT name_answers_target_id_check
        CHECK (char_length(target_id) <= 100);
    """
    + _CHECK_READ_DOOR
    + _CHECK_ANSWER_DOOR
    + r"""
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_recap_names_read(BIGINT, TEXT, BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_name_answer_add('
            'BIGINT, TEXT, BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    """
)

SQL = NAME_CHANGE_WORDS
