"""0270_web_pronoun_which

Pronoun questions on the website (#2811).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    _swaps,
)

# A pronoun's "Which one?" on the website (#2811, after #2810): a question about "she" or "her" can
# have one name to offer, or none once its names are refused, and is then answered with that name
# or with "Someone else" (the existing 'backstory-which-neither' action: the bot keeps a pronoun's
# connection with an unnamed person, `none_of_these`). The waiting list showed only questions with
# two names, so a DM who used only the website never saw these. A question about a name keeps
# needing two. The list says which questions are about a pronoun, by the same words as
# `memory.pronouns.PRONOUNS` (a test keeps the two lists equal). The pick door now also refuses a
# question's own end, which the bot refuses too (it sits in `candidates` as a filler and is never
# offered). Both doors are cut from their latest text by guarded replacement, so a drift makes this
# fail loudly instead of quietly changing a rule. No row changes.
PRONOUN_WORDS = (
    "he", "she", "his", "her", "hers", "him", "himself", "herself", "they", "their", "theirs",
    "them", "its", "who", "whose", "whom",
)  # fmt: skip

_PRONOUN_SQL = (
    "lower(btrim(btrim(regexp_replace(q.word, '\\s+', ' ', 'g')), '.,;:!?\"''“”‘’()'))"
    " = ANY (ARRAY[" + ", ".join(f"'{w}'" for w in PRONOUN_WORDS) + "])"
)

_PRONOUN_WAITING_SWAPS = (
    (
        "-- (a question left with fewer than two live candidates is not shown: nothing can be\n"
        "            -- picked, and the bot deletes it when it next reads the queue)",
        "-- (a question about a name left with fewer than two live candidates is not shown:\n"
        "            -- nothing can be picked, and the bot deletes it when it next reads the\n"
        '            -- queue. One about a pronoun is shown with one name or none: "Someone else")',
    ),
    (
        "'upload', w.upload, 'candidates', w.cands)",
        "'upload', w.upload,\n"
        "                    'candidates', COALESCE(w.cands, '[]'::jsonb), 'pronoun', w.pron)",
    ),
    ("SELECT q.*, (\n", "SELECT q.*, " + _PRONOUN_SQL + " AS pron, (\n"),
    (
        ">= 2), '[]'::jsonb));",
        ">= CASE WHEN w.pron THEN 0 ELSE 2 END), '[]'::jsonb));",
    ),
)

_PRONOUN_PICK_SWAPS = (
    (
        "OR clean = ANY (w.candidates))) THEN",
        "OR (clean = ANY (w.candidates) AND clean <> w.other_id))) THEN",
    ),
)

# Both doors are read back from the database as they are now, as 0266 does, and not rebuilt from
# the text of the migration that last wrote them: 0266 widened the name door in place (the
# 'name' actions and the unnamed guards), and making it again from older text would undo that.
WEB_PRONOUN_WHICH = (
    r"""
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
              AND p.proname = 'dmbot_web_waiting';
"""
    + _swaps("def", _PRONOUN_WAITING_SWAPS)
    + r"""
        EXECUTE def;

        SELECT pg_get_functiondef(p.oid) INTO def FROM pg_proc p
            WHERE p.pronamespace = current_schema()::regnamespace
              AND p.proname = 'dmbot_web_name_action_add';
"""
    + _swaps("def", _PRONOUN_PICK_SWAPS)
    + r"""
        EXECUTE def;
    END
    $do$;
"""
)

SQL = WEB_PRONOUN_WHICH
