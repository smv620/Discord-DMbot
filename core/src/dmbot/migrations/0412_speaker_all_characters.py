"""0412_speaker_all_characters

A player who plays two or more characters is labelled with all of them, in the order they were
made and joined with " / " ("Mara / Bryn"), in the Discord file's `{Cerric}` labels (#4004).
"""

from __future__ import annotations

# `dmbot_web_transcript_characters` (0095), rewritten: DMbot never guesses which of a player's
# characters spoke (`stamp.group_of_speakers` already says "not known" for two or more), and the
# player's own name would hide that it was in-character speech. The bot's own label
# (`cleaner.characters`) follows the same rule, so the site and the Discord file agree. A merged
# or unconfirmed character is left out as before. (The ids are compared as bytes, as Python
# compares them, so a tie in time orders the same in both.) The export caps a label at 80
# characters, so a very long joined label is cut there, the same on the site and in Discord.
# Same signature, so the grants stay; the function's search_path is set again, as a replace
# drops it. No table changes.
SQL = r"""
    CREATE OR REPLACE FUNCTION dmbot_web_transcript_characters(g BIGINT, c TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaigns WHERE guild_id = g AND id = c) THEN
            RETURN NULL;
        END IF;
        RETURN COALESCE((
            SELECT jsonb_object_agg(x.user_id::TEXT, x.name)
            FROM (
                WITH named AS (
                    -- every confirmed, non-secret character with a name of its own
                    SELECT e.id, e.created_at, e.role, e.played_by, a.text, a.id AS alias_id
                    FROM memory_entities e
                    JOIN memory_aliases a
                      ON a.guild_id = e.guild_id AND a.campaign_id = e.campaign_id
                     AND a.entity_id = e.id
                    WHERE e.guild_id = g AND e.campaign_id = c
                      AND e.type <> 'person' AND e.status = 'confirmed'
                      AND a.status = 'confirmed' AND NOT a.secret
                      AND a.key = (SELECT n.key FROM memory_aliases n
                                   WHERE n.guild_id = g AND n.campaign_id = c
                                     AND n.entity_id = e.id AND n.text = e.name
                                     AND n.status <> 'rejected'
                                   ORDER BY n.id LIMIT 1)
                ), by_role AS (
                    -- all of a player's characters, in the order they were made, joined with
                    -- " / " (one row for each character: its first own-name alias)
                    SELECT one.played_by AS user_id,
                           string_agg(one.text, ' / '
                                      ORDER BY one.created_at, one.id COLLATE "C") AS name
                    FROM (SELECT DISTINCT ON (id) id, created_at, played_by, text
                          FROM named
                          WHERE role = 'player_character' AND played_by IS NOT NULL
                          ORDER BY id, alias_id) one
                    GROUP BY one.played_by
                ), by_plays AS (
                    SELECT DISTINCT ON (p.played_by) p.played_by AS user_id, nm.text AS name
                    FROM memory_entities p
                    JOIN memory_relations r
                      ON r.guild_id = p.guild_id AND r.campaign_id = p.campaign_id
                     AND r.subject_id = p.id
                    JOIN (SELECT DISTINCT ON (id) id, text FROM named ORDER BY id, alias_id) nm
                      ON nm.id = r.object_id
                    WHERE p.guild_id = g AND p.campaign_id = c AND p.type = 'person'
                      AND p.status = 'confirmed' AND p.played_by IS NOT NULL
                      AND r.predicate = 'plays' AND r.status = 'confirmed' AND NOT r.secret
                      AND NOT EXISTS (SELECT 1 FROM by_role b WHERE b.user_id = p.played_by)
                    ORDER BY p.played_by, r.created_at DESC, r.id DESC
                )
                SELECT user_id, name FROM by_role
                UNION ALL
                SELECT user_id, name FROM by_plays
            ) x
            WHERE NOT EXISTS (SELECT 1 FROM campaign_dms d
                              WHERE d.guild_id = g AND d.campaign_id = c
                                AND d.user_id = x.user_id)), '{}'::jsonb);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_transcript_characters(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
"""
