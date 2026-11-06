"""0264_unnamed_hidden_from_players

The doors and search text that keep the DM's entries from players keep these too (#2790).
"""

from __future__ import annotations

# Every function that hides the DM's entries from players by `hidden_from_players` also hides an
# unnamed person (#2790): a confirmed one must never show on the website, in the search text or in
# a recap. Each is read back from the database as it is now, three spellings of the same guard are
# widened, and the function is made again with every setting it had (its pinned search path, its
# rights). The spellings, x being the table's alias: `NOT x.hidden_from_players`,
# `OR x.hidden_from_players)` and `(x.hidden_from_players OR`. A function where none changes has a
# guard this migration missed, and stops the migration instead of passing silently.
UNNAMED_HIDDEN_FROM_PLAYERS = r"""
    DO $$
    DECLARE
        r record;
        def text;
        fresh text;
        have int;
        paired int;
    BEGIN
        FOR r IN
            SELECT p.oid, p.proname FROM pg_proc p
            WHERE p.pronamespace = current_schema()::regnamespace
              AND p.proname IN (
                  'dmbot_entity_parts', 'dmbot_recap_check_names', 'dmbot_recap_shared',
                  'dmbot_web_all_names', 'dmbot_web_campaign_facts', 'dmbot_web_graph_overview',
                  'dmbot_web_relationships', 'dmbot_web_story_timeline')
            ORDER BY p.proname
        LOOP
            def := pg_get_functiondef(r.oid);
            fresh := regexp_replace(def, 'NOT (\w+)\.hidden_from_players',
                                    'NOT \1.hidden_from_players AND NOT \1.unnamed', 'g');
            fresh := regexp_replace(fresh, 'OR (\w+)\.hidden_from_players\)',
                                    'OR \1.hidden_from_players OR \1.unnamed)', 'g');
            fresh := regexp_replace(fresh, '\((\w+)\.hidden_from_players OR ',
                                    '(\1.hidden_from_players OR \1.unnamed OR ', 'g');
            IF fresh = def THEN
                RAISE EXCEPTION 'no hidden_from_players guard changed in %', r.proname;
            END IF;
            -- Guard by guard, not function by function: every way of saying "not hidden from
            -- players" left in the new text must have its unnamed partner next to it (a positive
            -- use, such as the one that lists the held names, is no guard and is left alone).
            SELECT count(*) INTO have FROM regexp_matches(
                fresh, 'NOT \w+\.hidden_from_players', 'g');
            SELECT count(*) INTO paired FROM regexp_matches(
                fresh, 'NOT (\w+)\.hidden_from_players AND NOT \1\.unnamed', 'g');
            IF have <> paired THEN
                RAISE EXCEPTION 'NOT x.hidden_from_players has no unnamed guard: %', r.proname;
            END IF;
            SELECT count(*) INTO have FROM regexp_matches(
                fresh, 'OR \w+\.hidden_from_players\)', 'g');
            IF have <> 0 THEN
                RAISE EXCEPTION 'OR x.hidden_from_players) has no unnamed guard: %', r.proname;
            END IF;
            SELECT count(*) INTO have FROM regexp_matches(
                fresh, '\(\w+\.hidden_from_players OR ', 'g');
            SELECT count(*) INTO paired FROM regexp_matches(
                fresh, '\((\w+)\.hidden_from_players OR \1\.unnamed OR ', 'g');
            IF have <> paired THEN
                RAISE EXCEPTION '(x.hidden_from_players OR has no unnamed guard: %', r.proname;
            END IF;
            SELECT count(*) INTO have FROM regexp_matches(
                fresh, 'OR \w+\.hidden_from_players OR ', 'g');
            SELECT count(*) INTO paired FROM regexp_matches(
                fresh, 'OR (\w+)\.hidden_from_players OR \1\.unnamed', 'g');
            IF have <> paired THEN
                RAISE EXCEPTION 'OR x.hidden_from_players OR has no unnamed guard: %', r.proname;
            END IF;
            EXECUTE fresh;
        END LOOP;
    END $$;
"""

SQL = UNNAMED_HIDDEN_FROM_PLAYERS
