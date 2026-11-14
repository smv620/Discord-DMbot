"""0348_question_counts_door

The website's read of the bot's count of waiting questions (#3438).
"""

from __future__ import annotations

# The Questions page lists, for each campaign the signed-in person is a game master of, how many
# questions of each kind wait. The bot is the one place that counts (`question_counts`, 0338), so
# this door only reads that table and the page cannot disagree with Discord. One server at a time,
# as the other list doors (`dmbot_web_campaign_facts`): the caller connects scoped to the server,
# which must be in the session's own list. A campaign appears only to its game masters (the caller
# is in `campaign_dms`), with no row for a kind that has none and `countedAt` empty before the bot
# has counted. Counts, times and the campaign's name only: never a word of any question. Nothing
# is written.
QUESTION_COUNTS_DOOR = r"""
    CREATE FUNCTION dmbot_web_question_counts(g BIGINT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        RETURN COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                       'id', c.id, 'name', c.name,
                       'countedAt', (SELECT max(q.counted_at) FROM question_counts q
                                     WHERE q.guild_id = g AND q.campaign_id = c.id),
                       'kinds', COALESCE((
                           SELECT jsonb_agg(jsonb_build_object(
                                      'kind', q.kind, 'count', q.n, 'oldest', q.oldest)
                                  ORDER BY q.kind)
                           FROM question_counts q
                           WHERE q.guild_id = g AND q.campaign_id = c.id AND q.n > 0),
                           '[]'::jsonb))
                   ORDER BY c.name, c.id)
            FROM campaigns c
            WHERE c.guild_id = g
              AND EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c.id
                            AND d.user_id = dmbot_current_user())),
            '[]'::jsonb);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_question_counts(BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_question_counts(BIGINT) FROM PUBLIC;
"""

SQL = QUESTION_COUNTS_DOOR

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_question_counts", "dmbot_web_question_counts(BIGINT)"),
)
