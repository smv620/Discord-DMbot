"""0284_web_node_quests

Each name's quests on the Story Graph, for the "Cluster by quest" layout (#2992).
"""

from __future__ import annotations

from dmbot.migrations._shared import _swaps

# "Cluster by quest" gathers names by the story thread (quest, mystery, hook, goal, promise) each is
# connected to directly. A quest can sit outside the steps the page asked for, so the door says it
# for each name it sends: `quests`, each `{id, name, links}` (how many connections join the name to
# that quest), the most-connected first. It is worked out from the viewer's own view only: a DM in
# Full View gets it; anyone else (a player, a player who peeks without being a DM, a DM in Players
# View) gets no `quests` key at all and so never learns of a quest or of a name's tie to one (the
# players' graph holds no story thread; #2989). Confirmed entries only, never a merged-away one. The
# door is read back from the database as it is and widened, its anchor found exactly once (a drift
# stops the migration), as 0275 and 0281 do. No table changes, no rows change.
_NODE_QUEST_SWAPS = (
    (
        "'secret', CASE WHEN sees_all THEN EXISTS (",
        "'quests', CASE WHEN sees_all THEN COALESCE((\n"
        "                        SELECT jsonb_agg(jsonb_build_object('id', t.id, 'name', t.name,\n"
        "                                                            'links', t.links)\n"
        "                                         ORDER BY t.links DESC, lower(t.name), t.id)\n"
        "                        FROM (\n"
        "                            SELECT q.id, q.name, count(*) AS links\n"
        "                            FROM memory_relations r\n"
        "                            JOIN memory_entities q\n"
        "                              ON q.guild_id = g AND q.campaign_id = c\n"
        "                             AND q.id = CASE WHEN r.subject_id = e.id THEN r.object_id\n"
        "                                             ELSE r.subject_id END\n"
        "                             AND q.type IN ('thread', 'quest', 'mystery', 'hook', 'goal',\n"
        "                                            'promise')\n"
        "                             AND q.status = 'confirmed' AND q.merged_into IS NULL\n"
        "                            WHERE r.guild_id = g AND r.campaign_id = c\n"
        "                              AND r.status = 'confirmed'\n"
        "                              AND (r.subject_id = e.id OR r.object_id = e.id)\n"
        "                            GROUP BY q.id, q.name) t), '[]'::jsonb) END,\n"
        "                    'secret', CASE WHEN sees_all THEN EXISTS (",
    ),
)

WEB_NODE_QUESTS = (
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
              AND p.proname = 'dmbot_web_relationships';
"""
    + _swaps("def", _NODE_QUEST_SWAPS)
    + r"""
        EXECUTE def;
    END
    $do$;
    """
)

SQL = WEB_NODE_QUESTS
