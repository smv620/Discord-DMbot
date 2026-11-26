"""0588_web_who_shown

The website's "Who knows this?" tells the bot which characters its list showed, so a save never
removes or confirms one the page did not show (#4968, Supervisor's condition 2 on #3940).
"""

from __future__ import annotations

# `link-who` (a connection) and `alias-who` (a secret other name) took `dm`, `everyone`, `common`
# or `some` and then one to fifty character ids (0314, 0523). GraphEngineer's #4971 reads one more
# part after the answer: ` shown <ids>`, every character the list showed, ticked or not
# (`web_actions._shown_of`). With it, a character the page showed and the DM kept who was DMbot's
# guess becomes the DM's word, one the page showed and the DM unticked goes, and one it did not show
# is left alone. This lets the door's shape check take that part after any of the four answers (one
# to fifty ids: a character list never shows more), and lifts the text limit of these two actions
# from 1700 to 3400 (an answer with fifty ids and fifty shown is about 3300 characters), in the
# door's function and in the table's own check. The door is read back from the database as it is
# and each anchor must be found exactly once, so a drift stops the migration. No row is changed.
_SHAPE_OLD = "AND clean !~ '^(dm|everyone|common|some( [0-9a-f]{32}){1,50})$')"
_SHAPE_NEW = (
    "AND clean !~ '^(dm|everyone|common|some( [0-9a-f]{32}){1,50})( shown( [0-9a-f]{32}){1,50})?$')"
)
_CAP_OLD = "char_length(clean) > (CASE WHEN act IN ('merge-many',"
_CAP_NEW = (
    "char_length(clean) > (CASE WHEN act IN ('link-who', 'alias-who') THEN 3400"
    " WHEN act IN ('merge-many',"
)


# The door checks that the ids of a `some` answer are confirmed characters or groups of this
# campaign (four places read `substr(clean, 6)`); they must only read the ids before the shown part.
# The shown ids are only shape-checked: they say what the page showed, and the bot never acts on a
# made-up one (a character it does not know is left alone).
_IDS_OLD = "substr(clean, 6)"
_IDS_NEW = "split_part(substr(clean, 6), ' shown ', 1)"


def _dollar(text: str) -> str:
    return "$q$" + text + "$q$"


SQL = (
    r"""
    -- The table's own text limit, read back as it is: the first case is the new one, so it wins
    -- for these two actions (the CASE must be there exactly once).
    DO $do$
    DECLARE
        d text;
        fresh text;
    BEGIN
        SELECT pg_get_constraintdef(oid) INTO d FROM pg_constraint
            WHERE conname = 'name_actions_text_check' AND conrelid = 'name_actions'::regclass;
        IF (length(d) - length(replace(d, 'CASE', ''))) <> 4 THEN
            RAISE EXCEPTION 'the text limit did not have the expected shape';
        END IF;
        fresh := regexp_replace(
            d, 'CASE\s+',
            'CASE WHEN (action = ANY (ARRAY[''link-who''::text, ''alias-who''::text])) THEN 3400 ',
            1, 1);
        ALTER TABLE name_actions DROP CONSTRAINT name_actions_text_check;
        EXECUTE 'ALTER TABLE name_actions ADD CONSTRAINT name_actions_text_check ' || fresh;
    END
    $do$;

    CREATE OR REPLACE FUNCTION pg_temp.swap_n(def text, old text, new text, n integer) RETURNS text
    LANGUAGE plpgsql AS $f$
    BEGIN
        IF (length(def) - length(replace(def, old, ''))) <> length(old) * n THEN
            RAISE EXCEPTION 'not found % times: %', n, left(old, 80);
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
              AND p.proname = 'dmbot_web_name_action_add';
        def := pg_temp.swap_n(def, $q$"""
    + _SHAPE_OLD
    + r"""$q$, $q$"""
    + _SHAPE_NEW
    + r"""$q$, 1);
        def := pg_temp.swap_n(def, $q$"""
    + _IDS_OLD
    + r"""$q$, $q$"""
    + _IDS_NEW
    + r"""$q$, 4);
        def := pg_temp.swap_n(def, $q$"""
    + _CAP_OLD
    + r"""$q$, $q$"""
    + _CAP_NEW
    + r"""$q$, 1);
        EXECUTE def;
    END
    $do$;
    """
)

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = ()
