"""0231_link_upload

Where a waiting connection came from (#2650).
"""

from __future__ import annotations

BACKSTORY_LINK_UPLOAD = """
    -- Which upload a waiting backstory connection came from (#2650): its name and date as the DM
    -- saw them ("Tavin's backstory, 24 Oct"), so Keep all can mean "from this backstory" and the
    -- DM screen can say "3 connections from Tavin's backstory are ready to check". `told` is set
    -- once the DM screen has said a connection is ready, so it is said once. Both columns have
    -- defaults: no existing row changes, and an old connection has no label.
    ALTER TABLE backstory_links
        ADD COLUMN upload TEXT NOT NULL DEFAULT '' CHECK (char_length(upload) <= 120),
        ADD COLUMN told BOOLEAN NOT NULL DEFAULT false;
"""

SQL = BACKSTORY_LINK_UPLOAD
