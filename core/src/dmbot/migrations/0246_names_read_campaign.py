"""0246_names_read_campaign

The lines it returns are this campaign's (#2729).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    _CHECK_READ_DOOR,
    _swap,
)

# The lines the names-to-check door returns (0242) were picked by server only: a mention's line is
# now also required to be in one of THIS campaign's sessions, so nothing of another campaign's
# transcript can come through, even by a pointer that names it (#2729). The door is rebuilt from
# 0242's text by guarded replacement, with its pinned search_path and REVOKE kept.
_CAMPAIGN_READ_DOOR = _swap(
    _CHECK_READ_DOOR,
    (
        (
            "      JOIN transcript_lines l\n        ON l.guild_id = g\n",
            "      JOIN transcript_sessions s\n        ON s.guild_id = g AND s.campaign_id = c\n"
            "      JOIN transcript_lines l\n        ON l.guild_id = g AND l.session_id = s.id\n",
        ),
    ),
)

NAMES_READ_CAMPAIGN = (
    _CAMPAIGN_READ_DOOR
    + r"""
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_recap_names_read(BIGINT, TEXT, BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_recap_names_read(BIGINT, TEXT, BIGINT) FROM PUBLIC;
    """
)

SQL = NAMES_READ_CAMPAIGN
