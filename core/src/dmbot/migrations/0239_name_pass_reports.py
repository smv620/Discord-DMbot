"""0239_name_pass_reports

What the name pass did, for the website (#2684).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    _isolate,
)

# What the name pass did to one session (#2684, #2628), kept so the website can show it and undo it
# (it was only ever posted to Discord, and an Undo deletes the lines that said a fix was made).
# `name_pass_reports`: one row for the session, the counts the DM is told (words left as heard,
# names offered as new, words left alone near a secret name) and whether PlotBot's answer was cut.
# `name_pass_fixes`: one row for each name fixed, with the Undo number, the list name and how many
# times, and when it was undone. Counts and the campaign's own list names only: the spellings as
# heard stay in `transcript_respells` (read from there while the fix stands, so they are never
# copied and go with the lines they came from). Deleted with the session; its keys follow a campaign
# that moves to another server.
NAME_PASS_REPORTS = (
    """
    CREATE TABLE name_pass_reports (
        guild_id    BIGINT NOT NULL,
        campaign_id TEXT NOT NULL,
        session_id  TEXT NOT NULL,
        unsure      INTEGER NOT NULL DEFAULT 0 CHECK (unsure >= 0),
        new_names   INTEGER NOT NULL DEFAULT 0 CHECK (new_names >= 0),
        guarded     INTEGER NOT NULL DEFAULT 0 CHECK (guarded >= 0),
        cut         BOOLEAN NOT NULL DEFAULT false,
        made_at     BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, session_id),
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        FOREIGN KEY (session_id, guild_id)
            REFERENCES transcript_sessions (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE
    );
    CREATE TABLE name_pass_fixes (
        guild_id    BIGINT NOT NULL,
        campaign_id TEXT NOT NULL,
        session_id  TEXT NOT NULL,
        batch       BIGINT NOT NULL,
        name        TEXT NOT NULL CHECK (char_length(name) BETWEEN 1 AND 200),
        times       INTEGER NOT NULL CHECK (times >= 0),
        made_at     BIGINT NOT NULL,
        undone_at   BIGINT,
        PRIMARY KEY (guild_id, campaign_id, session_id, batch),
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        FOREIGN KEY (session_id, guild_id)
            REFERENCES transcript_sessions (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE
    );
    -- (an Undo knows its batch and campaign, not the session)
    CREATE INDEX name_pass_fixes_by_batch ON name_pass_fixes (guild_id, campaign_id, batch);
    -- (the foreign keys to a session: deleting a session finds its rows by index, not a scan;
    -- the reports' primary key leads with the campaign, so it does not serve that)
    CREATE INDEX name_pass_reports_by_session ON name_pass_reports (session_id, guild_id);
    CREATE INDEX name_pass_fixes_by_session ON name_pass_fixes (session_id, guild_id);
    """
    + _isolate("name_pass_reports")
    + _isolate("name_pass_fixes")
)

SQL = NAME_PASS_REPORTS

ISOLATED_TABLES: tuple[str, ...] = (
    "name_pass_reports",
    "name_pass_fixes",
)
