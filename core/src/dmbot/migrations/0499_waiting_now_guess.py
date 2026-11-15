"""0499_waiting_now_guess

The website's connection card says what the campaign holds now and DMbot's guess, and the DM's
"let players see it" choice has its own action (#4432, the website half of #4429).
"""

from __future__ import annotations

# Three changes, each read back from the database as it is (many migrations changed these doors)
# with each anchor checked, so a drift stops the migration (the 0403 and 0436 way). No row is
# changed.
#
# 1. **`now` and `guess` on each waiting connection** (`dmbot_web_waiting`, DM only like the whole
#    door). `now` is the live, confirmed connection of the same kind between the same two entries
#    (either way round) whose words differ: `{predicate, detail, hidden}`, `hidden` meaning players
#    cannot see it (secret, not yet revealed, assumed, or touching an audience-limited entry). It
#    carries no name and no quote; the page words it through the same `link_words` as the waiting
#    connection. A different kind between the two
#    ("knows" beside "works for", or a friend against an enemy) is left to the bot, which decides
#    what a keep replaces with the ontology; copying that into SQL would let the two differ. `guess`
#    is the reason DMbot inferred it (`inferred_reason`, 0403), "" for a stated one. Until now the
#    list left inferred connections out because the website had no mark for them (#3928); the card
#    says "DMbot's guess" now, so they are listed and counted. Keep all leaves them out (the page).
# 2. **`backstory-keep-show`** joins the actions: the same as `backstory-keep` (a waiting
#    connection's id, no text, a flag), meaning "keep it and let players see it" when it replaces a
#    connection that was hidden (the bot applies it; `backstory-keep` never reveals). Every place
#    the door names `'backstory-keep'` in a list names the new action beside it, and none may name
#    it in a comparison (the migration stops if one does).
# 3. **The action list's own check** on `name_actions` takes the new word.
_LIST_OLD = "AND b.inferred_reason = '' ORDER BY b.created_at, b.id LIMIT 300"
_LIST_NEW = "ORDER BY b.created_at, b.id LIMIT 300"
_COUNT_OLD = "WHERE b.guild_id = g AND b.campaign_id = c AND b.inferred_reason = ''))"
_COUNT_NEW = "WHERE b.guild_id = g AND b.campaign_id = c))"
_FIELDS_OLD = "'truth', l.truth, 'upload', l.upload,"
_FIELDS_NEW = (
    "'truth', l.truth, 'upload', l.upload,\n"
    "                    'guess', l.inferred_reason,\n"
    "                    'now', (SELECT jsonb_build_object(\n"
    "                                'predicate', r.predicate, 'detail', r.detail,\n"
    "                                'hidden', r.secret OR r.unrevealed OR r.assumed\n"
    "                                          OR COALESCE((SELECT bool_or(e.audience_limited)\n"
    "                                                FROM memory_entities e\n"
    "                                                WHERE e.guild_id = g AND e.campaign_id = c\n"
    "                                                  AND e.id IN (r.subject_id,\n"
    "                                                               r.object_id)), false))\n"
    "                            FROM memory_relations r\n"
    "                            WHERE r.guild_id = g AND r.campaign_id = c\n"
    "                              AND r.status = 'confirmed' AND r.to_session_at IS NULL\n"
    "                              AND r.predicate = l.predicate AND r.detail <> l.detail\n"
    "                              AND NOT EXISTS (SELECT 1 FROM memory_relations q\n"
    "                                  WHERE q.guild_id = g AND q.campaign_id = c\n"
    "                                    AND q.status = 'confirmed' AND q.to_session_at IS NULL\n"
    "                                    AND q.predicate = l.predicate AND q.detail = l.detail\n"
    "                                    AND ((q.subject_id = l.subject_id\n"
    "                                          AND q.object_id = l.object_id)\n"
    "                                         OR (q.subject_id = l.object_id\n"
    "                                             AND q.object_id = l.subject_id)))\n"
    "                              AND ((r.subject_id = l.subject_id\n"
    "                                    AND r.object_id = l.object_id)\n"
    "                                   OR (r.subject_id = l.object_id\n"
    "                                       AND r.object_id = l.subject_id))\n"
    "                            ORDER BY r.created_at DESC, r.id LIMIT 1),"
)

SQL = (
    r"""
    DO $do$
    DECLARE
        d text;
    BEGIN
        SELECT pg_get_constraintdef(oid) INTO d FROM pg_constraint
            WHERE conname = 'name_actions_action_check' AND conrelid = 'name_actions'::regclass;
        IF (length(d) - length(replace(d, '])', ''))) <> 2 THEN
            RAISE EXCEPTION 'the action list did not have the expected shape';
        END IF;
        ALTER TABLE name_actions DROP CONSTRAINT name_actions_action_check;
        EXECUTE 'ALTER TABLE name_actions ADD CONSTRAINT name_actions_action_check '
            || replace(d, '])', ', ''backstory-keep-show''::text])');
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

    -- The queue door: the new action beside every list that names 'backstory-keep'.
    DO $do$
    DECLARE
        def text;
        fresh text;
    BEGIN
        SELECT pg_get_functiondef(p.oid) INTO def FROM pg_proc p
            WHERE p.pronamespace = current_schema()::regnamespace
              AND p.proname = 'dmbot_web_name_action_add';
        IF def !~ '''backstory-keep''' OR def ~ '(=|<>|!=)[[:space:]]*''backstory-keep''' THEN
            RAISE EXCEPTION 'the queue door did not have the expected shape';
        END IF;
        fresh := replace(def, '''backstory-keep''', '''backstory-keep'', ''backstory-keep-show''');
        EXECUTE fresh;
    END
    $do$;

    -- The waiting list: inferred connections are listed and counted, and each carries `guess`
    -- and `now`.
    DO $do$
    DECLARE
        def text;
    BEGIN
        SELECT pg_get_functiondef(p.oid) INTO def FROM pg_proc p
            WHERE p.pronamespace = current_schema()::regnamespace
              AND p.proname = 'dmbot_web_waiting';"""
    + f"\n        def := pg_temp.swap_n(def, $q${_LIST_OLD}$q$, $q${_LIST_NEW}$q$, 1);"
    + f"\n        def := pg_temp.swap_n(def, $q${_COUNT_OLD}$q$, $q${_COUNT_NEW}$q$, 1);"
    + f"\n        def := pg_temp.swap_n(def, $q${_FIELDS_OLD}$q$, $q${_FIELDS_NEW}$q$, 1);"
    + r"""
        EXECUTE def;
    END
    $do$;
    """
)

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = ()
