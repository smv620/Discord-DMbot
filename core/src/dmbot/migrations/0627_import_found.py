"""0627_import_found

"Add all" on the website for the names an import found (#5276, the import side of #3207).
"""

from __future__ import annotations

# `session_imports.found` keeps the ids of the names that session's name scan proposed, kind by
# kind, as `lore_uploads.found` does (0315): ids only, never a name or a word of the text, written
# when the scan ends. The queue door's `confirm-kinds` then accepts an import id as its target
# (a finished session of that import in this campaign), as it accepts a finished lore upload; the
# applier reads `found` across the import's sessions. The door text is read back as it is and each
# anchor must be found exactly once (as 0615). A new read door, `dmbot_web_import_waiting`, says
# how many names of each kind still wait, for a DM of the campaign only (NULL for anyone else):
# counts only, ideas left out (they need a look), as the upload's `waiting` is.
_QUEUE_SWAPS = (
    (
        "            IF act = 'confirm-kinds' AND NOT EXISTS (\n"
        "                   SELECT 1 FROM lore_uploads u\n"
        "                   WHERE u.guild_id = g AND u.campaign_id = c AND u.id = tgt\n"
        "                     AND u.state = 'done') THEN\n",
        "            IF act = 'confirm-kinds' AND NOT EXISTS (\n"
        "                   SELECT 1 FROM lore_uploads u\n"
        "                   WHERE u.guild_id = g AND u.campaign_id = c AND u.id = tgt\n"
        "                     AND u.state = 'done')\n"
        "               AND NOT EXISTS (\n"
        "                   SELECT 1 FROM session_imports i\n"
        "                   WHERE i.guild_id = g AND i.campaign_id = c AND i.import_id = tgt\n"
        "                     AND i.state = 'done') THEN\n",
        1,
    ),
)


def _dollar(text: str) -> str:
    return "$q$" + text + "$q$"


_STEPS = "".join(
    f"\n        def := pg_temp.swap_n(def, {_dollar(old)}, {_dollar(new)}, {n});"
    for old, new, n in _QUEUE_SWAPS
)

SQL = (
    r"""
    ALTER TABLE session_imports
        ADD COLUMN found JSONB NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(found) = 'object' AND pg_column_size(found) <= 120000);

    CREATE FUNCTION dmbot_web_import_waiting(g BIGINT, c TEXT, imp TEXT) RETURNS JSONB
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
            SELECT jsonb_object_agg(k.key, k.cnt)
            FROM (
                SELECT f.key, count(DISTINCT e.id) AS cnt
                FROM session_imports i
                CROSS JOIN LATERAL jsonb_each(i.found) f
                CROSS JOIN LATERAL jsonb_array_elements_text(
                    CASE WHEN jsonb_typeof(f.value) = 'array' THEN f.value
                         ELSE '[]'::jsonb END) x(id)
                JOIN memory_entities e
                  ON e.guild_id = g AND e.campaign_id = c AND e.id = x.id
                 AND e.status = 'proposed'
                WHERE i.guild_id = g AND i.campaign_id = c AND i.import_id = imp
                  AND f.key NOT IN ('idea', 'concept')
                GROUP BY f.key) k
            WHERE k.cnt > 0), '{}'::jsonb);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_import_waiting(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_import_waiting(BIGINT, TEXT, TEXT) FROM PUBLIC;

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
)

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_import_waiting", "dmbot_web_import_waiting(BIGINT, TEXT, TEXT)"),
)
