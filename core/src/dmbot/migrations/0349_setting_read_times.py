"""0349_setting_read_times

The settings answer door also tells when a choice was queued and taken (#3355).
"""

from __future__ import annotations

import importlib

from dmbot.migrations._shared import _swap

# `dmbot_web_setting_read` (0197) told the page a choice's state, reason and note, but not how long
# it has waited, so a choice the bot never picks up looked like it was working forever (CLAUDE.md,
# "Every wait shows that DMbot is still working"). The same function now also returns the times it
# already stores: `createdAt` (when the page queued it, seconds), `claimedAt` (when the bot took
# it; 0 before; milliseconds, as the queue stamps them) and `now` (the database's clock, seconds).
# Times only, nothing from the choice. The function is rebuilt from 0197's text by guarded
# replacement (a drift stops the migration); the search path is set again because CREATE OR
# REPLACE drops it, and the privileges stay.
_OLD = importlib.import_module("dmbot.migrations.0197_web_setting_actions").SETTING_ACTIONS

_START = _OLD.index("    CREATE FUNCTION dmbot_web_setting_read")
_END = _OLD.index("    $fn$;", _START) + len("    $fn$;")

_BODY = _swap(
    _OLD[_START:_END].replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1),
    (
        (
            "        RETURN jsonb_build_object('state', r.state, 'error', r.error, 'setting', "
            "r.setting,\n"
            "                                  'value', r.value, 'note', r.note);",
            "        RETURN jsonb_build_object('state', r.state, 'error', r.error, 'setting', "
            "r.setting,\n"
            "                                  'value', r.value, 'note', r.note,\n"
            "                                  'createdAt', r.created_at, 'claimedAt', "
            "r.claimed_at,\n"
            "                                  'now', floor(extract(epoch FROM "
            "clock_timestamp()))::BIGINT);",
        ),
    ),
)

SETTING_READ_TIMES = (
    _BODY
    + """
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_setting_read(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
"""
)

SQL = SETTING_READ_TIMES

ISOLATED_TABLES: tuple[str, ...] = ()
