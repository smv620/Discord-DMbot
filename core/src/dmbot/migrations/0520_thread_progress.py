"""0520_thread_progress

The session reader's progress on story threads the campaign already has, waiting for the DM
(#4522, quests slice 5b; design `docs/design/quests.md` §9.5 and Supervisor's ruling A and B on
#4522).
"""

from __future__ import annotations

from dmbot.migrations._shared import _isolate

# One row is one line of progress on one kept thread, read from one session: never story memory
# until the DM keeps it (the card's "Progress to check"). The new threads' table (0502) holds a
# new thread once per name; progress repeats on one thread, so it has its own table (ruling A).
# - `kind`: what the line changes.
#   - 'part': a new part of the thread (`part_kind`: an objective, a complication, a reward given,
#     help, or a time limit's words; `words` the reader's short words, `state` its state, `due`
#     a time limit's "when" in words, only for a time limit and never made a date here: the DM
#     gives the date, as slice 4c's offer);
#   - 'part_state': a part the thread has moves on (`part_id`, `state`: an objective done or
#     failed, a complication overcome, help returned, a reward settled or turned down);
#   - 'thread_state': the thread's own state (`state`: a hook taken up is "Happening now").
# - `change_key`: the same change on the same thread is offered once, whatever became of it.
# - `session_id`, `session_at`, `line_starts`, `speaker_ids`: the session and the lines it quotes,
#   and who said them (0509's rule for every after-session proposal): no words of the transcript
#   are kept; a waiting row is shown and kept only while all its speakers are recorded, and Stop
#   recording me deletes every waiting row that quotes the person. A kept or turned-down row is
#   the DM's answer and stays, without that person's id and its lines.
# - `kept_batch`: the change the keep made (one change for every line kept together, one Undo),
#   so an Undo puts the lines back to waiting.
# It goes with its thread (`ON DELETE CASCADE`) and with a part it moves, answered or not (an Undo
# that deletes the part's row takes the line, and its change may then be offered again), and
# follows a campaign
# moved to another server (`ON UPDATE CASCADE`, #2550). Per campaign with forced row-level
# security. Left out of backups as a wait (`campaigns/tables.py`): a re-read rebuilds it. No
# existing row changes.
SQL = r"""
    CREATE TABLE thread_progress (
        guild_id     BIGINT NOT NULL,
        campaign_id  TEXT NOT NULL,
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        id           TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{32}$'),
        thread_id    TEXT NOT NULL,
        FOREIGN KEY (guild_id, campaign_id, thread_id)
            REFERENCES memory_entities (guild_id, campaign_id, id)
            ON DELETE CASCADE ON UPDATE CASCADE,
        kind         TEXT NOT NULL CHECK (kind IN ('part', 'part_state', 'thread_state')),
        part_kind    TEXT NOT NULL DEFAULT '' CHECK (char_length(part_kind) <= 30),
        part_id      TEXT,
        FOREIGN KEY (guild_id, campaign_id, part_id)
            REFERENCES memory_thread_parts (guild_id, campaign_id, id)
            ON DELETE CASCADE ON UPDATE CASCADE,
        words        TEXT NOT NULL DEFAULT '' CHECK (char_length(words) <= 200),
        state        TEXT NOT NULL DEFAULT '' CHECK (char_length(state) <= 30),
        due          TEXT NOT NULL DEFAULT '' CHECK (char_length(due) <= 200),
        change_key   TEXT NOT NULL CHECK (char_length(change_key) = 64),
        session_id   TEXT NOT NULL CHECK (char_length(session_id) <= 64),
        session_at   BIGINT,
        line_starts  BIGINT[] NOT NULL DEFAULT '{}',
        speaker_ids  BIGINT[] NOT NULL DEFAULT '{}',
        status       TEXT NOT NULL DEFAULT 'waiting'
            CHECK (status IN ('waiting', 'kept', 'turned_down')),
        kept_batch   BIGINT,
        created_at   BIGINT NOT NULL CHECK (created_at > 0),
        PRIMARY KEY (guild_id, campaign_id, id),
        CHECK ((kind = 'part') = (part_kind <> '' AND words <> '')),
        CHECK (part_kind IN ('', 'objective', 'complication', 'reward_given', 'help',
                             'time_limit')),
        CHECK (due = '' OR part_kind = 'time_limit'),
        CHECK ((kind = 'part_state') = (part_id IS NOT NULL)),
        CHECK (kind = 'part' OR state <> ''),
        CHECK (status <> 'waiting'
               OR (cardinality(speaker_ids) >= 1 AND cardinality(line_starts) >= 1))
    );
    -- the same change on the same thread is offered once, whatever became of it
    CREATE UNIQUE INDEX thread_progress_once
        ON thread_progress (guild_id, campaign_id, thread_id, change_key);
    CREATE INDEX thread_progress_waiting
        ON thread_progress (guild_id, campaign_id, status);
    CREATE INDEX thread_progress_by_speaker ON thread_progress USING gin (speaker_ids);
    CREATE INDEX thread_progress_by_batch ON thread_progress (guild_id, campaign_id, kept_batch);
    """ + _isolate("thread_progress")

ISOLATED_TABLES: tuple[str, ...] = ("thread_progress",)
