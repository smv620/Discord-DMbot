"""0441_wizard_members

Who a character's player can be in the start-up wizard's step 3 (#4204): this server's members by
their Discord name, those who played with this DM here before first.
"""

from __future__ import annotations

# The wizard runs before a campaign exists, so no campaign's `campaign_dms` can vouch for the
# caller; the door asks what the wizard's own doors ask (`dmbot_wizard_me`: the signed-in account,
# a server in the session's own list). It lists, for that one server only, the members the bot has
# seen (`member_directory`) who are recorded now (a consent row under the current wording, as the
# import page's member search does), by display name or username. "Played with you before" is read
# live from this server's campaigns the caller is a DM of (`memory_entities.played_by` on a
# confirmed entry): nothing new is stored, and **nothing is read from any other server**: a person
# who played with this DM only elsewhere is not marked (Supervisor, #4204: data never crosses
# servers). Only people the bot has seen here are offered, so a name is always one the DM can
# recognise. Names and ids only, at most 50, in the order: played before, then by name.
SQL = r"""
    CREATE FUNCTION dmbot_web_wizard_members(g BIGINT, terms INTEGER, q TEXT) RETURNS JSONB
    LANGUAGE plpgsql STABLE SECURITY DEFINER AS $fn$
    DECLARE
        me BIGINT := dmbot_wizard_me(g);
        needle TEXT := lower(btrim(COALESCE(q, '')));
    BEGIN
        IF me IS NULL THEN
            RETURN NULL;
        END IF;
        RETURN COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                       'userId', t.user_id::TEXT, 'name', t.name, 'before', t.before)
                       ORDER BY t.before DESC, lower(t.name), t.user_id)
            FROM (
                SELECT u.user_id,
                       (array_agg(u.name ORDER BY u.rank, lower(u.name)))[1] AS name,
                       bool_or(u.before) AS before
                FROM (
                    SELECT d.user_id,
                           CASE WHEN d.display_name <> '' THEN d.display_name
                                ELSE d.username END AS name,
                           1 AS rank,
                           EXISTS (SELECT 1 FROM memory_entities e
                                   JOIN campaign_dms m ON m.guild_id = e.guild_id
                                        AND m.campaign_id = e.campaign_id
                                   WHERE e.guild_id = g AND e.played_by = d.user_id
                                     AND e.status = 'confirmed' AND m.user_id = me) AS before
                    FROM member_directory d
                    WHERE d.guild_id = g AND (d.display_name <> '' OR d.username <> '')
                      AND EXISTS (SELECT 1 FROM consent x
                                  WHERE x.guild_id = g AND x.user_id = d.user_id
                                    AND x.terms_version = terms)
                      AND (needle = '' OR position(needle IN lower(d.display_name || ' '
                                                                  || d.username)) > 0)
                ) u
                GROUP BY u.user_id
                ORDER BY bool_or(u.before) DESC, lower((array_agg(u.name))[1]), u.user_id
                LIMIT 50) t), '[]'::jsonb);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_wizard_members(BIGINT, INTEGER, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_wizard_members(BIGINT, INTEGER, TEXT) FROM PUBLIC;
"""

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_wizard_members", "dmbot_web_wizard_members(BIGINT, INTEGER, TEXT)"),
)
