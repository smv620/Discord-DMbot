"""0529_thread_guesses

Which mystery a table's guess answers (#4655, quests slice 7b; design `docs/design/quests.md` §7;
Supervisor's ruling 2 on #4655).
"""

from __future__ import annotations

from dmbot.migrations._shared import _isolate

# One row ties one belief (a theory or an accusation the DM placed from a session's reading) to the
# story thread it is a guess about, so the thread's 🧵 card can show "The table's guess: …" without
# guessing which beliefs belong to it. Only ids and a time, never a word.
# - It is written in the DM's place of the reading (`placing`), in the same change as the belief,
#   so one Undo takes both back, and it goes with its belief or its thread by the database
#   (a belief goes when the last person it quotes stops being recorded, or with its connection).
# - A belief is about at most one thread (the primary key): the reader names one mystery or none.
# - Nothing worked out is stored (§7): no right or wrong, no "solved".
SQL = r"""
    CREATE TABLE memory_thread_guesses (
        guild_id        BIGINT NOT NULL,
        campaign_id     TEXT NOT NULL,
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        id              TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{32}$'),
        thread_id       TEXT NOT NULL,
        belief_id       TEXT NOT NULL,
        created_at      BIGINT NOT NULL,
        made_by_commit  TEXT CONSTRAINT memory_thread_guesses_made_by_commit_check
            CHECK (made_by_commit ~ '^[0-9a-f]{40}$'),
        PRIMARY KEY (guild_id, campaign_id, id),
        UNIQUE (guild_id, campaign_id, belief_id),
        FOREIGN KEY (guild_id, campaign_id, thread_id)
            REFERENCES memory_entities (guild_id, campaign_id, id)
            ON DELETE CASCADE ON UPDATE CASCADE,
        FOREIGN KEY (guild_id, campaign_id, belief_id)
            REFERENCES memory_beliefs (guild_id, campaign_id, id)
            ON DELETE CASCADE ON UPDATE CASCADE
    );
    CREATE INDEX memory_thread_guesses_by_thread
        ON memory_thread_guesses (guild_id, campaign_id, thread_id);
    CREATE TRIGGER memory_thread_guesses_stamp_commit
        BEFORE INSERT OR UPDATE ON memory_thread_guesses
        FOR EACH ROW EXECUTE FUNCTION dmbot_stamp_commit();
    """ + _isolate("memory_thread_guesses")

ISOLATED_TABLES: tuple[str, ...] = ("memory_thread_guesses",)
