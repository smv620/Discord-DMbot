"""0504_thread_parts

The parts of a story thread: objectives, complications, rewards promised and given, help given or
lent, and time limits (#4471, quests slice 2; design `docs/design/quests.md` §4, §5, §5a, §5b, §8).
"""

from __future__ import annotations

from dmbot.migrations._shared import _isolate

# One row is one part of one thread (a thread entry of `memory_entities`), beside it, never an
# entry of its own (design §12: parts as entries would crowd the names list and the Story Graph).
# - `first_id`: the id of the first row of this part's history; a change in the story carries it,
#   so 👁️ / 🙈 and "it was wrong" act on one part's whole history and never on another part that
#   happens to share its words (the reviewer's finding on #4471).
# - `kind` and `state`: the kind's short list of states (§5), checked here pair by pair. Nothing
#   worked out is stored (§13 rule 4): "partly given", "passed", time left and done counts are
#   computed when shown, so no state says them.
# - `words`: what it is, in the source's or the DM's words ("find where Sephek sleeps", "100 gold").
# - `under_id`: an objective's parent objective (one level of sub-steps), or the promised reward a
#   `reward_given` row counts against (§5a). The store checks it is the same thread's, of the
#   right kind; a foreign key can't.
# - `who_id`: who promises the reward, gave it, or gave or lent the help. `about_id`: the entry the
#   part points at: the item that is the reward or the help, the event that brought a
#   complication or met an objective (§4's "points at", split in two so a reward can name both its
#   giver and the item; GraphEngineer's plan on #4471).
# - `amount` and `unit`: a reward's number when its words give one ("100 gold"), read by code, so a
#   promise can be counted against what was given. Rewards only.
# - `position`: the DM's order of objectives. `due`: a reward's 'up_front' or 'afterwards' ('' when
#   not said). `terms`: help's terms in the text's words ("returned unharmed").
# - Time limits: `limit_kind` soft or hard, and `due_minute` (a game minute) with `due_from` ('read'
#   or 'dm'), both empty until quests slice 4 counts them (§8); here so that slice needs no
#   migration.
# - Story time as connections keep it: a change in the story closes the row (`to_session_at`) and
#   opens a new one, so history stays; a mistake rejects it (§4, the rule ruled on #4258).
# - `status` 'read' (a reader's, waiting for the DM: quests slice 3), 'confirmed' (the DM's) or
#   'rejected'; `source` 'dm', 'lore' or 'session'. `table_knows`: players may see it (§10.2),
#   false for the DM's own typing until the DM shows it. The one reader is `thread_parts.parts_for`.
#
# One open row per thread, kind and words (§4). Per campaign with forced row-level security; every
# foreign key follows a campaign moved to another server (`ON UPDATE CASCADE`, #2550) and goes with
# its thread (`ON DELETE CASCADE`). An entry it names going leaves the part and its words
# (`ON DELETE SET NULL`, Supervisor's ruling on #4471). The store never deletes an entry a part
# names (the change log refuses while anything links to it), so this is the database's own guard.
# Logged in the change log, moved by merges and carried by the memory backup section (the DM's own
# story memory: nothing rebuilds it). No existing row changes.
SQL = r"""
    CREATE TABLE memory_thread_parts (
        guild_id        BIGINT NOT NULL,
        campaign_id     TEXT NOT NULL,
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        id              TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{32}$'),
        thread_id       TEXT NOT NULL,
        first_id        TEXT NOT NULL CHECK (first_id ~ '^[0-9a-f]{32}$'),
        kind            TEXT NOT NULL CHECK (kind IN ('objective', 'complication', 'reward',
                                                      'reward_given', 'help', 'time_limit')),
        words           TEXT NOT NULL CHECK (char_length(words) BETWEEN 1 AND 200),
        state           TEXT NOT NULL,
        under_id        TEXT,
        who_id          TEXT,
        about_id        TEXT,
        amount          DOUBLE PRECISION CHECK (amount > 0 AND amount <= 1000000000),
        unit            TEXT NOT NULL DEFAULT '' CHECK (char_length(unit) <= 30),
        position        INTEGER CHECK (position IS NULL OR position >= 0),
        due             TEXT NOT NULL DEFAULT '' CHECK (due IN ('', 'up_front', 'afterwards')),
        terms           TEXT NOT NULL DEFAULT '' CHECK (char_length(terms) <= 200),
        limit_kind      TEXT NOT NULL DEFAULT '' CHECK (limit_kind IN ('', 'soft', 'hard')),
        due_minute      BIGINT,
        due_from        TEXT NOT NULL DEFAULT '' CHECK (due_from IN ('', 'read', 'dm')),
        from_session_at BIGINT,
        to_session_at   BIGINT,
        from_game_time  BIGINT,
        to_game_time    BIGINT,
        status          TEXT NOT NULL CHECK (status IN ('read', 'confirmed', 'rejected')),
        source          TEXT NOT NULL CHECK (source IN ('dm', 'lore', 'session')),
        table_knows     BOOLEAN NOT NULL DEFAULT false,
        created_at      BIGINT NOT NULL CHECK (created_at > 0),
        made_by_commit  TEXT CONSTRAINT memory_thread_parts_made_by_commit_check
            CHECK (made_by_commit ~ '^[0-9a-f]{40}$'),
        PRIMARY KEY (guild_id, campaign_id, id),
        FOREIGN KEY (guild_id, campaign_id, thread_id)
            REFERENCES memory_entities (guild_id, campaign_id, id)
            ON DELETE CASCADE ON UPDATE CASCADE,
        FOREIGN KEY (guild_id, campaign_id, who_id)
            REFERENCES memory_entities (guild_id, campaign_id, id)
            ON DELETE SET NULL (who_id) ON UPDATE CASCADE,
        FOREIGN KEY (guild_id, campaign_id, about_id)
            REFERENCES memory_entities (guild_id, campaign_id, id)
            ON DELETE SET NULL (about_id) ON UPDATE CASCADE,
        FOREIGN KEY (guild_id, campaign_id, under_id)
            REFERENCES memory_thread_parts (guild_id, campaign_id, id)
            ON DELETE CASCADE ON UPDATE CASCADE,
        -- each kind's own states (design §5)
        CONSTRAINT memory_thread_parts_state CHECK (
            (kind = 'objective' AND state IN ('to_do', 'done', 'failed', 'not_needed'))
            OR (kind = 'complication' AND state IN ('in_the_way', 'overcome', 'no_longer_matters'))
            OR (kind = 'reward' AND state IN ('owed', 'settled', 'wont_be_given', 'turned_down'))
            OR (kind = 'reward_given' AND state = 'given')
            OR (kind = 'help' AND state IN ('to_keep', 'lent', 'returned', 'not_returned'))
            OR (kind = 'time_limit' AND state IN ('running', 'met', 'lifted'))
        ),
        -- each field only on the kinds it belongs to
        CONSTRAINT memory_thread_parts_fields CHECK (
            (amount IS NULL AND unit = '' OR kind IN ('reward', 'reward_given'))
            AND (position IS NULL OR kind = 'objective')
            AND (due = '' OR kind = 'reward')
            AND (terms = '' OR kind = 'help')
            AND (under_id IS NULL OR kind IN ('objective', 'reward_given'))
            AND (who_id IS NULL OR kind IN ('reward', 'reward_given', 'help'))
            AND (kind = 'time_limit' OR (limit_kind = '' AND due_minute IS NULL AND due_from = ''))
            AND ((due_minute IS NULL) = (due_from = ''))
            AND (under_id IS NULL OR under_id <> id)
        ),
        CONSTRAINT memory_thread_parts_time_order CHECK (
            to_session_at IS NULL OR from_session_at IS NULL OR to_session_at >= from_session_at
        )
    );
    -- one open row per thread, kind and words (design §4)
    CREATE UNIQUE INDEX memory_thread_parts_open
        ON memory_thread_parts (guild_id, campaign_id, thread_id, kind, lower(words))
        WHERE status <> 'rejected' AND to_session_at IS NULL;
    -- each foreign key's own index (deleting an entry checks every link)
    CREATE INDEX memory_thread_parts_thread
        ON memory_thread_parts (guild_id, campaign_id, thread_id);
    CREATE INDEX memory_thread_parts_first
        ON memory_thread_parts (guild_id, campaign_id, first_id);
    CREATE INDEX memory_thread_parts_under
        ON memory_thread_parts (guild_id, campaign_id, under_id) WHERE under_id IS NOT NULL;
    CREATE INDEX memory_thread_parts_who
        ON memory_thread_parts (guild_id, campaign_id, who_id) WHERE who_id IS NOT NULL;
    CREATE INDEX memory_thread_parts_about
        ON memory_thread_parts (guild_id, campaign_id, about_id) WHERE about_id IS NOT NULL;
    CREATE TRIGGER memory_thread_parts_stamp_commit
        BEFORE INSERT OR UPDATE ON memory_thread_parts
        FOR EACH ROW EXECUTE FUNCTION dmbot_stamp_commit();
    """ + _isolate("memory_thread_parts")

ISOLATED_TABLES: tuple[str, ...] = ("memory_thread_parts",)
