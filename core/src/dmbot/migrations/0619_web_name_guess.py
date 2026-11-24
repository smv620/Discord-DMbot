"""0619_web_name_guess

The website's name card starts on DMbot's best guess for every waiting name (#5218).
"""

from __future__ import annotations

import importlib

# `dmbot_recap_name_found` (0616) answered only for the waiting names that have a `name_found` row
# (the session-names card, off for now), so the website had a type, role and source only for those
# and every other name's kind menu started empty. It now answers for every waiting name of the
# recap: the join to `name_found` becomes a left join, so a name with no row comes back with its
# type, role, source and other spellings and a null `how`, which the page reads as "no found row".
# The door is rebuilt from 0616's own text so the two cannot drift apart; each replacement is
# asserted, so a change to the original that moves a spot fails here instead of quietly dropping
# it. The DM check, the consent rule of the line, the secret-spelling rule and the search path are
# 0616's, unchanged. No row is written.
_OLD = importlib.import_module("dmbot.migrations.0616_web_name_found")
_FROM = "CREATE FUNCTION dmbot_recap_name_found("
_JOIN = "                JOIN name_found f\n"
assert _OLD.SQL.count(_FROM) == 1 and _OLD.SQL.count(_JOIN) == 1

SQL = _OLD.SQL.replace(_FROM, "CREATE OR REPLACE FUNCTION dmbot_recap_name_found(", 1).replace(
    _JOIN, "                LEFT JOIN name_found f\n", 1
)

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = ()
