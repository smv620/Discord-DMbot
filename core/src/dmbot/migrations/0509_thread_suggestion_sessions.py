"""0509_thread_suggestion_sessions

A story-thread suggestion read from a session records its session and the lines it quotes (#4522,
quests slice 5a; design `docs/design/quests.md` §9.5 and Supervisor's ruling B on #4522).
"""

from __future__ import annotations

# Four columns on `thread_suggestions` (0502):
# - `session_id`: the stored session it was read from, NULL for one read from lore (slices 1, 3);
#   `session_at` that session's start, so a thread kept from it starts at that session
#   (`from_session_at`, as a thread the DM adds after a session does).
# - `line_starts`: when each line it quotes began (`started_ms`), its evidence, with its speaker
#   below: a line's place in the session moves when an earlier line is deleted, its start never
#   does (as `line_ref`). A session suggestion keeps no words of the transcript: its `quote` and its
#   parts' quotes stay '', and the DM's card reads the lines back from the transcript when it is
#   shown, so a line deleted or no longer readable is never shown from here.
# - `speaker_ids`: who said those lines, for the consent rule of every after-session proposal
#   (reveals 0394, deeds): a waiting row is counted, shown and kept only while all its speakers
#   are recorded in the server, `ThreadSuggestionStore.purge_stopped` drops it otherwise, and Stop
#   recording me deletes every waiting row that quotes the person (`thread_suggestions.forget`).
#   A kept row is the DM's and stays, with that person's id and its lines taken off; such a row
#   never waits again (an Undo or ↩ leaves it answered: `_CAN_WAIT`). "Delete my transcripts" and a
#   DM's removal of a speaker run the same forget.
# A session row has at least one speaker while it waits (the CHECK), so none escapes the sweep. No
# existing row is written (lore rows keep the defaults), so no row-level security opens.
SQL = r"""
    ALTER TABLE thread_suggestions
        ADD COLUMN session_id TEXT CHECK (session_id IS NULL OR char_length(session_id) <= 64),
        ADD COLUMN session_at BIGINT,
        ADD COLUMN line_starts BIGINT[] NOT NULL DEFAULT '{}',
        ADD COLUMN speaker_ids BIGINT[] NOT NULL DEFAULT '{}',
        ADD CONSTRAINT thread_suggestions_session_speakers
            CHECK (session_id IS NULL OR status <> 'waiting' OR cardinality(speaker_ids) >= 1);
    CREATE INDEX thread_suggestions_by_speaker
        ON thread_suggestions USING gin (speaker_ids);
    """
