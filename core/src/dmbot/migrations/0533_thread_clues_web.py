"""0533_thread_clues_web

The website lists and changes a story thread's clues (#4625, the web half of quests slice 6a): a
DM-only read door, and four more actions on the queue door, level with Discord's 🔎 Clues screen.
"""

from __future__ import annotations

# `dmbot_web_thread_clues(g, c, t)`: NULL for anyone who is not a DM of the campaign (or a server
# the caller is not in), as 0519's door; else `{status: 'not_found'}` for an entry that is not a
# kept entry of the campaign, or `{status: 'ok', clues}`: the clues tied to the thread (a live,
# kept `clue_to` connection from a kept clue entry), each `{id, name, falseLead, found}`, A to Z,
# once each. `found` is the website's own reading of "a player character knows it", worked out here
# from the who-knows rows and nothing else (Supervisor's ruling on #4625: the gate is not copied
# into SQL): a row on the clue's own entry held by a current player character (a confirmed
# `character` with the role `player_character`), or by the everyone point of view (common
# knowledge) while the players are shown the clue (confirmed, and not hidden, limited to an
# audience or unnamed) and the campaign has a player character at all (the gate's "any player
# character" has no one to ask without one). A row a hide put to sleep grants nothing. A row held
# by the party entity or by a former player character, a common row on a hidden clue and a row on
# a clue that is not kept never make a clue found. The bot's gate knows more ways (a group the
# character belongs to, a property, a connection or another name of the clue that the character
# knows), so on those the door says "not found yet" where Discord says "found", and never the other way round
# (`test_web_thread_clues` holds both together, each difference written down). The count's words
# are `thread_clues.found_text`'s, said by the web module. A player's reading never comes from
# here: the door answers only a DM, and `clue_to` is the DM's alone. No entry id leaves it but the
# clue's own, which the page needs to change it. Read only.
#
# The four actions go on the queue door as 0519's did, filed under no name with no target and no
# flag; the door checks only their shape, the bot applies each through the store call Discord's
# screen makes (`tie_clue`, `set_clue_word`, `untie_clue`, `undo`), one change with one Undo, and
# checks the rest again (the thread, the clue, the DM):
# - `clue-add`: `<thread id> <the clue's name>` (1 to 100 characters: the campaign's clue of that
#   name, else a new clue, hidden from the players until a character finds it);
# - `clue-word`: `<thread id> <clue id> false|true` (mark it a false lead, or not);
# - `clue-off`: `<thread id> <clue id>` (take it off this thread);
# - `clue-undo`: the change number one of these made, checked by the bot against the DM's own done
#   clue actions.
# A clue's name may be longer than a hundred characters with the thread id in front, so `clue-add`
# gets the long text limit, in the table's check and in the door's own list. The queue door's
# lists, shape check and text limit are read back from the database as they are and each anchor
# must be found exactly once, so a drift stops the migration. No row is changed.


ACTIONS = ("clue-add", "clue-word", "clue-off", "clue-undo")
_ID = "[0-9a-f]{32}"
_IDS = f"{_ID}(,{_ID}){{0,24}}"

_SHAPES = (
    f"           OR (act = 'clue-add' AND clean !~ '^{_ID} [^ ].{{0,99}}$')\n"
    f"           OR (act = 'clue-word' AND clean !~ '^{_ID} {_ID} (false|true)$')\n"
    f"           OR (act = 'clue-off' AND clean !~ '^{_ID} {_ID}$')\n"
    f"           OR (act = 'clue-undo' AND clean !~ '^[0-9]{{1,15}}$')\n"
)
_LAST_SHAPE = (
    f"           OR (act = 'progress-undo' AND clean !~ '^(keep [0-9]{{1,15}}|none {_IDS})$')\n"
)
_LIST = (
    "'part-add', 'part-fix', 'part-state', 'part-show', 'part-remove', 'branch-move',"
    " 'branch-not', 'part-undo', 'part-date', 'lang-add', 'lang-remove', 'lang-check',"
    " 'lang-undo', 'secret-keep', 'secret-make', 'secret-undo', 'progress-keep',"
    " 'progress-none', 'progress-undo'"
)
_NEW_LIST = _LIST + ", " + ", ".join(f"'{a}'" for a in ACTIONS)

_QUEUE_SWAPS = (
    # the allowed actions (the list that ends in THEN)
    (f"{_LIST})\n        THEN", f"{_NEW_LIST})\n        THEN", 1),
    # their shape, after 0528's
    (_LAST_SHAPE, _LAST_SHAPE + _SHAPES, 1),
    # no flag
    (
        f"{_LIST})\n               AND COALESCE(fl, false))",
        f"{_NEW_LIST})\n               AND COALESCE(fl, false))",
        1,
    ),
    # filed under no name
    (f"{_LIST}) THEN", f"{_NEW_LIST}) THEN", 1),
    # the long text limit list in the door (the function's own check, beside the table's)
    (
        "'maps-add', 'thread-keep', 'part-add', 'part-fix', 'progress-keep', 'progress-none',"
        " 'progress-undo')",
        "'maps-add', 'thread-keep', 'part-add', 'part-fix', 'progress-keep', 'progress-none',"
        " 'progress-undo', 'clue-add')",
        1,
    ),
)


def _dollar(text: str) -> str:
    return "$q$" + text + "$q$"


_STEPS = "".join(
    f"\n        def := pg_temp.swap_n(def, {_dollar(old)}, {_dollar(new)}, {n});"
    for old, new, n in _QUEUE_SWAPS
)

# Quotes doubled: spliced into the single-quoted EXECUTE string below.
_ADDED = ", ".join(f"''{a}''::text" for a in ACTIONS)

SQL = (
    r"""
    -- The allowed actions, read back from the table as it is and four more added at the end.
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
            || replace(d, '])', ', """
    + _ADDED
    + r"""])');
    END
    $do$;


    -- The text limit, read back as it is: a `clue-add` text gets the long limit `part-add` has.
    DO $do$
    DECLARE
        d text;
        fresh text;
        one text := ' WHEN (action = ''part-add''::text) THEN 1700';
        more text := ' WHEN (action = ''clue-add''::text) THEN 1700';
    BEGIN
        SELECT pg_get_constraintdef(oid) INTO d FROM pg_constraint
            WHERE conname = 'name_actions_text_check' AND conrelid = 'name_actions'::regclass;
        IF (length(d) - length(replace(d, one, ''))) <> length(one) THEN
            RAISE EXCEPTION 'the text limit did not have the expected shape';
        END IF;
        fresh := replace(d, one, more || one);
        ALTER TABLE name_actions DROP CONSTRAINT name_actions_text_check;
        EXECUTE 'ALTER TABLE name_actions ADD CONSTRAINT name_actions_text_check ' || fresh;
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

    CREATE FUNCTION dmbot_web_thread_clues(g BIGINT, c TEXT, t TEXT) RETURNS JSONB
    LANGUAGE plpgsql STABLE SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        IF NOT EXISTS (SELECT 1 FROM memory_entities x
                       WHERE x.guild_id = g AND x.campaign_id = c AND x.id = t
                         AND x.status = 'confirmed') THEN
            RETURN jsonb_build_object('status', 'not_found');
        END IF;
        RETURN jsonb_build_object('status', 'ok', 'clues', COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                       'id', l.clue_id, 'name', l.name, 'falseLead', l.false_lead,
                       'found', l.found) ORDER BY lower(l.name), l.clue_id)
            FROM (
                SELECT DISTINCT ON (e.id) e.id AS clue_id, e.name,
                       lower(btrim(COALESCE(r.detail, ''))) = 'false lead' AS false_lead,
                       EXISTS (
                           SELECT 1
                           FROM (SELECT * FROM memory_knowers
                                 WHERE cardinality(hidden_by) = 0) k
                           JOIN memory_perspectives p ON p.id = k.perspective_id
                                                     AND p.guild_id = k.guild_id
                                                     AND p.campaign_id = k.campaign_id
                           WHERE k.guild_id = g AND k.campaign_id = c
                             AND k.about_entity_id = e.id
                             AND ((p.entity_id IS NOT NULL AND EXISTS (
                                      SELECT 1 FROM memory_entities pc
                                      WHERE pc.guild_id = g AND pc.campaign_id = c
                                        AND pc.id = p.entity_id AND pc.type = 'character'
                                        AND pc.role = 'player_character'
                                        AND pc.status = 'confirmed'))
                                  OR (p.kind = 'everyone'
                                      AND NOT (e.hidden_from_players OR e.unnamed
                                               OR e.audience_limited)
                                      AND EXISTS (
                                          SELECT 1 FROM memory_entities pc
                                          WHERE pc.guild_id = g AND pc.campaign_id = c
                                            AND pc.type = 'character'
                                            AND pc.role = 'player_character'
                                            AND pc.status = 'confirmed')))) AS found
                FROM memory_relations r
                JOIN memory_entities e ON e.guild_id = r.guild_id
                                      AND e.campaign_id = r.campaign_id AND e.id = r.subject_id
                WHERE r.guild_id = g AND r.campaign_id = c AND r.predicate = 'clue_to'
                  AND r.status = 'confirmed' AND r.to_session_at IS NULL AND r.object_id = t
                  AND e.status = 'confirmed'
                ORDER BY e.id, r.id) l), '[]'::jsonb));
    END
    $fn$;

    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_thread_clues(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_thread_clues(BIGINT, TEXT, TEXT) FROM PUBLIC;
    """
)

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_thread_clues", "dmbot_web_thread_clues(BIGINT, TEXT, TEXT)"),
)
