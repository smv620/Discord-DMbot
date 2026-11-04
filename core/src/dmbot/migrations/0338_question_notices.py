"""0338_question_notices

"❓ Questions for you" (#3435): what DMbot is waiting on the DM to answer, counted by kind, and the
timer of the private reminders.
"""

from __future__ import annotations

from dmbot.migrations._shared import _isolate

# Two small tables, both written by the bot only, both new (no row changes, no backfill policy).
# `question_counts` is the bot's own count of each kind of question that waits for a campaign's
# DMs. The bot is the one place that counts (some kinds are worked out in Python), so the
# website's page reads this table through a DM-only door and cannot disagree with Discord. Counts,
# one time and a monotonic "answered so far" number only, never a name or a word of any question.
# `settled` is set only for a kind whose questions also leave by expiring (the "Did they mean…?"
# questions): it counts the answers given, so an expiry alone never looks like an answer.
# `question_notices` is the timer: one row for each DM of each campaign (`step` reminders sent,
# when the next is `due_at`, whether they pressed "Not now", the Questions message and the
# current reminder in their private chat). Message ids and times only. Both are left out of
# backups: they are timers and counts that the next round makes again.
SQL = (
    """
    CREATE TABLE question_counts (
        guild_id    BIGINT NOT NULL,
        campaign_id TEXT NOT NULL,
        kind        TEXT NOT NULL CHECK (kind ~ '^[a-z_]{1,20}$'),
        n           INTEGER NOT NULL CHECK (n >= 0),
        oldest      BIGINT,
        settled     BIGINT CHECK (settled IS NULL OR settled >= 0),
        counted_at  BIGINT NOT NULL CHECK (counted_at > 0),
        PRIMARY KEY (guild_id, campaign_id, kind),
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE
    );
    """
    + _isolate("question_counts")
    + """
    CREATE TABLE question_notices (
        guild_id    BIGINT NOT NULL,
        campaign_id TEXT NOT NULL,
        user_id     BIGINT NOT NULL CHECK (user_id > 0),
        step        INTEGER NOT NULL DEFAULT 0 CHECK (step >= 0),
        due_at      BIGINT,
        last_total  INTEGER NOT NULL DEFAULT 0 CHECK (last_total >= 0),
        muted       BOOLEAN NOT NULL DEFAULT FALSE,
        message_id  BIGINT,
        reminder_id BIGINT,
        answered_at BIGINT,
        PRIMARY KEY (guild_id, campaign_id, user_id),
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE
    );
    """
    + _isolate("question_notices")
)

ISOLATED_TABLES: tuple[str, ...] = ("question_counts", "question_notices")
