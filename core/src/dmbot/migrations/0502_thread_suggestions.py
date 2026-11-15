"""0502_thread_suggestions

The lore reader's story-thread suggestions, waiting for the DM (#4453, quests slice 1; design
`docs/design/quests.md` §9.1 and §9.6; Supervisor's ruling on #4453, 2026-11-15).
"""

from __future__ import annotations

from dmbot.migrations._shared import _isolate

# One row is one suggestion: a bundle the DM keeps or turns down as a whole (§9.1). It is a wait,
# never story memory: nothing here is an entry until the DM keeps it, so no names list, count,
# "Add all" or players' view ever reads it (Supervisor: it replaced proposed thread entries, which
# leaked into all of those). On ✅ Keep the store writes the thread entry, its state and the
# chosen people through the change log (one Undo) and marks the row 'kept'; 🚫 marks it
# 'turned_down', and its `name_key` stops the same suggestion coming back.
# - `kind`: 'quest' or 'hook' (slice 1); `name` is built by code from its parts, `aim` the text's
#   words, `quote` the short evidence the reader checked against the text.
# - `parts`: a JSON list of [part word, entry id] pairs: entry ids only, never free names
#   (Supervisor). An id may have been merged or deleted since; the keep follows a merge and skips
#   what is gone.
# - `kept_batch`: the change the keep made, so ↩ Undo on the card puts the suggestion back.
# Per campaign with forced row-level security; it follows a campaign moved to another server
# (`ON UPDATE CASCADE`, #2550) and goes with it. Left out of backups, like `backstory_links`: a
# re-read of the lore rebuilds it. No person's ID is stored. No existing row changes.
SQL = r"""
    CREATE TABLE thread_suggestions (
        guild_id     BIGINT NOT NULL,
        campaign_id  TEXT NOT NULL,
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        id           TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{32}$'),
        kind         TEXT NOT NULL CHECK (kind IN ('quest', 'hook')),
        name         TEXT NOT NULL CHECK (char_length(name) BETWEEN 1 AND 120),
        name_key     TEXT NOT NULL CHECK (char_length(name_key) BETWEEN 1 AND 120),
        aim          TEXT NOT NULL DEFAULT '' CHECK (char_length(aim) <= 240),
        quote        TEXT NOT NULL DEFAULT '' CHECK (char_length(quote) <= 400),
        parts        JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(parts) = 'array'),
        upload       TEXT NOT NULL DEFAULT '' CHECK (char_length(upload) <= 200),
        status       TEXT NOT NULL CHECK (status IN ('waiting', 'kept', 'turned_down')),
        kept_batch   BIGINT,
        created_at   BIGINT NOT NULL CHECK (created_at > 0),
        PRIMARY KEY (guild_id, campaign_id, id)
    );
    -- one suggestion per name, whatever became of it: a turned-down one is never suggested again
    CREATE UNIQUE INDEX thread_suggestions_once
        ON thread_suggestions (guild_id, campaign_id, name_key);
    """ + _isolate("thread_suggestions")

ISOLATED_TABLES: tuple[str, ...] = ("thread_suggestions",)
