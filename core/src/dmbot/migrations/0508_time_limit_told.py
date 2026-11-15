"""0508_time_limit_told

Which of a time limit's two private messages were said (quests slice 4b; design
`docs/design/quests.md` §8.4; Supervisor's ruling on #4448, choice A).
"""

from __future__ import annotations

from dmbot.migrations._shared import _isolate

# One row a message said: the limit it was about (its `first_id`, which ties a part's history
# together, so a limit made hard, or ticked met and back, is still the same limit), the date it was
# told for, and which message, "soon" (a game day before, or halfway when set less than two days
# ahead) or "passed" ("time's up"). Each is said once for each limit and date (§8.4: "Once each,
# for each limit"): a deadline moved to another date is told afresh (the reviewer on 4b).
#
# Kept apart from the change log on purpose (Supervisor's choice A): an Undo of a clock change, or
# a clock set back and forward again, never says a message twice, and no Undo brings one back. So
# the store writes it directly, claimed with ON CONFLICT DO NOTHING before the message goes, so two
# clock moves at once say it once. A message that then can't be delivered (the DM's private
# messages closed) is not said again: the 🧵 card and the pre-session note still show it.
#
# Per campaign with forced row-level security. It follows a campaign moved to another server and
# goes with its limit or its campaign (`ON UPDATE CASCADE`, `ON DELETE CASCADE`, #2550). Left
# out of backups as a wait (`campaigns/tables.py`): a restored campaign's limits are told afresh
# from where its clock stands. No person's ID is stored. No existing row changes.
SQL = r"""
    CREATE TABLE time_limit_told (
        guild_id     BIGINT NOT NULL,
        campaign_id  TEXT NOT NULL,
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        -- the limit's first row (`first_id`): it stays while any of the limit's history does, and
        -- a removal or a thread deleted takes it, and these rows with it
        first_id     TEXT NOT NULL,
        FOREIGN KEY (guild_id, campaign_id, first_id)
            REFERENCES memory_thread_parts (guild_id, campaign_id, id)
            ON DELETE CASCADE ON UPDATE CASCADE,
        due_minute   BIGINT NOT NULL,
        stage        TEXT NOT NULL CHECK (stage IN ('soon', 'passed')),
        told_at      BIGINT NOT NULL CHECK (told_at > 0),
        PRIMARY KEY (guild_id, campaign_id, first_id, due_minute, stage)
    );
    """ + _isolate("time_limit_told")

ISOLATED_TABLES: tuple[str, ...] = ("time_limit_told",)
