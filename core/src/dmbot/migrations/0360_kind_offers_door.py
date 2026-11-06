"""0360_kind_offers_door

The website's list of "Words DMbot stopped asking about" (#3577, the website half of #3033).
"""

from __future__ import annotations

# After a session DMbot may ask the DM whether words the table keeps using for a connection
# should become a kind of connection (0357's `memory_kind_offers`). A DM can say Never, or Not now
# (asked again after 3 more). This migration lets the website show those answers and lift them:
# - `dmbot_web_kind_offers(g, c)`: a DM-only read of the rows (the words exactly as they sit on the
#   campaign's connections, and the state), oldest answer first. The DM check is worked out in the
#   door; anyone else, a player included, gets NULL and never a word. The words are the table's own
#   words, so a DM-only door is the rule here as for every DM list.
# - the queue accepts one more action, `offer-clear` (the text is those words; no target, no flag,
#   at most 300 letters, the table's own limit); it is filed under no name (as `confirm-kinds` is)
#   and the door refuses words that are not a row of this campaign. The bot runs it through
#   `MemoryStore.clear_kind_offer`, so the words may be offered again after the next session.
# The queue door is read back from the database as it is (many migrations have changed it) and each
# anchor must be found the exact number of times, so a drift stops the migration instead of
# widening the wrong rule, as 0343, 0354 and 0358 do. No row is changed.
_SWAPS = (
    # the allowed actions (with the line's own indent: the same words in the other lists differ)
    (
        "\n                'name', 'name-join', 'name-undo', 'spelling-yes', 'spelling-no',"
        " 'players-line',",
        "\n                'name', 'name-join', 'name-undo', 'spelling-yes', 'spelling-no',"
        " 'players-line', 'offer-clear',",
        1,
    ),
    # the words may be as long as the table's own limit
    (
        "THEN 1700 WHEN act = 'players-line' THEN 120 ELSE 100 END)",
        "THEN 1700 WHEN act = 'players-line' THEN 120 WHEN act = 'offer-clear' THEN 300"
        " ELSE 100 END)",
        1,
    ),
    # the words are needed
    (
        "\n                   'name')\n               AND clean = '')",
        "\n                   'name', 'offer-clear')\n               AND clean = '')",
        1,
    ),
    # no flag
    (
        "'confirm-kinds', 'confirm-undo', 'players-line')\n               AND COALESCE(fl, false))",
        "'confirm-kinds', 'confirm-undo', 'players-line', 'offer-clear')\n"
        "               AND COALESCE(fl, false))",
        1,
    ),
    # filed under no name, like "Add all"
    (
        "           OR act IN ('confirm-kinds', 'confirm-undo') THEN\n"
        "            -- a waiting connection",
        "           OR act IN ('confirm-kinds', 'confirm-undo', 'offer-clear') THEN\n"
        "            -- a waiting connection",
        1,
    ),
    # the words must be an answer this campaign holds
    (
        "            ELSIF act = 'claim-pick' AND NOT EXISTS (",
        "            ELSIF act = 'offer-clear' AND NOT EXISTS (\n"
        "                   SELECT 1 FROM memory_kind_offers o\n"
        "                   WHERE o.guild_id = g AND o.campaign_id = c AND o.words_key = clean)"
        " THEN\n"
        "                RETURN jsonb_build_object('status', 'not_found');\n"
        "            ELSIF act = 'claim-pick' AND NOT EXISTS (",
        1,
    ),
)


def _dollar(text: str) -> str:
    return "$q$" + text + "$q$"


_STEPS = "".join(
    f"\n        def := pg_temp.swap_n(def, {_dollar(old)}, {_dollar(new)}, {n});"
    for old, new, n in _SWAPS
)

READ_DOOR = r"""
    CREATE FUNCTION dmbot_web_kind_offers(g BIGINT, c TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        RETURN COALESCE((
            SELECT jsonb_agg(jsonb_build_object('words', o.words_key, 'state', o.state)
                             ORDER BY o.created_at, o.words_key)
            FROM (SELECT * FROM memory_kind_offers x
                  WHERE x.guild_id = g AND x.campaign_id = c
                  ORDER BY x.created_at, x.words_key LIMIT 200) o), '[]'::jsonb);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_kind_offers(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_kind_offers(BIGINT, TEXT) FROM PUBLIC;
"""

SQL = (
    r"""
    ALTER TABLE name_actions DROP CONSTRAINT name_actions_action_check;
    ALTER TABLE name_actions ADD CONSTRAINT name_actions_action_check CHECK (action IN
        ('spell', 'name', 'name-join', 'name-undo', 'kind', 'add-other', 'remove-other', 'secret',
         'hide', 'merge', 'merge-many', 'remove', 'link-add', 'link-words', 'link-secret',
         'link-remove', 'link-pin', 'link-who', 'alias-who', 'confirm-kinds', 'confirm-undo',
         'claim-confirm', 'claim-reject', 'claim-pick', 'claim-unpick', 'backstory-keep',
         'backstory-skip', 'backstory-keep-all', 'backstory-which-pick',
         'backstory-which-neither', 'reading-place', 'reading-no', 'reading-undo',
         'names-fixed-undo', 'spelling-yes', 'spelling-no', 'told-add', 'told-remove',
         'players-line', 'offer-clear'));
    ALTER TABLE name_actions DROP CONSTRAINT name_actions_text_check;
    ALTER TABLE name_actions ADD CONSTRAINT name_actions_text_check CHECK (char_length(text) <=
        CASE WHEN action IN ('merge-many', 'backstory-keep-all', 'link-who', 'alias-who',
                             'confirm-kinds') THEN 1700
             WHEN action = 'offer-clear' THEN 300
             WHEN action = 'players-line' THEN 120 ELSE 100 END);

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
              AND p.proname = 'dmbot_web_name_action_add';"""
    + _STEPS
    + r"""
        EXECUTE def;
    END
    $do$;
    """
    + READ_DOOR
)

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_kind_offers", "dmbot_web_kind_offers(BIGINT, TEXT)"),
)
