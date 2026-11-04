"""0335_name_answers_split

Split a list of names, or say it is a group, on a recap's names to check (#3406, #3383).
"""

from __future__ import annotations

import importlib

from dmbot.migrations._shared import _swap

# The page's three new answers to a waiting name (Discord's "These are separate names", "Yes, that
# group" and "They're a group", #3403, #3404) and Undo for them. As every answer, the page only
# queues them and the bot applies them through the same store functions as Discord's buttons:
#   split       no arguments            the waiting list becomes one waiting name for each part
#   join_group  target = the group's id the list is a group the campaign knows
#   make_group  text = the group's name the list is a group the DM names
#   undo        target = the batch      takes the last of those back (any of the three)
# A finished one of those three stays as a row with state 'done', its `batch` (Undo's key), its
# `note` (counts and, for a group, its name: "Split into 3 names.") and, for a split, `undo_name`
# (the list as it was), until the hour's sweep or its Undo. The page reads the last row for a name
# through `dmbot_name_answer_read`. `undo` is for a name that is not waiting any more (a split
# turns the list down), so its door check is "a done row of this name has that batch", not "still
# waiting".
# A group's hints are worked out in the web layer by the same Python the store uses, from the small
# set of facts `dmbot_name_group_info` gives for the parts' spellings: only the DM sees any of it.
# The answer door is rebuilt from 0242's text by guarded replacement (a drift stops the migration),
# with its pinned search path; no row is changed.
_OLD = importlib.import_module("dmbot.migrations.0242_name_change_words")

_LOCK = (
    "        PERFORM pg_advisory_xact_lock(hashtextextended('name_answer:' || g::TEXT || c, 0));"
)
_ANSWER_DOOR = _swap(
    _OLD._CHECK_ANSWER_DOOR,
    (
        (
            "'respell', 'not_a_name', 'explain', 'change_words')",
            "'respell', 'not_a_name', 'explain', 'change_words',\n"
            "                          'split', 'join_group', 'make_group', 'undo')",
        ),
        (
            "OR (act IN ('confirm', 'same', 'not_a_name') AND clean <> '')",
            "OR (act IN ('confirm', 'same', 'not_a_name', 'split', 'join_group', 'undo')\n"
            "               AND clean <> '')\n"
            "           OR (act = 'make_group' AND char_length(clean) NOT BETWEEN 1 AND 120)",
        ),
        (
            "OR (act NOT IN ('same', 'change_words') AND COALESCE(tgt, '') <> '')",
            "OR (act NOT IN ('same', 'change_words', 'join_group', 'undo')\n"
            "               AND COALESCE(tgt, '') <> '')\n"
            "           OR (act = 'join_group' AND (tgt IS NULL OR tgt !~ '^[0-9a-f]{32}$'))\n"
            "           OR (act = 'undo' AND (tgt IS NULL OR tgt !~ '^[0-9]{1,18}$'))",
        ),
        (
            _LOCK,
            "        -- The group a list is said to be is a confirmed group (the store checks).\n"
            "        IF act = 'join_group' AND NOT EXISTS (\n"
            "               SELECT 1 FROM memory_entities t\n"
            "               WHERE t.guild_id = g AND t.campaign_id = c AND t.id = tgt\n"
            "                 AND t.type = 'faction' AND t.status = 'confirmed') THEN\n"
            "            RETURN jsonb_build_object('status', 'bad_target');\n"
            "        END IF;\n"
            "        " + _LOCK.lstrip(),
        ),
        (
            "        IF NOT EXISTS (SELECT 1 FROM recap_name_checks k",
            "        IF act <> 'undo' AND NOT EXISTS (SELECT 1 FROM recap_name_checks k",
        ),
        (
            "        IF EXISTS (SELECT 1 FROM name_answers a\n"
            "                   WHERE a.guild_id = g AND a.campaign_id = c AND a.entity_id = ent\n"
            "                     AND a.state IN ('waiting', 'working')) THEN\n"
            "            RETURN jsonb_build_object('status', 'busy');",
            "        -- Undo takes back a finished split or group of this name, by its batch.\n"
            "        IF act = 'undo' AND NOT EXISTS (\n"
            "               SELECT 1 FROM name_answers u\n"
            "               JOIN recap_name_checks k\n"
            "                 ON k.guild_id = u.guild_id AND k.campaign_id = u.campaign_id\n"
            "                AND k.entity_id = u.entity_id AND k.session_at = at_\n"
            "               WHERE u.guild_id = g AND u.campaign_id = c AND u.entity_id = ent\n"
            "                 AND u.state = 'done' AND u.batch = tgt::BIGINT\n"
            "                 AND u.action IN ('split', 'join_group', 'make_group')) THEN\n"
            "            RETURN jsonb_build_object('status', 'not_found');\n"
            "        END IF;\n"
            "        IF EXISTS (SELECT 1 FROM name_answers a\n"
            "                   WHERE a.guild_id = g AND a.campaign_id = c AND a.entity_id = ent\n"
            "                     AND a.state IN ('waiting', 'working')) THEN\n"
            "            RETURN jsonb_build_object('status', 'busy');",
        ),
    ),
)

NAME_ANSWERS_SPLIT = (
    r"""
    ALTER TABLE name_answers
        ADD COLUMN batch      BIGINT,
        ADD COLUMN undo_name  TEXT CHECK (undo_name IS NULL OR char_length(undo_name) <= 300),
        ADD COLUMN note       TEXT NOT NULL DEFAULT '' CHECK (char_length(note) <= 200);
    ALTER TABLE name_answers DROP CONSTRAINT name_answers_action_check;
    ALTER TABLE name_answers ADD CONSTRAINT name_answers_action_check CHECK (action IN
        ('confirm', 'same', 'respell', 'not_a_name', 'explain', 'change_words',
         'split', 'join_group', 'make_group', 'undo'));
    """
    + _ANSWER_DOOR
    + r"""
    -- The last answer to one of this recap's names, for the page to follow: its state, its note,
    -- the bot's refusal word and the batch to take it back by. For a DM of the campaign only
    -- (anyone else gets NULL); NULL too when the name has no row (an answer that finished and was
    -- deleted, or an undone one), which the page reads as done. The DM's own words and counts only.
    CREATE FUNCTION dmbot_name_answer_read(g BIGINT, c TEXT, at_ BIGINT, ent TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        RETURN (
            SELECT jsonb_build_object('state', a.state, 'note', a.note, 'error', a.error,
                                      'batch', a.batch)
            FROM name_answers a
            JOIN recap_name_checks k
              ON k.guild_id = a.guild_id AND k.campaign_id = a.campaign_id
             AND k.entity_id = a.entity_id AND k.session_at = at_
            WHERE a.guild_id = g AND a.campaign_id = c AND a.entity_id = ent
            ORDER BY a.created_at DESC, a.id DESC
            LIMIT 1);
    END
    $fn$;

    -- What the page needs to say whether a waiting list is a known group (Discord's "Yes, that
    -- group"): for the spellings of the listed parts (their lookup keys), the entry each belongs
    -- to, and the confirmed groups those entries are in. The same facts `MemoryStore.group_hints`
    -- reads (every spelling not turned down, a confirmed membership that is neither secret nor
    -- unrevealed, a confirmed group that is named and not hidden from the players), for a DM
    -- only; a guess is not a fact the hint may state (the door rule: connections are read
    -- confirmed only, #3417); the web layer picks the
    -- hints with the store's own rule, so the two cannot drift. At most 12 keys; ids, names and
    -- keys only.
    CREATE FUNCTION dmbot_name_group_info(g BIGINT, c TEXT, keys TEXT[]) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        IF keys IS NULL OR cardinality(keys) > 12 THEN
            RETURN jsonb_build_object('aliases', '[]'::jsonb, 'members', '[]'::jsonb,
                                      'groups', '[]'::jsonb);
        END IF;
        RETURN jsonb_build_object(
            'aliases', COALESCE((
                SELECT jsonb_agg(jsonb_build_object('key', x.key, 'entity', x.entity_id)
                                 ORDER BY x.key, x.created_at, x.id)
                FROM memory_aliases x
                WHERE x.guild_id = g AND x.campaign_id = c AND x.status <> 'rejected'
                  AND x.key = ANY (keys)), '[]'::jsonb),
            'members', COALESCE((
                SELECT jsonb_agg(jsonb_build_object('subject', r.subject_id, 'group', r.object_id))
                FROM memory_relations r
                WHERE r.guild_id = g AND r.campaign_id = c AND r.predicate = 'member_of'
                  AND r.status = 'confirmed' AND NOT r.secret AND NOT r.unrevealed
                  AND r.subject_id IN (SELECT x.entity_id FROM memory_aliases x
                                       WHERE x.guild_id = g AND x.campaign_id = c
                                         AND x.status <> 'rejected' AND x.key = ANY (keys))),
                '[]'::jsonb),
            'groups', COALESCE((
                SELECT jsonb_agg(jsonb_build_object('id', e.id, 'name', e.name)
                                 ORDER BY lower(e.name), e.id)
                FROM memory_entities e
                WHERE e.guild_id = g AND e.campaign_id = c AND e.type = 'faction'
                  AND e.status = 'confirmed' AND NOT e.hidden_from_players AND NOT e.unnamed
                  AND e.id IN (SELECT r.object_id FROM memory_relations r
                               WHERE r.guild_id = g AND r.campaign_id = c
                                 AND r.predicate = 'member_of'
                                 AND r.status = 'confirmed'
                                 AND NOT r.secret AND NOT r.unrevealed
                                 AND r.subject_id IN (SELECT x.entity_id FROM memory_aliases x
                                                      WHERE x.guild_id = g AND x.campaign_id = c
                                                        AND x.status <> 'rejected'
                                                        AND x.key = ANY (keys)))),
                '[]'::jsonb));
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_name_answer_add('
            'BIGINT, TEXT, BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_name_answer_read(BIGINT, TEXT, BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_name_group_info(BIGINT, TEXT, TEXT[])'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_name_answer_read(BIGINT, TEXT, BIGINT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_name_group_info(BIGINT, TEXT, TEXT[]) FROM PUBLIC;
    """
)

SQL = NAME_ANSWERS_SPLIT

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_name_answer_read", "dmbot_name_answer_read(BIGINT, TEXT, BIGINT, TEXT)"),
    ("dmbot_name_group_info", "dmbot_name_group_info(BIGINT, TEXT, TEXT[])"),
)
