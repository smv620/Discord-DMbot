"""The database schema, as one ordered list of migrations.

Rules:
- Never edit a migration that has shipped; add a new one.
- Every table holding a Discord server's data has a `guild_id BIGINT NOT NULL` column and
  the `guild_isolation` row-level-security policy (see `_isolate`). Child tables reference
  their parent with `(id, guild_id)`, so a row can't point at another server's data.
- Discord IDs are BIGINT. Timestamps are Unix seconds (BIGINT), matching the app.
- Tables holding a *person's* data (the website's accounts, #435) have a `user_id` and
  the `user_isolation` policy (see `_isolate_user`) instead; list them in
  USER_ISOLATED_TABLES. `installs` is listed there too, though it is keyed by server:
  it's visible to its server and to the person who installed DMbot (see its policies).
- The `dmbot.*` settings that open these doors (server, person, install server, plan
  writer, cleanup) are set by dmbot.db.Database, plus two one-statement switches inside
  a transaction it opened: dmbot.web.me (server by server for /me, then the person) and
  dmbot.entitlements.read (the person, for one read). The database enforces each door's
  limits, but which code may open which door is a Python boundary: only dmbot.web opens
  Database.plan_writer() (tests/test_entitlements_writer.py checks).
- Exception: a *routing* table may skip row-level security if it holds nothing but
  Discord server IDs (no names, settings, or campaign data), because a process has to
  find its servers before it can open a per-server transaction. List it in
  ROUTING_TABLES with a comment saying why.
"""

from __future__ import annotations

Migration = tuple[str, str]


def _isolate(table: str) -> str:
    """Row-level security: only the current server's rows are visible or writable."""
    return f"""
    ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;
    ALTER TABLE {table} FORCE ROW LEVEL SECURITY;
    CREATE POLICY guild_isolation ON {table}
        USING (guild_id = dmbot_current_guild())
        WITH CHECK (guild_id = dmbot_current_guild());
    """


INITIAL = (
    """
    -- The server set by Database.guild() for this transaction, or NULL (sees nothing).
    CREATE FUNCTION dmbot_current_guild() RETURNS BIGINT
        LANGUAGE sql STABLE
        AS $fn$ SELECT NULLIF(current_setting('dmbot.guild_id', true), '')::BIGINT $fn$;

    CREATE TABLE campaigns (
        id                     TEXT PRIMARY KEY,
        guild_id               BIGINT NOT NULL,
        name                   TEXT NOT NULL,
        name_key               TEXT NOT NULL,
        created_at             BIGINT NOT NULL,
        last_played_at         BIGINT,
        target_ruleset         TEXT NOT NULL,
        fallback_ruleset       TEXT NOT NULL,
        optional_rules_default BOOLEAN NOT NULL DEFAULT TRUE,
        dm_screen_channel_id   BIGINT,
        last_voice_channel_id  BIGINT,
        dm_screen_visibility   TEXT NOT NULL DEFAULT 'peek'
            CHECK (dm_screen_visibility IN ('private', 'peek', 'open')),
        UNIQUE (guild_id, name_key),
        UNIQUE (id, guild_id)
    );

    CREATE TABLE campaign_dms (
        campaign_id TEXT NOT NULL,
        guild_id    BIGINT NOT NULL,
        user_id     BIGINT NOT NULL,
        PRIMARY KEY (campaign_id, user_id),
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE
    );
    CREATE INDEX campaign_dms_by_guild ON campaign_dms (guild_id, campaign_id);

    CREATE TABLE campaign_optional_rules (
        campaign_id TEXT NOT NULL,
        guild_id    BIGINT NOT NULL,
        rule_id     TEXT NOT NULL,
        enabled     BOOLEAN NOT NULL,
        PRIMARY KEY (campaign_id, rule_id),
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE
    );

    CREATE TABLE consent (
        guild_id   BIGINT NOT NULL,
        user_id    BIGINT NOT NULL,
        granted_at BIGINT NOT NULL,
        PRIMARY KEY (guild_id, user_id)
    );
    """
    + _isolate("campaigns")
    + _isolate("campaign_dms")
    + _isolate("campaign_optional_rules")
    + _isolate("consent")
)

ACTIVE_SESSIONS = """
    -- The session being played right now in each server (at most one), so a restarted
    -- or moved process can rejoin voice and carry on (docs/PLAN.md, #70).
    CREATE TABLE active_sessions (
        guild_id          BIGINT PRIMARY KEY,
        campaign_id       TEXT NOT NULL,
        voice_channel_id  BIGINT NOT NULL,
        screen_channel_id BIGINT NOT NULL,
        started_by        BIGINT NOT NULL,
        started_at        BIGINT NOT NULL,
        notice_posted     BOOLEAN NOT NULL DEFAULT FALSE,
        -- Restarts in a row, so a crash loop can't spam the DM screen or retry forever.
        resume_count      INTEGER NOT NULL DEFAULT 0,
        last_resumed_at   BIGINT,
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE
    );

    -- Routing only: which servers have a live session, so each process can find the
    -- ones on its shards at startup. Server IDs and nothing else, so no row-level
    -- security (see the rules at the top of this file).
    CREATE TABLE live_session_guilds (
        guild_id BIGINT PRIMARY KEY
    );
    """ + _isolate("active_sessions")

CHANNEL_NUMBER = """
    -- The number in front of a campaign's channel names when they would clash with
    -- another campaign's (2frozens-cake / 2frznsck; docs/PLAN.md, "Channel structure").
    -- Chosen once, the first time the campaign's DM screen is made, so names never shift;
    -- 1 means no number. NULL until then. Not part of backups: channels belong to a server.
    ALTER TABLE campaigns ADD COLUMN channel_number INTEGER
        CHECK (channel_number IS NULL OR channel_number >= 1);
    """


# Shared columns and links for campaign-memory tables: scoped to one server AND one
# campaign, and deleted with the campaign.
def _memory_scope() -> str:
    return """
        guild_id    BIGINT NOT NULL,
        campaign_id TEXT NOT NULL,
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE,
    """


def _entity_link(column: str, *, cascade: bool = True) -> str:
    """A link to an entity in the SAME server and campaign (a composite key, so a row
    can never point into another campaign)."""
    action = " ON DELETE CASCADE" if cascade else ""
    return f"""
        FOREIGN KEY (guild_id, campaign_id, {column})
            REFERENCES memory_entities (guild_id, campaign_id, id){action}"""


_MEMORY_TABLES = (
    "memory_types",
    "memory_predicates",
    "memory_entities",
    "memory_aliases",
    "memory_mentions",
    "memory_relations",
    "memory_corrections",
    "memory_flags",
    "memory_changes",
)

CAMPAIGN_MEMORY = f"""
    -- Campaign memory (docs/PLAN.md, "Campaign memory (EntityBot)", #126): a property
    -- graph in plain tables. EntityBot (dmbot.memory) is the only writer, and every
    -- write is logged in memory_changes so it can be undone.

    -- Bumped by every change, so copies held in memory know when to reload.
    ALTER TABLE campaigns ADD COLUMN memory_version BIGINT NOT NULL DEFAULT 0;

    -- Campaign-only additions to the memory rules. The fixed core lives in code
    -- (dmbot.memory.ontology). Terms are never deleted, only deprecated.
    CREATE TABLE memory_types (
        {_memory_scope()}
        key         TEXT NOT NULL CHECK (key ~ '^[a-z][a-z0-9_]{{1,39}}$'),
        parent      TEXT NOT NULL,
        label       TEXT NOT NULL CHECK (length(label) BETWEEN 1 AND 60),
        description TEXT NOT NULL CHECK (length(description) BETWEEN 1 AND 300),
        examples    TEXT[] NOT NULL,
        reason      TEXT NOT NULL CHECK (length(reason) BETWEEN 1 AND 300),
        status      TEXT NOT NULL CHECK (status IN ('active', 'deprecated')),
        replaced_by TEXT,
        created_at  BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, key)
    );

    CREATE TABLE memory_predicates (
        {_memory_scope()}
        key             TEXT NOT NULL CHECK (key ~ '^[a-z][a-z0-9_]{{1,39}}$'),
        parent          TEXT,
        label           TEXT NOT NULL CHECK (length(label) BETWEEN 1 AND 60),
        description     TEXT NOT NULL CHECK (length(description) BETWEEN 1 AND 300),
        examples        TEXT[] NOT NULL,
        reason          TEXT NOT NULL CHECK (length(reason) BETWEEN 1 AND 300),
        subject_types   TEXT[] NOT NULL,
        object_types    TEXT[] NOT NULL,
        is_symmetric    BOOLEAN NOT NULL,
        max_per_subject INTEGER CHECK (max_per_subject IS NULL OR max_per_subject >= 1),
        conflicts_with  TEXT[] NOT NULL,
        status          TEXT NOT NULL CHECK (status IN ('active', 'deprecated')),
        replaced_by     TEXT,
        created_at      BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, key)
    );

    CREATE TABLE memory_entities (
        {_memory_scope()}
        id          TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{{32}}$'),
        type        TEXT NOT NULL,
        name        TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 100),
        description TEXT NOT NULL,
        status      TEXT NOT NULL
            CHECK (status IN ('proposed', 'confirmed', 'rejected', 'merged')),
        merged_into TEXT,
        source      TEXT NOT NULL,
        created_at  BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, id),
        CHECK ((status = 'merged') = (merged_into IS NOT NULL)),
        FOREIGN KEY (guild_id, campaign_id, merged_into)
            REFERENCES memory_entities (guild_id, campaign_id, id)
            DEFERRABLE INITIALLY DEFERRED
    );

    CREATE TABLE memory_aliases (
        {_memory_scope()}
        id          TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{{32}}$'),
        entity_id   TEXT NOT NULL,
        text        TEXT NOT NULL CHECK (length(text) BETWEEN 1 AND 100),
        key         TEXT NOT NULL CHECK (length(key) BETWEEN 1 AND 100),
        kind        TEXT NOT NULL
            CHECK (kind IN ('full', 'short', 'nickname', 'title', 'misheard')),
        used_by     TEXT,
        secret      BOOLEAN NOT NULL,
        status      TEXT NOT NULL CHECK (status IN ('proposed', 'confirmed', 'rejected')),
        sound_codes TEXT[] NOT NULL,
        source      TEXT NOT NULL,
        created_at  BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, id),
        UNIQUE (guild_id, campaign_id, entity_id, key),
        {_entity_link("entity_id")},
        {_entity_link("used_by", cascade=False)}
    );
    CREATE INDEX memory_aliases_by_key ON memory_aliases (guild_id, campaign_id, key);
    CREATE INDEX memory_aliases_by_used_by ON memory_aliases (guild_id, campaign_id, used_by)
        WHERE used_by IS NOT NULL;
    CREATE INDEX memory_entities_by_merged_into
        ON memory_entities (guild_id, campaign_id, merged_into) WHERE merged_into IS NOT NULL;

    -- "This part of this line refers to this entity." Lines will get their own table
    -- with the transcript channel (#124); until then a mention names its line.
    CREATE TABLE memory_mentions (
        {_memory_scope()}
        id                 TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{{32}}$'),
        entity_id          TEXT NOT NULL,
        session_started_at BIGINT,
        line_ref           TEXT NOT NULL CHECK (length(line_ref) <= 100),
        span_start         INTEGER NOT NULL CHECK (span_start >= 0),
        span_end           INTEGER NOT NULL CHECK (span_end > span_start),
        confidence         DOUBLE PRECISION NOT NULL CHECK (confidence BETWEEN 0 AND 1),
        method             TEXT NOT NULL
            CHECK (method IN ('exact', 'sound', 'spelling', 'context', 'dm')),
        created_at         BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, id),
        {_entity_link("entity_id")}
    );
    CREATE INDEX memory_mentions_by_entity ON memory_mentions (guild_id, campaign_id, entity_id);

    -- Facts are never overwritten: a change (ally to enemy) ends one and adds another.
    -- Times are when the fact holds: session start times now, game time with TimeBot.
    CREATE TABLE memory_relations (
        {_memory_scope()}
        id              TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{{32}}$'),
        subject_id      TEXT NOT NULL,
        predicate       TEXT NOT NULL,
        object_id       TEXT NOT NULL,
        detail          TEXT NOT NULL CHECK (length(detail) <= 100),
        confidence      DOUBLE PRECISION NOT NULL CHECK (confidence BETWEEN 0 AND 1),
        status          TEXT NOT NULL CHECK (status IN ('proposed', 'confirmed', 'rejected')),
        source          TEXT NOT NULL,
        mention_ids     TEXT[] NOT NULL,
        from_session_at BIGINT,
        to_session_at   BIGINT,
        from_game_time  BIGINT,
        to_game_time    BIGINT,
        secret          BOOLEAN NOT NULL,
        created_at      BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, id),
        CHECK (subject_id <> object_id),
        {_entity_link("subject_id")},
        {_entity_link("object_id")}
    );
    CREATE INDEX memory_relations_by_subject
        ON memory_relations (guild_id, campaign_id, subject_id);
    CREATE INDEX memory_relations_by_object
        ON memory_relations (guild_id, campaign_id, object_id);

    -- What a word heard should become ("fix"), or must stay ("keep": an Undo or
    -- "Keep as heard" became a "don't change this" rule).
    CREATE TABLE memory_corrections (
        {_memory_scope()}
        id         TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{{32}}$'),
        heard      TEXT NOT NULL CHECK (length(heard) BETWEEN 1 AND 100),
        heard_key  TEXT NOT NULL CHECK (length(heard_key) BETWEEN 1 AND 100),
        entity_id  TEXT,
        action     TEXT NOT NULL CHECK (action IN ('fix', 'keep')),
        source     TEXT NOT NULL,
        created_at BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, id),
        CHECK ((action = 'fix') = (entity_id IS NOT NULL)),
        {_entity_link("entity_id")}
    );
    CREATE INDEX memory_corrections_by_key
        ON memory_corrections (guild_id, campaign_id, heard_key);
    CREATE INDEX memory_corrections_by_entity
        ON memory_corrections (guild_id, campaign_id, entity_id) WHERE entity_id IS NOT NULL;

    -- A failed rule check, kept for review: never fixed silently.
    CREATE TABLE memory_flags (
        {_memory_scope()}
        id          TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{{32}}$'),
        kind        TEXT NOT NULL
            CHECK (kind IN ('wrong_subject', 'wrong_object', 'too_many', 'contradiction')),
        relation_id TEXT NOT NULL,
        other_id    TEXT,
        status      TEXT NOT NULL CHECK (status IN ('open', 'resolved')),
        created_at  BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, id),
        FOREIGN KEY (guild_id, campaign_id, relation_id)
            REFERENCES memory_relations (guild_id, campaign_id, id) ON DELETE CASCADE,
        FOREIGN KEY (guild_id, campaign_id, other_id)
            REFERENCES memory_relations (guild_id, campaign_id, id) ON DELETE CASCADE
    );
    CREATE INDEX memory_flags_by_relation ON memory_flags (guild_id, campaign_id, relation_id);
    CREATE INDEX memory_flags_by_other ON memory_flags (guild_id, campaign_id, other_id)
        WHERE other_id IS NOT NULL;

    -- Append-only log of every change, so any operation can be undone. One operation
    -- (a "batch") may change several rows. Not part of backups.
    CREATE TABLE memory_changes (
        {_memory_scope()}
        version    BIGINT NOT NULL,
        batch      BIGINT NOT NULL,
        table_name TEXT NOT NULL,
        row_id     TEXT NOT NULL,
        op         TEXT NOT NULL CHECK (op IN ('insert', 'update', 'delete')),
        before     JSONB,
        after      JSONB,
        source     TEXT NOT NULL,
        undoes     BIGINT,
        made_at    BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, version)
    );
    CREATE INDEX memory_changes_by_batch ON memory_changes (guild_id, campaign_id, batch);
    CREATE INDEX memory_changes_by_undo ON memory_changes (guild_id, campaign_id, undoes)
        WHERE undoes IS NOT NULL;
    """ + "".join(_isolate(t) for t in _MEMORY_TABLES)

CONSENT_OUTSIDE = """
    -- Which outside speech-to-text engine (deepgram, cloud) the consent request named
    -- when this yes was given; NULL = it named none (local Whisper). While the server
    -- uses an outside engine, only yeses for that same engine count; everyone else is
    -- asked again (#170, docs/PLAN.md "Consent"). Existing yeses: NULL.
    ALTER TABLE consent ADD COLUMN IF NOT EXISTS outside_to TEXT;
    """

CONSENT_TERMS = """
    -- Which wording each person agreed to, and how (#35). Rows from before this are
    -- treated as version 1 (before "anyone in this server can read the transcript"):
    -- we can't tell which wording each saw, so they no longer count and those people
    -- are asked again.
    ALTER TABLE consent ADD COLUMN terms_version INTEGER NOT NULL DEFAULT 1
        CHECK (terms_version >= 1);
    ALTER TABLE consent ADD COLUMN method TEXT NOT NULL DEFAULT 'unknown'
        CHECK (method IN ('private_message', 'consent_command', 'unknown'));
    """

TRANSCRIPT_CHANNEL = """
    -- The campaign's live transcript channel, dmb-transcript-<short name> (#124). Like
    -- the DM screen it belongs to the server, so it isn't part of backups.
    ALTER TABLE campaigns ADD COLUMN transcript_channel_id BIGINT;
    """

PLAYED_BY = """
    -- Which Discord user plays a player character (#126, step 3), so DMbot can label
    -- their lines with the character (#53) and hint the character's name.
    ALTER TABLE memory_entities ADD COLUMN played_by BIGINT;
    """

TRANSCRIPTS = (
    """
    -- Stored session transcripts (#41, #125): anyone in the server may download them.
    -- One row per session, kept by the campaign and deleted with it. A session that
    -- resumes after a restart keeps its row (same campaign and start time).
    CREATE TABLE transcript_sessions (
        id          TEXT PRIMARY KEY,
        guild_id    BIGINT NOT NULL,
        campaign_id TEXT NOT NULL,
        started_at  BIGINT NOT NULL,
        ended_at    BIGINT,
        -- Kept with every save, so listing sessions never reads their lines.
        line_count  INTEGER NOT NULL DEFAULT 0,
        speakers    BIGINT[] NOT NULL DEFAULT '{}',
        UNIQUE (id, guild_id),
        UNIQUE (campaign_id, guild_id, started_at),
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE
    );
    CREATE INDEX transcript_sessions_recent
        ON transcript_sessions (guild_id, campaign_id, started_at DESC)
        WHERE line_count > 0;

    -- One row per piece of speech, only from people who agreed to be recorded. `heard`
    -- is exactly what speech-to-text wrote and never changes; `text` is the cleaned
    -- version, NULL while it's the same as `heard` (always, until the Transcript
    -- Cleaner, #127). Never DM-screen content.
    -- Speaker names aren't stored: downloads use display names at the time.
    CREATE TABLE transcript_lines (
        id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        guild_id   BIGINT NOT NULL,
        session_id TEXT NOT NULL,
        started_ms BIGINT NOT NULL,
        user_id    BIGINT NOT NULL,
        heard      TEXT NOT NULL,
        text       TEXT,
        FOREIGN KEY (session_id, guild_id)
            REFERENCES transcript_sessions (id, guild_id) ON DELETE CASCADE
    );
    CREATE INDEX transcript_lines_order ON transcript_lines (session_id, started_ms, id);
    -- For "Delete my past transcripts" and retention (later).
    CREATE INDEX transcript_lines_speaker ON transcript_lines (guild_id, user_id);
    """
    + _isolate("transcript_sessions")
    + _isolate("transcript_lines")
)

MEMORY_HEARD = f"""
    -- How often each known name was said, per session and speaker (#126, #127): kept at
    -- the end of a session to rank speech-to-text hints. Counts, not words: small (one
    -- row per name, session and speaker), deleted with the name (no undo entry: these
    -- are observations, not edits) and with the campaign. The speaker is kept so a
    -- person's counts can be removed with their lines (Retention).
    CREATE TABLE memory_heard (
        {_memory_scope()}
        entity_id          TEXT NOT NULL,
        session_started_at BIGINT NOT NULL,
        speaker_id         BIGINT NOT NULL,
        times              INTEGER NOT NULL CHECK (times > 0),
        PRIMARY KEY (guild_id, campaign_id, session_started_at, entity_id, speaker_id),
        {_entity_link("entity_id")}
    );
    -- Deleting or merging a name, and reading counts per name.
    CREATE INDEX memory_heard_by_entity ON memory_heard (guild_id, campaign_id, entity_id);
    CREATE INDEX memory_heard_by_speaker ON memory_heard (guild_id, speaker_id);
    """ + _isolate("memory_heard")


CAMPAIGN_OWNER = """
    -- The campaign's owner (#437): the subscriber whose plan's hours and campaign count
    -- it uses. Set to the creating DM, and to whoever restores a backup as a new
    -- campaign; replacing a campaign keeps its owner. NULL for campaigns from before
    -- this (no creator was recorded; #437 decides how they get one).
    ALTER TABLE campaigns ADD COLUMN owner_user_id BIGINT;
    """

HANDOVER_OFFERS = """
    -- Offers to hand a campaign over to another subscriber (#437 part 1b). Only the
    -- owner offers; ownership moves only when the person offered accepts, within 7 days
    -- (created_at + 7 days, checked on every read and accept), and only if they still
    -- have a free campaign slot then. One open offer per campaign. An open offer found
    -- past its 7 days is marked 'expired' when the next one is made. Not in backups:
    -- an offer is between two people, not part of the campaign's story.
    CREATE TABLE campaign_handover_offers (
        id           BIGSERIAL PRIMARY KEY,
        guild_id     BIGINT NOT NULL,
        campaign_id  TEXT NOT NULL,
        from_user_id BIGINT NOT NULL,
        to_user_id   BIGINT NOT NULL CHECK (to_user_id <> from_user_id),
        created_at   BIGINT NOT NULL,
        status       TEXT NOT NULL DEFAULT 'open'
            CHECK (status IN ('open', 'accepted', 'declined', 'withdrawn', 'expired')),
        decided_at   BIGINT,
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE
    );
    CREATE UNIQUE INDEX campaign_handover_offers_one_open
        ON campaign_handover_offers (guild_id, campaign_id) WHERE status = 'open';
    -- Every offer of a campaign, for deleting it with the campaign (answered ones stay).
    CREATE INDEX campaign_handover_offers_by_campaign
        ON campaign_handover_offers (guild_id, campaign_id);
    """ + _isolate("campaign_handover_offers")

HANDOVER_NAMES = """
    -- Both people's display names as they were when the offer was made (#437, decided
    -- 2026-10-08): the account page shows "Oskar wants to hand you…", and the website
    -- holds nobody's name but the signed-in person's. They go with the row (deleted
    -- with the campaign); nothing else stores them. Correction to 0021's note: an open
    -- offer past its 7 days is marked expired when the next one is made, and also when
    -- anyone accepts, declines or withdraws it.
    -- 100: campaigns.models.NAME_ON_OFFER_MAX.
    ALTER TABLE campaign_handover_offers
        ADD COLUMN from_name TEXT NOT NULL DEFAULT '' CHECK (length(from_name) <= 100),
        ADD COLUMN to_name   TEXT NOT NULL DEFAULT '' CHECK (length(to_name) <= 100);
    ALTER TABLE campaign_handover_offers
        ALTER COLUMN from_name DROP DEFAULT,
        ALTER COLUMN to_name DROP DEFAULT;
    """

HANDOVER_OFFER_INDEXES = """
    -- The account page reads a person's open offers on every visit (#614): by who they
    -- were offered to, and by who offered them.
    CREATE INDEX campaign_handover_offers_open_to
        ON campaign_handover_offers (to_user_id) WHERE status = 'open';
    CREATE INDEX campaign_handover_offers_open_from
        ON campaign_handover_offers (from_user_id) WHERE status = 'open';
"""

HANDOVER_DELIVERED = """
    -- When DMbot sent the private message about an offer (#690). An offer made on the
    -- website is saved with neither and announced on dmbot_handover_offers. The bot
    -- that serves the server claims it (claimed_at, for a few minutes: a send cut short
    -- is tried again once the claim lapses), sends the message, then sets delivered_at,
    -- so it's sent once. Every offer before this was made in Discord and sent there and
    -- then. A writer that leaves delivered_at empty gets its offer sent by the bot.
    ALTER TABLE campaign_handover_offers
        ADD COLUMN delivered_at BIGINT,
        ADD COLUMN claimed_at   BIGINT;
    -- Migrations run with no server set, so row-level security hides every row: open
    -- the table to this one UPDATE (which also reads rows, hence FOR ALL), dropped again
    -- inside the migration's transaction.
    CREATE POLICY migrate_backfill ON campaign_handover_offers
        FOR ALL USING (true) WITH CHECK (true);
    UPDATE campaign_handover_offers SET delivered_at = created_at;
    DROP POLICY migrate_backfill ON campaign_handover_offers;
    -- The bot's sweep when it starts listening: open offers not sent yet.
    CREATE INDEX campaign_handover_offers_undelivered
        ON campaign_handover_offers (guild_id)
        WHERE status = 'open' AND delivered_at IS NULL;
    """

HANDOVER_EXPIRY = """
    -- An offer whose 7 days are up is announced once (#690): the owner is told, and the
    -- buttons come off the private message to the person offered (message_id). Offers
    -- already closed, or already past their 7 days, count as told, so nobody gets an old
    -- notice. end_told_at is set just before the owner is told, and cleared again if
    -- telling them failed for a moment (the next sweep tries again).
    ALTER TABLE campaign_handover_offers
        ADD COLUMN message_id  BIGINT,
        ADD COLUMN end_told_at BIGINT;
    -- Migrations run with no server set, so row-level security hides every row: open
    -- the table to this one UPDATE, dropped again inside the migration's transaction.
    CREATE POLICY migrate_backfill ON campaign_handover_offers
        FOR ALL USING (true) WITH CHECK (true);
    UPDATE campaign_handover_offers SET end_told_at = COALESCE(decided_at, created_at)
        WHERE status <> 'open'
           OR created_at + 604800 <= extract(epoch FROM now())::BIGINT;
    DROP POLICY migrate_backfill ON campaign_handover_offers;
    -- The sweep's "ended, nobody told yet" (stays nearly empty).
    CREATE INDEX campaign_handover_offers_untold
        ON campaign_handover_offers (guild_id)
        WHERE status = 'expired' AND end_told_at IS NULL;
    """

CHARACTER_SHEETS = f"""
    -- A player character's D&D Beyond sheet (#723; docs/PLAN.md, "D&D Beyond character
    -- sheets"): its link, and a small snapshot of names and numbers only (built by
    -- dmbot.memory.sheets, an allow list: never descriptions or rules text). One per
    -- character per campaign, never shared between campaigns; deleted with the
    -- character or the campaign; in backups. Not in the undo log: a refresh isn't a
    -- change the DM undoes. source: 'dndbeyond' (read from the link) or 'typed' (the
    -- player's own form, with no link).
    CREATE TABLE character_sheets (
        {_memory_scope()}
        entity_id  TEXT NOT NULL,
        url        TEXT CHECK (url ~ '^https://www[.]dndbeyond[.]com/characters/[0-9]{{1,12}}$'),
        sheet      JSONB CHECK (sheet IS NULL OR octet_length(sheet::text) <= 65536),
        source     TEXT CHECK (source IN ('dndbeyond', 'typed')),
        fetched_at BIGINT,
        PRIMARY KEY (guild_id, campaign_id, entity_id),
        CHECK (url IS NOT NULL OR sheet IS NOT NULL),
        CHECK ((sheet IS NULL) = (source IS NULL) AND (sheet IS NULL) = (fetched_at IS NULL)),
        {_entity_link("entity_id")}
    );
    """ + _isolate("character_sheets")

SHEET_PLAYER = """
    -- Who linked or typed in a character's sheet (#723 review): the player playing it
    -- then. A sheet is shown and used only while that same person plays the character,
    -- so a character given to someone else never shows the last player's link or
    -- details (whichever way it changed hands, an undo included). Rows from before this
    -- belong to whoever plays the character now. Migrations run with no server set, so
    -- row-level security hides every row: the backfill opens the table for itself.
    ALTER TABLE character_sheets ADD COLUMN player_id BIGINT;
    -- Open every table the statements read, not only the one they write (CLAUDE.md):
    -- the UPDATE reads memory_entities too.
    CREATE POLICY migrate_backfill ON character_sheets FOR ALL USING (true) WITH CHECK (true);
    CREATE POLICY migrate_backfill ON memory_entities FOR SELECT USING (true);
    UPDATE character_sheets s SET player_id = e.played_by
        FROM memory_entities e
        WHERE e.guild_id = s.guild_id AND e.campaign_id = s.campaign_id AND e.id = s.entity_id;
    DELETE FROM character_sheets WHERE player_id IS NULL;
    DROP POLICY migrate_backfill ON memory_entities;
    DROP POLICY migrate_backfill ON character_sheets;
    ALTER TABLE character_sheets ALTER COLUMN player_id SET NOT NULL;
    """

PEOPLE = """
    -- The table's people are entries of their own (#1188): a player or the DM is a
    -- `person`, and a `plays` link goes from the person to the character. Every character
    -- played by someone today gets its person (named `Player` and the last four digits of
    -- the Discord ID until a better name is known) and the link. `played_by` stays on the
    -- character as a mirror that the sheets and fights still read. Migrations run with no
    -- server set, so row-level security hides every row: the backfill opens every table
    -- it reads or writes for itself (CLAUDE.md).
    CREATE POLICY migrate_backfill ON memory_entities FOR ALL USING (true) WITH CHECK (true);
    CREATE POLICY migrate_backfill ON memory_aliases FOR ALL USING (true) WITH CHECK (true);
    CREATE POLICY migrate_backfill ON memory_relations FOR ALL USING (true) WITH CHECK (true);
    INSERT INTO memory_entities (guild_id, campaign_id, id, type, name, description, status,
            merged_into, source, created_at, played_by, role, needs_look)
        SELECT guild_id, campaign_id, md5(random()::text || clock_timestamp()::text),
            'person', 'Player ' || right(played_by::text, 4), '', 'confirmed', NULL,
            'migration', extract(epoch FROM now())::bigint, played_by, NULL, false
        FROM (SELECT DISTINCT guild_id, campaign_id, played_by FROM memory_entities
              WHERE role = 'player_character' AND played_by IS NOT NULL
                AND status IN ('proposed', 'confirmed')) players;
    INSERT INTO memory_aliases (guild_id, campaign_id, id, entity_id, text, key, kind, used_by,
            secret, status, sound_codes, source, created_at)
        SELECT guild_id, campaign_id, md5(random()::text || clock_timestamp()::text), id, name,
            lower(name), 'full', NULL, false, 'confirmed', ARRAY[]::text[], 'migration',
            extract(epoch FROM now())::bigint
        FROM memory_entities WHERE type = 'person' AND source = 'migration';
    INSERT INTO memory_relations (guild_id, campaign_id, id, subject_id, predicate, object_id,
            detail, confidence, status, source, mention_ids, from_session_at, to_session_at,
            from_game_time, to_game_time, secret, created_at)
        SELECT c.guild_id, c.campaign_id, md5(random()::text || clock_timestamp()::text),
            p.id, 'plays', c.id, '', 1.0, 'confirmed', 'migration', ARRAY[]::text[], NULL, NULL,
            NULL, NULL, false, extract(epoch FROM now())::bigint
        FROM memory_entities c
        JOIN memory_entities p ON p.guild_id = c.guild_id AND p.campaign_id = c.campaign_id
            AND p.type = 'person' AND p.played_by = c.played_by
            AND p.status IN ('proposed', 'confirmed')
        WHERE c.role = 'player_character' AND c.played_by IS NOT NULL
            AND c.status IN ('proposed', 'confirmed');
    DROP POLICY migrate_backfill ON memory_relations;
    DROP POLICY migrate_backfill ON memory_aliases;
    DROP POLICY migrate_backfill ON memory_entities;
    -- The table's links to its entries are checked at the end of a transaction, and an
    -- index can't be built while checks are waiting: run them now.
    SET CONSTRAINTS ALL IMMEDIATE;
    -- One person entry for each Discord user in a campaign.
    CREATE UNIQUE INDEX memory_entities_one_person
        ON memory_entities (guild_id, campaign_id, played_by)
        WHERE type = 'person' AND status IN ('proposed', 'confirmed');
    """

DEFINITIONS = """
    -- The official relationships, traceable (#1189). The definitions the code ships are kept
    -- here by version, written at every start (a key and version already here are never
    -- rewritten), so a fact can say which definition it was written under. Shared by every
    -- campaign and holding no campaign data: no guild_id, so no row-level security (like
    -- schema_migrations).
    CREATE TABLE ontology_predicate_versions (
        key             TEXT NOT NULL CHECK (key ~ '^[a-z][a-z0-9_]{1,39}$'),
        version         INTEGER NOT NULL CHECK (version >= 1),
        label           TEXT NOT NULL,
        inverse_label   TEXT NOT NULL,
        description     TEXT NOT NULL,
        covers          TEXT[] NOT NULL,
        not_this        TEXT[] NOT NULL,
        examples        TEXT[] NOT NULL,
        synonyms        TEXT[] NOT NULL,
        subject_types   TEXT[] NOT NULL,
        object_types    TEXT[] NOT NULL,
        is_symmetric    BOOLEAN NOT NULL,
        created_at      BIGINT NOT NULL,
        PRIMARY KEY (key, version)
    );
    -- A campaign's own relationships carry the same fields, with a version.
    ALTER TABLE memory_predicates
        ADD COLUMN covers        TEXT[] NOT NULL DEFAULT '{}',
        ADD COLUMN not_this      TEXT[] NOT NULL DEFAULT '{}',
        ADD COLUMN inverse_label TEXT NOT NULL DEFAULT '',
        ADD COLUMN synonyms      TEXT[] NOT NULL DEFAULT '{}',
        ADD COLUMN version       INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1);
    -- Every fact says which version of its relationship's definition it was written under.
    -- Facts from before were all written under version 1: the column's default gives them
    -- that without an UPDATE, so no row-level security has to be opened for it.
    ALTER TABLE memory_relations
        ADD COLUMN definition_version INTEGER NOT NULL DEFAULT 1 CHECK (definition_version >= 1);
    """

RULE_SETS = (
    f"""
    -- Shared rulesets (#1190; docs/PLAN.md, "Shared rulesets"): the one place rules cross from
    -- one campaign to another. A DM of a paid campaign publishes a set of its own house rules;
    -- another campaign's DM may follow it, in the same server or from another with a share
    -- code. Only rules cross: a set's content is house-rule text and the document link,
    -- never story, names, transcripts, notes or anything from a shared rulebook or adventure.
    --   rule_sets          the published set, in its publisher's campaign (server-isolated)
    --   rule_set_versions  each version's content; version 1 is the rules when it was shared
    --   rule_set_codes     share codes the publishing DM made (a code is the capability to see
    --                      the set; unguessable; turned off by the DM)
    --   rule_set_follows   a follower campaign's own copy of the version it uses, so its
    --                      rules keep working if the set goes (and for its backup)
    -- Every table is isolated to its own server and campaign like the rest. The one door
    -- across servers is `dmbot_set_by_code`: it reads a shared set by an active code and
    -- returns that set's name, its publisher campaign's name and its latest version, nothing
    -- else (like the pause and orphan doors, it opens the tables to itself through a setting
    -- it sets and puts back).
    CREATE TABLE rule_sets (
        {_memory_scope()}
        id         TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{{32}}$'),
        kind       TEXT NOT NULL CHECK (kind IN ('house_rules', 'homebrew')),
        name       TEXT NOT NULL CHECK (char_length(name) BETWEEN 1 AND 60),
        status     TEXT NOT NULL CHECK (status IN ('shared', 'unshared')),
        created_by BIGINT NOT NULL CHECK (created_by > 0),
        created_at BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, id),
        UNIQUE (id)
    );
    CREATE TABLE rule_set_versions (
        {_memory_scope()}
        set_id     TEXT NOT NULL,
        version    INTEGER NOT NULL CHECK (version >= 1),
        content    JSONB NOT NULL CHECK (jsonb_typeof(content) = 'object'),
        created_by BIGINT NOT NULL CHECK (created_by > 0),
        created_at BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, set_id, version),
        FOREIGN KEY (guild_id, campaign_id, set_id)
            REFERENCES rule_sets (guild_id, campaign_id, id) ON DELETE CASCADE
    );
    CREATE TABLE rule_set_codes (
        {_memory_scope()}
        code       TEXT NOT NULL CHECK (code ~ '^[A-Z2-7]{{10}}$'),
        set_id     TEXT NOT NULL,
        active     BOOLEAN NOT NULL,
        created_at BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, code),
        UNIQUE (code),
        FOREIGN KEY (guild_id, campaign_id, set_id)
            REFERENCES rule_sets (guild_id, campaign_id, id) ON DELETE CASCADE
    );
    CREATE TABLE rule_set_follows (
        {_memory_scope()}
        set_id       TEXT NOT NULL,
        kind         TEXT NOT NULL CHECK (kind IN ('house_rules', 'homebrew')),
        set_name     TEXT NOT NULL CHECK (char_length(set_name) BETWEEN 1 AND 60),
        publisher    TEXT NOT NULL CHECK (char_length(publisher) BETWEEN 1 AND 100),
        version      INTEGER NOT NULL CHECK (version >= 1),
        content      JSONB NOT NULL CHECK (jsonb_typeof(content) = 'object'),
        position     INTEGER NOT NULL DEFAULT 0 CHECK (position >= 0),
        updating     BOOLEAN NOT NULL,
        followed_by  BIGINT NOT NULL CHECK (followed_by > 0),
        followed_at  BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, set_id)
    );
    -- A campaign shares one house-rules set, and follows one.
    CREATE UNIQUE INDEX rule_sets_one_house_set
        ON rule_sets (guild_id, campaign_id) WHERE kind = 'house_rules';
    CREATE UNIQUE INDEX rule_set_follows_one_house_set
        ON rule_set_follows (guild_id, campaign_id) WHERE kind = 'house_rules';
    """
    + "".join(
        _isolate(t)
        for t in ("rule_sets", "rule_set_versions", "rule_set_codes", "rule_set_follows")
    )
    + """
    -- The door. Note: the policies below also let a caller read their OWN server's rows (the
    -- plain guild_isolation policy already does); what they add is the one set a code opens.
    -- The `campaigns` policy has no code condition of its own on purpose: its sub-select on
    -- rule_sets is itself filtered by these row-level rules, so it reaches only a set whose
    -- active code equals the one the function was given. Both settings are transaction-local,
    -- so an error inside the function rolls them back; the restore lines are for success.
    -- TO the role that owns the function (the one running this migration), never the
    -- website's role. Each policy needs the setting the function sets, and the code it sets.
    DO $do$
    BEGIN
        EXECUTE format(
            'CREATE POLICY code_door ON rule_set_codes FOR SELECT TO %I
                USING (dmbot_owner_sync() = ''setcode''
                       AND code = current_setting(''dmbot.set_code'', true) AND active)',
            current_user);
        EXECUTE format(
            'CREATE POLICY code_door ON rule_sets FOR SELECT TO %I
                USING (dmbot_owner_sync() = ''setcode'' AND status = ''shared''
                       AND id IN (SELECT set_id FROM rule_set_codes))',
            current_user);
        EXECUTE format(
            'CREATE POLICY code_door ON rule_set_versions FOR SELECT TO %I
                USING (dmbot_owner_sync() = ''setcode''
                       AND set_id IN (SELECT id FROM rule_sets))',
            current_user);
        EXECUTE format(
            'CREATE POLICY code_door ON campaigns FOR SELECT TO %I
                USING (dmbot_owner_sync() = ''setcode''
                       AND EXISTS (SELECT 1 FROM rule_sets s
                                   WHERE s.guild_id = campaigns.guild_id
                                     AND s.campaign_id = campaigns.id))',
            current_user);
    END
    $do$;
    CREATE FUNCTION dmbot_set_by_code(wanted TEXT)
        RETURNS TABLE (set_id TEXT, kind TEXT, set_name TEXT, publisher TEXT, version INTEGER,
                       content JSONB)
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    DECLARE
        before TEXT := COALESCE(current_setting('dmbot.owner_sync', true), '');
        before_code TEXT := COALESCE(current_setting('dmbot.set_code', true), '');
    BEGIN
        PERFORM set_config('dmbot.owner_sync', 'setcode', true);
        PERFORM set_config('dmbot.set_code', wanted, true);
        RETURN QUERY
            SELECT s.id, s.kind, s.name, c.name, v.version, v.content
            FROM rule_set_codes rc
            JOIN rule_sets s ON s.id = rc.set_id
            JOIN campaigns c ON c.guild_id = s.guild_id AND c.id = s.campaign_id
            JOIN rule_set_versions v ON v.guild_id = s.guild_id AND v.campaign_id = s.campaign_id
                AND v.set_id = s.id
            WHERE rc.code = wanted AND rc.active AND s.status = 'shared'
            ORDER BY v.version DESC
            LIMIT 1;
        PERFORM set_config('dmbot.owner_sync', before, true);
        PERFORM set_config('dmbot.set_code', before_code, true);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_set_by_code(TEXT) SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_set_by_code(TEXT) FROM PUBLIC;
"""
)

OWNER_MISSING = """
    -- When DMbot first saw that a campaign's owner is not in the server any more (#1214):
    -- after 7 days the campaign becomes ownerless, as if the account were deleted. NULL when the
    -- owner is here (or not known to be gone). A column with no default: no rows change, so no
    -- row-level-security policy is needed.
    ALTER TABLE campaigns ADD COLUMN owner_missing_since BIGINT;
    """

SHARED_CONFIRMATIONS = f"""
    -- Who confirmed the right to use shared material, and when (CLAUDE.md, IP rule:
    -- "Record who confirmed and when"; #252): one row per confirmation, what it was for
    -- (a names list now; later a shared story or a rulebook), and a SHA-256 fingerprint
    -- of the text that tells documents apart. Never the text or the file's name.
    -- Deleted with the campaign; carried in its backups.
    CREATE TABLE shared_confirmations (
        {_memory_scope()}
        id           TEXT NOT NULL,
        user_id      BIGINT NOT NULL,
        purpose      TEXT NOT NULL CHECK (purpose IN ('names_list', 'shared_story', 'rulebook')),
        fingerprint  TEXT NOT NULL CHECK (fingerprint ~ '^[0-9a-f]{{64}}$'),
        confirmed_at BIGINT NOT NULL,
        -- Read from a backup file, not pressed here (anyone may restore one).
        restored     BOOLEAN NOT NULL DEFAULT false,
        PRIMARY KEY (guild_id, campaign_id, id)
    );
    """ + _isolate("shared_confirmations")


def _isolate_user(table: str) -> str:
    """Row-level security for a person's own rows (website accounts, #435): only the
    signed-in user's rows are visible or writable. Set by Database.user()."""
    return f"""
    ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;
    ALTER TABLE {table} FORCE ROW LEVEL SECURITY;
    CREATE POLICY user_isolation ON {table}
        USING (user_id = dmbot_current_user())
        WITH CHECK (user_id = dmbot_current_user());
    """


def _setting(name: str, setting: str, kind: str) -> str:
    """A function reading one per-transaction setting, or NULL when it isn't set."""
    return f"""
    CREATE FUNCTION {name}() RETURNS {kind}
        LANGUAGE sql STABLE
        AS $fn$ SELECT NULLIF(current_setting('{setting}', true), '')::{kind} $fn$;
    """


TRANSCRIPT_ENGINES = """
    -- Which speech-to-text wrote each session down (#173): "engine model host", one
    -- per engine used (a session resumed after a restart may switch). Not secret: no
    -- keys, only the engine, its model and the endpoint's host.
    ALTER TABLE transcript_sessions ADD COLUMN engines TEXT[] NOT NULL DEFAULT '{}';
    """

DM_SCREEN_LEVEL = """
    -- How much DMbot says in the DM screen (#504): quiet, normal (the default) or
    -- chatty. Alerts are shown at every level.
    ALTER TABLE campaigns ADD COLUMN dm_screen_level TEXT NOT NULL DEFAULT 'normal'
        CHECK (dm_screen_level IN ('quiet', 'normal', 'chatty'));
    """

TRANSCRIPT_TOPICS = """
    -- The off-topic filter (#52): each line's topic (only off_topic is hidden, in the
    -- cleaned transcript; the as-heard one keeps everything) and how long it was said,
    -- for the "[1m 22s of off-topic chat skipped]" marker. Old lines: game, 0 ms.
    ALTER TABLE transcript_lines
        ADD COLUMN topic TEXT NOT NULL DEFAULT 'game'
            CHECK (topic IN ('game', 'table_talk', 'off_topic')),
        ADD COLUMN duration_ms INTEGER NOT NULL DEFAULT 0 CHECK (duration_ms >= 0);
    """

TRANSCRIPT_ENTITY = """
    -- Who a line was said as (#53): NULL when nobody is named (the player's character is
    -- shown from the sheet), 'narrating' for the DM's narration, or the confirmed, non-secret
    -- name of the NPC the DM was voicing. Never a secret name: the writer checks that.
    ALTER TABLE transcript_lines
        ADD COLUMN entity TEXT CHECK (entity IS NULL OR length(entity) BETWEEN 1 AND 120);
    """

TRANSCRIPT_SIDEBAR = """
    -- DM sidebar lines (#935): the DM's question and DMbot's answer, NULL for table speech.
    -- In the as-heard (raw) transcript for everyone who may read transcripts, never in the
    -- cleaned one (owner, #933). Every one records where it came from: a question its short
    -- id, how it came in and the speech-to-text used; a reply the question it answers, the
    -- AI model, the prompt version and the sources it used.
    ALTER TABLE transcript_lines
        ADD COLUMN sidebar TEXT CHECK (sidebar IN ('question', 'answer')),
        ADD COLUMN sidebar_ref TEXT,
        ADD COLUMN sidebar_reply_to TEXT,
        ADD COLUMN sidebar_via TEXT CHECK (sidebar_via IN ('voice-memo', 'typed', 'table-trigger')),
        ADD COLUMN sidebar_stt TEXT,
        ADD COLUMN sidebar_model TEXT,
        ADD COLUMN sidebar_prompt TEXT,
        ADD COLUMN sidebar_sources JSONB,
        ADD CONSTRAINT transcript_lines_sidebar_lineage
            CHECK (sidebar IS NOT NULL OR (sidebar_ref IS NULL AND sidebar_reply_to IS NULL
                AND sidebar_via IS NULL AND sidebar_stt IS NULL AND sidebar_model IS NULL
                AND sidebar_prompt IS NULL AND sidebar_sources IS NULL));
    """

FEEDBACK = """
    -- Messages sent from the website's "Say hello" page (#665). The message is also posted
    -- as a GitHub Discussion (with its date, nothing else); how to reach the sender stays
    -- here, with the team. No Discord id, no IP address: the form needs no sign-in.
    CREATE TABLE feedback (
        id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        kind          TEXT NOT NULL CHECK (kind IN ('feedback', 'question')),
        message       TEXT NOT NULL CHECK (char_length(message) BETWEEN 1 AND 2000),
        contact       TEXT CHECK (char_length(contact) BETWEEN 1 AND 200),
        discussion    INTEGER NOT NULL,  -- the GitHub Discussion's number
        created_at    BIGINT NOT NULL
    );
    -- Add-only: no policy lets anyone read or change a row (FORCE holds the table's
    -- owner, the bot's role, too; the bot never queries it, and the website's role may
    -- only INSERT and DELETE). The team reads contacts as the database's administrator.
    -- Contacts are kept for a year (privacy page, #665): the website's hourly sweep
    -- deletes older rows with a plain DELETE, which needs no right to read them.
    ALTER TABLE feedback ENABLE ROW LEVEL SECURITY;
    ALTER TABLE feedback FORCE ROW LEVEL SECURITY;
    CREATE POLICY add_only ON feedback FOR INSERT WITH CHECK (true);
    CREATE POLICY forget_after_a_year ON feedback FOR DELETE
        USING (dmbot_cleanup() = 'old-feedback' AND created_at <= dmbot_now() - 365 * 86400);
    """

WEB_ACCOUNTS = (
    _setting("dmbot_current_user", "dmbot.user_id", "BIGINT")
    + _setting("dmbot_current_session", "dmbot.session", "TEXT")
    + _setting("dmbot_install_guild", "dmbot.install_guild", "BIGINT")
    + _setting("dmbot_plan_writer", "dmbot.plan_writer", "TEXT")
    + _setting("dmbot_cleanup", "dmbot.cleanup", "TEXT")
    + """
    -- Settings, each set only for one transaction by dmbot.db.Database:
    --   dmbot.user_id       the website's signed-in person (Database.user)
    --   dmbot.session       the hash of the session cookie on this request (Database.session):
    --                       knowing the hash means holding the cookie
    --   dmbot.install_guild the one server a person is adding DMbot to or linking
    --                       (Database.user(install_guild=...)); unlocks only `installs`,
    --                       never the server's campaigns
    --   dmbot.plan_writer   set only by Database.plan_writer(): the payment webhook and
    --                       Try It, the only code allowed to change `entitlements`
    --   dmbot.cleanup       set only by Database.cleanup(): the website's hourly sweep
    --                       ('expired-sessions', and 'old-feedback' since 0026, #665)

    CREATE FUNCTION dmbot_now() RETURNS BIGINT
        LANGUAGE sql STABLE
        AS $fn$ SELECT extract(epoch FROM now())::BIGINT $fn$;

    -- A person who signed in on the website with Discord. Email is what Discord gives us
    -- (scope `email`), used only for account and plan messages (privacy page, #433).
    CREATE TABLE web_users (
        user_id            BIGINT PRIMARY KEY,
        email              TEXT,
        created_at         BIGINT NOT NULL,
        last_sign_in_at    BIGINT NOT NULL
    );

    -- Discord users who have started Try It, so it can be started only once (#435).
    -- Only the plan writer adds a row; nobody changes or removes one, and it stays after
    -- the account is deleted (it holds nothing but the Discord id; 0017 dropped the
    -- date), so deleting and signing up again doesn't give a second free month.
    CREATE TABLE try_it_used (
        user_id  BIGINT PRIMARY KEY,
        used_at  BIGINT NOT NULL
    );

    -- What each person has paid for, one row per person. Changed only by the plan writer
    -- (the payment webhook and Try It, dmbot.web, #435), which the database enforces
    -- with the dmbot.plan_writer setting; everyone else, including the bot's plan rules
    -- (#437), only reads it. `plan` matches plans.json. Caps are copied in when the plan
    -- is written, so a later price-list change never alters a plan someone bought.
    -- The payment company's events carry our Discord user id (checkout passes it as
    -- custom data), which is how the webhook finds the person.
    CREATE TABLE entitlements (
        user_id          BIGINT PRIMARY KEY REFERENCES web_users ON DELETE CASCADE,
        plan             TEXT NOT NULL
            CHECK (plan IN ('try-it', 'table', 'two-tables', 'guild', 'pro')),
        status           TEXT NOT NULL CHECK (status IN ('active', 'grace', 'lapsed')),
        hours_cap        INTEGER NOT NULL CHECK (hours_cap >= 0),
        -- Extra hours bought for the current period (+10 each, #432); back to 0 when a
        -- new period starts, so a renewal never wipes or keeps them by accident.
        extra_hours      INTEGER NOT NULL DEFAULT 0 CHECK (extra_hours >= 0),
        campaign_cap     INTEGER NOT NULL CHECK (campaign_cap >= 0),
        -- The billing period the hours belong to (Unix seconds).
        period_start     BIGINT NOT NULL,
        period_end       BIGINT NOT NULL CHECK (period_end > period_start),
        -- Status "grace": a failed payment must be fixed by then (7 days, #437).
        grace_ends_at    BIGINT,
        -- When the plan stopped (status "lapsed"): the 120-day retention counts from here.
        lapsed_at        BIGINT,
        -- When the plan last changed: on a downgrade, the first campaigns started after
        -- this stay active (#437).
        plan_changed_at  BIGINT NOT NULL,
        provider         TEXT NOT NULL,
        -- The payment company's own ids, for its customer page. Never card data.
        provider_customer_id      TEXT,
        provider_subscription_id  TEXT,
        -- The newest event applied, so an older event arriving late can't undo it.
        last_event_at    BIGINT NOT NULL,
        updated_at       BIGINT NOT NULL,
        CHECK ((status = 'grace') = (grace_ends_at IS NOT NULL)),
        CHECK ((status = 'lapsed') = (lapsed_at IS NOT NULL))
    );

    -- Payment events already applied, so a repeated delivery changes nothing (#435).
    -- Idempotency: INSERT ... ON CONFLICT DO NOTHING RETURNING in the same transaction as
    -- the entitlements change (the primary key spans everyone, even though each person
    -- sees only their own rows). Only the plan writer adds a row; nobody changes or
    -- removes one, so an event can never be applied twice. Ids only, no payment details.
    -- Events that name no person are not recorded here.
    CREATE TABLE payment_events (
        provider     TEXT NOT NULL,
        event_id     TEXT NOT NULL,
        user_id      BIGINT NOT NULL,
        received_at  BIGINT NOT NULL,
        PRIMARY KEY (provider, event_id)
    );
    CREATE INDEX payment_events_by_user ON payment_events (user_id);

    -- Website sessions. The cookie holds a random token; only its SHA-256 is stored, so a
    -- database leak can't be used to sign in. The Discord token is never stored: the
    -- servers Discord listed at sign-in are kept here, for this session only. An expired
    -- session can't be found by its hash; Database.cleanup() deletes expired rows.
    CREATE TABLE web_sessions (
        id_hash     TEXT PRIMARY KEY,
        user_id     BIGINT NOT NULL REFERENCES web_users ON DELETE CASCADE,
        created_at  BIGINT NOT NULL,
        expires_at  BIGINT NOT NULL,
        -- [{"id": "...", "name": "...", "manage": true}], from the `guilds` scope.
        guilds      JSONB NOT NULL DEFAULT '[]'
    );
    CREATE INDEX web_sessions_by_user ON web_sessions (user_id);
    CREATE INDEX web_sessions_expiry ON web_sessions (expires_at);

    -- Who added DMbot to which server (owner decision on #435). `via`: 'site' when added
    -- through the website's install button (the person is known), 'link' when the bot
    -- joined through a plain invite link (the person fills in later with "Link this
    -- server", after the web API checks they manage the server). Visible to that server
    -- and to the person who installed it; written only for the server being installed.
    CREATE TABLE installs (
        guild_id              BIGINT PRIMARY KEY,
        installed_by_user_id  BIGINT REFERENCES web_users ON DELETE SET NULL,
        installed_at          BIGINT NOT NULL,
        -- How DMbot arrived. The installer is empty for a link until someone links the
        -- server, and becomes empty again if that person deletes their account.
        via                   TEXT NOT NULL CHECK (via IN ('site', 'link'))
    );
    CREATE INDEX installs_by_user ON installs (installed_by_user_id)
        WHERE installed_by_user_id IS NOT NULL;
    """
    + _isolate_user("web_users")
    + """
    -- try_it_used and payment_events: the person reads their own rows; only the plan
    -- writer adds one; no policy allows changing or removing a row.
    ALTER TABLE try_it_used ENABLE ROW LEVEL SECURITY;
    ALTER TABLE try_it_used FORCE ROW LEVEL SECURITY;
    CREATE POLICY user_isolation ON try_it_used FOR SELECT
        USING (user_id = dmbot_current_user());
    CREATE POLICY plan_writer_insert ON try_it_used FOR INSERT
        WITH CHECK (user_id = dmbot_current_user() AND dmbot_plan_writer() = 'payments');

    ALTER TABLE payment_events ENABLE ROW LEVEL SECURITY;
    ALTER TABLE payment_events FORCE ROW LEVEL SECURITY;
    CREATE POLICY user_isolation ON payment_events FOR SELECT
        USING (user_id = dmbot_current_user());
    CREATE POLICY plan_writer_insert ON payment_events FOR INSERT
        WITH CHECK (user_id = dmbot_current_user() AND dmbot_plan_writer() = 'payments');

    -- entitlements: the person reads their own row; only the plan writer may change it.
    ALTER TABLE entitlements ENABLE ROW LEVEL SECURITY;
    ALTER TABLE entitlements FORCE ROW LEVEL SECURITY;
    CREATE POLICY user_isolation ON entitlements FOR SELECT
        USING (user_id = dmbot_current_user());
    CREATE POLICY plan_writer_insert ON entitlements FOR INSERT
        WITH CHECK (user_id = dmbot_current_user() AND dmbot_plan_writer() = 'payments');
    CREATE POLICY plan_writer_update ON entitlements FOR UPDATE
        USING (user_id = dmbot_current_user() AND dmbot_plan_writer() = 'payments')
        WITH CHECK (user_id = dmbot_current_user() AND dmbot_plan_writer() = 'payments');
    CREATE POLICY plan_writer_delete ON entitlements FOR DELETE
        USING (user_id = dmbot_current_user() AND dmbot_plan_writer() = 'payments');

    -- web_sessions: the person's own sessions, or the one session whose cookie was shown
    -- (not expired). The cookie alone can read its session, never change who owns it.
    -- The cleanup sweep sees and deletes expired sessions only.
    ALTER TABLE web_sessions ENABLE ROW LEVEL SECURITY;
    ALTER TABLE web_sessions FORCE ROW LEVEL SECURITY;
    CREATE POLICY user_isolation ON web_sessions
        USING (user_id = dmbot_current_user())
        WITH CHECK (user_id = dmbot_current_user());
    CREATE POLICY by_cookie ON web_sessions FOR SELECT
        USING (id_hash = dmbot_current_session() AND expires_at > dmbot_now());
    CREATE POLICY cleanup_read ON web_sessions FOR SELECT
        USING (dmbot_cleanup() = 'expired-sessions' AND expires_at <= dmbot_now());
    CREATE POLICY cleanup_delete ON web_sessions FOR DELETE
        USING (dmbot_cleanup() = 'expired-sessions' AND expires_at <= dmbot_now());

    -- installs: seen by its server and by its installer. The bot writes its own server's
    -- row (Database.guild). The website writes only the install server's row, only naming
    -- the signed-in person, and never takes over a row someone else installed or linked.
    -- Only the bot removes a row.
    ALTER TABLE installs ENABLE ROW LEVEL SECURITY;
    ALTER TABLE installs FORCE ROW LEVEL SECURITY;
    CREATE POLICY install_read ON installs FOR SELECT
        USING (guild_id = dmbot_current_guild()
               OR guild_id = dmbot_install_guild()
               OR installed_by_user_id = dmbot_current_user());
    CREATE POLICY install_insert ON installs FOR INSERT
        WITH CHECK (guild_id = dmbot_current_guild()
                    OR (guild_id = dmbot_install_guild()
                        AND installed_by_user_id = dmbot_current_user()));
    CREATE POLICY install_update ON installs FOR UPDATE
        USING (guild_id = dmbot_current_guild()
               OR (guild_id = dmbot_install_guild()
                   AND (installed_by_user_id IS NULL
                        OR installed_by_user_id = dmbot_current_user())))
        WITH CHECK (guild_id = dmbot_current_guild()
                    OR (guild_id = dmbot_install_guild()
                        AND installed_by_user_id = dmbot_current_user()));
    CREATE POLICY install_delete ON installs FOR DELETE
        USING (guild_id = dmbot_current_guild());
    """
)

WEB_SESSION_NAME = """
    -- The Discord name to greet the person with on the account page ("Hi, Belleros."),
    -- from sign-in, kept with the session only (#434, #435).
    ALTER TABLE web_sessions ADD COLUMN display_name TEXT NOT NULL DEFAULT '';
    """

INSTALLS_LEFT = """
    -- When DMbot left the server (kicked or the server deleted); NULL while it's there.
    -- Rows are never deleted on leave, so who added DMbot is kept if it comes back (#469).
    -- This replaces 0011's "only the bot removes a row": nobody removes one now.
    --
    -- `via` says how the installer was identified (#497): 'site' once Discord confirmed
    -- the install to the website for the signed-in person; 'link' for a plain join, kept
    -- when "Link this server" fills the installer in. The bot usually writes its 'link'
    -- row before the website's callback arrives, so 'site' overwrites it.
    --
    -- How each side writes a row (the contract, #469):
    -- - The bot, when it joins a server (Database.guild):
    --     INSERT ... VALUES (guild, NULL, now, 'link') ON CONFLICT (guild_id)
    --       DO UPDATE SET left_at = NULL
    --   and when it leaves: UPDATE installs SET left_at = now WHERE guild_id = guild.
    -- - The website, after Discord confirms an install (Database.user(install_guild=...)):
    --     INSERT ... VALUES (guild, me, now, 'site') ON CONFLICT (guild_id) DO UPDATE
    --       SET installed_by_user_id = EXCLUDED.installed_by_user_id, via = 'site',
    --           left_at = NULL
    --       WHERE installs.installed_by_user_id IS NULL
    --          OR installs.installed_by_user_id = EXCLUDED.installed_by_user_id
    --       RETURNING guild_id
    --   Someone else's install is skipped quietly (the WHERE is applied before the update
    --   policy; checked on Postgres 16): no row comes back, and the site says so.
    ALTER TABLE installs ADD COLUMN left_at BIGINT;
    COMMENT ON TABLE installs IS
        'Who added DMbot to which server. Never deleted on leave: left_at is set. '
        'Write contract: see migration 0014 in core/src/dmbot/schema.py.';
    """

# The website API's own database role (#498). Its policies only ever narrow what it sees;
# they never apply to the bot (role `dmbot`). The role itself is made by
# whoever runs Postgres (deploy/postgres-init/02-dmbot-web.sh, or the README for an
# existing server), because DMbot's own role can't create roles.
WEB_ROLE = "dmbot_web"

WEB_ROLE_LIMITS = """
    -- The servers in the signed-in session's own Discord list (#498): the session on this
    -- request (dmbot.session), belonging to the signed-in person (dmbot.user_id), not
    -- expired. Empty otherwise. Policies call it as (SELECT ...), so it runs once per
    -- statement, not per row. The policies themselves are made with the role's rights
    -- (WEB_ROLE_POLICIES): a policy for a role can only be made once the role exists.
    CREATE FUNCTION dmbot_web_guilds() RETURNS BIGINT[]
        LANGUAGE sql STABLE
        AS $fn$
            SELECT coalesce(array_agg((e->>'id')::BIGINT), '{}')
            FROM web_sessions s, jsonb_array_elements(s.guilds) AS e
            WHERE s.id_hash = dmbot_current_session()
              AND s.user_id = dmbot_current_user()
              AND s.expires_at > dmbot_now()
        $fn$;

    -- The same, only the servers the person could add bots to when they signed in.
    CREATE FUNCTION dmbot_web_managed_guilds() RETURNS BIGINT[]
        LANGUAGE sql STABLE
        AS $fn$
            SELECT coalesce(array_agg((e->>'id')::BIGINT), '{}')
            FROM web_sessions s, jsonb_array_elements(s.guilds) AS e
            WHERE s.id_hash = dmbot_current_session()
              AND s.user_id = dmbot_current_user()
              AND s.expires_at > dmbot_now()
              AND (e->>'manage')::BOOLEAN
        $fn$;
    """

TRY_IT_BARE_ID = """
    -- try_it_used outlives a deleted account (one free trial per person), so it keeps
    -- only the Discord id: no date (web's decision on #435, privacy page).
    ALTER TABLE try_it_used DROP COLUMN used_at;
"""

PAYMENT_EVENT_SUBSCRIPTION = """
    -- Which subscription each recorded payment event was about (#435). Deletion cancels at
    -- the period's end, so an old subscription's last news can arrive after the person has
    -- signed up again; knowing it was theirs before lets it be ignored, not retried.
    -- Rows from before this have no subscription id, so they keep the old retry (no real
    -- payment company was connected then, so there are none).
    ALTER TABLE payment_events ADD COLUMN subscription_id TEXT;
"""

PAYMENT_EVENT_KIND = """
    -- What each recorded payment event was (#922): the 7-day grace after a failed payment
    -- is only for someone who has paid before, and that is read from here. Rows from
    -- before this have no kind and count as paid (the writer reads NULL that way), so
    -- anyone recorded earlier keeps grace and still lapses when the plan ends. There is
    -- no backfill: nothing here changes a row.
    ALTER TABLE payment_events ADD COLUMN kind TEXT;
"""

# The website's role is held to its session's servers by these RESTRICTIVE policies
# (ANDed with each table's own), made `TO dmbot_web` so the bot's role never runs them
# and any role that is a member of dmbot_web is held too. Database.migrate (re)makes them
# with the grants, whenever the role exists. What they guard against: the API's code
# setting a server it shouldn't (a bug), not a taken-over API process, which can write
# its own session row.
WEB_ROLE_POLICIES = """
    DROP POLICY IF EXISTS web_session_servers ON campaigns;
    CREATE POLICY web_session_servers ON campaigns AS RESTRICTIVE TO dmbot_web
        USING (guild_id = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
        WITH CHECK (guild_id = ANY ((SELECT dmbot_web_guilds())::BIGINT[]));
    DROP POLICY IF EXISTS web_session_servers ON campaign_dms;
    CREATE POLICY web_session_servers ON campaign_dms AS RESTRICTIVE TO dmbot_web
        USING (guild_id = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
        WITH CHECK (guild_id = ANY ((SELECT dmbot_web_guilds())::BIGINT[]));
    -- Reading: the session's servers, or the person's own installs (the account page lists
    -- them even after a server leaves their list). Writing: only the one install server,
    -- only one they manage, and only naming themselves.
    DROP POLICY IF EXISTS web_session_servers ON installs;
    CREATE POLICY web_session_servers ON installs AS RESTRICTIVE TO dmbot_web
        USING (guild_id = ANY ((SELECT dmbot_web_guilds())::BIGINT[])
               OR installed_by_user_id = dmbot_current_user())
        WITH CHECK (guild_id = dmbot_install_guild()
                    AND guild_id = ANY ((SELECT dmbot_web_managed_guilds())::BIGINT[])
                    AND installed_by_user_id = dmbot_current_user());
    -- Accepting a hand-over (#614, #437 decision 7): the website may make the signed-in
    -- person a campaign's owner, and add them as one of its DMs, only while that campaign
    -- has an open offer to them that is under 7 days old. Checked here, not just in code.
    -- (The store's campaign lock, SELECT ... FOR UPDATE, passes this UPDATE rule too.)
    DROP POLICY IF EXISTS web_accept_handover ON campaigns;
    CREATE POLICY web_accept_handover ON campaigns AS RESTRICTIVE FOR UPDATE TO dmbot_web
        USING (EXISTS (SELECT 1 FROM campaign_handover_offers o
                       WHERE o.guild_id = campaigns.guild_id AND o.campaign_id = campaigns.id
                         AND o.to_user_id = dmbot_current_user() AND o.status = 'open'
                         AND o.created_at + 7 * 86400 > dmbot_now()))
        WITH CHECK (owner_user_id = dmbot_current_user());
    DROP POLICY IF EXISTS web_accept_handover ON campaign_dms;
    CREATE POLICY web_accept_handover ON campaign_dms AS RESTRICTIVE FOR INSERT TO dmbot_web
        WITH CHECK (user_id = dmbot_current_user()
                    AND EXISTS (SELECT 1 FROM campaign_handover_offers o
                                WHERE o.guild_id = campaign_dms.guild_id
                                  AND o.campaign_id = campaign_dms.campaign_id
                                  AND o.to_user_id = dmbot_current_user() AND o.status = 'open'
                                  AND o.created_at + 7 * 86400 > dmbot_now()));
    -- The person's own consent, in the session's servers (#1150: the data export; #1151: where
    -- DMbot may record them). It may read and delete (stop) its own row, never add or change one.
    DROP POLICY IF EXISTS web_own_rows ON consent;
    CREATE POLICY web_own_rows ON consent AS RESTRICTIVE TO dmbot_web
        USING (user_id = dmbot_current_user()
               AND guild_id = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
        WITH CHECK (user_id = dmbot_current_user()
                    AND guild_id = ANY ((SELECT dmbot_web_guilds())::BIGINT[]));
    -- Hand-over offers (#614): only ones the signed-in person sent or was sent, in the
    -- session's servers. The store's own checks (owner, recipient, 7 days) come on top.
    DROP POLICY IF EXISTS web_session_servers ON campaign_handover_offers;
    CREATE POLICY web_session_servers ON campaign_handover_offers AS RESTRICTIVE TO dmbot_web
        USING (guild_id = ANY ((SELECT dmbot_web_guilds())::BIGINT[])
               AND dmbot_current_user() IN (from_user_id, to_user_id))
        WITH CHECK (guild_id = ANY ((SELECT dmbot_web_guilds())::BIGINT[])
                    AND dmbot_current_user() IN (from_user_id, to_user_id));
    """

# The person's own character sheets (#1150), applied once the table has its player column (an
# older schema, as in a test of a backfill, doesn't).
WEB_ROLE_SHEET_POLICY = """
    DROP POLICY IF EXISTS web_own_rows ON character_sheets;
    CREATE POLICY web_own_rows ON character_sheets AS RESTRICTIVE TO dmbot_web
        USING (player_id = dmbot_current_user()
               AND guild_id = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
        WITH CHECK (player_id = dmbot_current_user()
                    AND guild_id = ANY ((SELECT dmbot_web_guilds())::BIGINT[]));
"""

# Applied only once the offers table has all four bot-only columns (an older schema, as in a test
# of a backfill, doesn't, and a rule can't name a column that isn't there).
WEB_ROLE_OFFER_POLICY = """
    -- Creating an offer (#728, #690 follow-up). The website's role has no INSERT on offers
    -- today (#625 only answers them). Whoever adds a route that creates offers must grant
    -- exactly OFFER_INSERT_COLUMNS, never the whole table, and this rule stays on top: an offer
    -- from the website is the signed-in person's own, still open, and not marked as sent,
    -- claimed or announced. Otherwise the site could save an offer as already delivered and the
    -- bot's check that the person is still in the server would be skipped.
    DROP POLICY IF EXISTS web_new_offer ON campaign_handover_offers;
    CREATE POLICY web_new_offer ON campaign_handover_offers AS RESTRICTIVE FOR INSERT TO dmbot_web
        WITH CHECK (delivered_at IS NULL AND claimed_at IS NULL AND message_id IS NULL
                    AND end_told_at IS NULL AND status = 'open' AND decided_at IS NULL
                    AND from_user_id = dmbot_current_user());
"""

ACCESS_GRANTS = (
    _setting("dmbot_grant_writer", "dmbot.grant_writer", "TEXT")
    + """
    -- Free access given by hand (#771; docs/PLAN.md, "Free access and the admin page").
    -- One row per Discord account: no link to web_users, since a grant must work for
    -- someone who never signed in to the website. "guild": Guild's caps from plans.json;
    -- "unlimited": no caps. A grant ends at ends_at or when revoked (kept, with
    -- revoked_at, for the log). entitlements is never written for this.
    --   dmbot.grant_writer  set only by Database.grant_writer(): the admin page's API,
    --                       the only code allowed to give, change or revoke a grant
    CREATE TABLE access_grants (
        discord_user_id BIGINT PRIMARY KEY CHECK (discord_user_id > 0),
        level           TEXT NOT NULL CHECK (level IN ('guild', 'unlimited')),
        ends_at         BIGINT,
        note            TEXT NOT NULL DEFAULT '' CHECK (char_length(note) <= 200),
        granted_by      TEXT NOT NULL CHECK (char_length(granted_by) BETWEEN 3 AND 320),
        granted_at      BIGINT NOT NULL,
        revoked_at      BIGINT
    );
    -- A person reads (and, deleting their account, deletes) only their own grant; the
    -- grant writer reads and writes any. A person's own row holds granted_by (the admin's
    -- email) and the note too: code reading it for them (entitlements) selects only the
    -- level and dates, and neither may ever reach /me. The website's role can set
    -- dmbot.grant_writer itself (as it can dmbot.plan_writer): which code opens that door
    -- is a Python boundary (tests/test_access.py), and part 2's admin sign-in guards it.
    ALTER TABLE access_grants ENABLE ROW LEVEL SECURITY;
    ALTER TABLE access_grants FORCE ROW LEVEL SECURITY;
    CREATE POLICY own_read ON access_grants FOR SELECT
        USING (discord_user_id = dmbot_current_user());
    CREATE POLICY own_delete ON access_grants FOR DELETE
        USING (discord_user_id = dmbot_current_user());
    CREATE POLICY grant_writer ON access_grants FOR ALL
        USING (dmbot_grant_writer() = 'admin') WITH CHECK (dmbot_grant_writer() = 'admin');

    -- Who gave, changed or revoked a grant, and when (add-only; ids, and the admin's
    -- own email only here).
    CREATE TABLE access_log (
        id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        admin_email     TEXT NOT NULL CHECK (char_length(admin_email) BETWEEN 3 AND 320),
        action          TEXT NOT NULL CHECK (action IN ('grant', 'change', 'revoke')),
        discord_user_id BIGINT NOT NULL CHECK (discord_user_id > 0),
        at              BIGINT NOT NULL
    );
    ALTER TABLE access_log ENABLE ROW LEVEL SECURITY;
    ALTER TABLE access_log FORCE ROW LEVEL SECURITY;
    CREATE POLICY grant_writer_add ON access_log FOR INSERT
        WITH CHECK (dmbot_grant_writer() = 'admin');
    CREATE POLICY grant_writer_read ON access_log FOR SELECT
        USING (dmbot_grant_writer() = 'admin');
    """
)

HOUSE_RULES = f"""
    -- A campaign's house rules (#865; docs/PLAN.md, "House rules"): the rule, the book rule
    -- it replaces (free text), and what happened to make it (free text, optional). Per
    -- campaign, never shared between campaigns or servers; deleted with the campaign; in
    -- its backups. Only the campaign's DMs change them (dmbot.rules.house checks that in
    -- the same transaction); anyone in the server may list them. The website's role has no
    -- grant on this table.
    --   number      the rule's own number, for good: the campaign's next one when it is
    --               made (campaigns.house_rules_made counts them, so a number is never
    --               used twice, even after a rule is removed). Alerts cite "house rule 12".
    --   version     counts changes to the rule, so an edit or a removal made from an old
    --               view can be told from one made on the current rule
    --   session_id  the stored transcript session it came from, for the later parts (an
    --               alert's Override, a rule declared aloud); nothing sets it yet, and a
    --               backup never carries it (it means nothing in another server)
    ALTER TABLE campaigns ADD COLUMN house_rules_made INTEGER NOT NULL DEFAULT 0
        CHECK (house_rules_made >= 0);
    CREATE TABLE house_rules (
        {_memory_scope()}
        number     INTEGER NOT NULL CHECK (number > 0),
        rule       TEXT NOT NULL CHECK (char_length(rule) BETWEEN 1 AND 500),
        supersedes TEXT CHECK (supersedes IS NULL OR char_length(supersedes) BETWEEN 1 AND 500),
        scenario   TEXT CHECK (scenario IS NULL OR char_length(scenario) BETWEEN 1 AND 500),
        session_id TEXT,
        created_by BIGINT NOT NULL CHECK (created_by > 0),
        created_at BIGINT NOT NULL,
        updated_at BIGINT NOT NULL,
        version    INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
        PRIMARY KEY (guild_id, campaign_id, number)
    );
    """ + _isolate("house_rules")

TEST_VOICE_CONSENT = """
    -- Test recordings (#1019; docs/PLAN.md, "Test recordings"): the second yes, "Save my voice
    -- for tests", given in a server listed in DMBOT_TEST_RECORDING_GUILDS. Separate from the
    -- recording consent: without it, nothing of the person is saved. Per server, only a
    -- date: the files themselves are never in the database.
    CREATE TABLE test_voice_consent (
        guild_id   BIGINT NOT NULL,
        user_id    BIGINT NOT NULL CHECK (user_id > 0),
        granted_at BIGINT NOT NULL,
        PRIMARY KEY (guild_id, user_id)
    );
    """ + _isolate("test_voice_consent")

USAGE = (
    _setting("dmbot_meter", "dmbot.meter", "TEXT")
    + """
    -- The hours meter (#437 part 2; docs/PLAN.md, "Plans and pricing"): DMbot's listening
    -- minutes, in two tables so no query ever crosses servers.
    --
    -- session_usage: per campaign and session, in that server's own scope and deleted
    -- with the campaign. One row per owner a session had: if the campaign is handed over
    -- mid-session, the minutes after the hand-over are recorded for the new owner.
    CREATE TABLE session_usage (
        guild_id           BIGINT NOT NULL,
        campaign_id        TEXT NOT NULL,
        session_started_at BIGINT NOT NULL,
        -- 0: nobody owned the campaign yet; those minutes are kept here so they are
        -- never billed to whoever takes it on later, and never reach owner_hours.
        owner_user_id      BIGINT NOT NULL CHECK (owner_user_id >= 0),
        minutes            INTEGER NOT NULL DEFAULT 0 CHECK (minutes >= 0),
        updated_at         BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, session_started_at, owner_user_id),
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE
    );
    """
    + _isolate("session_usage")
    + """
    -- owner_hours: an owner's minutes per plan month, summed over every campaign they own,
    -- in any server. Numbers only: no server, campaign or name, so reading it reveals
    -- nothing about where the hours went. It is scoped to the owner (like entitlements),
    -- and only the bot's meter (dmbot.meter, set by Database.meter(), which also sets
    -- the server being played in) may add to it; the owner may read their own row.
    CREATE TABLE owner_hours (
        owner_user_id BIGINT NOT NULL CHECK (owner_user_id > 0),
        month_start   BIGINT NOT NULL,
        minutes       INTEGER NOT NULL DEFAULT 0 CHECK (minutes >= 0),
        PRIMARY KEY (owner_user_id, month_start)
    );
    ALTER TABLE owner_hours ENABLE ROW LEVEL SECURITY;
    ALTER TABLE owner_hours FORCE ROW LEVEL SECURITY;
    CREATE POLICY own_read ON owner_hours FOR SELECT
        USING (owner_user_id = dmbot_current_user());
    CREATE POLICY meter_insert ON owner_hours FOR INSERT
        WITH CHECK (owner_user_id = dmbot_current_user() AND dmbot_meter() = 'bot');
    CREATE POLICY meter_update ON owner_hours FOR UPDATE
        USING (owner_user_id = dmbot_current_user() AND dmbot_meter() = 'bot')
        WITH CHECK (owner_user_id = dmbot_current_user() AND dmbot_meter() = 'bot');
    """
)

USAGE_GRACE = """
    -- The hours meter's cap finish (#437 part 2): when a session reaches the cap, it may
    -- run on for up to 2 hours, once a month. This records which session was given that
    -- grace, so a restart doesn't give it twice or take it away, and no other session gets
    -- it. A new column on an existing table: no rows change.
    ALTER TABLE owner_hours ADD COLUMN grace_session BIGINT;
    """

RULES_CARDS = """
    -- A campaign setting (#931): "Rules cards when a spell or creature is named", off by
    -- default. When on, a live session puts a card for a spell, condition or creature on
    -- the DM screen when it is named at the table. Only the campaign's DMs change it. It
    -- travels in a backup with the campaign's other settings.
    ALTER TABLE campaigns ADD COLUMN rules_cards BOOLEAN NOT NULL DEFAULT FALSE;
"""

MEMORY_KINDS = (
    f"""
    -- One kind for each memory entry (#1034, owner decision 2026-10-10; docs/STORY_MEMORY.md,
    -- "Entity kinds"): an entry's kind says what it fundamentally is (character, place, item,
    -- group, event, idea). Roles and categories are never kinds:
    --   role        player_character, npc or god, on a character, one at a time
    --   needs_look  the entry was moved here from an older kind (a named creature, a spell)
    --               and the DM keeps, links or deletes it; nothing is deleted for them
    -- and a character's species, creature type, stat block, class and background are links
    -- to the rules (memory_rule_links), kept by source and name; a name the rules data
    -- doesn't have keeps its plain words with known = false.
    ALTER TABLE memory_entities ADD COLUMN role TEXT
        CHECK (role IS NULL OR role IN ('player_character', 'npc', 'god'));
    ALTER TABLE memory_entities ADD COLUMN needs_look BOOLEAN NOT NULL DEFAULT FALSE;

    CREATE TABLE memory_rule_links (
        {_memory_scope()}
        id         TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{{32}}$'),
        entity_id  TEXT NOT NULL,
        kind       TEXT NOT NULL
            CHECK (kind IN ('species', 'creature_type', 'stat_block', 'class', 'background')),
        rules_source TEXT NOT NULL CHECK (char_length(rules_source) <= 40),
        name       TEXT NOT NULL CHECK (char_length(name) BETWEEN 1 AND 100),
        edition    TEXT CHECK (edition IS NULL OR edition IN ('2014', '2024')),
        known      BOOLEAN NOT NULL,
        created_at BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, id),
        CHECK (known = (rules_source <> '')),
        {_entity_link("entity_id")}
    );
    -- One species and one creature type for each character; any number of the others.
    CREATE UNIQUE INDEX memory_rule_links_one_species
        ON memory_rule_links (guild_id, campaign_id, entity_id) WHERE kind = 'species';
    CREATE UNIQUE INDEX memory_rule_links_one_type
        ON memory_rule_links (guild_id, campaign_id, entity_id) WHERE kind = 'creature_type';
    CREATE UNIQUE INDEX memory_rule_links_no_repeat
        ON memory_rule_links (guild_id, campaign_id, entity_id, kind, rules_source, name);
    """
    + _isolate("memory_rule_links")
    + """

    -- Move the older kinds. Migrations run with no server set, so row-level security hides
    -- every row: each table the statements read or write is opened for them, and closed
    -- again (CLAUDE.md, lesson from #704). The core kinds live in code; what is stored is
    -- campaigns' own rows: entries, and extension kinds and relationships that name them.
    CREATE POLICY migrate_backfill ON memory_entities FOR ALL USING (true) WITH CHECK (true);
    CREATE POLICY migrate_backfill ON memory_types FOR ALL USING (true) WITH CHECK (true);
    CREATE POLICY migrate_backfill ON memory_predicates FOR ALL USING (true) WITH CHECK (true);

    -- An entry of a campaign's own kind that sat under an older kind keeps what that meant:
    -- its role (a player's character stays one, with its player), or a look from the DM for
    -- a creature. Before the kinds' parents are moved below, and before the entries' own
    -- older kinds are, since it reads each entry's kind from memory_types.
    UPDATE memory_entities e SET
        role = CASE t.parent
            WHEN 'player_character' THEN 'player_character'
            WHEN 'npc' THEN 'npc'
            WHEN 'deity' THEN 'god' END,
        needs_look = (t.parent = 'creature')
        FROM memory_types t
        WHERE t.guild_id = e.guild_id AND t.campaign_id = e.campaign_id AND t.key = e.type
          AND t.parent IN ('player_character', 'npc', 'deity', 'creature');

    -- The older kinds, in one pass over the table. A named creature is a character; which
    -- role, if any, is the DM's call. A spell is rules vocabulary, not an entry: kept as an
    -- idea for the DM to look at (nothing is deleted).
    UPDATE memory_entities SET
        role = CASE type
            WHEN 'player_character' THEN 'player_character'
            WHEN 'npc' THEN 'npc'
            WHEN 'deity' THEN 'god' END,
        needs_look = type IN ('creature', 'spell'),
        type = CASE type WHEN 'spell' THEN 'concept' ELSE 'character' END
        WHERE type IN ('player_character', 'npc', 'deity', 'creature', 'spell');

    -- Whoever plays an entry plays a player character (only those were played before).
    UPDATE memory_entities SET role = 'player_character'
        WHERE played_by IS NOT NULL AND role IS NULL;

    -- A campaign's own kinds that sat under an older kind now sit under its replacement,
    -- and its own relationships that named an older kind name the replacement (each once,
    -- in the order they were given).
    UPDATE memory_types SET parent = 'character'
        WHERE parent IN ('player_character', 'npc', 'deity', 'creature');
    UPDATE memory_types SET parent = 'concept' WHERE parent = 'spell';
    UPDATE memory_predicates SET
        subject_types = ARRAY(SELECT x FROM (
            SELECT CASE
                WHEN t IN ('player_character', 'npc', 'deity', 'creature') THEN 'character'
                WHEN t = 'spell' THEN 'concept' ELSE t END AS x, min(ord) AS o
            FROM unnest(subject_types) WITH ORDINALITY AS u(t, ord) GROUP BY 1) q ORDER BY o),
        object_types = ARRAY(SELECT x FROM (
            SELECT CASE
                WHEN t IN ('player_character', 'npc', 'deity', 'creature') THEN 'character'
                WHEN t = 'spell' THEN 'concept' ELSE t END AS x, min(ord) AS o
            FROM unnest(object_types) WITH ORDINALITY AS u(t, ord) GROUP BY 1) q ORDER BY o)
        WHERE subject_types && ARRAY['player_character', 'npc', 'deity', 'creature', 'spell']
           OR object_types && ARRAY['player_character', 'npc', 'deity', 'creature', 'spell'];

    DROP POLICY migrate_backfill ON memory_predicates;
    DROP POLICY migrate_backfill ON memory_types;
    DROP POLICY migrate_backfill ON memory_entities;
    """
)

HOUSE_RULES_FILE = f"""
    -- A campaign's linked house-rules file (#969, migration 0040): the share link a DM set, and the
    -- fingerprint of the file they chose to "ignore until it changes". One row per campaign,
    -- deleted with it. The link can be a private share link, so it is never logged, never
    -- shown back in full (only the site it is on), and not part of a backup. Only the
    -- campaign's DMs set or clear it (dmbot.rules.house_file_link checks that in the same
    -- transaction). The website's role has no grant on this table.
    CREATE TABLE house_rules_file (
        {_memory_scope()}
        link    TEXT NOT NULL CHECK (char_length(link) BETWEEN 1 AND 2000),
        ignored TEXT CHECK (ignored IS NULL OR char_length(ignored) = 64),
        set_by  BIGINT NOT NULL CHECK (set_by > 0),
        set_at  BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id)
    );
    """ + _isolate("house_rules_file")

PAUSE = """
    -- Pausing a campaign (#957; docs/PLAN.md, "Plans and pricing"): a paused campaign keeps all
    -- its data, can't start a session, and doesn't count toward the owner's campaign cap. The
    -- owner pauses and unpauses it; a downgrade or a lapse pauses the ones over the new cap.
    ALTER TABLE campaigns ADD COLUMN paused BOOLEAN NOT NULL DEFAULT FALSE;
    -- The owner-level copy the cap counts from (0034) follows it. A new column with a default:
    -- no rows are rewritten, so no row-level security to open.
    ALTER TABLE owner_campaigns ADD COLUMN paused BOOLEAN NOT NULL DEFAULT FALSE;

    -- 0034's trigger function, now carrying `paused`. (CREATE OR REPLACE resets the function's
    -- settings, so the fixed search_path is set again below.)
    CREATE OR REPLACE FUNCTION dmbot_sync_owner_campaigns() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    DECLARE
        before TEXT := COALESCE(current_setting('dmbot.owner_sync', true), '');
    BEGIN
        PERFORM set_config('dmbot.owner_sync', 'trigger', true);
        IF TG_OP = 'INSERT' THEN
            IF NEW.owner_user_id IS NOT NULL THEN
                INSERT INTO owner_campaigns (owner_user_id, campaign_id, paused)
                    VALUES (NEW.owner_user_id, NEW.id, NEW.paused) ON CONFLICT DO NOTHING;
            END IF;
        ELSIF TG_OP = 'DELETE' THEN
            IF OLD.owner_user_id IS NOT NULL THEN
                DELETE FROM owner_campaigns
                    WHERE owner_user_id = OLD.owner_user_id AND campaign_id = OLD.id;
            END IF;
        ELSIF OLD.owner_user_id IS DISTINCT FROM NEW.owner_user_id THEN
            IF OLD.owner_user_id IS NOT NULL THEN
                DELETE FROM owner_campaigns
                    WHERE owner_user_id = OLD.owner_user_id AND campaign_id = OLD.id;
            END IF;
            IF NEW.owner_user_id IS NOT NULL THEN
                INSERT INTO owner_campaigns (owner_user_id, campaign_id, paused)
                    VALUES (NEW.owner_user_id, NEW.id, NEW.paused) ON CONFLICT DO NOTHING;
            END IF;
        ELSIF OLD.paused IS DISTINCT FROM NEW.paused AND NEW.owner_user_id IS NOT NULL THEN
            UPDATE owner_campaigns SET paused = NEW.paused
                WHERE owner_user_id = NEW.owner_user_id AND campaign_id = NEW.id;
        END IF;
        PERFORM set_config('dmbot.owner_sync', before, true);
        RETURN NULL;
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_sync_owner_campaigns() SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    DROP TRIGGER owner_campaigns_sync ON campaigns;
    CREATE TRIGGER owner_campaigns_sync
        AFTER INSERT OR DELETE OR UPDATE OF owner_user_id, paused ON campaigns
        FOR EACH ROW EXECUTE FUNCTION dmbot_sync_owner_campaigns();

    -- The count the cap uses (0034) leaves paused campaigns out. Its grants stay as they were.
    CREATE OR REPLACE FUNCTION dmbot_owned_campaigns() RETURNS BIGINT
        LANGUAGE sql STABLE SECURITY DEFINER
        AS $fn$
        SELECT count(*) FROM owner_campaigns
            WHERE owner_user_id = dmbot_current_user() AND NOT paused
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_owned_campaigns() SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;

    -- A downgrade or a lapse leaves an owner with more campaigns than the plan covers: this
    -- pauses the ones over `keep`, the most recently played staying active, and returns what it
    -- paused (so the owner can be told once). The campaigns are in other servers, which an
    -- owner-level call can't reach, so (like 0034's trigger) it runs with its owner's rights and
    -- opens `campaigns` to itself for this one person only, through a setting it sets and puts
    -- back. A guard against code that does this by mistake, not against a hostile process. Only
    -- the bot's role may call it (not the website's).
    -- Opens `campaigns` to the pause function only: TO the role that owns the function
    -- (the one running this migration), never the website's role, which could otherwise
    -- set the setting itself and read every campaign a person owns in every server.
    -- SELECT and UPDATE only: it can't insert or delete.
    DO $do$
    BEGIN
        EXECUTE format(
            'CREATE POLICY pause_read ON campaigns FOR SELECT TO %I
                USING (dmbot_owner_sync() = ''pause'' AND owner_user_id = dmbot_current_user())',
            current_user);
        EXECUTE format(
            'CREATE POLICY pause_by_owner ON campaigns FOR UPDATE TO %I
                USING (dmbot_owner_sync() = ''pause'' AND owner_user_id = dmbot_current_user())
                WITH CHECK (dmbot_owner_sync() = ''pause''
                            AND owner_user_id = dmbot_current_user())',
            current_user);
    END
    $do$;
    CREATE FUNCTION dmbot_pause_over_cap(keep INTEGER, live TEXT[] DEFAULT '{}')
        RETURNS TABLE (campaign_id TEXT, server_id BIGINT, campaign_name TEXT)
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    DECLARE
        me BIGINT := dmbot_current_user();
        before TEXT := COALESCE(current_setting('dmbot.owner_sync', true), '');
    BEGIN
        IF me IS NULL OR keep IS NULL OR keep < 0 THEN
            RETURN;
        END IF;
        -- The common case, nothing over the cap: answer from the small owner table before
        -- locking or scanning anything (this runs on every start).
        IF (SELECT count(*) FROM owner_campaigns oc
            WHERE oc.owner_user_id = me AND NOT oc.paused) <= keep THEN
            RETURN;
        END IF;
        PERFORM set_config('dmbot.owner_sync', 'pause', true);
        -- Same lock order as every other caller (campaign row first, then the per-person
        -- lock of campaign_cap), rows in a fixed order so two of these can't cross: a
        -- hand-over holding one campaign while it waits for the person lock must not meet
        -- us holding the person lock while we wait for that campaign.
        PERFORM 1 FROM campaigns c WHERE c.owner_user_id = me
            ORDER BY c.guild_id, c.id FOR UPDATE;
        PERFORM pg_advisory_xact_lock(hashtextextended('dmbot.owner_slots:' || me::text, 0));
        RETURN QUERY
        WITH ranked AS (
            SELECT c.id AS cid, c.guild_id AS gid,
                   -- A campaign DMbot is listening to now is kept first: pausing a live
                   -- table is refused everywhere else ("stop it first").
                   row_number() OVER (
                       ORDER BY (c.id = ANY(live)) DESC,
                                COALESCE(c.last_played_at, c.created_at) DESC,
                                c.created_at DESC, c.id) AS n
            FROM campaigns c
            WHERE c.owner_user_id = me AND NOT c.paused
        ), gone AS (
            UPDATE campaigns c SET paused = TRUE
            FROM ranked r
            WHERE c.id = r.cid AND c.guild_id = r.gid AND r.n > keep
            RETURNING c.id, c.guild_id, c.name
        )
        SELECT gone.id, gone.guild_id, gone.name FROM gone ORDER BY gone.name;
        PERFORM set_config('dmbot.owner_sync', before, true);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_pause_over_cap(INTEGER, TEXT[])'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_pause_over_cap(INTEGER, TEXT[]) FROM PUBLIC;
"""

GAME_CLOCKS = f"""
    -- A campaign's game clock (#965; docs/PLAN.md, "TimeBot"): game time, not real time,
    -- set and moved only by the DM's buttons and clear phrases. One row per campaign, and
    -- none until the DM sets it. Per campaign, never shared between campaigns or servers;
    -- deleted with the campaign; in its backups (without the message ids).
    --   minute          game minutes since the start of Day 1
    --   last_long_rest  the game minute the party last finished a long rest
    --   tired_told_for  the long-rest minute the 24-hours-without-a-rest line was said for
    --   channel_id, message_id  the pinned clock message on the DM screen, edited in place
    CREATE TABLE game_clocks (
        {_memory_scope()}
        minute         BIGINT NOT NULL CHECK (minute >= 0),
        last_long_rest BIGINT NOT NULL CHECK (last_long_rest >= 0),
        tired_told_for BIGINT CHECK (tired_told_for IS NULL OR tired_told_for >= 0),
        channel_id     BIGINT,
        message_id     BIGINT,
        updated_at     BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id)
    );
    """ + _isolate("game_clocks")

RETENTION = """
    -- Which retention warning a campaign has had (#964), so a restart doesn't repeat one:
    -- the stage (0 none, 1 the 14-day warning, 2 the 3-day one) and the delete date it was
    -- sent for. Playing a session moves the date, so the warnings start over. Columns with
    -- defaults: no rows change, so no row-level-security policy is needed.
    ALTER TABLE campaigns ADD COLUMN retention_warned_stage INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE campaigns ADD COLUMN retention_warned_for BIGINT;
"""

GAME_EFFECTS = f"""
    -- The game clock counts the timers it has numbered, so a number is never used twice even
    -- after a timer is ended (an old "has likely ended" message can't act on a newer timer).
    ALTER TABLE game_clocks ADD COLUMN effects_made INTEGER NOT NULL DEFAULT 0
        CHECK (effects_made >= 0);
    -- Timed effects the DM starts on a campaign's game clock (#998; docs/PLAN.md, "TimeBot"):
    -- Bless, Mage Armor and the like. Per campaign, never shared between campaigns or servers;
    -- deleted with the campaign; in its backups. Only the campaign's DMs start or end them
    -- (dmbot.timebot.effects checks that in the same transaction). Ends are game minutes, so
    -- a restart keeps them. At most 20 per campaign (checked in the same transaction).
    --   told  the "has likely ended" line was said (once); the DM then ends it or extends it
    CREATE TABLE game_effects (
        {_memory_scope()}
        number        INTEGER NOT NULL CHECK (number > 0),
        name          TEXT NOT NULL CHECK (char_length(name) BETWEEN 1 AND 60),
        target        TEXT CHECK (target IS NULL OR char_length(target) BETWEEN 1 AND 60),
        minutes       INTEGER NOT NULL CHECK (minutes > 0),
        concentration BOOLEAN NOT NULL DEFAULT FALSE,
        ends_minute   BIGINT NOT NULL CHECK (ends_minute >= 0),
        told          BOOLEAN NOT NULL DEFAULT FALSE,
        PRIMARY KEY (guild_id, campaign_id, number)
    );
    """ + _isolate("game_effects")

GAME_EFFECTS_EXTENDED = """
    -- The minutes the DM added with "Still going" (#1112), kept apart from `minutes` (how long
    -- it was timed for) so the "has likely ended" line can show the total and say it was
    -- extended. A column with a default: no existing row changes, so no backfill policy.
    ALTER TABLE game_effects ADD COLUMN extended INTEGER NOT NULL DEFAULT 0
        CHECK (extended >= 0);
"""

GAMETIME_CHANNEL = """
    -- The players' game-time channel (docs/PLAN.md, "TimeBot"; #1116): `dmb-gametime-<slug>`,
    -- everyone reads, only DMbot posts, with the players' pinned clock ("Day 5, morning")
    -- edited in place. Its ids are channel and message ids: they mean nothing in another
    -- server, so (like the DM screen's) none goes into a backup. Nullable columns: no row
    -- changes, so no row-level-security policy is needed.
    ALTER TABLE campaigns ADD COLUMN gametime_channel_id BIGINT;
    ALTER TABLE game_clocks ADD COLUMN players_channel_id BIGINT;
    ALTER TABLE game_clocks ADD COLUMN players_message_id BIGINT;
"""

DMBCHAT_CHANNEL = """
    -- The table's DMbot chat channel (docs/PLAN.md, "Table questions: anyone may ask"; #1187):
    -- `dmb-chat-<slug>`, where anyone at the table asks DMbot a quick rules question and the
    -- answer is posted for everyone. Its id is a channel id: it means nothing in another
    -- server, so (like the DM screen's) none goes into a backup. A nullable column: no row
    -- changes, so no row-level-security policy is needed.
    ALTER TABLE campaigns ADD COLUMN dmbchat_channel_id BIGINT;
"""

TABLE_QUESTIONS = """
    -- A player's question to DMbot, and the answer posted in the table's DMbot chat (#1187):
    -- a new kind of sidebar line, `table_question`, shown in the as-heard (raw) transcript as
    -- `[Table Q via=...]`, never in the cleaned one. A question typed in the DMbot chat has
    -- the new "via" `chat`. Only two CHECK constraints are replaced to allow the new values:
    -- no row changes, so no row-level-security policy is needed.
    ALTER TABLE transcript_lines DROP CONSTRAINT transcript_lines_sidebar_check;
    ALTER TABLE transcript_lines ADD CONSTRAINT transcript_lines_sidebar_check
        CHECK (sidebar IN ('question', 'answer', 'table_question'));
    ALTER TABLE transcript_lines DROP CONSTRAINT transcript_lines_sidebar_via_check;
    ALTER TABLE transcript_lines ADD CONSTRAINT transcript_lines_sidebar_via_check
        CHECK (sidebar_via IN ('voice-memo', 'typed', 'table-trigger', 'table-chat', 'chat'));
"""

DNDBEYOND_INVITE = """
    -- The DM's own D&D Beyond campaign invite link (#1119; docs/PLAN.md, "The D&D Beyond
    -- campaign invite"), passed on to players on a button. Only the one standard form is
    -- kept (dmbot.campaigns.invite checks it too). A nullable column: no row changes, so no
    -- backfill policy.
    ALTER TABLE campaigns ADD COLUMN dndbeyond_invite TEXT
        CHECK (dndbeyond_invite IS NULL OR dndbeyond_invite ~
            '^https://www[.]dndbeyond[.]com/campaigns/join/[A-Za-z0-9]{4,64}$');
"""

TABLE_CHAT = """
    -- Typed chat at the table (#1118; docs/PLAN.md, "Typed chat at the table"). The voice
    -- channel's own text chat is read while DMbot listens (and only from people who agreed);
    -- `table_chat_channel_id` is at most one more text channel the DM picked, and
    -- `table_chat` is the DM's switch (on for everyone, because the consent wording that
    -- covers it was asked again, version 5). A typed line is stored with `typed`, shown only
    -- in the as-heard transcript and tagged "(typed)". Nullable or defaulted columns: no row
    -- changes, so no backfill policy.
    ALTER TABLE campaigns
        ADD COLUMN table_chat BOOLEAN NOT NULL DEFAULT TRUE,
        ADD COLUMN table_chat_channel_id BIGINT;
    ALTER TABLE transcript_lines ADD COLUMN typed BOOLEAN NOT NULL DEFAULT FALSE;
    -- A DM sidebar question typed in the table's chat has its own "via" (no speech-to-text).
    ALTER TABLE transcript_lines DROP CONSTRAINT transcript_lines_sidebar_via_check;
    ALTER TABLE transcript_lines ADD CONSTRAINT transcript_lines_sidebar_via_check
        CHECK (sidebar_via IN ('voice-memo', 'typed', 'table-trigger', 'table-chat'));
"""

COMBAT_STATE = """
    -- Combat state (#1127; docs/PLAN.md, "Combat state"): in combat or not, and which round,
    -- kept with the game clock so it survives a restart. `combat_round` is NULL out of combat.
    -- `combat_secs` is the game seconds already spent toward the next whole minute (a round
    -- is 6 seconds; the clock itself counts whole minutes). `side_initiative` is a campaign
    -- switch (off): DMbot tracks rounds only, never turn order, either way. Nullable or
    -- defaulted columns: no row changes, so no backfill policy.
    ALTER TABLE game_clocks
        ADD COLUMN combat_round INTEGER
            CHECK (combat_round IS NULL OR combat_round BETWEEN 1 AND 99),
        ADD COLUMN combat_secs INTEGER NOT NULL DEFAULT 0 CHECK (combat_secs BETWEEN 0 AND 59);
    ALTER TABLE campaigns ADD COLUMN side_initiative BOOLEAN NOT NULL DEFAULT FALSE;
"""

SHEET_QUESTIONS = """
    -- Sheet re-reads (#1128; docs/PLAN.md, "Re-reading D&D Beyond"): whether DMbot may ask this
    -- sheet's player, privately, when a re-read doesn't show what was said at the table. On
    -- unless the player turns it off from their character sheet panel. Defaulted column: no
    -- row changes, so no backfill policy.
    ALTER TABLE character_sheets ADD COLUMN ask_mismatch BOOLEAN NOT NULL DEFAULT TRUE;
"""

FIGHTBOT = f"""
    -- FightBot (#1129; docs/PLAN.md, "FightBot"): one row per player per campaign, in that
    -- campaign's own server scope, deleted with the campaign. Kept on its own (not on a
    -- character's sheet row) so that forgetting a sheet, or having a second character, never
    -- turns reminders back on.
    --   never          the player chose Never, or turned FightBot off: no reminders at all
    --   quiet_since    the start (Unix seconds) of the session they said "Quiet until next
    --                  session" in: quiet for that session only, restarts included
    --   snoozed_until  Unix seconds: no reminders before then
    --   last_call_at   Unix seconds of the last reminder DMbot worked out for them, so a
    --                  restart cannot send another within two minutes
    CREATE TABLE fightbot_players (
        {_memory_scope()}
        player_id     BIGINT NOT NULL,
        never         BOOLEAN NOT NULL DEFAULT FALSE,
        quiet_since   BIGINT,
        snoozed_until BIGINT,
        last_call_at  BIGINT,
        PRIMARY KEY (guild_id, campaign_id, player_id)
    );
    """ + _isolate("fightbot_players")

OWNER_CAMPAIGNS = (
    _setting("dmbot_owner_sync", "dmbot.owner_sync", "TEXT")
    + """
    -- Which campaigns each person owns, across every server (#437 part 2c; docs/PLAN.md,
    -- "Plans and pricing"). The campaign cap needs "how many campaigns does this person
    -- own", and `campaigns` is isolated per server, so like owner_hours this is a table of
    -- its own: two ids, no server, no name, so reading it reveals nothing about where the
    -- campaigns are. It is scoped to the owner (the meter door reads it with that person
    -- set), and the website's role has no grant on it.
    CREATE TABLE owner_campaigns (
        owner_user_id BIGINT NOT NULL CHECK (owner_user_id > 0),
        campaign_id   TEXT NOT NULL,
        PRIMARY KEY (owner_user_id, campaign_id)
    );
    ALTER TABLE owner_campaigns ENABLE ROW LEVEL SECURITY;
    ALTER TABLE owner_campaigns FORCE ROW LEVEL SECURITY;
    CREATE POLICY own_read ON owner_campaigns FOR SELECT
        USING (owner_user_id = dmbot_current_user());
    -- Written only by the trigger below, which sets dmbot.owner_sync for its own statements
    -- and clears it again. (A guard against code that writes it by mistake, as the meter's
    -- setting is; not against a hostile process.)
    CREATE POLICY sync_write ON owner_campaigns
        USING (dmbot_owner_sync() = 'trigger')
        WITH CHECK (dmbot_owner_sync() = 'trigger');

    -- Keeps the table in step with `campaigns` however a campaign is made, restored, handed
    -- over, deleted or removed with its server. The trigger runs inside a server-scoped
    -- transaction (or the website's, for an accepted hand-over) but writes an owner-scoped
    -- table, and the caller may have no grant on it: so the function runs with its owner's
    -- rights (SECURITY DEFINER), with a search_path fixed below, and does nothing but this
    -- one insert, delete or move.
    CREATE FUNCTION dmbot_sync_owner_campaigns() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    DECLARE
        before TEXT := COALESCE(current_setting('dmbot.owner_sync', true), '');
    BEGIN
        PERFORM set_config('dmbot.owner_sync', 'trigger', true);
        IF TG_OP = 'INSERT' THEN
            IF NEW.owner_user_id IS NOT NULL THEN
                INSERT INTO owner_campaigns (owner_user_id, campaign_id)
                    VALUES (NEW.owner_user_id, NEW.id) ON CONFLICT DO NOTHING;
            END IF;
        ELSIF TG_OP = 'DELETE' THEN
            IF OLD.owner_user_id IS NOT NULL THEN
                DELETE FROM owner_campaigns
                    WHERE owner_user_id = OLD.owner_user_id AND campaign_id = OLD.id;
            END IF;
        ELSIF OLD.owner_user_id IS DISTINCT FROM NEW.owner_user_id THEN
            IF OLD.owner_user_id IS NOT NULL THEN
                DELETE FROM owner_campaigns
                    WHERE owner_user_id = OLD.owner_user_id AND campaign_id = OLD.id;
            END IF;
            IF NEW.owner_user_id IS NOT NULL THEN
                INSERT INTO owner_campaigns (owner_user_id, campaign_id)
                    VALUES (NEW.owner_user_id, NEW.id) ON CONFLICT DO NOTHING;
            END IF;
        END IF;
        PERFORM set_config('dmbot.owner_sync', before, true);
        RETURN NULL;
    END
    $fn$;
    -- Fixed: the system catalogue, this schema (the one the migration runs in), and the
    -- temporary schema last, so nothing a caller makes can stand in for a name used above.
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_sync_owner_campaigns() SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_sync_owner_campaigns() FROM PUBLIC;
    CREATE TRIGGER owner_campaigns_sync
        AFTER INSERT OR DELETE OR UPDATE OF owner_user_id ON campaigns
        FOR EACH ROW EXECUTE FUNCTION dmbot_sync_owner_campaigns();
    -- A row trigger never sees TRUNCATE, which would leave the table counting campaigns that
    -- are gone: so it is refused, and anything removing campaigns in bulk must use DELETE
    -- (a server's removal does).
    CREATE FUNCTION dmbot_refuse_campaigns_truncate() RETURNS trigger
        LANGUAGE plpgsql
        AS $fn$
    BEGIN
        RAISE EXCEPTION 'Use DELETE on campaigns, not TRUNCATE (a trigger keeps owner_campaigns)';
    END
    $fn$;
    CREATE TRIGGER campaigns_no_truncate
        BEFORE TRUNCATE ON campaigns
        FOR EACH STATEMENT EXECUTE FUNCTION dmbot_refuse_campaigns_truncate();

    -- The one count anyone but the owner's own rows may see, for the website's accept of a
    -- hand-over: its role has no grant on owner_campaigns, so it asks this. It returns only
    -- how many campaigns the person set for this transaction owns (dmbot_current_user(), as
    -- the table's own policy), takes no argument, and so cannot be pointed at anyone else.
    CREATE FUNCTION dmbot_owned_campaigns() RETURNS BIGINT
        LANGUAGE sql STABLE SECURITY DEFINER
        AS $fn$
        SELECT count(*) FROM owner_campaigns WHERE owner_user_id = dmbot_current_user()
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_owned_campaigns() SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_owned_campaigns() FROM PUBLIC;

    -- The campaigns that exist now. Migrations run with no server set, so row-level security
    -- hides every row: the backfill opens the table it reads and the one it writes for
    -- itself (CLAUDE.md), dropped again inside the migration's transaction.
    CREATE POLICY migrate_backfill ON campaigns FOR SELECT USING (true);
    CREATE POLICY migrate_backfill ON owner_campaigns FOR INSERT WITH CHECK (true);
    INSERT INTO owner_campaigns (owner_user_id, campaign_id)
        SELECT owner_user_id, id FROM campaigns WHERE owner_user_id IS NOT NULL;
    DROP POLICY migrate_backfill ON owner_campaigns;
    DROP POLICY migrate_backfill ON campaigns;
    """
)

CAMPAIGN_GUILDS = """
    -- Which servers have ever held a campaign (#989), so the daily retention job can reach
    -- the campaigns of servers the bot has since left: it can't ask Discord about those.
    -- Server IDs and nothing else, so no row-level security (a routing table; see the rules
    -- at the top of this file). A plain trigger fills it however a campaign is made or
    -- restored; the retention job removes a server once it has no campaigns left.
    CREATE TABLE campaign_guilds (
        guild_id BIGINT PRIMARY KEY
    );
    CREATE FUNCTION dmbot_note_campaign_guild() RETURNS trigger
        LANGUAGE plpgsql
        AS $fn$
    BEGIN
        INSERT INTO campaign_guilds (guild_id) VALUES (NEW.guild_id) ON CONFLICT DO NOTHING;
        RETURN NULL;
    END
    $fn$;
    CREATE TRIGGER campaign_guilds_note
        AFTER INSERT ON campaigns
        FOR EACH ROW EXECUTE FUNCTION dmbot_note_campaign_guild();
    -- The servers that hold campaigns now. Migrations run with no server set, so row-level
    -- security hides every campaign: the backfill opens the one table it reads for itself
    -- (CLAUDE.md), dropped again inside the migration's transaction.
    CREATE POLICY migrate_backfill ON campaigns FOR SELECT USING (true);
    INSERT INTO campaign_guilds (guild_id) SELECT DISTINCT guild_id FROM campaigns;
    DROP POLICY migrate_backfill ON campaigns;
    """

OWNER_LEFT = """
    -- A campaign whose owner deleted their account is kept, ownerless (#1059; owner decision
    -- 2026-10-11, #922): the players may still want to read and download it. `owner_left_at`
    -- is when it lost its owner; the retention job deletes it 120 days later unless another DM
    -- takes it on (which clears this). NULL for a campaign that never had an owner, or has one.
    -- A column with no default: no rows change, so no row-level-security policy is needed.
    ALTER TABLE campaigns ADD COLUMN owner_left_at BIGINT;

    -- Deleting an account must reach the person's campaigns in every server, which no
    -- server-scoped call can. Like the pause function (0037) this is the one door: it runs with
    -- its owner's rights, opens `campaigns` to itself for this one person through a setting it
    -- sets and puts back, can only turn that person's campaigns ownerless, and takes no person
    -- as an argument (it uses the one set for the transaction). Only the website's role calls it.
    -- (orphan_read also shows the row it has just made ownerless: an UPDATE's new row has to
    -- pass the SELECT rule too.)
    DO $do$
    BEGIN
        EXECUTE format(
            'CREATE POLICY orphan_read ON campaigns FOR SELECT TO %I
                USING (dmbot_owner_sync() = ''orphan''
                       AND (owner_user_id = dmbot_current_user()
                            OR (owner_user_id IS NULL AND owner_left_at IS NOT NULL)))',
            current_user);
        EXECUTE format(
            'CREATE POLICY orphan_update ON campaigns FOR UPDATE TO %I
                USING (dmbot_owner_sync() = ''orphan'' AND owner_user_id = dmbot_current_user())
                WITH CHECK (dmbot_owner_sync() = ''orphan'' AND owner_user_id IS NULL
                            AND owner_left_at IS NOT NULL)',
            current_user);
    END
    $do$;
    CREATE FUNCTION dmbot_orphan_owned_campaigns(at BIGINT) RETURNS BIGINT
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    DECLARE
        me BIGINT := dmbot_current_user();
        before TEXT := COALESCE(current_setting('dmbot.owner_sync', true), '');
        n BIGINT;
    BEGIN
        IF me IS NULL OR at IS NULL OR at <= 0 THEN
            RETURN 0;
        END IF;
        PERFORM set_config('dmbot.owner_sync', 'orphan', true);
        -- Campaign rows in a fixed order, like the pause function, so a hand-over or a pause
        -- running at the same time can't cross with this.
        PERFORM 1 FROM campaigns c WHERE c.owner_user_id = me
            ORDER BY c.guild_id, c.id FOR UPDATE;
        UPDATE campaigns SET owner_user_id = NULL, owner_left_at = at
            WHERE owner_user_id = me;
        GET DIAGNOSTICS n = ROW_COUNT;
        PERFORM set_config('dmbot.owner_sync', before, true);
        RETURN n;
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_orphan_owned_campaigns(BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_orphan_owned_campaigns(BIGINT) FROM PUBLIC;
"""

PLAN_ENDS = """
    -- A paid plan that its owner has cancelled, which keeps working to the end of the period
    -- already paid for (#824): the payment company's "cancelled" event stores that end here, and
    -- a renewal or a resume clears it. NULL while the plan will renew. A nullable column: no rows
    -- change, so no row-level-security policy is needed.
    ALTER TABLE entitlements ADD COLUMN ends_at BIGINT;

    -- The hours a person has used in one plan month, for the account page's hours bar. The
    -- website's role has no grant on owner_hours, and the table is scoped to the owner, so this
    -- is the one door: it takes no person (it uses the one set for the transaction, so it can't
    -- be pointed at anyone else) and returns one number, no server or campaign.
    CREATE FUNCTION dmbot_hours_used(month BIGINT) RETURNS BIGINT
        LANGUAGE sql STABLE SECURITY DEFINER
        AS $fn$
        SELECT COALESCE((SELECT minutes FROM owner_hours
            WHERE owner_user_id = dmbot_current_user() AND month_start = month), 0)::BIGINT
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_hours_used(BIGINT) SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_hours_used(BIGINT) FROM PUBLIC;
"""

EMAIL_SIGN_IN = (
    """
    -- Website sign-in by an emailed link (#1231; docs/PLAN.md, "Accounts"). A link is one use and
    -- lasts 15 minutes; only its SHA-256 is kept, like a session cookie, so a database copy
    -- can't sign anyone in. Made under the person's own scope; used (once) by whoever holds the
    -- token, through the same setting as a session cookie's hash.
    CREATE TABLE email_sign_in_links (
        token_hash  TEXT PRIMARY KEY,
        user_id     BIGINT NOT NULL REFERENCES web_users ON DELETE CASCADE,
        created_at  BIGINT NOT NULL,
        expires_at  BIGINT NOT NULL,
        used_at     BIGINT
    );
    CREATE INDEX email_sign_in_links_by_user ON email_sign_in_links (user_id);
    ALTER TABLE email_sign_in_links ENABLE ROW LEVEL SECURITY;
    ALTER TABLE email_sign_in_links FORCE ROW LEVEL SECURITY;
    CREATE POLICY user_isolation ON email_sign_in_links
        USING (user_id = dmbot_current_user())
        WITH CHECK (user_id = dmbot_current_user());
    -- Redeeming: the one unexpired, unused link whose hash was shown. It can be read and
    -- marked used, never made or changed in any other way.
    -- (A used link stays readable by its holder until it expires: the update's RETURNING is
    -- checked against this policy with the new row, which has `used_at` set.)
    CREATE POLICY by_token_read ON email_sign_in_links FOR SELECT
        USING (token_hash = dmbot_current_session() AND expires_at > dmbot_now());
    CREATE POLICY by_token_use ON email_sign_in_links FOR UPDATE
        USING (token_hash = dmbot_current_session() AND used_at IS NULL
               AND expires_at > dmbot_now())
        WITH CHECK (token_hash = dmbot_current_session());

    -- The name the person last signed in with, kept so a personal link can greet them with no
    -- session open. Nullable: no rows change, so no new policy.
    ALTER TABLE web_users ADD COLUMN display_name TEXT;
    -- The email lookup below compares lower case, so it needs this to avoid reading every row.
    CREATE INDEX web_users_by_email ON web_users (lower(email));

"""
    + _setting("dmbot_email_lookup", "dmbot.email_lookup", "TEXT")
    + """
    -- Who an email address belongs to, for sending a sign-in link. The website's role can't
    -- read web_users without a person set, and nobody is set yet when someone types an
    -- address, so this is the one door: an address in, at most one account number out. An
    -- address shared by two accounts finds neither (a link goes to no one rather than the
    -- wrong person). The API never says whether it found anyone.
    CREATE FUNCTION dmbot_user_by_email(addr TEXT) RETURNS BIGINT
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    DECLARE
        found BIGINT;
        before TEXT := COALESCE(current_setting('dmbot.email_lookup', true), '');
    BEGIN
        IF addr IS NULL OR addr = '' THEN
            RETURN NULL;
        END IF;
        PERFORM set_config('dmbot.email_lookup', 'on', true);
        -- The email Discord gave, or the one the payment company has (`payment_email`, added by
        -- 0067: a function body is looked up when it runs, so this migration may come first).
        -- Exactly one account must match; an address two accounts share finds neither.
        SELECT min(user_id) INTO found FROM web_users
            WHERE lower(email) = lower(addr) OR lower(payment_email) = lower(addr)
            HAVING count(*) = 1;
        PERFORM set_config('dmbot.email_lookup', before, true);
        RETURN found;
    END
    $fn$;
    -- web_users is forced to a person's own row, and the function runs with its owner's rights,
    -- so it needs a way in: a read-only policy TO the role that owns the function (the one
    -- running this migration), open only while the function has set its own setting. Never the
    -- website's role, which could otherwise set the setting itself and read every account's
    -- email.
    DO $do$
    BEGIN
        EXECUTE format(
            'CREATE POLICY email_lookup ON web_users FOR SELECT TO %I
                USING (dmbot_email_lookup() = ''on'')',
            current_user);
    END
    $do$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_user_by_email(TEXT) SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_user_by_email(TEXT) FROM PUBLIC;
    """
)

# What the website's role may touch at all: its own tables, and only reads of the two
# server tables /me needs. Everything else (consent, transcripts, memory...) is refused
# outright. Applied by Database.migrate whenever the role exists, so a new table is never
# opened to it by accident: it has to be added here.
# The functions the website's role may call (name, signature), granted in Database.migrate.
WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_owned_campaigns", "dmbot_owned_campaigns()"),
    ("dmbot_orphan_owned_campaigns", "dmbot_orphan_owned_campaigns(BIGINT)"),
    ("dmbot_hours_used", "dmbot_hours_used(BIGINT)"),
    ("dmbot_claim_email", "dmbot_claim_email(BIGINT, TEXT, BOOLEAN, INTEGER, INTEGER)"),
    # Sign-in by emailed link (#1231).
    ("dmbot_user_by_email", "dmbot_user_by_email(TEXT)"),
    # Experience points (#1228): the one door to the person's all-time minutes.
    ("dmbot_minutes_total", "dmbot_minutes_total()"),
    ("dmbot_user_of", "dmbot_user_of(TEXT)"),  # links made before a move (#1230)
    ("dmbot_moved_to", "dmbot_moved_to(BIGINT, TEXT, TEXT)"),  # payments after a move (#1230)
    ("dmbot_moves_to_sync", "dmbot_moves_to_sync()"),  # the job that updates Stripe (#1230)
    ("dmbot_move_synced", "dmbot_move_synced(BIGINT)"),
    ("dmbot_move_failed", "dmbot_move_failed(BIGINT)"),
    ("dmbot_previous_ids", "dmbot_previous_ids()"),  # the export's earlier Discord accounts
    ("dmbot_forget_account_history", "dmbot_forget_account_history()"),  # account deletion
    ("dmbot_set_payment_email", "dmbot_set_payment_email(TEXT, TEXT, BIGINT)"),  # #1242
    ("dmbot_forget_email_records", "dmbot_forget_email_records()"),  # account deletion, #1287
    # A move code asked for on the website (#1246).
    ("dmbot_web_move_code", "dmbot_web_move_code(BIGINT, TEXT, BIGINT, TEXT, BIGINT)"),
    ("dmbot_web_cancel_move_code", "dmbot_web_cancel_move_code(BIGINT, TEXT, BIGINT)"),
    ("dmbot_web_forget_move_asks", "dmbot_web_forget_move_asks(BIGINT)"),
)
# The columns the website may fill when it creates a hand-over offer (#728): who, where and the
# two names. Left out on purpose, so they take their defaults: status and decided_at (an offer
# starts open), and delivered_at, claimed_at, message_id and end_told_at, which only the bot
# sets (a writer that leaves delivered_at empty gets its offer sent by the bot). Not granted
# yet: no website route creates offers. The grant, when it comes, is
# `INSERT (<these>)` on campaign_handover_offers; the policy `web_new_offer` above backs it.
OFFER_INSERT_COLUMNS: tuple[str, ...] = (
    "guild_id",
    "campaign_id",
    "from_user_id",
    "to_user_id",
    "created_at",
    "from_name",
    "to_name",
)
WEB_ROLE_GRANTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("SELECT", ("schema_migrations",)),
    # The person's own consent and character sheets, for the data export (#1150); the
    # restrictive web_own_rows policies hold it to their own rows in their servers. The right
    # to stop (DELETE) on consent is the website's Stop recording me (#1151): the same
    # policies hold it to the person's own row; it can never add or change a yes.
    ("SELECT", ("character_sheets",)),
    ("SELECT, DELETE", ("consent",)),
    # Accepting a hand-over (#614) makes the person the owner and one of the DMs; the
    # store locks the campaign row first (FOR UPDATE needs an UPDATE right), and answering
    # changes only an offer's status.
    ("SELECT, UPDATE (owner_user_id)", ("campaigns",)),
    ("SELECT, INSERT", ("campaign_dms",)),
    ("SELECT, UPDATE (status, decided_at)", ("campaign_handover_offers",)),
    ("SELECT, INSERT, UPDATE", ("installs", "entitlements")),
    ("SELECT, INSERT", ("payment_events", "try_it_used")),
    # The unsubscribe link (#1229) may be written, never read: the list stays the bot's. The
    # website sends its own account emails through dmbot_claim_email (WEB_ROLE_FUNCTIONS), and
    # has no grant on sent_emails.
    ("INSERT", ("email_unsubscribed",)),
    ("INSERT, DELETE", ("feedback",)),
    # Support tickets (#1340): added by the signed-in person, read and closed only through the
    # admin page's door (the policies hold SELECT and UPDATE to it); the sweep and account
    # deletion delete. The UPDATE is for the status column alone.
    ("SELECT, INSERT, DELETE", ("support_tickets",)),
    ("UPDATE (status)", ("support_tickets",)),
    ("SELECT, INSERT, UPDATE, DELETE", ("web_users", "web_sessions")),
    # Emailed sign-in links (#1231): made and used by the website, never read by anyone else.
    # (Only `used_at` can be changed: a holder of a link can spend it, never revive or move it.)
    ("SELECT, INSERT, DELETE", ("email_sign_in_links",)),
    ("UPDATE (used_at)", ("email_sign_in_links",)),
    # Free access (#771): the admin page gives, changes and revokes grants (through
    # Database.grant_writer()); account deletion deletes the person's own.
    ("SELECT, INSERT, UPDATE, DELETE", ("access_grants",)),
    ("SELECT, INSERT", ("access_log",)),
)

SET_UPDATES = (
    f"""
    -- Shared rulesets part 2 (#1191): updates to a set, and who may write it.
    --   rule_set_follows.code   the share code a follower used, so its session start can ask
    --                           whether the set has a newer version. A code the publisher
    --                           turned off stops NEW followers only (owner decision): follows
    --                           made before keep getting updates, until Stop sharing.
    --   rule_set_codes.deactivated_at  when a code was turned off, for that comparison (codes
    --                           turned off before this migration have none, and keep their
    --                           old meaning: they do not update earlier follows)
    --   rule_set_follows.declined_version  the version an Undo sent back: DMbot does not
    --                           take it again at a session start, only a newer one; the DM
    --                           can still choose it with Pick changes
    --   rule_set_follows.previous_*, taken_*  the copy before the last update was taken, so
    --                           Undo needs no memory, and who took it (NULL: DMbot did, at a
    --                           session start; "auto")
    --   rule_set_versions.source  who made the version: the publisher's DMs or co-editors,
    --                           an accepted suggestion, or an accepted change found in the
    --                           shared document (the log of who and when is created_by and
    --                           created_at)
    --   rule_set_editors        people the publisher named as co-editors of its set
    --   house_rules.from_set    a rule that came from a followed set in a Cherry pick, as
    --                           "<set> house rules, v4": merged rules rank below the
    --                           campaign's own
    -- New columns with defaults or NULL: no row is rewritten, so no row-level security to open.
    ALTER TABLE rule_set_follows
        ADD COLUMN code TEXT CHECK (code IS NULL OR code ~ '^[A-Z2-7]{{10}}$'),
        ADD COLUMN previous_version INTEGER,
        ADD COLUMN previous_content JSONB,
        ADD COLUMN declined_version INTEGER,
        ADD COLUMN taken_by BIGINT,
        ADD COLUMN taken_source TEXT CHECK (taken_source IS NULL OR taken_source IN ('auto', 'dm'));
    ALTER TABLE rule_set_codes ADD COLUMN deactivated_at BIGINT;
    ALTER TABLE rule_set_versions
        ADD COLUMN source TEXT NOT NULL DEFAULT 'publisher'
        CHECK (source IN ('publisher', 'suggestion', 'document'));
    ALTER TABLE house_rules
        ADD COLUMN from_set TEXT CHECK (from_set IS NULL OR char_length(from_set) <= 200);
    CREATE TABLE rule_set_editors (
        {_memory_scope()}
        set_id      TEXT NOT NULL,
        user_id     BIGINT NOT NULL CHECK (user_id > 0),
        added_by    BIGINT NOT NULL CHECK (added_by > 0),
        added_at    BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, set_id, user_id),
        FOREIGN KEY (guild_id, campaign_id, set_id)
            REFERENCES rule_sets (guild_id, campaign_id, id) ON DELETE CASCADE
    );
    """
    + _isolate("rule_set_editors")
    + """
    -- The update door: like dmbot_set_by_code (0061) but for a follower asking whether the set it
    -- already follows has a newer version. A code turned off AFTER the follow still answers; one
    -- turned off before it, or a set that is no longer shared, does not. Same pattern as that
    -- door: policies for the function's owner only, keyed on the setting it sets.
    DO $do$
    BEGIN
        EXECUTE format(
            'CREATE POLICY update_door ON rule_set_codes FOR SELECT TO %I
                USING (dmbot_owner_sync() = ''setupdate''
                       AND code = current_setting(''dmbot.set_code'', true)
                       AND (active OR deactivated_at >=
                            NULLIF(current_setting(''dmbot.set_followed_at'', true),
                                   '''')::BIGINT))',
            current_user);
        EXECUTE format(
            'CREATE POLICY update_door ON rule_sets FOR SELECT TO %I
                USING (dmbot_owner_sync() = ''setupdate'' AND status = ''shared''
                       AND id IN (SELECT set_id FROM rule_set_codes))',
            current_user);
        EXECUTE format(
            'CREATE POLICY update_door ON rule_set_versions FOR SELECT TO %I
                USING (dmbot_owner_sync() = ''setupdate''
                       AND set_id IN (SELECT id FROM rule_sets))',
            current_user);
        EXECUTE format(
            'CREATE POLICY update_door ON campaigns FOR SELECT TO %I
                USING (dmbot_owner_sync() = ''setupdate''
                       AND EXISTS (SELECT 1 FROM rule_sets s
                                   WHERE s.guild_id = campaigns.guild_id
                                     AND s.campaign_id = campaigns.id))',
            current_user);
    END
    $do$;
    CREATE FUNCTION dmbot_set_update_by_code(wanted TEXT, followed_at BIGINT)
        RETURNS TABLE (set_id TEXT, kind TEXT, set_name TEXT, publisher TEXT, version INTEGER,
                       content JSONB)
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    DECLARE
        before TEXT := COALESCE(current_setting('dmbot.owner_sync', true), '');
        before_code TEXT := COALESCE(current_setting('dmbot.set_code', true), '');
        before_at TEXT := COALESCE(current_setting('dmbot.set_followed_at', true), '');
    BEGIN
        PERFORM set_config('dmbot.owner_sync', 'setupdate', true);
        PERFORM set_config('dmbot.set_code', wanted, true);
        PERFORM set_config('dmbot.set_followed_at', followed_at::TEXT, true);
        RETURN QUERY
            SELECT s.id, s.kind, s.name, c.name, v.version, v.content
            FROM rule_set_codes rc
            JOIN rule_sets s ON s.id = rc.set_id
            JOIN campaigns c ON c.guild_id = s.guild_id AND c.id = s.campaign_id
            JOIN rule_set_versions v ON v.guild_id = s.guild_id AND v.campaign_id = s.campaign_id
                AND v.set_id = s.id
            WHERE rc.code = wanted AND s.status = 'shared'
            ORDER BY v.version DESC
            LIMIT 1;
        PERFORM set_config('dmbot.owner_sync', before, true);
        PERFORM set_config('dmbot.set_code', before_code, true);
        PERFORM set_config('dmbot.set_followed_at', before_at, true);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_set_update_by_code(TEXT, BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_set_update_by_code(TEXT, BIGINT) FROM PUBLIC;
    """
)

MAIL = """
    -- Sending email (#1229; docs/PLAN.md, "Accounts"). Two small tables of ids and times, never
    -- an address or any text:
    --   sent_emails         who was sent which kind of email and when, so a one-off email goes
    --                       once and the daily cap can be counted (a kind that may repeat has
    --                       a round number in its name)
    --   email_unsubscribed  people who asked for no offer emails; written by the website's
    --                       unsubscribe link (which proves it with a signed token) and read by
    --                       the sender
    -- Like the routing tables they belong to no server, and they are written by jobs with no
    -- person set, so there is no row-level security to open.
    CREATE TABLE sent_emails (
        user_id BIGINT NOT NULL CHECK (user_id > 0),
        kind    TEXT   NOT NULL CHECK (char_length(kind) BETWEEN 1 AND 60),
        sent_at BIGINT NOT NULL,
        PRIMARY KEY (user_id, kind)
    );
    CREATE INDEX sent_emails_by_day ON sent_emails (sent_at);
    CREATE TABLE email_unsubscribed (
        user_id BIGINT PRIMARY KEY CHECK (user_id > 0),
        at      BIGINT NOT NULL
    );
    -- The one door for sending an email, for the bot and the website alike (the website's
    -- role has no grant on sent_emails: the table is everyone's email history). Under one
    -- lock it counts today's rows, applies the caps the caller passes (never above 400 in
    -- all and 300 for offers, so the sign-in links and move codes people ask for always have
    -- room), writes the row (an email of this kind goes once) and answers with a status:
    -- 'sent' (go ahead), 'duplicate' (already sent), 'capped' (the day is full) or 'refused'
    -- (not an email kind). The day and the time are the database's own. A failed send keeps
    -- its row (at most once), except an offer, which the bot gives back with
    -- dmbot_release_email so one hiccup doesn't lose it. Kinds starting "dm:" are private
    -- message records, not emails, and are refused here.
    CREATE FUNCTION dmbot_claim_email(who BIGINT, what TEXT, offer BOOLEAN, cap INTEGER,
                                      offer_cap INTEGER) RETURNS TEXT
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    #variable_conflict use_column
    DECLARE
        stamp BIGINT := extract(epoch FROM now())::BIGINT;
        today BIGINT;
        added INTEGER;
        most INTEGER := LEAST(COALESCE(cap, 400), 400);
        most_offers INTEGER := LEAST(COALESCE(offer_cap, 300), 300, most);
    BEGIN
        IF who IS NULL OR who <= 0 OR what IS NULL OR char_length(what) NOT BETWEEN 1 AND 60
           OR what LIKE 'dm:%' THEN
            RETURN 'refused';
        END IF;
        PERFORM pg_advisory_xact_lock(hashtextextended('dmbot.email_day', 0));
        SELECT count(*) INTO today FROM sent_emails s
            WHERE s.sent_at >= stamp - stamp % 86400 AND s.kind NOT LIKE 'dm:%';
        IF EXISTS (SELECT 1 FROM sent_emails s WHERE s.user_id = who AND s.kind = what) THEN
            RETURN 'duplicate';
        END IF;
        IF today >= most OR (offer AND today >= most_offers) THEN
            RETURN 'capped';
        END IF;
        INSERT INTO sent_emails (user_id, kind, sent_at) VALUES (who, what, stamp)
            ON CONFLICT DO NOTHING;
        GET DIAGNOSTICS added = ROW_COUNT;
        RETURN CASE WHEN added = 1 THEN 'sent' ELSE 'duplicate' END;
    END
    $fn$;
    -- Gives back the claim of an email that did not go (bot only: the website's role has no
    -- EXECUTE), so the next run tries again.
    CREATE FUNCTION dmbot_release_email(who BIGINT, what TEXT) RETURNS VOID
        LANGUAGE sql SECURITY DEFINER
        AS $fn$
        DELETE FROM sent_emails s WHERE s.user_id = who AND s.kind = what;
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_claim_email(BIGINT, TEXT, BOOLEAN, INTEGER, INTEGER)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_release_email(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_claim_email(BIGINT, TEXT, BOOLEAN, INTEGER, INTEGER) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_release_email(BIGINT, TEXT) FROM PUBLIC;
    """

EMAIL_RECORDS = """
    -- Deleting an account also deletes the person's sent-email records (#1287; docs/PLAN.md,
    -- "Accounts"). The website's role has no grant on sent_emails (it is everyone's email
    -- history), so this door deletes the signed-in person's own rows and nobody else's.
    -- email_unsubscribed stays on purpose: it is the never-email-me-again list.
    CREATE FUNCTION dmbot_forget_email_records() RETURNS INTEGER
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    DECLARE
        gone INTEGER;
    BEGIN
        DELETE FROM sent_emails s WHERE s.user_id = dmbot_current_user();
        GET DIAGNOSTICS gone = ROW_COUNT;
        RETURN gone;
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_forget_email_records()'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_forget_email_records() FROM PUBLIC;
    """

TRIAL = """
    -- Try It's last-day message and automatic extension (#1229; docs/PLAN.md, "Accounts").
    --   trial_extensions   one row for each 30 days added after Try It ended, so the bot can
    --                      tell the person at their next /dmbot start (told_at) and the next
    --                      extension counts from this one. Ids and times only.
    -- And two doors for the bot's daily job, which acts for no one person: it may list who is
    -- in Try It (with the facts the rules need) and extend one person's Try It. Both are
    -- SECURITY DEFINER, granted to the bot's role only, never the website's.
    -- Like try_it_used, these rows are ids and times only and are not removed with an account:
    -- a person who deletes their account and signs up again is not messaged twice.
    -- (sent_emails also holds the private-message records of these jobs, kinds starting "dm:";
    -- they are not emails and do not count toward the daily cap.)
    CREATE TABLE trial_extensions (
        user_id     BIGINT NOT NULL CHECK (user_id > 0),
        extended_at BIGINT NOT NULL,
        told_at     BIGINT,
        PRIMARY KEY (user_id, extended_at)
    );
    -- Like the orphan and pause doors (0037, 0044), the functions open the tables they read to
    -- themselves through a setting they set and put back, and only for this one purpose.
    DO $do$
    BEGIN
        EXECUTE format(
            'CREATE POLICY trial_read ON entitlements FOR SELECT TO %I
                USING (dmbot_owner_sync() = ''trial'')', current_user);
        EXECUTE format(
            'CREATE POLICY trial_update ON entitlements FOR UPDATE TO %I
                USING (dmbot_owner_sync() = ''trial'' AND plan = ''try-it'')
                WITH CHECK (dmbot_owner_sync() = ''trial'' AND plan = ''try-it'')',
            current_user);
        EXECUTE format(
            'CREATE POLICY trial_read ON web_users FOR SELECT TO %I
                USING (dmbot_owner_sync() = ''trial'')', current_user);
        EXECUTE format(
            'CREATE POLICY trial_read ON owner_hours FOR SELECT TO %I
                USING (dmbot_owner_sync() = ''trial'')', current_user);
        EXECUTE format(
            'CREATE POLICY trial_read ON payment_events FOR SELECT TO %I
                USING (dmbot_owner_sync() = ''trial'')', current_user);
        EXECUTE format(
            'CREATE POLICY trial_read ON access_grants FOR SELECT TO %I
                USING (dmbot_owner_sync() = ''trial'')', current_user);
    END
    $do$;
    CREATE FUNCTION dmbot_trial_people(since BIGINT, now BIGINT)
        RETURNS TABLE (user_id BIGINT, dmbot_id TEXT, email TEXT, period_end BIGINT,
                       minutes BIGINT, paid BOOLEAN, extensions INTEGER, granted BOOLEAN)
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    #variable_conflict use_column
    DECLARE
        before TEXT := COALESCE(current_setting('dmbot.owner_sync', true), '');
    BEGIN
        PERFORM set_config('dmbot.owner_sync', 'trial', true);
        RETURN QUERY
        SELECT e.user_id, u.dmbot_id, u.email, e.period_end,
               COALESCE((SELECT sum(h.minutes) FROM owner_hours h
                         WHERE h.owner_user_id = e.user_id), 0)::BIGINT,
               EXISTS (SELECT 1 FROM payment_events p WHERE p.user_id = e.user_id
                       AND (p.kind IS NULL OR p.kind = ANY(
                            ARRAY['subscription_started', 'subscription_renewed',
                                  'extra_hours_bought']))),
               (SELECT count(*)::INTEGER FROM trial_extensions t WHERE t.user_id = e.user_id),
               EXISTS (SELECT 1 FROM access_grants g WHERE g.discord_user_id = e.user_id
                       AND g.revoked_at IS NULL
                       AND (g.ends_at IS NULL OR g.ends_at > now))
        FROM entitlements e JOIN web_users u ON u.user_id = e.user_id
        WHERE e.plan = 'try-it' AND e.status = 'active' AND e.period_end >= since;
        PERFORM set_config('dmbot.owner_sync', before, true);
    END
    $fn$;
    -- Try It's 8 hours are 8 hours in all: an extension has a cap of 8 hours less the minutes
    -- already used, counted in minutes (`minutes_taken` comes off the cap). The new cap is
    -- worked out here from the meter, whatever the caller thinks.
    ALTER TABLE entitlements ADD COLUMN minutes_taken INTEGER NOT NULL DEFAULT 0
        CHECK (minutes_taken >= 0);
    CREATE FUNCTION dmbot_extend_trial(who BIGINT, now BIGINT, days INTEGER)
        RETURNS BOOLEAN
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    #variable_conflict use_column
    DECLARE
        before TEXT := COALESCE(current_setting('dmbot.owner_sync', true), '');
        used BIGINT;
        changed INTEGER;
    BEGIN
        PERFORM set_config('dmbot.owner_sync', 'trial', true);
        -- Only a Try It that has ended and is still the person's plan, for 30 days, three
        -- days after the end, for someone who never paid and has used under 4 hours in all.
        IF days <> 30 THEN
            PERFORM set_config('dmbot.owner_sync', before, true);
            RETURN FALSE;
        END IF;
        SELECT COALESCE(sum(h.minutes), 0) INTO used FROM owner_hours h
            WHERE h.owner_user_id = who;
        UPDATE entitlements
           SET period_start = now, period_end = now + days * 86400, hours_cap = 8,
               minutes_taken = used, extra_hours = 0, plan_changed_at = now, updated_at = now
         WHERE entitlements.user_id = who AND plan = 'try-it' AND status = 'active'
           AND period_end + 259200 <= now AND used < 240
           AND NOT EXISTS (SELECT 1 FROM payment_events p WHERE p.user_id = who
                           AND (p.kind IS NULL OR p.kind = ANY(
                                ARRAY['subscription_started', 'subscription_renewed',
                                      'extra_hours_bought'])));
        GET DIAGNOSTICS changed = ROW_COUNT;
        PERFORM set_config('dmbot.owner_sync', before, true);
        IF changed = 0 THEN
            RETURN FALSE;
        END IF;
        INSERT INTO trial_extensions (user_id, extended_at) VALUES (who, now);
        RETURN TRUE;
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_trial_people(BIGINT, BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_extend_trial(BIGINT, BIGINT, INTEGER)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_trial_people(BIGINT, BIGINT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_extend_trial(BIGINT, BIGINT, INTEGER) FROM PUBLIC;
    """

PAYMENT_EMAIL = """
    -- The email Stripe has for the account's payments (#1242; docs/PLAN.md, "Accounts"),
    -- kept from the payment webhook beside the Discord email the website sign-in gives.
    -- Sign-in links and Try It emails use it before the Discord email; move codes go to it
    -- and nowhere else. Never logged or shown back in full.
    ALTER TABLE web_users ADD COLUMN payment_email TEXT
        CHECK (payment_email IS NULL OR char_length(payment_email) BETWEEN 3 AND 254);
    -- When the company said so (its event time): an older event never replaces a newer one.
    ALTER TABLE web_users ADD COLUMN payment_email_at BIGINT;
    -- Sign-in by emailed link finds an account by lower(email) or lower(payment_email); without
    -- this every request would scan web_users.
    CREATE INDEX web_users_by_payment_email ON web_users (lower(payment_email))
        WHERE payment_email IS NOT NULL;
    -- A changed address arrives with the company's customer id, not ours: this door finds the
    -- account by the customer id stored with its plan and sets the address, only for an event
    -- newer than the one already kept. The website's role has no other way to look an account
    -- up by customer id.
    DO $do$
    BEGIN
        EXECUTE format(
            'CREATE POLICY paymail ON web_users FOR ALL TO %I'
            ' USING (dmbot_owner_sync() = ''paymail'')'
            ' WITH CHECK (dmbot_owner_sync() = ''paymail'')',
            current_user);
        EXECUTE format(
            'CREATE POLICY paymail ON entitlements FOR SELECT TO %I'
            ' USING (dmbot_owner_sync() = ''paymail'')',
            current_user);
    END
    $do$;
    CREATE FUNCTION dmbot_set_payment_email(cust TEXT, addr TEXT, happened BIGINT)
        RETURNS BOOLEAN
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    #variable_conflict use_column
    DECLARE
        before TEXT := COALESCE(current_setting('dmbot.owner_sync', true), '');
        changed INTEGER;
    BEGIN
        IF cust IS NULL OR addr IS NULL OR char_length(addr) NOT BETWEEN 3 AND 254 THEN
            RETURN FALSE;
        END IF;
        PERFORM set_config('dmbot.owner_sync', 'paymail', true);
        UPDATE web_users u SET payment_email = addr, payment_email_at = happened
            FROM entitlements e
            WHERE e.user_id = u.user_id AND e.provider_customer_id = cust
              AND (u.payment_email_at IS NULL OR u.payment_email_at < happened);
        GET DIAGNOSTICS changed = ROW_COUNT;
        PERFORM set_config('dmbot.owner_sync', before, true);
        RETURN changed > 0;
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_set_payment_email(TEXT, TEXT, BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_set_payment_email(TEXT, TEXT, BIGINT) FROM PUBLIC;
    -- Try It's job reads the address through its door: the payment email first.
    CREATE OR REPLACE FUNCTION dmbot_trial_people(since BIGINT, now BIGINT)
        RETURNS TABLE (user_id BIGINT, dmbot_id TEXT, email TEXT, period_end BIGINT,
                       minutes BIGINT, paid BOOLEAN, extensions INTEGER, granted BOOLEAN)
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    #variable_conflict use_column
    DECLARE
        before TEXT := COALESCE(current_setting('dmbot.owner_sync', true), '');
    BEGIN
        PERFORM set_config('dmbot.owner_sync', 'trial', true);
        RETURN QUERY
        SELECT e.user_id, u.dmbot_id, COALESCE(u.payment_email, u.email), e.period_end,
               COALESCE((SELECT sum(h.minutes) FROM owner_hours h
                         WHERE h.owner_user_id = e.user_id), 0)::BIGINT,
               EXISTS (SELECT 1 FROM payment_events p WHERE p.user_id = e.user_id
                       AND (p.kind IS NULL OR p.kind = ANY(
                            ARRAY['subscription_started', 'subscription_renewed',
                                  'extra_hours_bought']))),
               (SELECT count(*)::INTEGER FROM trial_extensions t WHERE t.user_id = e.user_id),
               EXISTS (SELECT 1 FROM access_grants g WHERE g.discord_user_id = e.user_id
                       AND g.revoked_at IS NULL
                       AND (g.ends_at IS NULL OR g.ends_at > now))
        FROM entitlements e JOIN web_users u ON u.user_id = e.user_id
        WHERE e.plan = 'try-it' AND e.status = 'active' AND e.period_end >= since;
        PERFORM set_config('dmbot.owner_sync', before, true);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_trial_people(BIGINT, BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    """

MOVES = """
    -- Moving an account to a new Discord account (#1230; docs/PLAN.md, "Accounts"). A paid
    -- account may move once in 30 days; the plan, its hours, the Stripe link, the campaigns it
    -- owns and its place in them go to the new Discord ID in ONE transaction. The DMbot ID
    -- stays. Recording consent does not move.
    --   move_codes      the one move code an account has asked for: only its hash, when it
    --                   was made and runs out, and when it was used
    --   account_moves   the "moved" marker: which Discord ID moved to which, when, and the
    --                   DMbot ID that went with it; told_at is when the old account was
    --                   told (the first time it uses DMbot again). Unscoped like try_it_used.
    CREATE TABLE move_codes (
        user_id    BIGINT PRIMARY KEY CHECK (user_id > 0),
        code_hash  TEXT NOT NULL UNIQUE CHECK (char_length(code_hash) = 64),
        created_at BIGINT NOT NULL,
        expires_at BIGINT NOT NULL,
        used_at    BIGINT
    );
    CREATE TABLE account_moves (
        old_user_id BIGINT PRIMARY KEY CHECK (old_user_id > 0),
        new_user_id BIGINT NOT NULL CHECK (new_user_id > 0),  -- not unique: A to B to C to B
        dmbot_id    TEXT NOT NULL,
        moved_at    BIGINT NOT NULL,
        told_at     BIGINT,
        -- The payment company's ids at the move, so the website's job can point them at the
        -- new Discord ID after the commit (stripe_done_at NULL = still to do).
        customer_id        TEXT,
        subscription_id    TEXT,
        stripe_done_at     BIGINT,
        -- Backoff for the update, kept here so it survives a restart and every web process
        -- agrees: tries so far, and when the last one was (database time).
        stripe_attempts    INTEGER NOT NULL DEFAULT 0,
        stripe_last_try_at BIGINT
    );
    CREATE INDEX account_moves_by_id ON account_moves (dmbot_id, moved_at);
    CREATE INDEX account_moves_by_new ON account_moves (new_user_id);
    -- Every Discord account a DMbot account has used (owner request, 2026-10-14): ONE row for
    -- each move, written inside dmbot_move_account in the same transaction, and never
    -- changed. The only deletion is the account's own deletion (dmbot_forget_account_history).
    -- email_used is the payment email at the time of the move (where the move code went).
    -- account_moves stays the routing marker; this is the record. Row-level security by
    -- person: a row is visible to the account that holds its DMbot ID; the website reads
    -- through dmbot_previous_ids(), and has no grant on the table.
    CREATE TABLE account_id_history (
        id          BIGSERIAL PRIMARY KEY,
        dmbot_id    TEXT   NOT NULL,
        old_user_id BIGINT NOT NULL CHECK (old_user_id > 0),
        new_user_id BIGINT NOT NULL CHECK (new_user_id > 0),
        moved_at    BIGINT NOT NULL,
        email_used  TEXT
    );
    CREATE INDEX account_id_history_by_id ON account_id_history (dmbot_id, id);
    ALTER TABLE account_id_history ENABLE ROW LEVEL SECURITY;
    ALTER TABLE account_id_history FORCE ROW LEVEL SECURITY;
    CREATE POLICY user_isolation ON account_id_history FOR SELECT
        USING (dmbot_id IN (SELECT u.dmbot_id FROM web_users u
                            WHERE u.user_id = dmbot_current_user()));
    DO $do$
    BEGIN
        EXECUTE format(
            'CREATE POLICY move_insert ON account_id_history FOR INSERT TO %I'
            ' WITH CHECK (dmbot_owner_sync() = ''move'')',
            current_user);
        EXECUTE format(
            'CREATE POLICY forget_delete ON account_id_history FOR DELETE TO %I'
            ' USING (dmbot_owner_sync() = ''forget_history'')',
            current_user);
    END
    $do$;
    CREATE INDEX account_moves_to_sync ON account_moves (moved_at) WHERE stripe_done_at IS NULL;
    -- Rows that point at the account follow its Discord ID when the move changes it.
    ALTER TABLE entitlements DROP CONSTRAINT entitlements_user_id_fkey,
        ADD CONSTRAINT entitlements_user_id_fkey FOREIGN KEY (user_id)
            REFERENCES web_users ON DELETE CASCADE ON UPDATE CASCADE;
    ALTER TABLE installs DROP CONSTRAINT installs_installed_by_user_id_fkey,
        ADD CONSTRAINT installs_installed_by_user_id_fkey FOREIGN KEY (installed_by_user_id)
            REFERENCES web_users ON DELETE SET NULL ON UPDATE CASCADE;
    -- A move changes the Discord ID on the account's own row (the DMbot ID stays), so the
    -- unique check on the DMbot ID is never in play. Only the move may do that; any other
    -- change of either still fails. An old Discord ID that moved away can't make a new
    -- account on the website: its DMbot ID is the moved account's.
    -- (SECURITY DEFINER: it reads account_moves, which the website's role can't.)
    CREATE OR REPLACE FUNCTION dmbot_web_users_id() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    BEGIN
        IF TG_OP = 'INSERT' THEN
            IF EXISTS (SELECT 1 FROM account_moves WHERE old_user_id = NEW.user_id) THEN
                RAISE EXCEPTION 'This account moved'
                    USING ERRCODE = 'P0001', HINT = 'account_moved';
            END IF;
            NEW.dmbot_id := dmbot_base36(NEW.user_id);
        ELSIF NEW.dmbot_id IS DISTINCT FROM OLD.dmbot_id THEN
            RAISE EXCEPTION 'A DMbot ID never changes';
        ELSIF NEW.user_id <> OLD.user_id AND dmbot_owner_sync() <> 'move' THEN
            RAISE EXCEPTION 'A DMbot ID never changes';
        END IF;
        RETURN NEW;
    END
    $fn$;
    -- The move door opens the tables it touches to itself through a setting it sets and puts
    -- back (like the orphan and Try It doors). One policy for each table, for the function's
    -- owner only.
    DO $do$
    DECLARE
        t TEXT;
    BEGIN
        FOREACH t IN ARRAY ARRAY['web_users', 'entitlements', 'web_sessions', 'installs',
                                 'payment_events', 'owner_hours', 'session_usage',
                                 'try_it_used', 'campaigns', 'campaign_dms',
                                 'memory_entities', 'active_sessions',
                                 'campaign_handover_offers']
        LOOP
            EXECUTE format(
                'CREATE POLICY move_all ON %I FOR ALL TO %I
                    USING (dmbot_owner_sync() = ''move'')
                    WITH CHECK (dmbot_owner_sync() = ''move'')',
                t, current_user);
        END LOOP;
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_users_id() SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    CREATE FUNCTION dmbot_move_account(old BIGINT, new BIGINT, now BIGINT, which TEXT)
        RETURNS TEXT
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    #variable_conflict use_column
    DECLARE
        before TEXT := COALESCE(current_setting('dmbot.owner_sync', true), '');
        acct RECORD;
        ent RECORD;
        owned TEXT[];
        outcome TEXT := 'moved';
    BEGIN
        PERFORM set_config('dmbot.owner_sync', 'move', true);
        IF old IS NULL OR new IS NULL OR old <= 0 OR new <= 0 OR old = new THEN
            outcome := 'same';
        END IF;
        -- Same lock order as the hand-over and the pause door: the campaigns (in a fixed
        -- order), then the people. A payment for either account waits for the move.
        IF old IS NOT NULL AND new IS NOT NULL AND old > 0 AND new > 0 AND old <> new THEN
            PERFORM 1 FROM campaigns c WHERE c.owner_user_id IN (old, new)
                ORDER BY c.guild_id, c.id FOR UPDATE;
            PERFORM pg_advisory_xact_lock(
                hashtextextended('dmbot.owner_slots:' || least(old, new)::text, 0));
            PERFORM pg_advisory_xact_lock(
                hashtextextended('dmbot.owner_slots:' || greatest(old, new)::text, 0));
            PERFORM pg_advisory_xact_lock(
                hashtextextended('dmbot.plan_writer:' || least(old, new)::text, 0));
            PERFORM pg_advisory_xact_lock(
                hashtextextended('dmbot.plan_writer:' || greatest(old, new)::text, 0));
        END IF;
        IF outcome = 'moved' THEN
            SELECT * INTO acct FROM web_users WHERE user_id = old FOR UPDATE;
            IF NOT FOUND THEN
                outcome := 'no_account';
            END IF;
        END IF;
        IF outcome = 'moved' THEN
            SELECT * INTO ent FROM entitlements WHERE user_id = old FOR UPDATE;
            IF NOT FOUND OR ent.plan = 'try-it' OR ent.status <> 'active'
               OR ent.provider_subscription_id IS NULL THEN
                outcome := 'not_paid';
            END IF;
        END IF;
        IF outcome <> 'moved' THEN  -- nothing below reads a record that was never found
            PERFORM set_config('dmbot.owner_sync', before, true);
            RETURN outcome;
        END IF;
        -- The code is checked and spent here, in the same transaction as the move, so two
        -- moves with one code can't both happen.
        IF outcome = 'moved' THEN
            PERFORM 1 FROM move_codes m WHERE m.user_id = old AND m.code_hash = which
                AND m.used_at IS NULL AND m.expires_at > now FOR UPDATE;
            IF NOT FOUND THEN
                outcome := 'bad_code';
            END IF;
        END IF;
        IF outcome <> 'moved' THEN
            PERFORM set_config('dmbot.owner_sync', before, true);
            RETURN outcome;
        END IF;
        IF outcome = 'moved' AND EXISTS (
                SELECT 1 FROM active_sessions a JOIN campaigns c
                    ON c.id = a.campaign_id AND c.guild_id = a.guild_id
                WHERE c.owner_user_id = new) THEN
            outcome := 'target_listening';
        END IF;
        IF outcome = 'moved' AND EXISTS (SELECT 1 FROM account_moves
                                         WHERE dmbot_id = acct.dmbot_id
                                           AND moved_at > now - 30 * 86400) THEN
            outcome := 'too_soon';
        END IF;
        IF outcome = 'moved' AND EXISTS (SELECT 1 FROM entitlements
                                         WHERE user_id = new AND plan <> 'try-it'
                                           AND status <> 'lapsed') THEN
            outcome := 'new_has_plan';
        END IF;
        -- The target must be a fresh account, or the account this one came from (moving back).
        -- Any plan it ever had, or being the target of an earlier move, is its own history.
        IF outcome = 'moved' AND (
               EXISTS (SELECT 1 FROM entitlements e WHERE e.user_id = new AND e.plan <> 'try-it')
            OR EXISTS (SELECT 1 FROM payment_events p WHERE p.user_id = new
                       AND (p.kind IS NULL OR p.kind = ANY(
                            ARRAY['subscription_started', 'subscription_renewed',
                                  'extra_hours_bought'])))
            OR EXISTS (SELECT 1 FROM account_moves m
                       WHERE m.new_user_id = new AND m.dmbot_id <> acct.dmbot_id)
            OR EXISTS (SELECT 1 FROM account_moves m
                       WHERE m.old_user_id = new AND m.dmbot_id <> acct.dmbot_id)) THEN
            outcome := 'target_history';
        END IF;
        IF outcome = 'moved' AND EXISTS (
                SELECT 1 FROM active_sessions a JOIN campaigns c
                    ON c.id = a.campaign_id AND c.guild_id = a.guild_id
                WHERE c.owner_user_id = old) THEN
            outcome := 'listening';
        END IF;
        IF outcome = 'moved' AND (SELECT count(*) FROM campaigns
                                  WHERE owner_user_id IN (old, new)) > ent.campaign_cap THEN
            outcome := 'too_many_campaigns';
        END IF;
        IF outcome <> 'moved' THEN
            PERFORM set_config('dmbot.owner_sync', before, true);
            RETURN outcome;
        END IF;

        SELECT COALESCE(array_agg(id), '{}') INTO owned FROM campaigns WHERE owner_user_id = old;
        -- Whatever the new account had of its own that is not a paid plan goes: a Try It
        -- or a stopped plan, and its sign-ins.
        DELETE FROM entitlements WHERE user_id = new;
        DELETE FROM web_users WHERE user_id = new;
        DELETE FROM web_sessions WHERE user_id = old;
        -- The account's own row takes the new Discord ID; its plan and installs follow it.
        UPDATE web_users SET user_id = new WHERE user_id = old;
        UPDATE payment_events SET user_id = new WHERE user_id = old;

        -- The new account's own Try It minutes don't count toward the paid plan: they go.
        DELETE FROM owner_hours WHERE owner_user_id = new;
        INSERT INTO owner_hours (owner_user_id, month_start, minutes, grace_session)
            SELECT new, month_start, minutes, grace_session FROM owner_hours
             WHERE owner_user_id = old
            ON CONFLICT (owner_user_id, month_start)
            DO UPDATE SET minutes = owner_hours.minutes + EXCLUDED.minutes;
        DELETE FROM owner_hours WHERE owner_user_id = old;
        UPDATE session_usage SET owner_user_id = new WHERE owner_user_id = old;

        -- An offer to hand a campaign over, to or from the old account, ends with the move.
        UPDATE campaign_handover_offers SET status = 'withdrawn', decided_at = now
            WHERE status = 'open' AND (from_user_id = old OR to_user_id = old
                                       OR from_user_id = new OR to_user_id = new);
        UPDATE campaigns SET owner_user_id = new WHERE owner_user_id = old;
        DELETE FROM campaign_dms d WHERE d.user_id = old AND d.campaign_id = ANY(owned)
            AND EXISTS (SELECT 1 FROM campaign_dms n
                        WHERE n.campaign_id = d.campaign_id AND n.user_id = new);
        UPDATE campaign_dms SET user_id = new WHERE user_id = old AND campaign_id = ANY(owned);
        UPDATE memory_entities e SET played_by = new
            WHERE e.played_by = old AND e.campaign_id = ANY(owned)
              AND NOT EXISTS (SELECT 1 FROM memory_entities x
                              WHERE x.guild_id = e.guild_id AND x.campaign_id = e.campaign_id
                                AND x.type = 'person' AND x.played_by = new
                                AND x.status IN ('proposed', 'confirmed'));

        -- One free trial for each person, and a moved ID can never start another.
        INSERT INTO try_it_used (user_id) VALUES (old), (new) ON CONFLICT DO NOTHING;
        DELETE FROM move_codes WHERE user_id = old;
        -- "No offer emails" is about the person, so it goes with the account.
        INSERT INTO email_unsubscribed (user_id, at)
            SELECT new, at FROM email_unsubscribed WHERE user_id = old
            ON CONFLICT DO NOTHING;
        -- Moving back: the earlier marker for this Discord ID ends, it is the account again.
        DELETE FROM account_moves WHERE old_user_id = new;
        INSERT INTO account_id_history (dmbot_id, old_user_id, new_user_id, moved_at, email_used)
            VALUES (acct.dmbot_id, old, new, now, acct.payment_email);
        INSERT INTO account_moves (old_user_id, new_user_id, dmbot_id, moved_at, customer_id,
                                   subscription_id, stripe_done_at)
            VALUES (old, new, acct.dmbot_id, now, ent.provider_customer_id,
                    ent.provider_subscription_id,
                    CASE WHEN ent.provider_customer_id IS NULL
                          AND ent.provider_subscription_id IS NULL THEN now END);
        PERFORM set_config('dmbot.owner_sync', before, true);
        RETURN 'moved';
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_move_account(BIGINT, BIGINT, BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_move_account(BIGINT, BIGINT, BIGINT, TEXT) FROM PUBLIC;
    -- Two small doors for the website's role, which has no grant on account_moves:
    --   dmbot_user_of(id)   the Discord ID that holds a DMbot ID now (an unsubscribe or
    --                       personal link carries the ID the account was made with, and the
    --                       account may have moved since), or no row
    --   dmbot_moved_to(old, subscription, customer)
    --                       the Discord ID a moved account's plan went to, only when that
    --                       plan is the one the payment event names, so a plan the old
    --                       account buys later is never sent to the new one
    CREATE FUNCTION dmbot_user_of(id TEXT) RETURNS BIGINT
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    #variable_conflict use_column
    DECLARE
        before TEXT := COALESCE(current_setting('dmbot.owner_sync', true), '');
        found BIGINT;
    BEGIN
        PERFORM set_config('dmbot.owner_sync', 'move', true);
        SELECT u.user_id INTO found FROM web_users u WHERE u.dmbot_id = id;
        PERFORM set_config('dmbot.owner_sync', before, true);
        RETURN found;
    END
    $fn$;
    CREATE FUNCTION dmbot_moved_to(old BIGINT, sub TEXT, cust TEXT) RETURNS BIGINT
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    #variable_conflict use_column
    DECLARE
        before TEXT := COALESCE(current_setting('dmbot.owner_sync', true), '');
        found BIGINT;
        here BIGINT := old;
        hop BIGINT;
    BEGIN
        PERFORM set_config('dmbot.owner_sync', 'move', true);
        -- Follow the moves (a few at most) to the account that holds the plan now.
        FOR i IN 1..5 LOOP
            SELECT m.new_user_id INTO hop FROM account_moves m WHERE m.old_user_id = here;
            EXIT WHEN hop IS NULL;
            here := hop;
            IF EXISTS (SELECT 1 FROM entitlements e
                       WHERE e.user_id = here
                         AND ((sub IS NOT NULL AND e.provider_subscription_id = sub)
                              OR (cust IS NOT NULL AND e.provider_customer_id = cust))) THEN
                found := here;
                EXIT;
            END IF;
            hop := NULL;
        END LOOP;
        PERFORM set_config('dmbot.owner_sync', before, true);
        RETURN found;
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_user_of(TEXT) SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_moved_to(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_user_of(TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_moved_to(BIGINT, TEXT, TEXT) FROM PUBLIC;
    -- The payment company's records name the Discord ID the plan was bought with. After a
    -- move commits, the website's job points them at the new one: these two doors say which
    -- moves still need it, and that one is done (nothing else of account_moves is readable).
    CREATE FUNCTION dmbot_moves_to_sync()
        RETURNS TABLE (old_user_id BIGINT, new_user_id BIGINT, customer_id TEXT,
                       subscription_id TEXT)
        LANGUAGE sql SECURITY DEFINER
        AS $fn$
        -- Never tried first, then the longest ago; one that failed waits a minute, two,
        -- four... up to an hour, so a few stuck moves can't crowd out a new one.
        SELECT m.old_user_id, m.new_user_id, m.customer_id, m.subscription_id
            FROM account_moves m
            WHERE m.stripe_done_at IS NULL
              AND (m.stripe_last_try_at IS NULL
                   OR m.stripe_last_try_at + LEAST(60 * (2 ^ LEAST(m.stripe_attempts, 6))::BIGINT,
                                                   3600) <= extract(epoch FROM now())::BIGINT)
            ORDER BY m.stripe_last_try_at NULLS FIRST, m.moved_at
            LIMIT 20;
    $fn$;
    CREATE FUNCTION dmbot_move_failed(who BIGINT) RETURNS VOID
        LANGUAGE sql SECURITY DEFINER
        AS $fn$
        UPDATE account_moves SET stripe_attempts = stripe_attempts + 1,
                                 stripe_last_try_at = extract(epoch FROM now())::BIGINT
            WHERE old_user_id = who AND stripe_done_at IS NULL;
    $fn$;
    CREATE FUNCTION dmbot_move_synced(who BIGINT) RETURNS VOID
        LANGUAGE sql SECURITY DEFINER
        AS $fn$
        UPDATE account_moves SET stripe_done_at = extract(epoch FROM now())::BIGINT
            WHERE old_user_id = who AND stripe_done_at IS NULL;
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_moves_to_sync() SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_move_synced(BIGINT) SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_move_failed(BIGINT) SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_moves_to_sync() FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_move_synced(BIGINT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_move_failed(BIGINT) FROM PUBLIC;
    -- The signed-in person's earlier Discord accounts, oldest first, for the data export. The
    -- function runs as the table's owner, whose row-level security is the same person-only
    -- policy, so it can only ever return the caller's own rows.
    CREATE FUNCTION dmbot_previous_ids()
        RETURNS TABLE (old_user_id BIGINT, moved_at BIGINT, email_used TEXT)
        LANGUAGE sql STABLE SECURITY DEFINER
        AS $fn$
        SELECT h.old_user_id, h.moved_at, h.email_used FROM account_id_history h ORDER BY h.id;
    $fn$;
    -- Account deletion takes the history with it: the person's own DMbot ID only.
    CREATE FUNCTION dmbot_forget_account_history() RETURNS INTEGER
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    DECLARE
        before TEXT := COALESCE(current_setting('dmbot.owner_sync', true), '');
        mine TEXT;
        gone INTEGER;
    BEGIN
        SELECT u.dmbot_id INTO mine FROM web_users u WHERE u.user_id = dmbot_current_user();
        IF mine IS NULL THEN
            RETURN 0;
        END IF;
        PERFORM set_config('dmbot.owner_sync', 'forget_history', true);
        DELETE FROM account_id_history h WHERE h.dmbot_id = mine;
        GET DIAGNOSTICS gone = ROW_COUNT;
        PERFORM set_config('dmbot.owner_sync', before, true);
        RETURN gone;
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_previous_ids() SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_forget_account_history()'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_previous_ids() FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_forget_account_history() FROM PUBLIC;
    -- What a move would take, for the confirmation screen: the plan and the name of each
    -- campaign. Names only; nothing else of a campaign leaves.
    CREATE FUNCTION dmbot_move_preview(old BIGINT) RETURNS TABLE (kind TEXT, label TEXT)
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    #variable_conflict use_column
    DECLARE
        before TEXT := COALESCE(current_setting('dmbot.owner_sync', true), '');
    BEGIN
        PERFORM set_config('dmbot.owner_sync', 'move', true);
        RETURN QUERY SELECT 'plan'::TEXT, e.plan FROM entitlements e WHERE e.user_id = old;
        RETURN QUERY SELECT 'campaign'::TEXT, c.name FROM campaigns c
            WHERE c.owner_user_id = old ORDER BY c.name, c.id;
        PERFORM set_config('dmbot.owner_sync', before, true);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_move_preview(BIGINT) SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_move_preview(BIGINT) FROM PUBLIC;
    """


# Numbered 0063: 0060 to 0062 (owner gone, shared rulesets, their updates) have merged. A
# migration list may have gaps, so a later number never has to wait for an earlier one.
DMBOT_ID = """
    -- The DMbot ID (#1228; docs/PLAN.md, "Accounts"): a Discord ID in base 36, the Stripe invoice
    -- prefix, fixed for the life of the account. The database makes it (one source of truth for
    -- every account, including the ones made before this), a trigger fills it on insert whatever
    -- the caller says, and another refuses any later change. dmbot.account_id is the same
    -- function in Python, and a test holds the two together.
    CREATE FUNCTION dmbot_base36(n BIGINT) RETURNS TEXT
        LANGUAGE plpgsql IMMUTABLE
        SET search_path = pg_catalog
        AS $fn$
    DECLARE
        digits CONSTANT TEXT := '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ';
        rest BIGINT := n;
        out TEXT := '';
    BEGIN
        IF n IS NULL OR n <= 0 THEN
            RAISE EXCEPTION 'A Discord ID is a positive number';
        END IF;
        WHILE rest > 0 LOOP
            out := substr(digits, (rest % 36)::INTEGER + 1, 1) || out;
            rest := rest / 36;
        END LOOP;
        IF char_length(out) > 12 THEN
            RAISE EXCEPTION 'That ID is too big for a DMbot ID';
        END IF;
        RETURN out;
    END
    $fn$;
    ALTER TABLE web_users ADD COLUMN dmbot_id TEXT;
    -- Migrations run with no server or person set, so row-level security hides every row:
    -- open the table to this one UPDATE, dropped again inside the migration's transaction.
    CREATE POLICY migrate_backfill ON web_users FOR ALL USING (true) WITH CHECK (true);
    UPDATE web_users SET dmbot_id = dmbot_base36(user_id);
    DROP POLICY migrate_backfill ON web_users;
    ALTER TABLE web_users
        ALTER COLUMN dmbot_id SET NOT NULL,
        ADD CONSTRAINT web_users_dmbot_id_form CHECK (dmbot_id ~ '^[1-9A-Z][0-9A-Z]{0,11}$'),
        ADD CONSTRAINT web_users_dmbot_id_unique UNIQUE (dmbot_id);
    CREATE FUNCTION dmbot_web_users_id() RETURNS trigger
        LANGUAGE plpgsql
        AS $fn$
    BEGIN
        IF TG_OP = 'INSERT' THEN
            NEW.dmbot_id := dmbot_base36(NEW.user_id);
        ELSIF NEW.dmbot_id IS DISTINCT FROM OLD.dmbot_id OR NEW.user_id <> OLD.user_id THEN
            RAISE EXCEPTION 'A DMbot ID never changes';
        END IF;
        RETURN NEW;
    END
    $fn$;
    -- Experience points (#1228): all-time minutes of the person set for the transaction. The
    -- website's role has no grant on owner_hours, so, like dmbot_hours_used, this is the one
    -- door: it takes no person, uses the one set for the transaction, and returns one number.
    CREATE FUNCTION dmbot_minutes_total() RETURNS BIGINT
        LANGUAGE sql STABLE SECURITY DEFINER
        AS $fn$
        SELECT COALESCE((SELECT sum(minutes) FROM owner_hours
            WHERE owner_user_id = dmbot_current_user()), 0)::BIGINT
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_minutes_total() SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_minutes_total() FROM PUBLIC;
    CREATE TRIGGER web_users_dmbot_id BEFORE INSERT OR UPDATE ON web_users
        FOR EACH ROW EXECUTE FUNCTION dmbot_web_users_id();
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_users_id() SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    """

# The website's way to a move code (#1246). The website's role has no grant on move_codes or
# account_moves, so it asks through this one door, which does the whole job in one transaction:
# the account must be on a paid plan and not have moved in the last 30 days, the code's hash is
# stored (the earlier code stops working), and the limits are counted here, in the database, so
# every web process agrees and a restart forgets nothing: 3 codes an hour for an account and 5
# an hour for a connection. `src` is a keyed hash (HMAC) of the connection's address: the address
# itself is never stored, and the hash can't be turned back into one without the server's secret.
# The caps match account_move.py's ASKS and MOVE_GAP_S; a test holds them together.
WEB_MOVE_CODES = """
    CREATE TABLE move_code_asks (
        -- Goes with the account (deletion) and follows it through a move.
        user_id  BIGINT NOT NULL CHECK (user_id > 0)
                 REFERENCES web_users ON DELETE CASCADE ON UPDATE CASCADE,
        source   TEXT   NOT NULL CHECK (char_length(source) = 64),
        asked_at BIGINT NOT NULL
    );
    CREATE INDEX move_code_asks_by_user ON move_code_asks (user_id, asked_at);
    CREATE INDEX move_code_asks_by_source ON move_code_asks (source, asked_at);
    CREATE FUNCTION dmbot_web_move_code(who BIGINT, src TEXT, now BIGINT, hash TEXT, ends BIGINT)
        RETURNS TEXT
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    #variable_conflict use_column
    DECLARE
        before TEXT := COALESCE(current_setting('dmbot.owner_sync', true), '');
        account TEXT;
        ent RECORD;
        outcome TEXT := 'ok';
    BEGIN
        PERFORM set_config('dmbot.owner_sync', 'move', true);
        -- One ask at a time for an account, so two at once can't both pass the count.
        SELECT u.dmbot_id INTO account FROM web_users u WHERE u.user_id = who FOR UPDATE;
        IF account IS NULL THEN
            outcome := 'no_account';
        ELSE
            SELECT e.plan, e.status, e.provider_subscription_id INTO ent
                FROM entitlements e WHERE e.user_id = who;
            IF NOT FOUND OR ent.plan = 'try-it' OR ent.status <> 'active'
               OR ent.provider_subscription_id IS NULL THEN
                outcome := 'not_paid';
            ELSIF EXISTS (SELECT 1 FROM account_moves m
                          WHERE m.dmbot_id = account AND m.moved_at > now - 2592000) THEN
                outcome := 'too_soon';
            ELSE
                -- One count at a time for a connection too, so asks from several accounts at
                -- once can't all see "fewer than 5".
                PERFORM pg_advisory_xact_lock(hashtextextended('dmbot.move_ask:' || src, 0));
                DELETE FROM move_code_asks a WHERE a.asked_at <= now - 3600;
                IF (SELECT count(*) FROM move_code_asks a WHERE a.user_id = who) >= 3
                   OR (SELECT count(*) FROM move_code_asks a WHERE a.source = src) >= 5 THEN
                    outcome := 'slow_down';
                ELSE
                    INSERT INTO move_code_asks (user_id, source, asked_at) VALUES (who, src, now);
                    INSERT INTO move_codes (user_id, code_hash, created_at, expires_at)
                        VALUES (who, hash, now, LEAST(ends, now + 1800))
                        ON CONFLICT (user_id) DO UPDATE SET code_hash = EXCLUDED.code_hash,
                            created_at = EXCLUDED.created_at, expires_at = EXCLUDED.expires_at,
                            used_at = NULL;
                END IF;
            END IF;
        END IF;
        PERFORM set_config('dmbot.owner_sync', before, true);
        RETURN outcome;
    END
    $fn$;
    -- The email didn't go, so no code should be waiting that nobody has seen, and the ask
    -- is given back: a failed send must not use up an hour's three. Only that very code is
    -- removed (a newer one asked for meanwhile, and already mailed, must stay); `at` is the
    -- time the ask was made.
    CREATE FUNCTION dmbot_web_cancel_move_code(who BIGINT, hash TEXT, at BIGINT) RETURNS VOID
        LANGUAGE sql SECURITY DEFINER
        AS $fn$
            DELETE FROM move_codes WHERE user_id = who AND code_hash = hash AND used_at IS NULL;
            DELETE FROM move_code_asks WHERE ctid = (SELECT a.ctid FROM move_code_asks a
                WHERE a.user_id = who AND a.asked_at = at LIMIT 1);
        $fn$;
    -- The hourly sweep: asks older than the window are of no use.
    CREATE FUNCTION dmbot_web_forget_move_asks(now BIGINT) RETURNS BIGINT
        LANGUAGE sql SECURITY DEFINER
        AS $fn$
            WITH gone AS (DELETE FROM move_code_asks WHERE asked_at <= now - 3600 RETURNING 1)
            SELECT count(*) FROM gone
        $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_move_code(BIGINT, TEXT, BIGINT, TEXT, BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_cancel_move_code(BIGINT, TEXT, BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_forget_move_asks(BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_move_code(BIGINT, TEXT, BIGINT, TEXT, BIGINT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_cancel_move_code(BIGINT, TEXT, BIGINT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_forget_move_asks(BIGINT) FROM PUBLIC;
"""

MEMORY_STATES = (
    f"""
    -- Story memory 5a, slice 1 (#1267; docs/STORY_MEMORY.md, "State facts"):
    --   memory_states         one current condition for a character, item or place (alive, dead,
    --                         intact, destroyed…). A new one closes the old (closed_at), so the
    --                         history is kept with when and who said so. Own table, not a
    --                         relationship. Like every memory table it is one campaign's.
    --   memory_entities.unique_item  an item of which only one exists. NULL: nobody said, so the
    --                         code decides from the name (a proper name means yes). Nullable so
    --                         this migration changes no existing row.
    --   memory_flags.kind     three new kinds from the new checks.
    CREATE TABLE memory_states (
        {_memory_scope()}
        id              TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{{32}}$'),
        entity_id       TEXT NOT NULL,
        condition       TEXT NOT NULL CHECK (condition IN
            ('alive', 'dead', 'missing', 'captured', 'transformed', 'unknown',
             'intact', 'destroyed', 'lost')),
        from_session_at BIGINT,
        source          TEXT NOT NULL,
        created_at      BIGINT NOT NULL,
        closed_at       BIGINT,
        PRIMARY KEY (guild_id, campaign_id, id),
        {_entity_link("entity_id")}
    );
    -- One current condition for each entry.
    CREATE UNIQUE INDEX memory_states_one_current
        ON memory_states (guild_id, campaign_id, entity_id) WHERE closed_at IS NULL;
    CREATE INDEX memory_states_by_entity ON memory_states (guild_id, campaign_id, entity_id);
    """
    + _isolate("memory_states")
    + """

    ALTER TABLE memory_entities ADD COLUMN unique_item BOOLEAN;

    ALTER TABLE memory_flags DROP CONSTRAINT memory_flags_kind_check;
    ALTER TABLE memory_flags ADD CONSTRAINT memory_flags_kind_check CHECK (kind IN
        ('wrong_subject', 'wrong_object', 'too_many', 'contradiction',
         'held_twice', 'place_loop', 'acts_after_end'));
    """
)

RULE_SUGGESTIONS = (
    f"""
    -- "Suggest to <set>" (#1259, #1191 part 2b): a following campaign's DM sends one of its own
    -- house rules to the set's publisher. Only the rule's words cross, never who sent it.
    --   rule_set_suggestions       in the PUBLISHER's campaign: the words, the answer, nothing
    --                              that says which campaign, server or DM sent them
    --   rule_set_suggestions_sent  in the FOLLOWER's campaign: what its DM sent, to which set,
    --                              and when it was told the answer. The two are linked only by
    --                              the random id the follower made, which is a key to one row.
    -- The doors across servers are the functions below: one adds a suggestion to a set the
    -- caller follows (and answers where it went, for DMbot to post the note), one tells the
    -- answers to the ids it is given. Like the other doors they open the tables to
    -- themselves through a setting they set and put back.
    CREATE TABLE rule_set_suggestions (
        {_memory_scope()}
        id          TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{{32}}$'),
        set_id      TEXT NOT NULL,
        rule        TEXT NOT NULL CHECK (char_length(rule) BETWEEN 1 AND 500),
        instead     TEXT CHECK (instead IS NULL OR char_length(instead) BETWEEN 1 AND 500),
        status      TEXT NOT NULL CHECK (status IN ('open', 'accepted', 'declined', 'present')),
        created_at  BIGINT NOT NULL,
        resolved_at BIGINT,
        resolved_by BIGINT,
        version     INTEGER,
        noted_at    BIGINT,  -- when the note reached the publisher's DM screen (NULL: not yet)
        PRIMARY KEY (guild_id, campaign_id, id),
        UNIQUE (id),
        FOREIGN KEY (guild_id, campaign_id, set_id)
            REFERENCES rule_sets (guild_id, campaign_id, id) ON DELETE CASCADE
    );
    CREATE INDEX rule_set_suggestions_open ON rule_set_suggestions (set_id) WHERE status = 'open';
    CREATE TABLE rule_set_suggestions_sent (
        {_memory_scope()}
        id          TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{{32}}$'),
        set_id      TEXT NOT NULL,
        set_name    TEXT NOT NULL CHECK (char_length(set_name) BETWEEN 1 AND 60),
        rule        TEXT NOT NULL CHECK (char_length(rule) BETWEEN 1 AND 500),
        sent_by     BIGINT NOT NULL CHECK (sent_by > 0),
        sent_at     BIGINT NOT NULL,
        told_at     BIGINT,
        PRIMARY KEY (guild_id, campaign_id, id)
    );
    """
    + _isolate("rule_set_suggestions")
    + _isolate("rule_set_suggestions_sent")
    + """
    DO $do$
    BEGIN
        EXECUTE format(
            'CREATE POLICY suggest_door ON rule_sets FOR SELECT TO %I
                USING (dmbot_owner_sync() = ''suggest'' AND status = ''shared''
                       AND id = current_setting(''dmbot.set_id'', true))',
            current_user);
        EXECUTE format(
            'CREATE POLICY suggest_door_read ON rule_set_suggestions FOR SELECT TO %I
                USING (dmbot_owner_sync() = ''suggest''
                       AND set_id = current_setting(''dmbot.set_id'', true))',
            current_user);
        EXECUTE format(
            'CREATE POLICY suggest_door_add ON rule_set_suggestions FOR INSERT TO %I
                WITH CHECK (dmbot_owner_sync() = ''suggest''
                            AND set_id = current_setting(''dmbot.set_id'', true))',
            current_user);
        EXECUTE format(
            'CREATE POLICY answer_door ON rule_set_suggestions FOR SELECT TO %I
                USING (dmbot_owner_sync() = ''suggestanswer''
                       AND id = ANY(string_to_array(
                           COALESCE(current_setting(''dmbot.suggestion_ids'', true), $q$$q$),
                           $q$,$q$)))',
            current_user);
    END
    $do$;
    -- Adds a suggestion to a set the caller's campaign follows. 'ok' (with the publisher's
    -- server and campaign, which DMbot uses to post the note and never shows anyone), 'full'
    -- (the set already has `set_cap` open suggestions), or 'gone' (the caller does not follow it,
    -- or it is not shared any more).
    CREATE FUNCTION dmbot_suggest_rule(
        sid TEXT, wanted_set TEXT, follower_campaign TEXT, new_rule TEXT, new_instead TEXT,
        now_at BIGINT, set_cap INTEGER, wait_s INTEGER)
        RETURNS TABLE (outcome TEXT, pub_guild BIGINT, pub_campaign TEXT)
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    DECLARE
        before TEXT := COALESCE(current_setting('dmbot.owner_sync', true), '');
        before_set TEXT := COALESCE(current_setting('dmbot.set_id', true), '');
        found_guild BIGINT;
        found_campaign TEXT;
        waiting INTEGER;
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM rule_set_follows f
                       WHERE f.guild_id = dmbot_current_guild()
                         AND f.campaign_id = follower_campaign
                         AND f.set_id = wanted_set AND f.updating) THEN
            RETURN QUERY SELECT 'gone'::TEXT, NULL::BIGINT, NULL::TEXT;
            RETURN;
        END IF;
        PERFORM set_config('dmbot.owner_sync', 'suggest', true);
        PERFORM set_config('dmbot.set_id', wanted_set, true);
        SELECT s.guild_id, s.campaign_id INTO found_guild, found_campaign
            FROM rule_sets s WHERE s.id = wanted_set AND s.status = 'shared';
        IF found_guild IS NULL THEN
            PERFORM set_config('dmbot.owner_sync', before, true);
            PERFORM set_config('dmbot.set_id', before_set, true);
            RETURN QUERY SELECT 'gone'::TEXT, NULL::BIGINT, NULL::TEXT;
            RETURN;
        END IF;
        -- Two tables suggesting at once take turns, so the cap holds.
        PERFORM pg_advisory_xact_lock(hashtext(wanted_set));
        SELECT count(*) INTO waiting FROM rule_set_suggestions q
            WHERE q.set_id = wanted_set AND q.status = 'open'
              AND q.created_at > now_at - wait_s;  -- an unanswered one stops counting
        IF waiting >= set_cap THEN
            PERFORM set_config('dmbot.owner_sync', before, true);
            PERFORM set_config('dmbot.set_id', before_set, true);
            RETURN QUERY SELECT 'full'::TEXT, NULL::BIGINT, NULL::TEXT;
            RETURN;
        END IF;
        INSERT INTO rule_set_suggestions (guild_id, campaign_id, id, set_id, rule, instead,
                                          status, created_at)
            VALUES (found_guild, found_campaign, sid, wanted_set, new_rule, new_instead,
                    'open', now_at);
        PERFORM set_config('dmbot.owner_sync', before, true);
        PERFORM set_config('dmbot.set_id', before_set, true);
        RETURN QUERY SELECT 'ok'::TEXT, found_guild, found_campaign;
    END
    $fn$;
    -- The answers to the suggestions with these ids (a comma-separated list the caller made
    -- itself): the id is the key, so nothing else of the publisher's can be reached.
    CREATE FUNCTION dmbot_suggestion_answers(ids TEXT)
        RETURNS TABLE (id TEXT, status TEXT, version INTEGER)
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    DECLARE
        before TEXT := COALESCE(current_setting('dmbot.owner_sync', true), '');
        before_ids TEXT := COALESCE(current_setting('dmbot.suggestion_ids', true), '');
    BEGIN
        PERFORM set_config('dmbot.owner_sync', 'suggestanswer', true);
        PERFORM set_config('dmbot.suggestion_ids', ids, true);
        RETURN QUERY
            SELECT q.id, q.status, q.version FROM rule_set_suggestions q
            WHERE q.id = ANY(string_to_array(ids, ','));
        PERFORM set_config('dmbot.owner_sync', before, true);
        PERFORM set_config('dmbot.suggestion_ids', before_ids, true);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_suggest_rule('
            'TEXT, TEXT, TEXT, TEXT, TEXT, BIGINT, INTEGER, INTEGER)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_suggestion_answers(TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_suggest_rule(
        TEXT, TEXT, TEXT, TEXT, TEXT, BIGINT, INTEGER, INTEGER) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_suggestion_answers(TEXT) FROM PUBLIC;
    """
)

MEMORY_CLAIMS = (
    """
    -- Story memory 5a, slice 2 (#1297; docs/STORY_MEMORY.md, section 1): what the story
    -- extractor read out of a session, held as proposals until the DM says yes or no. One row
    -- for each claim, as text (the memory may not know the names yet), with how it was said, the
    -- transcript lines it cites, who spoke them (all of them: consent is checked again when the
    -- DM answers, and a person who stops being recorded has their claims deleted), which prompt
    -- and model made it, and its status: proposed, confirmed, rejected or dropped (the rules
    -- refused it; the reason is kept). A claim is never a fact: a confirmed one has been written
    -- through the store as the DM's own, and fact_id says which (the relationship's id, or the
    -- entry for a change of state), so a fact can always be traced back to the claim and the
    -- lines it came from. Not in the undo log. Like every memory table it is one campaign's.
    CREATE TABLE memory_claims (
        """
    + _memory_scope()
    + """
        id                 TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{32}$'),
        subject            TEXT NOT NULL,
        relationship       TEXT NOT NULL,
        object             TEXT NOT NULL,
        details            TEXT NOT NULL DEFAULT '',
        how                TEXT NOT NULL CHECK (how IN ('dm_said', 'player_said', 'plan')),
        lines              TEXT[] NOT NULL,
        speaker_ids        BIGINT[] NOT NULL DEFAULT '{}',
        source             TEXT NOT NULL,
        prompt_version     TEXT NOT NULL,
        model              TEXT NOT NULL,
        status             TEXT NOT NULL CHECK (status IN
            ('proposed', 'confirmed', 'rejected', 'dropped')),
        reason             TEXT NOT NULL DEFAULT '',
        session_started_at BIGINT,
        created_at         BIGINT NOT NULL,
        decided_at         BIGINT,
        fact_id            TEXT,
        PRIMARY KEY (guild_id, campaign_id, id)
    );
    CREATE INDEX memory_claims_waiting ON memory_claims (guild_id, campaign_id, status);
    """
    + _isolate("memory_claims")
    + """
    """
)

SUPPORT_TICKETS = (
    _setting("dmbot_support_admin", "dmbot.support_admin", "TEXT")
    + """
    -- Private support tickets from paying people on the website (#1340; docs/PLAN.md,
    -- "QAbot and support tickets"). They hold personal details, so they never become public
    -- GitHub issues. `feedback` (0026) is the model: the website adds a row and cannot read
    -- it back. The one difference is the admin page, which is served by the website's own
    -- role, so it needs a way to read and close tickets that nobody else has:
    --   dmbot.support_admin  set only by Database.support_admin(): the admin page's API,
    --                        the only code allowed to read a ticket or change its status
    -- The website's role can set that itself (as it can dmbot.grant_writer): which code opens
    -- the door is a Python boundary (tests/test_support.py), and the admin sign-in guards it.
    --   user_id  the signed-in account that sent it (a Discord id, as in web_users)
    --   plan     the plan's id when it was sent, so the team sees who is paying for what
    --   source   where it came from; 'email' is for a later step (reading the mailbox)
    CREATE TABLE support_tickets (
        id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        created_at  BIGINT NOT NULL,
        user_id     BIGINT NOT NULL CHECK (user_id > 0),
        plan        TEXT NOT NULL CHECK (char_length(plan) BETWEEN 1 AND 40),
        subject     TEXT NOT NULL CHECK (char_length(subject) BETWEEN 1 AND 120),
        message     TEXT NOT NULL CHECK (char_length(message) BETWEEN 1 AND 4000),
        source      TEXT NOT NULL DEFAULT 'website' CHECK (source IN ('website', 'email')),
        status      TEXT NOT NULL DEFAULT 'new' CHECK (status IN ('new', 'answered', 'closed'))
    );
    CREATE INDEX support_tickets_newest ON support_tickets (created_at DESC);
    ALTER TABLE support_tickets ENABLE ROW LEVEL SECURITY;
    ALTER TABLE support_tickets FORCE ROW LEVEL SECURITY;
    -- A person can add a ticket only for their own account, and only a new one from the
    -- website: they can't read any ticket (their own included) or change one.
    CREATE POLICY add_own ON support_tickets FOR INSERT
        WITH CHECK (user_id = dmbot_current_user() AND source = 'website' AND status = 'new');
    -- The admin page's door: read every ticket, and change a status (the column grant lets
    -- nothing else change).
    CREATE POLICY support_admin_read ON support_tickets FOR SELECT
        USING (dmbot_support_admin() = 'admin');
    CREATE POLICY support_admin_status ON support_tickets FOR UPDATE
        USING (dmbot_support_admin() = 'admin') WITH CHECK (dmbot_support_admin() = 'admin');
    -- Deleting an account deletes that person's own tickets (like their grant and sessions);
    -- the hourly sweep deletes tickets older than 12 months without needing to read them.
    CREATE POLICY own_delete ON support_tickets FOR DELETE
        USING (user_id = dmbot_current_user());
    CREATE POLICY forget_after_a_year ON support_tickets FOR DELETE
        USING (dmbot_cleanup() = 'old-tickets' AND created_at <= dmbot_now() - 365 * 86400);
    """
)

MEMORY_CLAIMS_BY_FACT = """
    -- The recall service (#1350) finds the claim a fact came from by the fact's id; without this
    -- it would read every confirmed claim of the campaign to find them. An index only: no row
    -- changes, so no row-level-security opening is needed.
    CREATE INDEX memory_claims_by_fact ON memory_claims (guild_id, campaign_id, fact_id)
        WHERE fact_id IS NOT NULL;
    """

MIGRATIONS: tuple[Migration, ...] = (
    ("0001_initial", INITIAL),
    ("0002_active_sessions", ACTIVE_SESSIONS),
    ("0003_channel_number", CHANNEL_NUMBER),
    ("0004_campaign_memory", CAMPAIGN_MEMORY),
    ("0005_consent_terms", CONSENT_TERMS),
    ("0006_consent_outside", CONSENT_OUTSIDE),
    ("0007_transcript_channel", TRANSCRIPT_CHANNEL),
    ("0008_played_by", PLAYED_BY),
    ("0009_transcripts", TRANSCRIPTS),
    ("0010_memory_heard", MEMORY_HEARD),
    ("0011_web_accounts", WEB_ACCOUNTS),
    ("0012_transcript_engines", TRANSCRIPT_ENGINES),
    ("0013_web_session_name", WEB_SESSION_NAME),
    ("0014_installs_left_at", INSTALLS_LEFT),
    ("0015_dm_screen_level", DM_SCREEN_LEVEL),
    ("0016_web_role_limits", WEB_ROLE_LIMITS),
    ("0017_try_it_bare_id", TRY_IT_BARE_ID),
    ("0018_payment_event_subscription", PAYMENT_EVENT_SUBSCRIPTION),
    ("0019_shared_confirmations", SHARED_CONFIRMATIONS),
    ("0020_campaign_owner", CAMPAIGN_OWNER),
    ("0021_campaign_handover_offers", HANDOVER_OFFERS),
    ("0022_handover_offer_names", HANDOVER_NAMES),
    ("0023_handover_offer_indexes", HANDOVER_OFFER_INDEXES),
    ("0024_transcript_topics", TRANSCRIPT_TOPICS),
    ("0025_handover_delivered", HANDOVER_DELIVERED),
    ("0026_feedback", FEEDBACK),
    ("0027_handover_expiry", HANDOVER_EXPIRY),
    ("0028_character_sheets", CHARACTER_SHEETS),
    ("0029_sheet_player", SHEET_PLAYER),
    ("0030_access_grants", ACCESS_GRANTS),
    ("0031_house_rules", HOUSE_RULES),
    ("0032_usage", USAGE),
    ("0033_usage_grace", USAGE_GRACE),
    ("0034_owner_campaigns", OWNER_CAMPAIGNS),
    ("0035_rules_cards", RULES_CARDS),
    ("0036_transcript_sidebar", TRANSCRIPT_SIDEBAR),
    ("0037_pause", PAUSE),
    ("0038_payment_event_kind", PAYMENT_EVENT_KIND),
    ("0039_retention", RETENTION),
    ("0040_house_rules_file", HOUSE_RULES_FILE),
    ("0041_game_clocks", GAME_CLOCKS),
    ("0042_game_effects", GAME_EFFECTS),
    ("0043_test_voice_consent", TEST_VOICE_CONSENT),
    ("0044_memory_kinds", MEMORY_KINDS),
    ("0045_campaign_guilds", CAMPAIGN_GUILDS),
    ("0046_transcript_entity", TRANSCRIPT_ENTITY),
    ("0047_owner_left", OWNER_LEFT),
    ("0048_plan_ends", PLAN_ENDS),
    ("0049_game_effects_extended", GAME_EFFECTS_EXTENDED),
    ("0050_gametime_channel", GAMETIME_CHANNEL),
    ("0051_dndbeyond_invite", DNDBEYOND_INVITE),
    ("0052_table_chat", TABLE_CHAT),
    ("0053_combat_state", COMBAT_STATE),
    ("0054_sheet_questions", SHEET_QUESTIONS),
    ("0055_fightbot", FIGHTBOT),
    ("0056_dmbchat_channel", DMBCHAT_CHANNEL),
    ("0057_table_questions", TABLE_QUESTIONS),
    ("0058_people", PEOPLE),
    ("0059_definitions", DEFINITIONS),
    ("0060_owner_missing", OWNER_MISSING),
    ("0061_rule_sets", RULE_SETS),
    ("0062_set_updates", SET_UPDATES),
    ("0063_dmbot_id", DMBOT_ID),
    ("0064_mail", MAIL),
    ("0065_trial", TRIAL),
    ("0066_email_sign_in", EMAIL_SIGN_IN),
    ("0067_payment_email", PAYMENT_EMAIL),
    ("0068_moves", MOVES),  # 0066 is the sign-in-by-email migration (#1237)
    ("0069_web_move_codes", WEB_MOVE_CODES),  # the website's door to a move code (#1246)
    ("0070_memory_states", MEMORY_STATES),  # 0069 is the website's move code (#1246)
    ("0071_email_records", EMAIL_RECORDS),  # the email records (#1288)
    ("0072_rule_suggestions", RULE_SUGGESTIONS),  # the shared-rules suggestions (#1261)
    ("0073_memory_claims", MEMORY_CLAIMS),
    ("0074_support_tickets", SUPPORT_TICKETS),  # private support tickets (#1340)
    (
        "0076_claims_by_fact",
        MEMORY_CLAIMS_BY_FACT,
    ),  # the recall service finds a fact's claim (#1350)
)

# Tables that must have row-level security. A test checks every table in the schema
# except these is listed here, so a new table can't forget its policy.
ISOLATED_TABLES = (
    "test_voice_consent",
    "campaigns",
    "campaign_dms",
    "campaign_optional_rules",
    "consent",
    "active_sessions",
    *_MEMORY_TABLES,
    "transcript_sessions",
    "transcript_lines",
    "memory_heard",
    "shared_confirmations",
    "campaign_handover_offers",
    "character_sheets",
    "house_rules",
    "house_rules_file",
    "session_usage",
    "game_clocks",
    "game_effects",
    "memory_rule_links",
    "fightbot_players",
    "rule_sets",
    "rule_set_versions",
    "rule_set_codes",
    "rule_set_follows",
    "rule_set_editors",
    "memory_states",
    "memory_claims",
    "rule_set_suggestions",
    "rule_set_suggestions_sent",
)
# A person's own rows (the website, #435): row-level security on `user_id`, set by
# Database.user(). Sessions can also be found by their cookie hash (Database.session()),
# and installs are visible to their server and to the person who installed DMbot.
USER_ISOLATED_TABLES = (
    "web_users",
    "try_it_used",
    "entitlements",
    "payment_events",
    "web_sessions",
    "email_sign_in_links",
    "installs",
    # Every Discord account a DMbot account used; keyed by its DMbot ID (#1230).
    "account_id_history",
    # Keyed by Discord account (#771): the person's own grant, or the grant writer.
    "access_grants",
    # Not per person: only the grant writer adds or reads a row (#771).
    "access_log",
    # A person adds their own ticket and can read none; only the support admin door reads (#1340).
    "support_tickets",
    # An owner's hours per plan month; only the meter adds to it (#437).
    "owner_hours",
    # Which campaigns an owner has, across servers; kept by a trigger on campaigns (#437).
    "owner_campaigns",
)
# Add-only: no policy allows reading a row; the team reads them as the database's
# administrator (#665).
WRITE_ONLY_TABLES = ("feedback",)
# Hold only server IDs (see the rules at the top of this file).
ROUTING_TABLES = ("live_session_guilds", "campaign_guilds")
UNSCOPED_TABLES = (
    "schema_migrations",
    "ontology_predicate_versions",
    "sent_emails",
    "email_unsubscribed",
    "trial_extensions",
    "move_codes",
    "account_moves",
    "move_code_asks",
    *ROUTING_TABLES,
)
