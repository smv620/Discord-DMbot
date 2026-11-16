"""0517_thread_keep_lines

A story-thread suggestion on the website shows what is in it, and a keep says which lines stay on
(#4501, quests slice 3, web half): a DM-only read door for the lines, and `thread-keep`'s text takes
their positions.
"""

from __future__ import annotations

# `dmbot_web_thread_lines(g, c)`: NULL for anyone who is not a DM of the campaign (or a server the
# caller is not in), as 0505's door; else, for each waiting suggestion, its `lines` in the card's
# order (the parts DMbot read, then its branches; a line's position in the list is what a keep
# names, `Waiting.lines`). A part is its kind, words, state, when it is due, its terms, its limit
# and the giver by name; a branch is its name, state and its people by their part word and name. A
# name carries whether the entry is still waiting to be kept (`waiting`), as Discord's card says
# "not kept yet". Entry ids never leave the door, and an entry that is gone is left out. This is
# the third reader of the table on purpose (the DM's own card only): no count, no names list, no
# players' view. Read only.
#
# `thread-keep` (0505) carried `<id> <positions of the people left on or -> [<name>]`. It may now
# carry the positions of the lines left on, `+<n,n,...>` or `+-` for none, between the people and
# the name; without it the keep keeps none of the lines, as before, so an older page keeps working.
# The queue door's shape check is read back from the database as it is and its anchor must be found
# exactly once, so a drift stops the migration. No row is changed.
_ID = "[0-9a-f]{32}"
_OLD = (
    f"OR (act = 'thread-keep' AND clean !~"
    f" '^{_ID} (-|[0-9]{{1,2}}(,[0-9]{{1,2}}){{0,24}})( [^ ].{{0,99}})?$')"
)
_NEW = (
    f"OR (act = 'thread-keep' AND clean !~"
    f" '^{_ID} (-|[0-9]{{1,2}}(,[0-9]{{1,2}}){{0,24}})"
    r"( \+(-|[0-9]{1,2}(,[0-9]{1,2}){0,49}))?"
    f"( [^ ].{{0,99}})?$')"
)


def _dollar(text: str) -> str:
    return "$q$" + text + "$q$"


SQL = (
    r"""
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
        def := pg_temp.swap_n(def, """
    + _dollar(_OLD)
    + ", "
    + _dollar(_NEW)
    + r""", 1);
        EXECUTE def;
    END
    $do$;

    CREATE FUNCTION dmbot_web_thread_lines(g BIGINT, c TEXT) RETURNS JSONB
    LANGUAGE plpgsql STABLE SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object(
            'status', 'ok',
            'suggestions', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                           'id', s.id,
                           'lines', COALESCE((
                               SELECT jsonb_agg(l.line ORDER BY l.pos)
                               FROM (
                                   SELECT r.ord - 1 AS pos, jsonb_build_object(
                                       'branch', false, 'kind', r.v ->> 'kind',
                                       'words', r.v ->> 'words', 'state', r.v ->> 'state',
                                       'due', COALESCE(r.v ->> 'due', ''),
                                       'terms', COALESCE(r.v ->> 'terms', ''),
                                       'limit', COALESCE(r.v ->> 'limit_kind', ''),
                                       'who', (SELECT jsonb_build_object(
                                                   'name', e.name,
                                                   'waiting', e.status <> 'confirmed')
                                               FROM memory_entities e
                                               WHERE e.guild_id = g AND e.campaign_id = c
                                                 AND e.id = r.v ->> 'who')) AS line
                                   FROM jsonb_array_elements(
                                            COALESCE(s.extra -> 'reads', '[]'::jsonb))
                                        WITH ORDINALITY AS r(v, ord)
                                   UNION ALL
                                   SELECT jsonb_array_length(
                                              COALESCE(s.extra -> 'reads', '[]'::jsonb))
                                          + b.ord - 1, jsonb_build_object(
                                       'branch', true, 'name', b.v ->> 'name',
                                       'state', b.v ->> 'state',
                                       'people', COALESCE((
                                           SELECT jsonb_agg(jsonb_build_object(
                                                      'part', q.p ->> 0, 'name', e.name,
                                                      'waiting', e.status <> 'confirmed')
                                                  ORDER BY q.o)
                                           FROM jsonb_array_elements(
                                                    COALESCE(b.v -> 'parts', '[]'::jsonb))
                                                WITH ORDINALITY AS q(p, o)
                                           JOIN memory_entities e
                                             ON e.guild_id = g AND e.campaign_id = c
                                            AND e.id = q.p ->> 1), '[]'::jsonb))
                                   FROM jsonb_array_elements(
                                            COALESCE(s.extra -> 'branches', '[]'::jsonb))
                                        WITH ORDINALITY AS b(v, ord)) l), '[]'::jsonb))
                           ORDER BY s.created_at, s.id)
                FROM thread_suggestions s
                WHERE s.guild_id = g AND s.campaign_id = c AND s.status = 'waiting'),
                '[]'::jsonb));
    END
    $fn$;

    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_thread_lines(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_thread_lines(BIGINT, TEXT) FROM PUBLIC;
    """
)

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_thread_lines", "dmbot_web_thread_lines(BIGINT, TEXT)"),
)
