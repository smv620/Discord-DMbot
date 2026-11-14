"""0394_reveal_proposals

Who learned what, as the session reader proposed it, waiting for the DM (#2996, part 2 slice 1).
"""

from __future__ import annotations

from dmbot.migrations._shared import _isolate

SQL = r"""
    -- A reveal the story extractor thinks happened (#2996): one character learned one statement
    -- DMbot already holds (an entry, a property, a connection or an other name), by a successful
    -- check or by being told. It is a proposal only: `knowers.learn`, on the DM's press, is what
    -- records one. **Ids only, no words**: who, which statement, how (the check's name from a
    -- fixed list, or the teller's id) and the numbers of the lines it came from, never a line's
    -- words, so even a leaked row says only "this character, that statement". `speaker_ids` are
    -- the people whose lines it came from: a waiting row goes when any of them stops being
    -- recorded. 'kept' and 'dismissed' rows stay, so a (character, statement) is never asked
    -- about twice in a session. No foreign key to the statement: one deleted since is told to the
    -- DM ("that is gone") when the proposal is answered.
    CREATE TABLE memory_reveal_proposals (
        guild_id           BIGINT NOT NULL,
        campaign_id        TEXT NOT NULL,
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        id                 TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{32}$'),
        character_id       TEXT NOT NULL CHECK (character_id ~ '^[0-9a-f]{32}$'),
        statement_kind     TEXT NOT NULL
                           CHECK (statement_kind IN ('entry', 'property', 'connection', 'alias')),
        statement_id       TEXT NOT NULL CHECK (statement_id ~ '^[0-9a-f]{32}$'),
        how                TEXT NOT NULL CHECK (how IN ('check', 'tell')),
        check_name         TEXT NOT NULL DEFAULT '' CHECK (check_name IN
                               ('', 'Perception', 'Investigation', 'Insight', 'Nature', 'History',
                                'Arcana', 'Religion', 'Survival', 'Medicine')),
        teller_id          TEXT CHECK (teller_id IS NULL OR teller_id ~ '^[0-9a-f]{32}$'),
        lines              TEXT[] NOT NULL CHECK (cardinality(lines) <= 10),
        speaker_ids        BIGINT[] NOT NULL,
        session_started_at BIGINT,
        prompt_version     TEXT NOT NULL CHECK (char_length(prompt_version) BETWEEN 1 AND 40),
        model              TEXT NOT NULL CHECK (char_length(model) <= 80),
        status             TEXT NOT NULL CHECK (status IN ('proposed', 'kept', 'dismissed')),
        created_at         BIGINT NOT NULL CHECK (created_at > 0),
        decided_at         BIGINT,
        made_by_commit     TEXT CONSTRAINT memory_reveal_proposals_made_by_commit_check
            CHECK (made_by_commit ~ '^[0-9a-f]{40}$'),
        PRIMARY KEY (guild_id, campaign_id, id),
        -- (a check names its check and no teller; a tell names its teller and no check)
        CHECK ((how = 'check') = (check_name <> '' AND teller_id IS NULL)),
        CHECK ((how = 'tell') = (teller_id IS NOT NULL AND check_name = '')),
        CHECK (teller_id IS NULL OR teller_id <> character_id),
        -- (a waiting row cites someone, so the consent check can always reach it)
        CHECK (status = 'kept' OR cardinality(speaker_ids) >= 1)
    );
    CREATE INDEX memory_reveal_proposals_by_status
        ON memory_reveal_proposals (guild_id, campaign_id, status);
    -- One proposal for a (character, statement) in a session, even if two runs overlap; also the
    -- index the store's "already proposed?" read uses. (Sessions with no start share one scope.)
    CREATE UNIQUE INDEX memory_reveal_proposals_once
        ON memory_reveal_proposals (guild_id, campaign_id, session_started_at, character_id,
                                    statement_kind, statement_id) NULLS NOT DISTINCT;
    CREATE TRIGGER memory_reveal_proposals_stamp_commit
        BEFORE INSERT OR UPDATE ON memory_reveal_proposals
        FOR EACH ROW EXECUTE FUNCTION dmbot_stamp_commit();
    """ + _isolate("memory_reveal_proposals")

ISOLATED_TABLES: tuple[str, ...] = ("memory_reveal_proposals",)
