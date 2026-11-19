"""0559_web_group_mask

The website's knowers door says which statements a group holds that the players are not shown, and
every confirmed player character (#4807; Supervisor's ruling on #4806, party knowledge slice 5a).
"""

from __future__ import annotations

# On Discord a group's who-knows row (a faction's, say) reaches its members, except that a hide is
# not given away through the group: a row a group holds on a statement that is hidden from the
# players or not yet confirmed (on the statement itself or on an entry it names) reaches no player
# character, a character who left the party included. An NPC member still gets it and the group's
# own row stays (`gate._not_shown`'s second set, `gate.Statements.dark` and `.players`). A limited
# audience, a secret or not-yet-revealed connection and a secret other name are not hides: they are
# how the DM gives something to some, a group among them.
# The website builds the same gate input from this door, which sent neither list, so a faction's
# row on a hidden statement still reached its player characters there. Now `dark` is the list of
# (kind, statement id) pairs a group holds that are hidden by that rule, worked out from the same
# columns the bot reads, and `allPlayers` is every confirmed player character in the party or not
# (`party_record.player_characters`; `playerCharacters` is the current party only). A group is the
# object of a confirmed `member_of` connection and not the party itself, as in `gate.load`.
# Counts and ids only; no table changes, no rows change; the door is read back as it is and its
# anchor found exactly once (a drift stops the migration), as 0553 does.
_ANCHOR = "'liveEntries', COALESCE(("


def _out(entry: str) -> str:
    """`entry` (a SQL id expression) is an entry the players are not shown as a hide: not
    confirmed, or hidden from them (`entry_out`), or a person the story never named (0264: every
    guard against `hidden_from_players` keeps the unnamed out too). Limited and unrevealed are not
    hides."""
    return (
        "NOT EXISTS (SELECT 1 FROM memory_entities he WHERE he.guild_id = g "
        f"AND he.campaign_id = c AND he.id = {entry} AND he.status = 'confirmed' "
        "AND NOT he.hidden_from_players AND NOT he.unnamed)"
    )


_HIDDEN = (
    f"({_out('k.about_entity_id')} AND k.about_entity_id IS NOT NULL) "
    "OR (k.state_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM memory_states hs "
    "WHERE hs.guild_id = g AND hs.campaign_id = c AND hs.id = k.state_id "
    f"AND NOT {_out('hs.entity_id')})) "
    "OR (k.relation_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM memory_relations hr "
    "WHERE hr.guild_id = g AND hr.campaign_id = c AND hr.id = k.relation_id "
    f"AND hr.status = 'confirmed' AND NOT {_out('hr.subject_id')} "
    f"AND NOT {_out('hr.object_id')})) "
    "OR (k.alias_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM memory_aliases ha "
    "WHERE ha.guild_id = g AND ha.campaign_id = c AND ha.id = k.alias_id "
    f"AND ha.status <> 'rejected' AND NOT {_out('ha.entity_id')} "
    f"AND (ha.used_by IS NULL OR NOT {_out('ha.used_by')})))"
)

_NEW = (
    "-- the statements a group holds that the players are not shown (hidden or not yet\n"
    "            -- confirmed, on it or an entry it names): a group's row on one reaches no\n"
    "            -- player character, as `gate.Statements.dark` says\n"
    "            'dark', COALESCE((\n"
    "                SELECT jsonb_agg(DISTINCT jsonb_build_array(\n"
    "                           CASE WHEN k.about_entity_id IS NOT NULL THEN 'entry'\n"
    "                                WHEN k.state_id IS NOT NULL THEN 'property'\n"
    "                                WHEN k.relation_id IS NOT NULL THEN 'connection'\n"
    "                                ELSE 'alias' END,\n"
    "                           COALESCE(k.about_entity_id, k.state_id, k.relation_id,\n"
    "                                    k.alias_id)))\n"
    "                FROM (SELECT * FROM memory_knowers WHERE cardinality(hidden_by) = 0) k\n"
    "                JOIN memory_perspectives p ON p.id = k.perspective_id\n"
    "                WHERE k.guild_id = g AND k.campaign_id = c AND p.entity_id IS NOT NULL\n"
    "                  AND EXISTS (SELECT 1 FROM memory_relations gr\n"
    "                              WHERE gr.guild_id = g AND gr.campaign_id = c\n"
    "                                AND gr.predicate = 'member_of' AND gr.status = 'confirmed'\n"
    "                                AND gr.object_id = p.entity_id\n"
    "                                AND NOT EXISTS (SELECT 1 FROM memory_entities pe\n"
    "                                                WHERE pe.guild_id = g AND pe.campaign_id = c\n"
    "                                                  AND pe.id = gr.object_id AND pe.party))\n"
    f"                  AND ({_HIDDEN})), '[]'::jsonb),\n"
    "            -- every confirmed player character, in the party or not (`playerCharacters` is\n"
    "            -- the party now)\n"
    "            'allPlayers', COALESCE((\n"
    "                SELECT jsonb_agg(x.id ORDER BY x.id) FROM memory_entities x\n"
    "                WHERE x.guild_id = g AND x.campaign_id = c AND x.type = 'character'\n"
    "                  AND x.role = 'player_character' AND x.status = 'confirmed'),\n"
    "                '[]'::jsonb),\n            "
)


def _dollar(text: str) -> str:
    return "$q$" + text + "$q$"


SQL = (
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
              AND p.proname = 'dmbot_web_knowers_data';
        def := pg_temp.swap_once(def, """
    + _dollar(_ANCHOR)
    + ", "
    + _dollar(_NEW + _ANCHOR)
    + r""");
        EXECUTE def;
    END
    $do$;
    """
)

ISOLATED_TABLES: tuple[str, ...] = ()
WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = ()
