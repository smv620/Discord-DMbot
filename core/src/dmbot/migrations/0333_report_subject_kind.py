"""0333_report_subject_kind

The kind of each connection's subject in the after-session report, for the place words (#3368).
"""

from __future__ import annotations

import importlib

# A place tie reads by what its subject is: a character "lives in" or "was last seen in" a town,
# a town "is in" a region (#3363). The report door sent only the two names, so a character's line
# in the report still said "is in". Each connection it sends now carries `subjectKind`, the same
# word the graph door sends (a character's role as player character, NPC or `deity`, else the
# entry's own type): a fixed word, never a name or any text. The door is rebuilt from 0306's own
# text, as 0306 rebuilds 0302's, so the two cannot drift apart; the replacement is asserted, so a
# change to the original that moves the spot fails here instead of quietly dropping it. Nothing
# else in the door changes and no row is written. Replacing a function drops its settings, so the
# door text (with its search path, and PUBLIC revoked) is carried over whole.
_OLD = importlib.import_module("dmbot.migrations.0306_report_liar_current")
_FROM = "CREATE OR REPLACE FUNCTION dmbot_web_report"
_SUBJECT = "'id', r.id, 'subject', s.name,"
_KIND = (
    "CASE WHEN s.type = 'character' THEN CASE s.role WHEN 'player_character' THEN "
    "'player_character' WHEN 'npc' THEN 'npc' WHEN 'god' THEN 'deity' ELSE 'character' END "
    "ELSE s.type END"
)
assert _OLD.SQL.count(_FROM) == 1
_DOOR = _OLD.SQL[_OLD.SQL.index(_FROM) :]
_COUNT = _DOOR.count(_SUBJECT)
assert _COUNT == 4  # (0249 builds every connection it sends from one template, used four times)

REPORT_SUBJECT_KIND = r"""
    -- Each connection the report sends (the two sides of a difference, a liar's claim and the
    -- truth) now says what its subject is, so its line reads "was last seen in" or "lives in" for
    -- a character and "is in" for a place. A word, never a name or text; no row is written.
    """ + _DOOR.replace(_SUBJECT, f"{_SUBJECT} 'subjectKind', {_KIND},")

SQL = REPORT_SUBJECT_KIND

ISOLATED_TABLES: tuple[str, ...] = ()
