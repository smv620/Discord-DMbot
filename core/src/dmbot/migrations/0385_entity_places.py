"""0385_entity_places

"Where was it said?" for any live character or group of a campaign, for the graph clean-up page
(#3767).
"""

from __future__ import annotations

import importlib

# The graph clean-up page (#3740) offers "Where was it said?" for an isolated entry with no kept
# scene. The recap's door (`dmbot_recap_name_places`, 0245 and 0334) only answers for a name waiting
# in one recap, so a kept entry, which most isolated ones are, got nothing (Supervisor, #3767 item
# 1). This door answers for one entry of the campaign: a live (waiting or kept, not merged)
# character or group, the kinds the clean-up page lists. Everything else is the recap door's own
# text, rebuilt from 0334 so the two cannot drift apart: the same DM-only check (NULL for anyone
# else), up to three windows, newest first, of up to three table lines around each place, read from
# transcript_lines now, the words and name only of a speaker with a consent row at this moment, and
# `more` for the places not shown. An entry with a secret other name gets nothing, as in the recap
# door. Each replacement is asserted, so a change to the original that moves a spot fails here.
# The answer also names the entry, so the page can mark the name in each line. No row is written.
_OLD = importlib.import_module("dmbot.migrations.0334_name_places_newest")
_FROM = "CREATE OR REPLACE FUNCTION dmbot_recap_name_places(g BIGINT, c TEXT, at_ BIGINT)"
_TO = "CREATE FUNCTION dmbot_web_entity_places(g BIGINT, c TEXT, eid TEXT)"
_PICK_ENTITY = """            SELECT e.id
            FROM recap_name_checks k
            JOIN memory_entities e
              ON e.guild_id = k.guild_id AND e.campaign_id = k.campaign_id AND e.id = k.entity_id
            WHERE k.guild_id = g AND k.campaign_id = c AND k.session_at = at_
              AND e.status = 'proposed'
"""
_ONE_ENTITY = """            SELECT e.id, e.name
            FROM memory_entities e
            WHERE e.guild_id = g AND e.campaign_id = c AND e.id = eid
              AND e.merged_into IS NULL AND e.status IN ('proposed', 'confirmed')
              AND e.type IN ('character', 'faction')
"""
_RESULT = "jsonb_build_object(\n                'windows', wins,"
_NAMED_RESULT = "jsonb_build_object(\n                'name', ent.name, 'windows', wins,"
_ALTER = "dmbot_recap_name_places(BIGINT, TEXT, BIGINT)"
_NEW_ALTER = "dmbot_web_entity_places(BIGINT, TEXT, TEXT)"

_SRC = _OLD.SQL[_OLD.SQL.index("CREATE OR REPLACE FUNCTION dmbot_recap_name_places") :]
assert _SRC.count(_FROM) == 1 and _SRC.count(_PICK_ENTITY) == 1
assert _SRC.count(_RESULT) == 1 and _SRC.count(_ALTER) == 2

ENTITY_PLACES = (
    _SRC.replace(_FROM, _TO)
    .replace(_PICK_ENTITY, _ONE_ENTITY)
    .replace(_RESULT, _NAMED_RESULT)
    .replace(_ALTER, _NEW_ALTER)
)

SQL = ENTITY_PLACES

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_entity_places", "dmbot_web_entity_places(BIGINT, TEXT, TEXT)"),
)
