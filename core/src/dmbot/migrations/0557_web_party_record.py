"""0557_web_party_record

The website's party record (#4778 part a2, party knowledge slice 2a and 4a; Supervisor's ruling on
#4537): when each player character was in the party, "Has left the party" with its reason, and
"Back in the party", each one change with an Undo.
"""

from __future__ import annotations

# The DM's changes to who is in the party go on the queue door as the join question's did: filed
# under no name, no target, no flag. `party-leave` ("Has left the party") carries
# `<character> <why>` (the character's id, then one of left, retired, died, away); `party-back`
# ("Back in the party") the character's id; `party-undo` the change number the answer gave
# (digits). The bot runs the store's own `leave_party` and `back_in_party`, which check the
# character is a confirmed player character who is in (or out of) the party, and an Undo only of a
# change this DM's own done `party-leave` or `party-back` made (read again). The read door,
# `dmbot_web_party_record`, lists each confirmed player character with its stretches (from, to and
# why) and the number of the session its open stretch began at, as the card's line says it;
# DM only (NULL for anyone else). Ids, names, times and numbers, never a word of the story. The
# queue door's lists, shape check and the table's action list are read back as they are and each
# anchor must be found exactly once, so a drift stops the migration. No row is changed.
ACTIONS = ("party-leave", "party-back", "party-undo")
_ID = "[0-9a-f]{32}"
_PREV = "'clue-undo', 'lore-pdf-estimate', 'join-everyone', 'join-party', 'join-undo'"
_NEXT = _PREV + ", 'party-leave', 'party-back', 'party-undo'"

_LAST_SHAPE = "           OR (act = 'clue-undo' AND clean !~ '^[0-9]{1,15}$')\n"
_QUEUE_SWAPS = (
    (f"{_PREV})\n        THEN", f"{_NEXT})\n        THEN", 1),
    (
        _LAST_SHAPE + f"           OR (act = 'lore-pdf-estimate' AND clean !~ '^{_ID}$')\n"
        f"           OR (act IN ('join-everyone', 'join-party') AND clean !~ '^{_ID}$')\n"
        "           OR (act = 'join-undo' AND clean !~ '^[0-9]{1,15}$')\n",
        _LAST_SHAPE + f"           OR (act = 'lore-pdf-estimate' AND clean !~ '^{_ID}$')\n"
        f"           OR (act IN ('join-everyone', 'join-party') AND clean !~ '^{_ID}$')\n"
        "           OR (act = 'join-undo' AND clean !~ '^[0-9]{1,15}$')\n"
        f"           OR (act = 'party-leave' AND clean !~ '^{_ID} (left|retired|died|away)$')\n"
        f"           OR (act = 'party-back' AND clean !~ '^{_ID}$')\n"
        "           OR (act = 'party-undo' AND clean !~ '^[0-9]{1,15}$')\n",
        1,
    ),
    (
        f"{_PREV})\n               AND COALESCE(fl, false))",
        f"{_NEXT})\n               AND COALESCE(fl, false))",
        1,
    ),
    (f"{_PREV}) THEN", f"{_NEXT}) THEN", 1),
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
    -- The allowed actions, read back from the table as it is and one more added at the end.
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

    CREATE FUNCTION dmbot_web_party_record(g BIGINT, c TEXT) RETURNS JSONB
    LANGUAGE plpgsql STABLE SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        RETURN COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                       'id', e.id, 'name', e.name,
                       'inParty', (EXISTS (
                           SELECT 1 FROM memory_party_spans s0
                           WHERE s0.guild_id = e.guild_id AND s0.campaign_id = e.campaign_id
                             AND s0.entity_id = e.id AND s0.to_at IS NULL)
                           OR NOT EXISTS (
                           SELECT 1 FROM memory_party_spans s1
                           WHERE s1.guild_id = e.guild_id AND s1.campaign_id = e.campaign_id
                             AND s1.entity_id = e.id)),
                       'spans', COALESCE((
                           SELECT jsonb_agg(jsonb_build_object(
                                      'from', s.from_at, 'to', s.to_at, 'ended', s.ended,
                                      -- null when a stretch began at a moment that is no
                                      -- session's start (a character added mid-campaign)
                                      'session', (SELECT n.number FROM (
                                          SELECT t.started_at, row_number()
                                              OVER (ORDER BY t.started_at, t.id) AS number
                                          FROM transcript_sessions t
                                          WHERE t.guild_id = g AND t.campaign_id = c) n
                                          WHERE n.started_at = s.from_at LIMIT 1))
                                  ORDER BY s.from_at, s.id)
                           FROM memory_party_spans s
                           WHERE s.guild_id = e.guild_id AND s.campaign_id = e.campaign_id
                             AND s.entity_id = e.id), '[]'::jsonb))
                   ORDER BY lower(e.name), e.id)
            FROM memory_entities e
            WHERE e.guild_id = g AND e.campaign_id = c AND e.type = 'character'
              AND e.role = 'player_character' AND e.status = 'confirmed'
              -- merged names are not characters any more (the store's own scope hides them)
              AND e.merged_into IS NULL), '[]'::jsonb);
    END
    $fn$;

    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_party_record(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_party_record(BIGINT, TEXT) FROM PUBLIC;
    """
)

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_party_record", "dmbot_web_party_record(BIGINT, TEXT)"),
)
