"""0334_name_places_newest

A name to check shows up to three places, newest first (#3384).
"""

from __future__ import annotations

import importlib

# The owner asked that every name question can show where it was said (#3384). Discord's button
# shows up to three places, newest first (CloudDev-lead's #3389); the website's door sent the
# earliest place and, with more than two, the middle one. It now sends up to three, newest first,
# so the page's button says the same as the card in Discord. Only the choice of places and their
# order change: a place is still a window of lines read now, kept only for a speaker who has agreed
# at this moment, and the door is still the campaign's DMs' alone. `more` is still the places left
# unshown. The door is rebuilt from 0245's own text, so the two cannot drift apart; each
# replacement is asserted, so a change to the original that moves the spot fails here instead of
# quietly dropping it. Replacing a function keeps its owner and grants; the search path is pinned
# again and PUBLIC stays revoked, as the carried-over text does. No row is written.
_OLD = importlib.import_module("dmbot.migrations.0245_recap_name_places")
_FROM = "CREATE FUNCTION dmbot_recap_name_places"
_PICK = "WHERE r.cnt <= 2 OR r.rn = 1 OR r.rn = r.cnt / 2 + 1\n                ORDER BY r.rn"
_NEWEST_THREE = "WHERE r.rn > r.cnt - 3\n                ORDER BY r.rn DESC"
_ORDER = "ORDER BY q.at NULLS LAST, q.ms, q.sid, q.uid"
# A place with no session date (the column may be empty) is the oldest, not the newest: the numbers
# below run earliest first, so the undated ones now sit at the start, not the end.
_ORDER_UNDATED_OLDEST = "ORDER BY q.at NULLS FIRST, q.ms, q.sid, q.uid"
assert _OLD.SQL.count(_FROM) == 1
_DOOR = _OLD.SQL[_OLD.SQL.index(_FROM) :]
assert _DOOR.count(_PICK) == 1 and _DOOR.count(_ORDER) == 1

RECAP_NAME_PLACES_NEWEST = r"""
    -- dmbot_recap_name_places: where each waiting name of a recap was said (#2403, #3384), for the
    -- campaign's DMs only. Per name, up to three windows, the newest place first, of up to three
    -- table lines around the line the name was heard in, and how many places are left unshown.
    -- Nothing is copied: the words are read from transcript_lines now, and a line is kept only if
    -- its speaker has a consent row for this server at this moment (see 0245).
    """ + (
    _DOOR.replace(_FROM, "CREATE OR REPLACE FUNCTION dmbot_recap_name_places")
    .replace(_PICK, _NEWEST_THREE)
    .replace(_ORDER, _ORDER_UNDATED_OLDEST)
)

SQL = RECAP_NAME_PLACES_NEWEST

ISOLATED_TABLES: tuple[str, ...] = ()
