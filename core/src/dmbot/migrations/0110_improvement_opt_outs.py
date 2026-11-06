"""0110_improvement_opt_outs

The improvement opt-out (#1609).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    _isolate_user,
)

IMPROVEMENT_OPT_OUTS = """
    -- A person's choice not to have their words used to improve DMbot (#1609; docs/PLAN.md,
    -- "Data, ownership and deletion"). Per Discord account, across all servers: it is the
    -- person's choice, not a campaign's. A row means "opted out"; no row is the default (DMbot
    -- may use their words). Nothing is deleted by it, and running their table is unaffected.
    -- Kept when an account is deleted (no link to web_users): forgetting the choice would put
    -- their words back in. A person reads and writes only their own row (user_isolation); the
    -- exports read it one person at a time through Database.user(), so nothing here lets one
    -- person see another's choice. A new table: no rows change, so no backfill policy.
    CREATE TABLE improvement_opt_outs (
        user_id      BIGINT PRIMARY KEY CHECK (user_id > 0),
        opted_out_at BIGINT NOT NULL
    );
    """ + _isolate_user("improvement_opt_outs")

SQL = IMPROVEMENT_OPT_OUTS

USER_ISOLATED_TABLES: tuple[str, ...] = (
    # A person's choice not to have their words used to improve DMbot (#1609).
    "improvement_opt_outs",
)
