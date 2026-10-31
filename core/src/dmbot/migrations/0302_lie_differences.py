"""0302_lie_differences

A lie the party got wrong, in the after-session report (#3091, part 2).
"""

from __future__ import annotations

import importlib

# The report door is 0249's function with one more word on each difference. It is built from that
# file's own text, so the two cannot drift apart; each replacement is asserted, so a change to the
# original that moves the spot fails here instead of quietly dropping the new word.
_OLD = importlib.import_module("dmbot.migrations.0249_report_read_door").REPORT_READ_DOOR
_TRUTH_WORD = "'truth', CASE WHEN m.truth_relation_id IS NULL"
_LIAR = r"""'liar', CASE WHEN m.kind = 'lie' THEN (
                                   SELECT x.name FROM memory_beliefs lb
                                   JOIN memory_evidence le
                                     ON le.guild_id = g AND le.campaign_id = c
                                    AND le.belief_id = lb.id AND le.kind = 'source'
                                   JOIN memory_entities x
                                     ON x.guild_id = g AND x.campaign_id = c
                                    AND x.id = le.target_id AND x.type = 'character'
                                    AND x.status = 'confirmed'
                                   WHERE lb.guild_id = g AND lb.campaign_id = c
                                     AND lb.perspective_id = m.perspective_id
                                     AND lb.relation_id = m.relation_id
                                     AND lb.status IN ('suspected_lie', 'known_lie')
                                   ORDER BY lb.seq DESC, le.added_at LIMIT 1) END,
                               """
assert _OLD.count(_TRUTH_WORD) == 1 and _OLD.count("CREATE FUNCTION dmbot_web_report") == 1

LIE_DIFFERENCES = r"""
    -- A third kind of difference: the party (or a player's character) thinks someone lied about a
    -- connection that the DM's view holds as true (STORY_MEMORY 9a). The connection to The truth
    -- is the same connection, so the existing check (denied has no truth connection) still holds.
    ALTER TABLE memory_differences DROP CONSTRAINT memory_differences_kind_check;
    ALTER TABLE memory_differences ADD CONSTRAINT memory_differences_kind_check
        CHECK (kind IN ('denied', 'clashes', 'lie'));
    -- The report door again, with the liar's name on a lie (the DM's alone, like all of it: the
    -- door still checks the signed-in DM, and the name is the character's own; a liar who is gone
    -- or not a kept character comes back empty and the page says "someone"). Replacing a function
    -- drops its settings, so the search path is pinned again below and PUBLIC stays revoked.
    """ + _OLD.replace(
    "CREATE FUNCTION dmbot_web_report", "CREATE OR REPLACE FUNCTION dmbot_web_report"
).replace(_TRUTH_WORD, _LIAR + _TRUTH_WORD)

SQL = LIE_DIFFERENCES

ISOLATED_TABLES: tuple[str, ...] = ()
