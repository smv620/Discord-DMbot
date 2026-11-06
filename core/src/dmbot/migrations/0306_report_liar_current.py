"""0306_report_liar_current

The report door names a liar only while the view's newest belief is still a lie (#3124).
"""

from __future__ import annotations

import importlib

# 0302's door is rebuilt from that file's own text, so the two cannot drift apart; each
# replacement is asserted, so a change to the original that moves the spot fails here instead of
# quietly dropping it.
_OLD = importlib.import_module("dmbot.migrations.0302_lie_differences")
_FROM = "CREATE OR REPLACE FUNCTION dmbot_web_report"
_NEWEST = "SELECT lb.id FROM memory_beliefs lb"
_JOIN = "AND le.belief_id = nb.id AND le.kind = 'source'"
_NEWEST_WITH_STATUS = "SELECT lb.id, lb.status FROM memory_beliefs lb"
# The evidence is only looked at when that newest belief is a lie. Filtering the inner select
# instead would reach back past a newer theory to an older lie and name its liar.
_JOIN_IF_LIE = (
    "AND le.belief_id = nb.id AND le.kind = 'source'\n"
    + " " * 36
    + ("AND nb.status IN ('suspected_lie', 'known_lie')")
)
assert _OLD.SQL.count(_FROM) == 1
_DOOR = _OLD.SQL[_OLD.SQL.index(_FROM) :]
assert _DOOR.count(_NEWEST) == 1 and _DOOR.count(_JOIN) == 1

READING_LIAR_CURRENT = r"""
    -- 0302's report door named the liar from the view's newest belief without checking that the
    -- belief is still a lie. If the view has since moved on to a theory that carries a "told by"
    -- source, that source is not a liar. Now the liar is named only while the newest belief is a
    -- suspected or known lie; otherwise it is empty and the page says "someone". Nothing else in
    -- the door changes, and no row is written. Replacing a function drops its settings, so the
    -- search path is pinned again (with the rest of the door) and PUBLIC stays revoked.
    """ + _DOOR.replace(_NEWEST, _NEWEST_WITH_STATUS).replace(_JOIN, _JOIN_IF_LIE)

SQL = READING_LIAR_CURRENT

ISOLATED_TABLES: tuple[str, ...] = ()
